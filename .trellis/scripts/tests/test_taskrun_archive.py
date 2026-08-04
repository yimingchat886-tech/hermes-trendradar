from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common.task_activity import classify_task_activity
from common.task_store import cmd_archive
from taskrun import TaskRun, close_task_run
from test_task_activity import taskrun_terminal_proof_fixture


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class TaskRunArchiveTests(unittest.TestCase):
    def test_drifted_terminal_proof_fails_before_archive_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, task_dir, proof = taskrun_terminal_proof_fixture(Path(tmp))
            proof.write_text("{}\n", encoding="utf-8")
            head = git(root, "rev-parse", "HEAD")
            args = argparse.Namespace(
                name=task_dir.name,
                no_commit=False,
                force_archive=False,
                reason="",
            )
            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cmd_archive(args), 1)
            self.assertEqual(git(root, "rev-parse", "HEAD"), head)
            self.assertTrue(task_dir.is_dir())
            self.assertFalse(
                (root / ".trellis" / ".runtime" / "archive-transactions").exists()
            )

    def test_retired_v1_proof_archives_without_recreating_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, task_dir, _ = taskrun_terminal_proof_fixture(Path(tmp))
            args = argparse.Namespace(
                name=task_dir.name,
                no_commit=False,
                force_archive=False,
                reason="",
            )
            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cmd_archive(args), 0)
            archived = root / ".trellis" / "tasks" / "archive" / "2026-08" / task_dir.name
            self.assertFalse(task_dir.exists())
            self.assertTrue((archived / "terminal-proof.json").is_file())
            self.assertFalse((root / ".trellis" / ".runtime" / "taskrun").exists())
            self.assertFalse(classify_task_activity(archived, root).active)

    def test_closed_taskrun_archives_and_retires_runtime_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-b", "main")
            git(root, "config", "user.name", "TaskRun Archive")
            git(root, "config", "user.email", "archive@example.invalid")
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            (root / "BOARD.md").write_text("# Board\n", encoding="utf-8")
            git(root, "add", "README.md", "BOARD.md")
            git(root, "commit", "-m", "fixture")
            base = git(root, "rev-parse", "HEAD")

            task_name = "08-04-taskrun-archive-fixture"
            task_dir = root / ".trellis" / "tasks" / task_name
            task_dir.mkdir(parents=True)
            seed = {
                "id": "taskrun-archive-fixture",
                "name": "taskrun-archive-fixture",
                "status": "planning",
                "tier": "light",
                "meta": {
                    "taskrun_strategy": "single",
                    "workflow_mode": "taskrun_v2",
                },
            }
            run = TaskRun.initialize(
                root,
                "taskrun-archive-fixture",
                actor="fixture",
                task_dir_name=task_name,
                task_json=seed,
            )
            run.record_started(operation_id="start:fixture", actor="fixture")
            run.record_terminal(
                operation_id="terminal:fixture",
                actor="fixture",
                disposition="completed",
                authorization_ref="user-terminal:fixture",
                evidence={
                    "base_commit": base,
                    "commit_reconciliation": {"head_commit": base},
                },
            )
            (task_dir / "task.json").write_bytes(run.task_projection_bytes())
            (task_dir / "prd.md").write_text("# Fixture\n", encoding="utf-8")
            close_task_run(
                run,
                operation_id="close:taskrun-archive-fixture:1",
                task_dir=task_dir,
                actor="fixture",
            )
            session = root / ".trellis" / ".runtime" / "sessions" / "fixture.json"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"current_task": f".trellis/tasks/{task_name}"}) + "\n",
                encoding="utf-8",
            )
            board = root / ".trellis" / "scripts" / "board.py"
            board.parent.mkdir(parents=True)
            board.write_text(
                "from pathlib import Path\nPath('BOARD.md').write_text('# Board\\n')\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                name=task_name,
                no_commit=True,
                force_archive=False,
                reason="",
            )

            with (
                mock.patch("common.task_store.get_repo_root", return_value=root),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()),
            ):
                head = git(root, "rev-parse", "HEAD")
                self.assertEqual(cmd_archive(args), 1)
                self.assertEqual(git(root, "rev-parse", "HEAD"), head)
                self.assertTrue(task_dir.is_dir())
                self.assertFalse((task_dir / "terminal-proof.json").exists())
                args.no_commit = False
                with mock.patch(
                    "common.task_store._retire_taskrun_authority",
                    side_effect=RuntimeError("injected cleanup failure"),
                ):
                    self.assertEqual(cmd_archive(args), 1)
                journals = list(
                    (root / ".trellis" / ".runtime" / "archive-transactions").glob("*.json")
                )
                self.assertEqual(len(journals), 1)
                self.assertEqual(
                    json.loads(journals[0].read_text(encoding="utf-8"))["phase"],
                    "recovery_required",
                )
                sqlite_bytes = run.path.read_bytes()
                run.path.write_bytes(sqlite_bytes + b"drift")
                self.assertEqual(cmd_archive(args), 1)
                self.assertTrue(run.path.is_file())
                run.path.write_bytes(sqlite_bytes)
                with mock.patch("common.active_task._remove_file", return_value=False):
                    self.assertEqual(cmd_archive(args), 1)
                self.assertFalse(run.path.parent.exists())
                self.assertTrue(session.exists())
                self.assertEqual(
                    json.loads(journals[0].read_text(encoding="utf-8"))["phase"],
                    "recovery_required",
                )
                with mock.patch(
                    "common.archive_transaction.discard_committed_archive_transaction",
                    side_effect=RuntimeError("injected journal cleanup failure"),
                ):
                    self.assertEqual(cmd_archive(args), 1)
                self.assertFalse(session.exists())
                self.assertEqual(
                    json.loads(journals[0].read_text(encoding="utf-8"))["phase"],
                    "recovery_required",
                )
                self.assertEqual(cmd_archive(args), 0)
                self.assertEqual(cmd_archive(args), 0)

            archived = root / ".trellis" / "tasks" / "archive" / "2026-08" / task_name
            self.assertTrue(archived.is_dir())
            self.assertFalse(task_dir.exists())
            self.assertFalse(run.path.parent.exists())
            self.assertFalse(session.exists())
            self.assertFalse(
                list(
                    (root / ".trellis" / ".runtime" / "archive-transactions").glob("*.json")
                )
            )
            self.assertFalse(list(archived.glob(".task.json.*.close-claim*")))
            self.assertFalse(classify_task_activity(archived, root).active)
            self.assertEqual(
                git(root, "worktree", "list", "--porcelain").count("worktree "),
                1,
            )


if __name__ == "__main__":
    unittest.main()
