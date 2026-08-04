from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import task as task_cli
from common.io import read_json
from common.task_context import _validate_v2_task
from common.task_store import (
    _write_template_files,
    cmd_archive,
    cmd_cancel,
    cmd_set_scope,
)
from taskrun import RESULT_FIELDS, TaskRun, bootstrap_task_run, close_task_run
from taskrun import operator as operator_module
from test_task_activity import taskrun_terminal_proof_fixture
from test_taskrun_operator import TASK, git, prepare_repo


HOOK_SPEC = importlib.util.spec_from_file_location(
    "inject_workflow_state",
    SCRIPT_DIR.parents[1] / ".codex/hooks/inject-workflow-state.py",
)
assert HOOK_SPEC and HOOK_SPEC.loader
HOOK = importlib.util.module_from_spec(HOOK_SPEC)
HOOK_SPEC.loader.exec_module(HOOK)


def configure_task(root: Path, strategy: str) -> Path:
    task_dir = root / ".trellis/tasks" / TASK
    task = read_json(task_dir / "task.json")
    task["status"] = "planning"
    task["meta"] = {
        "taskrun_strategy": strategy,
        "workflow_mode": "taskrun_v2",
    }
    (task_dir / "task.json").write_text(
        json.dumps(task, indent=2) + "\n", encoding="utf-8"
    )
    (task_dir / "state-events.jsonl").unlink(missing_ok=True)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", f"configure {strategy}")
    return task_dir


def single_request(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "actor": "operator",
                "authorization_ref": "user-execution-signal:single:1",
                "reviewer_id": "reviewer-a",
                "worker_id": "worker-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def loop_request(path: Path) -> None:
    action = {
        "action_id": "slice-a",
        "checks": ["python3 -m unittest slice-a"],
        "dependencies": [],
        "problem_id": "problem-slice-a",
        "requirement_ids": ["FIXTURE-REQ-001"],
        "resources": ["resource-a"],
        "result_schema": sorted(RESULT_FIELDS),
        "touches": ["src/**"],
    }
    path.write_text(
        json.dumps(
            {
                "actions": [action],
                "actor": "operator",
                "authorization_ref": "user-execution-signal:loop:1",
                "concurrency": 1,
                "reviewer_id": "reviewer-a",
                "worker_ids": ["worker-a"],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def start(
    root: Path,
    request: Path,
    task_ref: str = TASK,
    hook: mock.Mock | None = None,
) -> int:
    args = argparse.Namespace(dir=task_ref, taskrun_input=str(request))
    with (
        mock.patch("task.get_repo_root", return_value=root),
        mock.patch("task.resolve_context_key", return_value=None),
        mock.patch("task.run_task_hooks", new=hook or mock.Mock()),
        redirect_stdout(io.StringIO()),
    ):
        return task_cli.cmd_start(args)


def seed_legacy_evidence(root: Path) -> dict[Path, bytes]:
    values = {
        root / ".trellis/tasks/07-01-legacy-hsm/task.json": {
            "id": "legacy-hsm",
            "meta": {"workflow_mode": "harness_state_machine"},
            "status": "in_progress",
        },
        root / ".trellis/tasks/07-02-legacy-loop/task.json": {
            "id": "legacy-loop",
            "meta": {"workflow_mode": "loop_v1"},
            "status": "in_progress",
        },
        root / ".trellis/tasks/archive/2026-07/07-03-old/task.json": {
            "id": "old",
            "meta": {"workflow_mode": "harness_state_machine"},
            "status": "completed",
        },
    }
    for path, value in values.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    ledger = root / ".trellis/.runtime/loop-v1/parents/legacy/ledger.sqlite3"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"legacy-ledger\n")
    paths = [*values, ledger]
    return {path: path.read_bytes() for path in paths}


class TaskRunCutoverTests(unittest.TestCase):
    def test_committed_terminal_proof_validates_without_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, task_dir, _ = taskrun_terminal_proof_fixture(Path(tmp))
            self.assertEqual(_validate_v2_task(task_dir, root), 0)

    def test_running_task_uses_the_implementation_breadcrumb(self) -> None:
        self.assertEqual(
            HOOK.resolve_breadcrumb_key("running", "codex", {}),
            "in_progress-inline",
        )
        self.assertEqual(
            HOOK.resolve_breadcrumb_key("running", "claude", {}),
            "in_progress",
        )
        self.assertEqual(
            HOOK.resolve_breadcrumb_key("cancelled", "codex", {}),
            "completed-inline",
        )

    def test_single_start_creates_one_replayable_taskrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            legacy = seed_legacy_evidence(root)
            task_dir = configure_task(root, "single")
            request = root / "start.json"
            single_request(request)
            hook = mock.Mock()

            self.assertEqual(start(root, request, hook=hook), 0)
            hook.assert_not_called()
            first = read_json(task_dir / "task.json")
            run_id = first["meta"]["task_run"]["id"]
            run = TaskRun.open(root, run_id)
            digest = run.authority_digest()
            self.assertEqual(run.snapshot()["execution"]["config"]["strategy"], "single")
            self.assertEqual(first["status"], "running")
            self.assertEqual(_validate_v2_task(task_dir, root), 0)

            self.assertEqual(start(root, request), 0)
            self.assertEqual(TaskRun.open(root, run_id).authority_digest(), digest)
            self.assertEqual(
                len(list((root / ".trellis/.runtime/taskrun/runs").iterdir())),
                1,
            )
            self.assertEqual(
                {path: path.read_bytes() for path in legacy},
                legacy,
            )

    def test_v3_template_path_matches_validation_and_single_admission(self) -> None:
        source_repo = SCRIPT_DIR.parents[1]
        for tier in ("parent", "child", "light"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prepare_repo(root)
                task_dir = configure_task(root, "single")
                task = read_json(task_dir / "task.json")
                task.update(
                    {
                        "children": [],
                        "owner": "codex",
                        "parent": None,
                        "tier": tier,
                    }
                )
                (task_dir / "task.json").write_text(
                    json.dumps(task, indent=2) + "\n", encoding="utf-8"
                )
                (task_dir / "prd.md").unlink()
                _write_template_files(
                    task_dir,
                    source_repo,
                    tier,
                    f"Template {tier}",
                    f"Template {tier} description",
                )

                with redirect_stdout(io.StringIO()):
                    self.assertGreater(_validate_v2_task(task_dir, root), 0)
                self.assertFalse((root / ".trellis/.runtime/taskrun").exists())

                prd_path = task_dir / "prd.md"
                text = prd_path.read_text(encoding="utf-8")
                if tier == "child":
                    product = root / "docs/PRD/product.md"
                    product.parent.mkdir(parents=True)
                    product.write_text(
                        "# Product\n\n## Requirements\n\n"
                        "- `TEMPLATE-REQ-001` [owner: codex]: Template contract.\n",
                        encoding="utf-8",
                    )
                    git(root, "add", ".")
                    git(root, "commit", "-q", "-m", "accepted product")
                    accepted = git(root, "rev-parse", "HEAD")
                    text = (
                        text.replace("- Git commit: `TODO`", f"- Git commit: `{accepted}`")
                        .replace(
                            "- PRD paths: `TODO`",
                            "- PRD paths: `docs/PRD/product.md`",
                        )
                        .replace("- REQ IDs: `TODO-REQ-001`", "- REQ IDs: `TEMPLATE-REQ-001`")
                    )
                else:
                    text = text.replace("TODO-REQ-001", "TEMPLATE-REQ-001").replace(
                        "owner: TODO", "owner: codex"
                    )
                text = (
                    text.replace("- `TODO`", "- `python3 -m unittest fixture`")
                    .replace("- TODO", "- Filled.")
                    .replace("TBD", "Filled.")
                )
                prd_path.write_text(text, encoding="utf-8")
                git(root, "add", ".")
                git(root, "commit", "-q", "-m", f"filled {tier} template")

                with redirect_stdout(io.StringIO()):
                    self.assertEqual(_validate_v2_task(task_dir, root), 0)
                request = root / "start.json"
                single_request(request)
                self.assertEqual(start(root, request), 0)
                projection = read_json(task_dir / "task.json")
                run = TaskRun.open(root, projection["meta"]["task_run"]["id"])
                action = run.snapshot()["execution"]["config"]["start_envelope"][
                    "actions"
                ][0]
                self.assertEqual(action["requirement_ids"], ["TEMPLATE-REQ-001"])
                self.assertEqual(
                    action["checks"], ["python3 -m unittest fixture"]
                )

    def test_validation_and_start_share_read_only_prd_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            (task_dir / "prd.md").write_text(
                "# Deprecated\n\n## REQ-ID\n\n"
                "- FIXTURE-REQ-001: Old form.\n\n"
                "## Verification Commands\n\n"
                "- `python3 -m unittest fixture`\n",
                encoding="utf-8",
            )
            request = root / "start.json"
            single_request(request)
            before = {
                "task": (task_dir / "task.json").read_bytes(),
                "board": (root / "BOARD.md").read_bytes(),
            }

            validation_output = io.StringIO()
            with redirect_stdout(validation_output):
                self.assertGreater(_validate_v2_task(task_dir, root), 0)
            start_output = io.StringIO()
            args = argparse.Namespace(dir=TASK, taskrun_input=str(request))
            with (
                mock.patch("task.get_repo_root", return_value=root),
                mock.patch("task.resolve_context_key", return_value=None),
                mock.patch("task.run_task_hooks", new=mock.Mock()),
                redirect_stdout(start_output),
            ):
                self.assertEqual(task_cli.cmd_start(args), 1)

            error = "PRD must not contain the deprecated ## REQ-ID section"
            self.assertIn(error, validation_output.getvalue())
            self.assertIn(error, start_output.getvalue())
            self.assertEqual((task_dir / "task.json").read_bytes(), before["task"])
            self.assertEqual((root / "BOARD.md").read_bytes(), before["board"])
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())

    def test_admitted_legacy_prd_reopens_from_immutable_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            legacy_prd = (
                "# Historical\n\n## REQ-ID\n\n"
                "- FIXTURE-REQ-001: Historical form.\n\n"
                "## Verification Commands\n\n"
                "- `python3 -m unittest fixture`\n"
            )
            (task_dir / "prd.md").write_text(legacy_prd, encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "historical admitted plan")
            task = read_json(task_dir / "task.json")
            envelope = operator_module._start_envelope(
                root,
                task_dir.name,
                task,
                legacy_prd.encode("utf-8"),
                authorization_ref="user-execution-signal:single:1",
                worker_id="worker-a",
                reviewer_id="reviewer-a",
                provider_id="local",
                action_risk="low",
                low_risk_mode="aggregate",
                attempts=4,
                attempt_offset=0,
                recorded_preflight={
                    "requirement_ids": ["FIXTURE-REQ-001"],
                    "verification_commands": ["python3 -m unittest fixture"],
                },
            )
            run = bootstrap_task_run(
                root,
                task_dir.name,
                task,
                actor="operator",
                strategy="single",
                start_envelope=envelope,
            )
            (task_dir / "task.json").write_bytes(run.task_projection_bytes())
            before = run.authority_digest()
            request = root / "start.json"
            single_request(request)

            self.assertEqual(start(root, request), 0)
            self.assertEqual(TaskRun.open(root, run.task_run_id).authority_digest(), before)

    def test_finish_skips_configurable_hooks_for_taskrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            active = SimpleNamespace(
                task_path=task_dir.relative_to(root).as_posix(),
                source="test",
            )
            hook = mock.Mock()
            with (
                mock.patch("task.get_repo_root", return_value=root),
                mock.patch("task.clear_active_task", return_value=active),
                mock.patch("task.run_task_hooks", new=hook),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(task_cli.cmd_finish(argparse.Namespace()), 0)
            hook.assert_not_called()

    def test_cancelled_taskrun_projection_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            request = root / "start.json"
            single_request(request)
            self.assertEqual(start(root, request), 0)
            task = read_json(task_dir / "task.json")
            run = TaskRun.open(root, task["meta"]["task_run"]["id"])
            run.record_terminal(
                operation_id="terminal:cancelled:1",
                actor="operator",
                disposition="cancelled",
                authorization_ref="user-terminal:cancelled:1",
                evidence={"verification_digest": "c" * 64},
            )
            (task_dir / "task.json").write_bytes(run.task_projection_bytes())
            close_task_run(
                run,
                operation_id=f"close:{run.task_run_id}:1",
                task_dir=task_dir,
                actor="operator",
            )

            self.assertEqual(_validate_v2_task(task_dir, root), 0)

    def test_loop_start_uses_the_same_taskrun_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "loop")
            request = root / "start.json"
            loop_request(request)

            self.assertEqual(start(root, request), 0)
            task = read_json(task_dir / "task.json")
            run = TaskRun.open(root, task["meta"]["task_run"]["id"])
            config = run.snapshot()["execution"]["config"]
            self.assertEqual(config["strategy"], "loop")
            self.assertEqual([item["action_id"] for item in config["start_envelope"]["actions"]], ["slice-a"])
            self.assertFalse((root / ".trellis/.runtime/loop-v1").exists())

    def test_invalid_start_input_is_write_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            request = root / "start.json"
            single_request(request)
            value = json.loads(request.read_text(encoding="utf-8"))
            value["accepted"] = True
            request.write_text(json.dumps(value) + "\n", encoding="utf-8")
            board = root / "BOARD.md"
            board.write_bytes(b"unchanged-board\n")
            pointer = root / ".trellis/.runtime/sessions/existing.json"
            pointer.parent.mkdir(parents=True)
            pointer.write_bytes(b"unchanged-pointer\n")
            ledger = root / ".trellis/.runtime/loop-v1/existing.sqlite3"
            ledger.parent.mkdir(parents=True)
            ledger.write_bytes(b"unchanged-ledger\n")
            before = {
                "task": (task_dir / "task.json").read_bytes(),
                "board": board.read_bytes(),
                "pointer": pointer.read_bytes(),
                "ledger": ledger.read_bytes(),
            }

            self.assertEqual(start(root, request), 1)
            self.assertEqual((task_dir / "task.json").read_bytes(), before["task"])
            self.assertEqual(board.read_bytes(), before["board"])
            self.assertEqual(pointer.read_bytes(), before["pointer"])
            self.assertEqual(ledger.read_bytes(), before["ledger"])
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())

    def test_external_same_named_task_path_cannot_select_repository_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            external = Path(tmp) / TASK
            external.mkdir()
            (external / "task.json").write_bytes((task_dir / "task.json").read_bytes())
            request = Path(tmp) / "start.json"
            single_request(request)

            self.assertEqual(start(root, request, str(external)), 1)
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())

    def test_taskrun_validation_rejects_competing_hsm_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            (task_dir / "state-events.jsonl").write_text("{}\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertGreater(_validate_v2_task(task_dir, root), 0)

    def test_taskrun_cancel_rejects_redirected_task_roots_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            tasks_dir = root / ".trellis/tasks"
            symlink = tasks_dir / "task-alias"
            symlink.symlink_to(task_dir, target_is_directory=True)
            nested = tasks_dir / "nested" / TASK
            nested.parent.mkdir()
            shutil.copytree(task_dir, nested)

            refs = (
                (symlink.name, symlink),
                (nested.relative_to(root).as_posix(), nested),
            )
            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                mock.patch("common.task_store.run_task_hooks"),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                for ref, selected in refs:
                    with self.subTest(ref=ref):
                        args = argparse.Namespace(
                            name=ref,
                            reason="redirected root regression",
                            authorized_by="jym",
                            superseded_by=None,
                            rtm_disposition=None,
                            rtm_id=[],
                        )
                        self.assertEqual(cmd_cancel(args), 1)
                        self.assertTrue(selected.is_symlink() or selected.is_dir())

            self.assertEqual(read_json(task_dir / "task.json")["status"], "planning")
            self.assertEqual(read_json(nested / "task.json")["status"], "planning")

    def test_taskrun_archive_rejects_redirected_task_roots_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            cancel = argparse.Namespace(
                name=TASK,
                reason="archive root regression",
                authorized_by="jym",
                superseded_by=None,
                rtm_disposition=None,
                rtm_id=[],
            )
            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                mock.patch("common.task_store.run_task_hooks"),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cmd_cancel(cancel), 0)

            tasks_dir = root / ".trellis/tasks"
            symlink = tasks_dir / "task-alias"
            symlink.symlink_to(task_dir, target_is_directory=True)
            nested = tasks_dir / "nested" / TASK
            nested.parent.mkdir()
            shutil.copytree(task_dir, nested)
            refs = (
                (symlink.name, symlink),
                (nested.relative_to(root).as_posix(), nested),
            )
            before = (task_dir / "task.json").read_bytes()

            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                for ref, selected in refs:
                    with self.subTest(ref=ref):
                        args = argparse.Namespace(
                            name=ref,
                            no_commit=True,
                            force_archive=False,
                            reason="",
                        )
                        self.assertEqual(cmd_archive(args), 1)
                        self.assertTrue(selected.is_symlink() or selected.is_dir())

            self.assertTrue(task_dir.is_dir())
            self.assertEqual((task_dir / "task.json").read_bytes(), before)
            self.assertFalse(
                (tasks_dir / "archive" / datetime.now().strftime("%Y-%m") / TASK).exists()
            )

    def test_unadmitted_taskrun_can_cancel_and_archive_without_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            cancel = argparse.Namespace(
                name=TASK,
                reason="legacy path",
                authorized_by="jym",
                superseded_by=None,
                rtm_disposition=None,
                rtm_id=[],
            )
            archive = argparse.Namespace(
                name=TASK,
                no_commit=True,
                force_archive=False,
                reason="",
            )
            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                mock.patch("common.task_store.run_task_hooks"),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cmd_cancel(cancel), 0)
                self.assertEqual(cmd_archive(archive), 0)
            self.assertFalse(task_dir.exists())
            archived = root / ".trellis/tasks/archive" / datetime.now().strftime("%Y-%m") / TASK
            self.assertEqual(read_json(archived / "task.json")["status"], "cancelled")
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())

    def test_generic_metadata_command_cannot_mutate_admitted_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_repo(root)
            task_dir = configure_task(root, "single")
            request = root / "start.json"
            single_request(request)
            self.assertEqual(start(root, request), 0)
            before = (task_dir / "task.json").read_bytes()

            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    cmd_set_scope(argparse.Namespace(dir=TASK, scope="changed")),
                    1,
                )
            self.assertEqual((task_dir / "task.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
