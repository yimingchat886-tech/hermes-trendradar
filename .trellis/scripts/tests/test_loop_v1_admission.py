from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common.io import read_json
from common.task_context import _validate_v2_task
from common.task_activity import classify_task_activity
from common.task_store import cmd_archive, cmd_cancel, cmd_create
from loop_v1.qualification import (
    HARNESS_SOURCE_ROLE,
    QualificationStatus,
    configured_qualification,
)
from loop_v1.pre_admission import (
    PreAdmissionCancellationError,
    cancel_pre_admission_parent,
)
from loop_v1.orchestrator import archive_pre_admission_operator
import task as task_cli


def create_args(
    *,
    slug: str,
    tier: str = "light",
    parent: str | None = None,
    workflow_mode: str | None = None,
    strategy: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        title=slug.replace("-", " "),
        slug=slug,
        assignee="jym",
        priority="P2",
        description="",
        parent=parent,
        package=None,
        tier=tier,
        owner="codex",
        touches=[],
        workflow_mode=workflow_mode,
        strategy=strategy,
    )


def write_config(
    repo: Path,
    *,
    enabled: bool,
    parent_default: str = "current_trellis",
    runtime_mode: str | None = HARNESS_SOURCE_ROLE,
) -> None:
    trellis = repo / ".trellis"
    trellis.mkdir(parents=True, exist_ok=True)
    role_line = f"  runtime_mode: {runtime_mode}\n" if runtime_mode is not None else ""
    (trellis / "config.yaml").write_text(
        "loop_v1:\n"
        f"  admission_enabled: {'true' if enabled else 'false'}\n"
        f"  parent_default: {parent_default}\n"
        f"{role_line}",
        encoding="utf-8",
    )


def enable_taskrun_cutover(repo: Path) -> None:
    trellis = repo / ".trellis"
    trellis.mkdir(parents=True, exist_ok=True)
    config = trellis / "config.yaml"
    current = config.read_text(encoding="utf-8") if config.is_file() else ""
    config.write_text(
        current + "taskrun_v1:\n  new_code_tasks: true\n",
        encoding="utf-8",
    )


def write_parent(repo: Path, *, workflow_mode: str = "loop_v1") -> Path:
    parent = repo / ".trellis" / "tasks" / "07-13-parent"
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "task.json").write_text(
        json.dumps(
            {
                "id": "parent",
                "name": "parent",
                "title": "Parent",
                "tier": "parent",
                "status": "in_progress",
                "children": [],
                "meta": {"workflow_mode": workflow_mode},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return parent


def write_parent_governance(parent: Path, *, rtm_status: str = "planned") -> None:
    (parent / "governance.md").write_text(
        "# Governance\n\n"
        "## Child Index\n\n"
        "| Child | Status |\n"
        "|---|---|\n\n"
        "## RTM\n\n"
        "| REQ-ID | Child | Status | Evidence |\n"
        "|---|---|---|---|\n"
        f"| LOOP-VAL-001 | parent-direct | {rtm_status} | planning record |\n\n"
        "## External Review\n\n"
        "### PRD Review\n\n"
        "- Reviewed.\n\n"
        "## Boundary Pass\n\n"
        "- Checked.\n",
        encoding="utf-8",
    )


def archive_args(name: str) -> argparse.Namespace:
    return argparse.Namespace(name=name, no_commit=True, force_archive=False, reason="")


def cancel_args(name: str) -> argparse.Namespace:
    return argparse.Namespace(
        name=name,
        reason="loop-owned lifecycle",
        authorized_by="jym",
        superseded_by=None,
        rtm_disposition=None,
        rtm_id=[],
    )


def write_pre_admission_parent(repo: Path, name: str = "07-13-parent") -> Path:
    parent = repo / ".trellis" / "tasks" / name
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "task.json").write_text(
        json.dumps(
            {
                "branch": None,
                "cancelledAt": None,
                "children": [],
                "commit": None,
                "completedAt": None,
                "id": name,
                "meta": {"workflow_mode": "loop_v1"},
                "name": name,
                "parent": None,
                "pr_url": None,
                "status": "planning",
                "subtasks": [],
                "tier": "parent",
                "worktree_path": None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return parent


def run_public_task(repo: Path, *argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        mock.patch.object(sys, "argv", ["task.py", *argv]),
        mock.patch("common.task_store.get_repo_root", return_value=repo),
        mock.patch("common.task_context.get_repo_root", return_value=repo),
        mock.patch.object(task_cli, "get_repo_root", return_value=repo),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        result = task_cli.main()
    return result, stdout.getvalue(), stderr.getvalue()


def cancellation_surfaces(repo: Path, parent: Path) -> tuple[object, ...]:
    event = parent / "state-events.jsonl"
    sessions = repo / ".trellis" / ".runtime" / "sessions"
    return (
        (parent / "task.json").read_bytes(),
        (event.exists(), event.read_bytes() if event.is_file() else None),
        tuple(
            (path.name, path.read_bytes())
            for path in sorted(sessions.glob("*.json"))
        )
        if sessions.is_dir()
        else (),
        (repo / "BOARD.md").read_bytes(),
    )


def _update_task(parent: Path, **changes: object) -> None:
    task = read_json(parent / "task.json")
    task.update(changes)
    (parent / "task.json").write_text(
        json.dumps(task, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_runtime_residue(repo: Path, run_id: str) -> None:
    runtime = (
        repo
        / ".trellis"
        / ".runtime"
        / "loop-v1"
        / "parents"
        / run_id
    )
    runtime.mkdir(parents=True)
    (runtime / "ledger.sqlite3").write_bytes(b"residue")


class LoopV1AdmissionTests(unittest.TestCase):
    def run_create(
        self,
        repo: Path,
        args: argparse.Namespace,
        *,
        mock_state_initialization: bool = True,
        hook: mock.Mock | None = None,
    ) -> tuple[int, str]:
        stderr = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(mock.patch("common.task_store.get_repo_root", return_value=repo))
            stack.enter_context(mock.patch("common.task_store.generate_task_date_prefix", return_value="07-13"))
            stack.enter_context(mock.patch("common.task_store.run_git", return_value=(0, "main\n", "")))
            stack.enter_context(mock.patch("common.task_store._write_template_files"))
            if mock_state_initialization:
                stack.enter_context(mock.patch("common.task_store._init_state_if_supported"))
            stack.enter_context(mock.patch("common.task_store._has_subagent_platform", return_value=False))
            stack.enter_context(
                mock.patch("common.task_store.run_task_hooks", new=hook or mock.Mock())
            )
            stack.enter_context(mock.patch("common.active_task.resolve_context_key", return_value=None))
            with redirect_stderr(stderr):
                result = cmd_create(args)
        return result, stderr.getvalue()

    def test_no_selector_keeps_current_trellis_modes(self) -> None:
        for tier in ("light", "parent"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                write_config(repo, enabled=False)

                result, _ = self.run_create(repo, create_args(slug=f"plain-{tier}", tier=tier))

                self.assertEqual(result, 0)
                task = read_json(repo / ".trellis" / "tasks" / f"07-13-plain-{tier}" / "task.json")
                self.assertEqual(task["meta"]["workflow_mode"], "harness_state_machine")

    def test_taskrun_cutover_defaults_new_tasks_to_single(self) -> None:
        for tier in ("light", "parent"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                enable_taskrun_cutover(repo)
                hook = mock.Mock()

                result, stderr = self.run_create(
                    repo,
                    create_args(slug=f"taskrun-{tier}", tier=tier),
                    mock_state_initialization=False,
                    hook=hook,
                )

                self.assertEqual(result, 0, stderr)
                hook.assert_not_called()
                task_dir = repo / ".trellis/tasks" / f"07-13-taskrun-{tier}"
                task = read_json(task_dir / "task.json")
                self.assertEqual(
                    task["meta"],
                    {"taskrun_strategy": "single", "workflow_mode": "taskrun_v2"},
                )
                self.assertFalse((task_dir / "state-events.jsonl").exists())

    def test_taskrun_cutover_accepts_explicit_loop_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            enable_taskrun_cutover(repo)

            result, stderr = self.run_create(
                repo,
                create_args(slug="taskrun-loop", strategy="loop"),
            )

            self.assertEqual(result, 0, stderr)
            task = read_json(
                repo / ".trellis/tasks/07-13-taskrun-loop/task.json"
            )
            self.assertEqual(task["meta"]["workflow_mode"], "taskrun_v2")
            self.assertEqual(task["meta"]["taskrun_strategy"], "loop")

    def test_taskrun_cutover_rejects_competing_workflow_before_mutation(self) -> None:
        for workflow_mode in ("current_trellis", "loop_v1", "loop_v4"):
            with self.subTest(workflow_mode=workflow_mode), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                enable_taskrun_cutover(repo)
                board = repo / "BOARD.md"
                board.write_bytes(b"unchanged-board\n")
                sessions = repo / ".trellis/.runtime/sessions"
                sessions.mkdir(parents=True)
                pointer = sessions / "existing.json"
                pointer.write_bytes(b"unchanged-pointer\n")
                ledger = repo / ".trellis/.runtime/loop-v1/existing.sqlite3"
                ledger.parent.mkdir(parents=True)
                ledger.write_bytes(b"unchanged-ledger\n")
                before = {
                    "board": board.read_bytes(),
                    "pointer": pointer.read_bytes(),
                    "ledger": ledger.read_bytes(),
                }

                result, stderr = self.run_create(
                    repo,
                    create_args(
                        slug=f"rejected-{workflow_mode}",
                        tier="parent",
                        workflow_mode=workflow_mode,
                    ),
                )

                self.assertEqual(result, 1)
                self.assertIn("cannot admit new lifecycle authority", stderr)
                self.assertFalse(
                    (repo / ".trellis/tasks" / f"07-13-rejected-{workflow_mode}").exists()
                )
                self.assertEqual(board.read_bytes(), before["board"])
                self.assertEqual(pointer.read_bytes(), before["pointer"])
                self.assertEqual(ledger.read_bytes(), before["ledger"])

    def test_taskrun_cutover_cannot_extend_legacy_parent(self) -> None:
        for workflow_mode in ("harness_state_machine", "loop_v1"):
            with self.subTest(workflow_mode=workflow_mode), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                parent = write_parent(repo, workflow_mode=workflow_mode)
                before = (parent / "task.json").read_bytes()
                enable_taskrun_cutover(repo)

                result, stderr = self.run_create(
                    repo,
                    create_args(slug="legacy-child", parent=str(parent)),
                )

                self.assertEqual(result, 1)
                self.assertIn("cannot extend legacy", stderr)
                self.assertFalse(
                    (repo / ".trellis/tasks/07-13-legacy-child").exists()
                )
                self.assertEqual((parent / "task.json").read_bytes(), before)

    def test_taskrun_cutover_cannot_mutate_admitted_parent_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo, workflow_mode="taskrun_v1")
            task = read_json(parent / "task.json")
            task["meta"].update(
                {
                    "taskrun_strategy": "single",
                    "task_run": {
                        "authority": "sqlite",
                        "id": "tr-example",
                        "projection": True,
                    },
                }
            )
            (parent / "task.json").write_text(
                json.dumps(task, indent=2) + "\n", encoding="utf-8"
            )
            before = (parent / "task.json").read_bytes()
            enable_taskrun_cutover(repo)

            result, stderr = self.run_create(
                repo,
                create_args(slug="late-child", parent=str(parent)),
            )

            self.assertEqual(result, 1)
            self.assertIn("cannot extend legacy", stderr)
            self.assertFalse((repo / ".trellis/tasks/07-13-late-child").exists())
            self.assertEqual((parent / "task.json").read_bytes(), before)

    def test_explicit_current_trellis_selector_preserves_internal_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            result, _ = self.run_create(
                repo,
                create_args(
                    slug="current-parent",
                    tier="parent",
                    workflow_mode="current_trellis",
                ),
            )

            self.assertEqual(result, 0)
            task = read_json(
                repo / ".trellis" / "tasks" / "07-13-current-parent" / "task.json"
            )
            self.assertEqual(task["meta"]["workflow_mode"], "harness_state_machine")

    def test_disabled_loop_v1_rejects_before_task_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, enabled=False)

            result, stderr = self.run_create(
                repo,
                create_args(slug="loop-parent", tier="parent", workflow_mode="loop_v1"),
            )

            self.assertEqual(result, 1)
            self.assertIn("Loop v1 admission is disabled", stderr)
            self.assertFalse((repo / ".trellis" / "tasks").exists())

    def test_configuration_cannot_bypass_missing_qualification_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, enabled=True, parent_default="loop_v1")

            result, stderr = self.run_create(
                repo,
                create_args(slug="default-loop-parent", tier="parent"),
            )

            self.assertEqual(result, 1)
            self.assertIn("runtime is not qualified", stderr)
            self.assertFalse((repo / ".trellis" / "tasks").exists())

    def test_repository_role_rejects_task_mutation_before_create(self) -> None:
        for runtime_mode in (None, "unknown"):
            with self.subTest(runtime_mode=runtime_mode), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                write_config(
                    repo,
                    enabled=True,
                    parent_default="loop_v1",
                    runtime_mode=runtime_mode,
                )

                result, stderr = self.run_create(
                    repo,
                    create_args(slug="role-rejected", tier="parent"),
                )

                self.assertEqual(result, 1)
                self.assertIn("runtime_mode", stderr)
                self.assertFalse((repo / ".trellis" / "tasks").exists())

    def test_downstream_role_requires_installed_runtime_receipts_before_create(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(
                repo,
                enabled=True,
                parent_default="loop_v1",
                runtime_mode="downstream_project",
            )

            result, stderr = self.run_create(
                repo,
                create_args(slug="downstream-unqualified", tier="parent"),
            )

            self.assertEqual(result, 1)
            self.assertIn("installed runtime", stderr)
            self.assertFalse((repo / ".trellis" / "tasks").exists())

    def test_enabled_admission_cannot_implicitly_default_to_current_trellis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, enabled=True, parent_default="current_trellis")

            status = configured_qualification(repo)
            self.assertFalse(status.valid)
            self.assertEqual(
                status.issues,
                ("enabled Loop v1 admission requires parent_default 'loop_v1'",),
            )

            result, stderr = self.run_create(
                repo,
                create_args(slug="misconfigured-default", tier="parent"),
            )
            self.assertEqual(result, 1)
            self.assertIn("requires parent_default 'loop_v1'", stderr)
            self.assertFalse((repo / ".trellis" / "tasks").exists())

            result, _ = self.run_create(
                repo,
                create_args(
                    slug="explicit-current",
                    tier="parent",
                    workflow_mode="current_trellis",
                ),
            )
            self.assertEqual(result, 0)
            task = read_json(
                repo / ".trellis" / "tasks" / "07-13-explicit-current" / "task.json"
            )
            self.assertEqual(task["meta"]["workflow_mode"], "harness_state_machine")

    def test_historical_and_unknown_selectors_fail_closed(self) -> None:
        for workflow_mode, message in (
            ("loop_v4", "historical"),
            ("future_loop", "unknown workflow mode"),
            (" ", "requires a non-empty value"),
        ):
            with self.subTest(workflow_mode=workflow_mode), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                result, stderr = self.run_create(
                    repo,
                    create_args(
                        slug=f"bad-{workflow_mode}",
                        tier="parent",
                        workflow_mode=workflow_mode,
                    ),
                )

                self.assertEqual(result, 1)
                self.assertIn(message, stderr)
                self.assertFalse((repo / ".trellis" / "tasks").exists())

    def test_light_and_child_cannot_select_workflow_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo)
            parent_before = (parent / "task.json").read_bytes()

            for tier, parent_arg in (("light", None), ("child", str(parent))):
                with self.subTest(tier=tier):
                    result, stderr = self.run_create(
                        repo,
                        create_args(
                            slug=f"selected-{tier}",
                            tier=tier,
                            parent=parent_arg,
                            workflow_mode="loop_v1",
                        ),
                    )
                    self.assertEqual(result, 1)
                    self.assertIn("parent-only", stderr)
                    self.assertFalse(
                        (repo / ".trellis" / "tasks" / f"07-13-selected-{tier}").exists()
                    )

            self.assertEqual((parent / "task.json").read_bytes(), parent_before)

    def test_child_inherits_loop_v1_from_parent_while_admission_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, enabled=False)
            parent = write_parent(repo)

            result, _ = self.run_create(
                repo,
                create_args(slug="loop-child", parent=str(parent)),
            )

            self.assertEqual(result, 0)
            child = read_json(repo / ".trellis" / "tasks" / "07-13-loop-child" / "task.json")
            self.assertEqual(child["tier"], "child")
            self.assertEqual(child["parent"], parent.name)
            self.assertEqual(child["meta"]["workflow_mode"], "loop_v1")
            self.assertEqual(read_json(parent / "task.json")["children"], ["07-13-loop-child"])

    def test_child_cannot_extend_historical_loop_v4_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo, workflow_mode="loop_v4")
            parent_before = (parent / "task.json").read_bytes()

            result, stderr = self.run_create(
                repo,
                create_args(slug="historical-child", parent=str(parent)),
            )

            self.assertEqual(result, 1)
            self.assertIn("historical", stderr)
            self.assertFalse(
                (repo / ".trellis" / "tasks" / "07-13-historical-child").exists()
            )
            self.assertEqual((parent / "task.json").read_bytes(), parent_before)

    def test_loop_v1_parent_skips_hsm_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, enabled=True, parent_default="loop_v1")
            qualified = QualificationStatus(True, True, "sha256:" + "a" * 64, ())

            with (
                mock.patch(
                    "loop_v1.qualification.configured_qualification",
                    return_value=qualified,
                ),
                mock.patch("state_machine.init_task") as init_task,
            ):
                result, stderr = self.run_create(
                    repo,
                    create_args(slug="qualified-loop", tier="parent"),
                    mock_state_initialization=False,
                )

            self.assertEqual(result, 0, stderr)
            task = read_json(repo / ".trellis" / "tasks" / "07-13-qualified-loop" / "task.json")
            self.assertEqual(task["meta"], {"workflow_mode": "loop_v1"})
            init_task.assert_not_called()

    def test_current_trellis_parent_keeps_hsm_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            result, stderr = self.run_create(
                repo,
                create_args(slug="current-hsm", tier="parent"),
                mock_state_initialization=False,
            )

            self.assertEqual(result, 0, stderr)
            task = read_json(repo / ".trellis" / "tasks" / "07-13-current-hsm" / "task.json")
            self.assertEqual(task["meta"]["workflow_mode"], "harness_state_machine")
            self.assertEqual(task["meta"]["state_machine"]["kind"], "parent")
            self.assertEqual(task["meta"]["state_machine"]["schema_version"], 2)

    def test_new_current_trellis_child_records_schema_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(
                repo,
                workflow_mode="harness_state_machine",
            )

            result, stderr = self.run_create(
                repo,
                create_args(slug="current-child", parent=str(parent)),
                mock_state_initialization=False,
            )

            self.assertEqual(result, 0, stderr)
            child = read_json(
                repo / ".trellis" / "tasks" / "07-13-current-child" / "task.json"
            )
            self.assertEqual(child["meta"]["state_machine"]["kind"], "child")
            self.assertEqual(child["meta"]["state_machine"]["schema_version"], 2)

    def test_loop_v1_validation_checks_mode_without_hsm_done_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo)
            write_parent_governance(parent, rtm_status="planned")
            qualified = QualificationStatus(True, True, "sha256:" + "b" * 64, ())

            output = io.StringIO()
            with (
                mock.patch(
                    "loop_v1.qualification.configured_qualification",
                    return_value=qualified,
                ),
                redirect_stdout(output),
            ):
                errors = _validate_v2_task(parent, repo)

            self.assertEqual(errors, 0, output.getvalue())

            data = read_json(parent / "task.json")
            data["meta"]["state_machine"] = {"kind": "parent"}
            (parent / "task.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch(
                    "loop_v1.qualification.configured_qualification",
                    return_value=qualified,
                ),
                redirect_stdout(output),
            ):
                errors = _validate_v2_task(parent, repo)
            self.assertEqual(errors, 1)
            self.assertIn("must not declare HSM state-machine metadata", output.getvalue())

            data["meta"] = {"workflow_mode": "unknown"}
            (parent / "task.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                errors = _validate_v2_task(parent, repo)
            self.assertEqual(errors, 1)
            self.assertIn(
                "must be harness_state_machine, loop_v1, taskrun_v1, or taskrun_v2",
                output.getvalue(),
            )

    def test_loop_v1_validation_rejects_invalid_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo)
            write_parent_governance(parent)
            invalid = QualificationStatus(True, False, None, ("receipt mismatch",))

            output = io.StringIO()
            with (
                mock.patch(
                    "loop_v1.qualification.configured_qualification",
                    return_value=invalid,
                ),
                redirect_stdout(output),
            ):
                errors = _validate_v2_task(parent, repo)

            self.assertEqual(errors, 1)
            self.assertIn("Loop v1 runtime is not qualified: receipt mismatch", output.getvalue())

    def test_generic_loop_lifecycle_commands_reject_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_parent(repo)
            before = (parent / "task.json").read_bytes()

            stderr = io.StringIO()
            with (
                mock.patch("common.task_store.get_repo_root", return_value=repo),
                redirect_stderr(stderr),
            ):
                self.assertEqual(cmd_cancel(cancel_args(parent.name)), 1)
                self.assertEqual(cmd_archive(archive_args(parent.name)), 1)

            self.assertIn("pre-admission cancellation requires planning status", stderr.getvalue())
            self.assertIn("archive is controlled by loop_v1.orchestrator", stderr.getvalue())
            self.assertEqual((parent / "task.json").read_bytes(), before)
            self.assertFalse((repo / ".trellis" / "tasks" / "archive").exists())

    def test_public_pre_admission_cancel_current_and_non_current(self) -> None:
        for current in (True, False):
            with self.subTest(current=current), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                parent = write_pre_admission_parent(repo)
                other = write_pre_admission_parent(repo, "07-13-other")
                scripts = repo / ".trellis" / "scripts"
                scripts.mkdir(parents=True)
                (scripts / "board.py").symlink_to(SCRIPT_DIR / "board.py")
                (repo / "BOARD.md").write_text("stale board\n", encoding="utf-8")
                sessions = repo / ".trellis" / ".runtime" / "sessions"
                sessions.mkdir(parents=True)
                session = sessions / "codex-test.json"
                active = parent if current else other
                session.write_text(
                    json.dumps(
                        {"current_task": active.relative_to(repo).as_posix()}
                    )
                    + "\n",
                    encoding="utf-8",
                )
                preserved_session = session.read_bytes()

                result, _, stderr = run_public_task(
                    repo,
                    "cancel",
                    parent.name,
                    "--reason",
                    "accepted incident containment",
                    "--authorized-by",
                    "jym",
                )

                self.assertEqual(result, 0, stderr)
                cancelled = read_json(parent / "task.json")
                self.assertEqual(cancelled["status"], "cancelled")
                events = [
                    json.loads(line)
                    for line in (parent / "state-events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                self.assertEqual([event["event"] for event in events], ["task_cancelled"])
                self.assertEqual(session.exists(), not current)
                if not current:
                    self.assertEqual(session.read_bytes(), preserved_session)
                board = (repo / "BOARD.md").read_text(encoding="utf-8")
                self.assertNotEqual(board, "stale board\n")
                self.assertNotIn(parent.name, board)
                self.assertIn(other.name, board)

                valid, stdout, validate_stderr = run_public_task(
                    repo, "validate", parent.name
                )
                self.assertEqual(valid, 0, stdout + validate_stderr)

                replay, _, replay_stderr = run_public_task(
                    repo,
                    "cancel",
                    parent.name,
                    "--reason",
                    "accepted incident containment",
                    "--authorized-by",
                    "jym",
                )
                self.assertEqual(replay, 0, replay_stderr)
                self.assertEqual(
                    len(
                        (parent / "state-events.jsonl")
                        .read_text(encoding="utf-8")
                        .splitlines()
                    ),
                    1,
                )

    def test_public_pre_admission_archive_is_physical_residue_free_and_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_pre_admission_parent(repo)
            scripts = repo / ".trellis" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "board.py").symlink_to(SCRIPT_DIR / "board.py")
            (repo / "BOARD.md").write_text("stale board\n", encoding="utf-8")
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Admission Test"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "admission@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            cancelled, _, stderr = run_public_task(
                repo,
                "cancel",
                parent.name,
                "--reason",
                "canary cancellation",
                "--authorized-by",
                "jym",
            )
            self.assertEqual(cancelled, 0, stderr)
            task_digest = f"sha256:{sha256((parent / 'task.json').read_bytes()).hexdigest()}"
            request = {
                "commit_enabled": False,
                "expected_task_digest": task_digest,
            }

            archived = archive_pre_admission_operator(repo, parent.name, request)
            replay = archive_pre_admission_operator(repo, parent.name, request)
            archived_dir = repo / archived["location"]

            self.assertFalse(parent.exists())
            self.assertTrue(archived_dir.is_dir())
            self.assertFalse(archived["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertFalse(
                (
                    repo
                    / ".trellis"
                    / ".runtime"
                    / "loop-v1"
                    / "parents"
                    / parent.name
                ).exists()
            )
            self.assertFalse(classify_task_activity(archived_dir, repo).active)
            self.assertEqual(_validate_v2_task(archived_dir, repo), 0)

    def test_public_pre_admission_cancel_rejections_preserve_all_surfaces(
        self,
    ) -> None:
        cases = {
            "initialized status": lambda repo, parent: _update_task(
                parent, status="in_progress"
            ),
            "event residue": lambda repo, parent: (
                parent / "state-events.jsonl"
            ).write_text('{"event":"residue"}\n', encoding="utf-8"),
            "child residue": lambda repo, parent: _update_task(
                parent, children=["07-13-child"]
            ),
            "git residue": lambda repo, parent: _update_task(
                parent, commit="a" * 40
            ),
            "runtime residue": lambda repo, parent: _write_runtime_residue(
                repo, parent.name
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                parent = write_pre_admission_parent(repo)
                (repo / "BOARD.md").write_text("stable board\n", encoding="utf-8")
                sessions = repo / ".trellis" / ".runtime" / "sessions"
                sessions.mkdir(parents=True)
                (sessions / "codex-test.json").write_text(
                    json.dumps(
                        {"current_task": parent.relative_to(repo).as_posix()}
                    )
                    + "\n",
                    encoding="utf-8",
                )
                mutate(repo, parent)
                before = cancellation_surfaces(repo, parent)

                result, _, _ = run_public_task(
                    repo,
                    "cancel",
                    parent.name,
                    "--reason",
                    "must reject",
                    "--authorized-by",
                    "jym",
                )

                self.assertEqual(result, 1)
                self.assertEqual(cancellation_surfaces(repo, parent), before)

    def test_public_pre_admission_helper_failure_preserves_all_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            parent = write_pre_admission_parent(repo)
            (repo / "BOARD.md").write_text("stable board\n", encoding="utf-8")
            before = cancellation_surfaces(repo, parent)

            with mock.patch(
                "loop_v1.pre_admission.cancel_pre_admission_parent",
                side_effect=PreAdmissionCancellationError("injected failure"),
            ):
                result, _, _ = run_public_task(
                    repo,
                    "cancel",
                    parent.name,
                    "--reason",
                    "must reject",
                    "--authorized-by",
                    "jym",
                )

            self.assertEqual(result, 1)
            self.assertEqual(cancellation_surfaces(repo, parent), before)

    def test_pre_admission_direct_entry_rejects_noncanonical_run_paths(self) -> None:
        invalid = ("", ".", "..", "/absolute", "nested/run", "a//b", "a/../b", r"a\b")
        for run_id in invalid:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                task_dir = repo / ".trellis" / "tasks" / "07-13-parent"
                task_dir.mkdir(parents=True)
                task = {
                    "branch": None,
                    "cancelledAt": None,
                    "children": [],
                    "commit": None,
                    "completedAt": None,
                    "id": run_id,
                    "meta": {"workflow_mode": "loop_v1"},
                    "name": run_id,
                    "parent": None,
                    "pr_url": None,
                    "status": "planning",
                    "subtasks": [],
                    "tier": "parent",
                    "worktree_path": None,
                }
                task_path = task_dir / "task.json"
                task_path.write_text(
                    json.dumps(task, sort_keys=True) + "\n", encoding="utf-8"
                )
                before = task_path.read_bytes()

                with (
                    mock.patch("loop_v1.orchestrator._operator_lock") as delegate,
                    self.assertRaises(PreAdmissionCancellationError),
                ):
                    cancel_pre_admission_parent(
                        task_dir,
                        repo,
                        actor="jym",
                        reason="test rejection",
                        superseded_by=None,
                    )

                delegate.assert_not_called()
                self.assertEqual(task_path.read_bytes(), before)
                self.assertFalse((task_dir / "state-events.jsonl").exists())
                self.assertFalse((repo / ".trellis" / ".runtime").exists())

    def test_pre_admission_direct_entry_keeps_canonical_run_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task_dir = repo / ".trellis" / "tasks" / "07-13-parent"
            task_dir.mkdir(parents=True)
            task = {
                "branch": None,
                "cancelledAt": None,
                "children": [],
                "commit": None,
                "completedAt": None,
                "id": "parent",
                "meta": {"workflow_mode": "loop_v1"},
                "name": "parent",
                "parent": None,
                "pr_url": None,
                "status": "planning",
                "subtasks": [],
                "tier": "parent",
                "worktree_path": None,
            }
            (task_dir / "task.json").write_text(
                json.dumps(task, sort_keys=True) + "\n", encoding="utf-8"
            )

            self.assertTrue(
                cancel_pre_admission_parent(
                    task_dir,
                    repo,
                    actor="jym",
                    reason="canonical cancellation",
                    superseded_by=None,
                )
            )
            self.assertEqual(read_json(task_dir / "task.json")["status"], "cancelled")
            self.assertFalse(
                cancel_pre_admission_parent(
                    task_dir,
                    repo,
                    actor="jym",
                    reason="canonical cancellation",
                    superseded_by=None,
                )
            )

    def test_create_help_exposes_taskrun_strategy_and_legacy_selector(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "task.py"), "create", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workflow-mode WORKFLOW_MODE", result.stdout)
        self.assertIn("Legacy parent selector", result.stdout)
        self.assertIn("--strategy {single,loop}", result.stdout)


if __name__ == "__main__":
    unittest.main()
