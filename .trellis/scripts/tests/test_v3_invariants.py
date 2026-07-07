from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import state_machine
import common.io as common_io
from board import build_board
from common import config, trellis_config
from common.active_task import clear_active_task, resolve_active_task, set_active_task
from common.io import write_json
from common.safe_commit import safe_git_add, safe_trellis_paths_to_add
from common.task_context import _validate_v2_task
from common.task_store import (
    _advance_child_to_archived,
    _advance_parent_to_archived,
    _done_gate_errors,
    _template_text,
    _write_template_files,
)
from common.tasks import children_progress, get_all_statuses
from state_machine import StateMachineError, apply_event, init_task, status


def make_harness_task(task_dir: Path, tier: str = "child") -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": task_dir.name,
        "name": task_dir.name,
        "title": task_dir.name,
        "tier": tier,
        "meta": {"workflow_mode": "harness_state_machine"},
    }
    (task_dir / "task.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_board_task(
    tasks_dir: Path,
    name: str,
    *,
    status_value: str = "in_progress",
    tier: str = "child",
    children: list[str] | None = None,
    parent: str | None = None,
    current_state: str | None = None,
) -> None:
    meta: dict[str, object] = {"workflow_mode": "harness_state_machine"}
    if current_state:
        meta["state_machine"] = {"kind": tier, "current_state": current_state}
    task_dir = tasks_dir / name
    task_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": name,
        "name": name,
        "title": name,
        "status": status_value,
        "tier": tier,
        "owner": "codex",
        "assignee": "jym",
        "children": children or [],
        "parent": parent,
        "meta": meta,
    }
    (task_dir / "task.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class StateMachineInvariantTests(unittest.TestCase):
    def test_canonical_parent_and_child_transitions_archive(self) -> None:
        cases = {
            "child": [
                ("plan_drafted", "child_waiting_completion_signal"),
                ("completion_signal_received", "child_commit_ready"),
                ("commit_created", "child_archive_ready"),
                ("child_archive_completed", "child_archived"),
            ],
            "parent": [
                ("prd_drafted", "parent_waiting_completion_signal"),
                ("parent_completion_signal_received", "parent_commit_ready"),
                ("parent_commit_created", "parent_archive_ready"),
                ("parent_archive_completed", "parent_archived"),
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            for kind, transitions in cases.items():
                with self.subTest(kind=kind):
                    task_dir = Path(tmp) / kind
                    make_harness_task(task_dir, kind)

                    init = init_task(task_dir, kind, by="test")
                    self.assertEqual(
                        init["state_machine"]["current_state"],
                        f"{kind}_{'plan' if kind == 'child' else 'prd'}_draft",
                    )

                    for event, expected in transitions:
                        result = apply_event(task_dir, event, by="test")
                        self.assertEqual(result["state_machine"]["current_state"], expected)

                    self.assertEqual(status(task_dir)["event_count"], len(transitions) + 1)
                    with self.assertRaises(StateMachineError):
                        apply_event(task_dir, transitions[0][0], by="test")

    def test_blocker_only_allows_resolve_and_restores_prior_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "blocked-child"
            make_harness_task(task_dir, "child")

            init_task(task_dir, "child", by="test")
            blocked = apply_event(task_dir, "blocker_opened", by="test")
            self.assertEqual(blocked["state_machine"]["current_state"], "child_blocked")
            self.assertEqual(blocked["state_machine"]["blocked_from_state"], "child_plan_draft")

            with self.assertRaises(StateMachineError):
                apply_event(task_dir, "plan_drafted", by="test")

            resolved = apply_event(task_dir, "blocker_resolved", by="test")
            self.assertEqual(resolved["state_machine"]["current_state"], "child_plan_draft")
            self.assertIsNone(resolved["state_machine"]["blocked_from_state"])

    def test_state_machine_rolls_log_back_when_task_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "child"
            make_harness_task(task_dir, "child")
            init_task(task_dir, "child", by="test")
            task_path = task_dir / "task.json"
            log_path = task_dir / "state-events.jsonl"
            old_task = task_path.read_bytes()
            old_log = log_path.read_bytes()

            real_replace = state_machine.os.replace
            replace_count = 0

            def fail_task_replace(src: Path, dst: Path) -> None:
                nonlocal replace_count
                replace_count += 1
                if replace_count == 2 and Path(dst).name == "task.json":
                    raise OSError("simulated task replace failure")
                real_replace(src, dst)

            with mock.patch("state_machine.os.replace", side_effect=fail_task_replace):
                with self.assertRaises(StateMachineError):
                    apply_event(task_dir, "plan_drafted", by="test")

            self.assertEqual(task_path.read_bytes(), old_task)
            self.assertEqual(log_path.read_bytes(), old_log)
            self.assertEqual(list(task_dir.glob(".*.tmp")), [])


class AtomicJsonWriteTests(unittest.TestCase):
    def test_write_json_preserves_existing_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text('{"old": true}\n', encoding="utf-8")

            with mock.patch("common.io.os.replace", side_effect=OSError("simulated replace failure")):
                self.assertFalse(write_json(path, {"new": True}))

            self.assertEqual(path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(Path(tmp).glob(".task.json.*.tmp")), [])

    def test_write_json_uses_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            calls: list[tuple[Path, Path]] = []
            real_replace = common_io.os.replace

            def record_replace(src: Path, dst: Path) -> None:
                calls.append((Path(src), Path(dst)))
                real_replace(src, dst)

            with mock.patch("common.io.os.replace", side_effect=record_replace):
                self.assertTrue(write_json(path, {"name": "task"}))

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"name": "task"})
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
            self.assertEqual([dst for _, dst in calls], [path])


class YamlParserUnificationTests(unittest.TestCase):
    def test_full_config_reuses_hook_safe_parser(self) -> None:
        self.assertIs(config.parse_simple_yaml, trellis_config.parse_simple_yaml)

    def test_yaml_subset_fixtures(self) -> None:
        fixtures = {
            "comments": (
                """
# top comment
session_auto_commit: true # inline comment
url: https://example.test/#frag
quoted: "value # not comment"
""",
                {
                    "quoted": "value # not comment",
                    "session_auto_commit": "true",
                    "url": "https://example.test/#frag",
                },
            ),
            "quotes": (
                """
plain: hello
single: 'hello world'
double: "hello world"
nested: "echo 'hi'"
mismatch: "hello'
""",
                {
                    "double": "hello world",
                    "mismatch": "\"hello'",
                    "nested": "echo 'hi'",
                    "plain": "hello",
                    "single": "hello world",
                },
            ),
            "nested_dicts": (
                """
packages:
  cli:
    path: packages/cli
    git: true
  docs:
    path: docs-site
session:
  spec_scope: active_task
""",
                {
                    "packages": {
                        "cli": {"git": "true", "path": "packages/cli"},
                        "docs": {"path": "docs-site"},
                    },
                    "session": {"spec_scope": "active_task"},
                },
            ),
            "lists": (
                """
hooks:
  after_create:
    - echo created
    - "echo # not comment"
platforms:
  - codex
  - claude
""",
                {
                    "hooks": {"after_create": ["echo created", "echo # not comment"]},
                    "platforms": ["codex", "claude"],
                },
            ),
        }

        for name, (content, expected) in fixtures.items():
            with self.subTest(name=name):
                self.assertEqual(config.parse_simple_yaml(content), expected)


class BoardStatusCleanupTests(unittest.TestCase):
    def test_board_uses_state_machine_status_for_waiting_and_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            make_board_task(
                tasks_dir,
                "parent-waiting",
                status_value="in_progress",
                tier="parent",
                current_state="parent_waiting_completion_signal",
            )
            make_board_task(
                tasks_dir,
                "blocked-child",
                status_value="in_progress",
                current_state="child_blocked",
                parent="parent-waiting",
            )

            board = build_board(repo)

            self.assertIn("| parent-waiting | parent | codex | parent_waiting_completion_signal |", board)
            self.assertIn("- parent-waiting (parent_waiting_completion_signal)", board)
            self.assertRegex(board, r"\| blocked-child \| child \| codex \| child_blocked \| [^|]+ \| yes \|")

    def test_board_counts_archived_child_state_as_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            make_board_task(
                tasks_dir,
                "parent-progress",
                tier="parent",
                children=["archived-child", "active-child"],
                current_state="parent_prd_draft",
            )
            make_board_task(
                tasks_dir,
                "archived-child",
                current_state="child_archived",
                parent="parent-progress",
            )
            make_board_task(
                tasks_dir,
                "active-child",
                current_state="child_plan_draft",
                parent="parent-progress",
            )

            statuses = get_all_statuses(tasks_dir)
            board = build_board(repo)

            self.assertEqual(statuses["archived-child"], "child_archived")
            self.assertEqual(children_progress(["archived-child", "active-child"], statuses), " [1/2 done]")
            self.assertIn("| parent-progress [1/2 done] |", board)
            self.assertNotIn("| archived-child |", board)

    def test_board_ignores_legacy_blocked_status_when_state_machine_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            make_board_task(
                tasks_dir,
                "ghost-child",
                status_value="blocked",
                current_state="child_plan_draft",
            )

            board = build_board(repo)

            self.assertRegex(board, r"\| ghost-child \| child \| codex \| child_plan_draft \| [^|]+ \| no \|")


class DoneGateInvariantTests(unittest.TestCase):
    def write_task_json(
        self,
        task_dir: Path,
        *,
        tier: str,
        parent: str | None = None,
        children: list[str] | None = None,
        status_value: str = "in_progress",
    ) -> None:
        data = {
            "id": task_dir.name,
            "name": task_dir.name,
            "title": task_dir.name,
            "status": status_value,
            "tier": tier,
            "owner": "codex",
            "children": children or [],
            "parent": parent,
            "meta": {"workflow_mode": "harness_state_machine"},
        }
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_child_done_gate_requires_meaningful_stage_report_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            parent = tasks_dir / "parent-task"
            child = tasks_dir / "child-task"
            parent.mkdir(parents=True)
            child.mkdir()

            (parent / "governance.md").write_text(
                "# Governance\n\n### PRD Review\n\n- Reviewed.\n\n## RTM\n\n| A | B |\n",
                encoding="utf-8",
            )
            (child / "stage-report.md").write_text(
                "# Stage Report\n\n## Acceptance\n\n- [ ] TBD\n\n## Verification\n\n- Pending implementation.\n",
                encoding="utf-8",
            )
            data = {"tier": "child", "parent": "parent-task"}

            errors = _done_gate_errors(child, data, repo)
            self.assertIn(
                "stage-report.md ## Acceptance is empty or still template text",
                errors,
            )

            (child / "stage-report.md").write_text(
                "# Stage Report\n\n## Acceptance\n\n- [x] Invariant checks pass.\n\n## Verification\n\n- unittest passed.\n",
                encoding="utf-8",
            )
            self.assertEqual(_done_gate_errors(child, data, repo), [])

    def test_validate_reuses_child_done_gate_acceptance_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            parent = tasks_dir / "parent-task"
            child = tasks_dir / "child-task"
            self.write_task_json(parent, tier="parent", children=["child-task"])
            self.write_task_json(child, tier="child", parent="parent-task")
            (parent / "governance.md").write_text(
                "# Governance\n\n### PRD Review\n\n- Reviewed.\n\n",
                encoding="utf-8",
            )
            (child / "prd.md").write_text(
                "# Child\n\n## REQ-ID\n\n- M2-REQ-001: X\n\n## Verification Commands\n\n- true\n",
                encoding="utf-8",
            )
            (child / "stage-report.md").write_text(
                "# Stage Report\n\n## Acceptance\n\n- [ ] TBD\n",
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                errors = _validate_v2_task(child, repo)

            self.assertEqual(errors, 1)
            self.assertIn(
                "stage-report.md ## Acceptance is empty or still template text",
                output.getvalue(),
            )

    def test_validate_reuses_parent_done_gate_rtm_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = repo / ".trellis" / "tasks" / "parent-task"
            self.write_task_json(parent, tier="parent")
            (parent / "governance.md").write_text(
                "# Governance\n\n"
                "## Child Index\n\n| Child | Status |\n|---|---|\n\n"
                "## RTM\n\n| Req | Child | Status |\n|---|---|---|\n| REQ-001 | child | planned |\n\n"
                "## External Review\n\n### PRD Review\n\n- Reviewed.\n\n"
                "## Boundary Pass\n\n- Checked.\n",
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                errors = _validate_v2_task(parent, repo)

            self.assertEqual(errors, 1)
            self.assertIn("RTM still contains planned rows", output.getvalue())


class TemplateGovernanceInvariantTests(unittest.TestCase):
    def test_v3_templates_reference_protocol_spec_and_avoid_staged_metadata(self) -> None:
        repo = SCRIPT_DIR.parents[1]
        expected = {
            "parent": ("prd.md", "governance.md"),
            "child": ("prd.md", "stage-report.md"),
            "light": ("prd.md", "stage-report.md"),
        }

        for tier, names in expected.items():
            for name in names:
                with self.subTest(tier=tier, name=name):
                    text = _template_text(repo, tier, name)
                    self.assertTrue((repo / ".trellis" / "templates" / "v3" / tier / name).is_file())
                    forbidden = (
                        r"staged_" + r"delivery|meta\.staged_" + r"delivery|"
                        r"workflow_mode.*staged_" + r"overlay"
                    )
                    self.assertIn(".trellis/spec/project/protocol-phrases.md", text)
                    self.assertNotRegex(text, forbidden)

    def test_generated_v3_child_sample_passes_validation_after_evidence_fill(self) -> None:
        source_repo = SCRIPT_DIR.parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_dir = repo / ".trellis" / "tasks"
            parent = tasks_dir / "parent-task"
            child = tasks_dir / "child-task"
            make_board_task(tasks_dir, "parent-task", tier="parent", children=["child-task"])
            make_board_task(tasks_dir, "child-task", tier="child", parent="parent-task")

            _write_template_files(parent, source_repo, "parent", "Parent", "Parent description")
            _write_template_files(child, source_repo, "child", "Child", "Child description")

            governance = parent / "governance.md"
            governance.write_text(
                governance.read_text(encoding="utf-8")
                .replace("### PRD Review\n\nTODO", "### PRD Review\n\n- Reviewed.")
                .replace("## Boundary Pass\n\nTODO", "## Boundary Pass\n\n- Checked."),
                encoding="utf-8",
            )
            stage_report = child / "stage-report.md"
            stage_report.write_text(
                stage_report.read_text(encoding="utf-8").replace(
                    "- [ ] TODO",
                    "- [x] Generated sample evidence recorded.",
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                errors = _validate_v2_task(child, repo)

            self.assertEqual(errors, 0, output.getvalue())


class ArchiveTransitionDerivationTests(unittest.TestCase):
    def test_child_soft_archive_uses_transitions_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "child"
            make_harness_task(task_dir, "child")
            init_task(task_dir, "child", by="test")
            apply_event(task_dir, "plan_drafted", by="test")
            apply_event(task_dir, "completion_signal_received", by="test")
            apply_event(task_dir, "commit_created", by="test")

            original = copy.deepcopy(state_machine.TRANSITIONS)
            try:
                state_machine.TRANSITIONS["child"] = dict(state_machine.TRANSITIONS["child"])
                del state_machine.TRANSITIONS["child"][("child_archive_ready", "child_archive_completed")]
                state_machine.TRANSITIONS["child"][("child_archive_ready", "child_archive_finished")] = (
                    "child_archived"
                )

                _advance_child_to_archived(task_dir)
            finally:
                state_machine.TRANSITIONS.clear()
                state_machine.TRANSITIONS.update(original)

            self.assertEqual(status(task_dir)["state_machine"]["current_state"], "child_archived")

    def test_parent_archive_uses_transitions_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "parent"
            make_harness_task(task_dir, "parent")
            init_task(task_dir, "parent", by="test")
            apply_event(task_dir, "prd_drafted", by="test")
            apply_event(task_dir, "parent_completion_signal_received", by="test")
            apply_event(task_dir, "parent_commit_created", by="test")

            original = copy.deepcopy(state_machine.TRANSITIONS)
            try:
                state_machine.TRANSITIONS["parent"] = dict(state_machine.TRANSITIONS["parent"])
                del state_machine.TRANSITIONS["parent"][("parent_archive_ready", "parent_archive_completed")]
                state_machine.TRANSITIONS["parent"][("parent_archive_ready", "parent_archive_finished")] = (
                    "parent_archived"
                )

                _advance_parent_to_archived(task_dir)
            finally:
                state_machine.TRANSITIONS.clear()
                state_machine.TRANSITIONS.update(original)

            self.assertEqual(status(task_dir)["state_machine"]["current_state"], "parent_archived")

    def test_soft_archive_rejects_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "blocked-child"
            make_harness_task(task_dir, "child")
            init_task(task_dir, "child", by="test")
            apply_event(task_dir, "blocker_opened", by="test")

            with self.assertRaisesRegex(StateMachineError, "cannot soft-archive from state: child_blocked"):
                _advance_child_to_archived(task_dir)


class SafeCommitInvariantTests(unittest.TestCase):
    def test_safe_trellis_paths_scope_to_current_task_and_workspace_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".trellis" / "workspace" / "jym").mkdir(parents=True)
            (repo / ".trellis" / "workspace" / "jym" / "journal-1.md").write_text("x\n", encoding="utf-8")
            (repo / ".trellis" / "workspace" / "jym" / "index.md").write_text("x\n", encoding="utf-8")
            (repo / ".trellis" / ".developer").write_text("name=jym\n", encoding="utf-8")
            (repo / ".trellis" / "tasks" / "current").mkdir(parents=True)
            (repo / ".trellis" / "tasks" / "other").mkdir()
            (repo / ".trellis" / ".runtime").mkdir()

            paths = safe_trellis_paths_to_add(repo, task_name="current")

            self.assertIn(".trellis/tasks/current", paths)
            self.assertIn(".trellis/workspace/jym/journal-1.md", paths)
            self.assertNotIn(".trellis/tasks/other", paths)
            self.assertNotIn(".trellis/.runtime", paths)
            self.assertNotIn(".trellis", paths)

    def test_safe_git_add_never_retries_with_force(self) -> None:
        calls: list[list[str]] = []

        def fake_run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            calls.append(args)
            return 1, "", "ignored by .gitignore"

        with mock.patch("common.safe_commit.run_git", side_effect=fake_run_git):
            success, used_force, stderr = safe_git_add([".trellis/tasks/current"], Path("/repo"))

        self.assertFalse(success)
        self.assertFalse(used_force)
        self.assertEqual(stderr, "ignored by .gitignore")
        self.assertEqual(calls, [["add", "--", ".trellis/tasks/current"]])
        self.assertNotIn("-f", calls[0])


class ActiveSessionInvariantTests(unittest.TestCase):
    def test_active_task_pointers_are_session_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task_a = repo / ".trellis" / "tasks" / "task-a"
            task_b = repo / ".trellis" / "tasks" / "task-b"
            task_a.mkdir(parents=True)
            task_b.mkdir()

            with mock.patch.dict(os.environ, {"TRELLIS_CONTEXT_ID": "window-a"}, clear=True):
                set_active_task("task-a", repo)
                self.assertEqual(resolve_active_task(repo).task_path, ".trellis/tasks/task-a")

            with mock.patch.dict(os.environ, {"TRELLIS_CONTEXT_ID": "window-b"}, clear=True):
                set_active_task("task-b", repo)
                self.assertEqual(resolve_active_task(repo).task_path, ".trellis/tasks/task-b")

            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(resolve_active_task(repo).task_path)

            with mock.patch.dict(os.environ, {"TRELLIS_CONTEXT_ID": "window-a"}, clear=True):
                cleared = clear_active_task(repo)
                self.assertEqual(cleared.task_path, ".trellis/tasks/task-a")

            with mock.patch.dict(os.environ, {"TRELLIS_CONTEXT_ID": "window-b"}, clear=True):
                self.assertEqual(resolve_active_task(repo).task_path, ".trellis/tasks/task-b")


if __name__ == "__main__":
    unittest.main()
