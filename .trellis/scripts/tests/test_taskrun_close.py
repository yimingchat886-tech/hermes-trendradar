from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from taskrun import (
    FAULT_BOUNDARIES,
    CloseError,
    InjectedFailure,
    OperationConflict,
    TaskRun,
    close_task_run,
)


def create_fixture(
    root: Path,
    *,
    run_id: str = "fixture-run",
    disposition: str = "completed",
) -> tuple[TaskRun, Path, Path]:
    task_name = f"07-31-{run_id}"
    task_dir = root / ".trellis" / "tasks" / task_name
    task_dir.mkdir(parents=True)
    task = {
        "id": run_id,
        "name": run_id,
        "title": f"Fixture {run_id}",
        "status": "planning",
        "tier": "child",
        "owner": "codex",
        "assignee": "jym",
        "children": [],
        "meta": {"workflow_mode": "taskrun_v1"},
    }
    run = TaskRun.initialize(
        root,
        run_id,
        actor="fixture-actor",
        task_dir_name=task_name,
        task_json=task,
    )
    run.record_started(operation_id=f"start:{run_id}", actor="operator")
    run.record_terminal(
        operation_id=f"terminal:{run_id}",
        actor="operator",
        disposition=disposition,
        authorization_ref="user-terminal:1",
        evidence={"verification_digest": "c" * 64},
    )
    (task_dir / "task.json").write_bytes(run.task_projection_bytes())
    (task_dir / "prd.md").write_text("# Fixture\n", encoding="utf-8")
    session = root / ".trellis" / ".runtime" / "sessions" / "fixture.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        json.dumps({"current_task": f".trellis/tasks/{task_name}"}) + "\n",
        encoding="utf-8",
    )
    (root / "BOARD.md").write_bytes(b"# Stable Board\n")
    return run, task_dir, session


def close(
    run: TaskRun,
    task_dir: Path,
    *,
    actor: str = "operator",
    fault_after: str | None = None,
) -> dict[str, object]:
    return close_task_run(
        run,
        operation_id=f"close:{run.task_run_id}:1",
        task_dir=task_dir,
        actor=actor,
        fault_after=fault_after,
    )


class TaskRunCloseTests(unittest.TestCase):
    def test_close_is_status_only_exact_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, session = create_fixture(root)
            board = (root / "BOARD.md").read_bytes()
            pointer = session.read_bytes()
            prd = (task_dir / "prd.md").read_bytes()

            result = close(run, task_dir)
            event_count = len(run.events())
            projected_bytes = (task_dir / "task.json").read_bytes()
            replay = close(run, task_dir)

            self.assertEqual(result, replay)
            self.assertEqual(result["status"], "closed")
            self.assertEqual(result["phase"], "projected")
            self.assertTrue(task_dir.is_dir())
            self.assertFalse((root / ".trellis" / "tasks" / "archive").exists())
            self.assertEqual(session.read_bytes(), pointer)
            self.assertEqual((root / "BOARD.md").read_bytes(), board)
            self.assertEqual((task_dir / "prd.md").read_bytes(), prd)
            self.assertEqual((task_dir / "task.json").read_bytes(), projected_bytes)
            self.assertEqual(len(run.events()), event_count)

            projected = json.loads(projected_bytes)
            self.assertEqual(projected["status"], "completed")
            self.assertEqual(projected["meta"]["task_run"]["state"], "closed")
            self.assertNotIn("archive_path", projected["meta"]["task_run"])
            self.assertNotIn("commit", projected)
            self.assertTrue(run.snapshot()["closed"])
            self.assertEqual(run.snapshot()["close"]["effects"], {})
            self.assertEqual(
                run.get_operation("close:fixture-run:1")["phase"], "projected"
            )
            self.assertEqual(
                run.projection_status("task_json")["digest"],
                result["projection_digest"],
            )
            self.assertEqual(run.projection_status("close")["status"], "current")
            self.assertEqual(run.projection_status("board")["status"], "missing")

            (root / "BOARD.md").write_bytes(board + b"# Later Board update\n")
            self.assertEqual(close(run, task_dir), result)
            self.assertTrue(
                (root / "BOARD.md").read_bytes().endswith(b"# Later Board update\n")
            )

    def test_cancelled_close_needs_no_git_or_external_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, session = create_fixture(
                root,
                run_id="cancelled",
                disposition="cancelled",
            )
            board = (root / "BOARD.md").read_bytes()
            pointer = session.read_bytes()

            result = close(run, task_dir)
            projected = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "closed")
            self.assertEqual(projected["status"], "cancelled")
            self.assertNotIn("commit", projected)
            self.assertFalse((root / ".git").exists())
            self.assertTrue(task_dir.is_dir())
            self.assertEqual(session.read_bytes(), pointer)
            self.assertEqual((root / "BOARD.md").read_bytes(), board)

    def test_every_durable_boundary_recovers_on_exact_replay(self) -> None:
        for index, boundary in enumerate(FAULT_BOUNDARIES):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run, task_dir, session = create_fixture(
                    root,
                    run_id=f"fault-{index}",
                )
                board = (root / "BOARD.md").read_bytes()
                pointer = session.read_bytes()

                with self.assertRaises(InjectedFailure):
                    close(run, task_dir, fault_after=boundary)

                self.assertEqual(run.snapshot()["closed"], boundary != "close_prepared")
                result = close(run, task_dir)
                event_ids = [event["event_id"] for event in run.events()]

                self.assertEqual(result["status"], "closed")
                self.assertTrue(run.snapshot()["closed"])
                self.assertEqual(len(event_ids), len(set(event_ids)))
                self.assertTrue(task_dir.is_dir())
                self.assertEqual(session.read_bytes(), pointer)
                self.assertEqual((root / "BOARD.md").read_bytes(), board)

    def test_changed_replay_input_conflicts_before_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, _session = create_fixture(root)
            preimage = (task_dir / "task.json").read_bytes()

            with self.assertRaises(InjectedFailure):
                close(run, task_dir, fault_after="close_prepared")
            event_count = len(run.events())

            with self.assertRaises(OperationConflict):
                close(run, task_dir, actor="different-operator")

            self.assertEqual((task_dir / "task.json").read_bytes(), preimage)
            self.assertEqual(len(run.events()), event_count)
            self.assertEqual(close(run, task_dir)["status"], "closed")

    def test_projection_conflict_fails_closed_and_recovers_from_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, session = create_fixture(root)
            preimage = (task_dir / "task.json").read_bytes()
            board = (root / "BOARD.md").read_bytes()
            pointer = session.read_bytes()

            with self.assertRaises(InjectedFailure):
                close(run, task_dir, fault_after="authority_committed")

            changed = json.loads(preimage)
            changed["concurrent_projection"] = True
            changed_bytes = (json.dumps(changed, indent=2) + "\n").encode("utf-8")
            (task_dir / "task.json").write_bytes(changed_bytes)

            with self.assertRaisesRegex(CloseError, "changed after close preparation"):
                close(run, task_dir)

            self.assertEqual((task_dir / "task.json").read_bytes(), changed_bytes)
            self.assertEqual(run.projection_status("task_json")["status"], "failed")
            self.assertEqual(
                run.get_operation("close:fixture-run:1")["phase"],
                "authority_committed",
            )
            self.assertEqual(session.read_bytes(), pointer)
            self.assertEqual((root / "BOARD.md").read_bytes(), board)

            (task_dir / "task.json").write_bytes(b"[]\n")
            with self.assertRaises(CloseError):
                close(run, task_dir)

            task_json = task_dir / "task.json"
            task_json.write_bytes(preimage)
            real_link = os.link

            def concurrent_link(source, destination, *args, **kwargs):
                if Path(destination) == task_json and Path(source) != task_json:
                    task_json.write_bytes(changed_bytes)
                return real_link(source, destination, *args, **kwargs)

            with mock.patch("taskrun.close.os.link", side_effect=concurrent_link):
                with self.assertRaisesRegex(CloseError, "changed during close projection"):
                    close(run, task_dir)

            self.assertEqual(task_json.read_bytes(), changed_bytes)
            (task_dir / "task.json").write_bytes(preimage)
            self.assertEqual(close(run, task_dir)["status"], "closed")
            self.assertEqual(run.projection_status("task_json")["status"], "current")

    def test_interrupted_projection_claim_recovers_without_duplicate_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, _session = create_fixture(root)
            operation_id = "close:fixture-run:1"
            task_json = task_dir / "task.json"

            with self.assertRaises(InjectedFailure):
                close(run, task_dir, fault_after="authority_committed")

            claim = task_json.with_name(
                f".{task_json.name}."
                f"{sha256(operation_id.encode('utf-8')).hexdigest()}.close-claim"
            )
            candidate = claim.with_name(f"{claim.name}.candidate")
            task_json.rename(claim)
            candidate.write_bytes(run.task_projection_bytes())
            event_count = len(run.events())

            self.assertEqual(close(run, task_dir)["status"], "closed")
            self.assertTrue(task_json.is_file())
            self.assertFalse(claim.exists())
            self.assertFalse(candidate.exists())
            self.assertEqual(
                [event["event_type"] for event in run.events()].count("closed"),
                1,
            )
            self.assertEqual(len(run.events()), event_count + 1)

    def test_projected_replay_reports_and_recovers_projection_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, _session = create_fixture(root)
            result = close(run, task_dir)
            task_json = task_dir / "task.json"
            final_projection = task_json.read_bytes()
            event_count = len(run.events())

            task_json.write_bytes(b"[]\n")
            with self.assertRaisesRegex(CloseError, "JSON object"):
                close(run, task_dir)

            task_json.unlink()

            with self.assertRaisesRegex(CloseError, "unreadable"):
                close(run, task_dir)

            self.assertEqual(run.projection_status("task_json")["status"], "failed")
            self.assertEqual(len(run.events()), event_count)
            task_json.write_bytes(final_projection)
            self.assertEqual(close(run, task_dir), result)
            self.assertEqual(run.projection_status("task_json")["status"], "current")

    def test_unrelated_legacy_and_taskrun_authority_bytes_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, task_dir, _session = create_fixture(root)
            legacy = root / ".trellis" / "tasks" / "07-31-legacy"
            legacy.mkdir()
            (legacy / "task.json").write_text(
                '{"id":"legacy","status":"in_progress"}\n', encoding="utf-8"
            )
            (legacy / "state-events.jsonl").write_text(
                '{"event":"init"}\n', encoding="utf-8"
            )
            ledger = root / ".trellis" / ".runtime" / "loop-v1" / "parents" / "legacy"
            ledger.mkdir(parents=True)
            (ledger / "ledger.sqlite3").write_bytes(b"legacy-authority")
            other = TaskRun.initialize(
                root,
                "other-run",
                actor="fixture-actor",
                task_dir_name="07-31-other-run",
                task_json={
                    "id": "other-run",
                    "title": "Other Run",
                    "status": "planning",
                    "meta": {"workflow_mode": "taskrun_v1"},
                },
            )
            paths = (
                legacy / "task.json",
                legacy / "state-events.jsonl",
                ledger / "ledger.sqlite3",
                other.path,
            )
            before = {path: sha256(path.read_bytes()).hexdigest() for path in paths}

            close(run, task_dir)

            self.assertEqual(
                before,
                {path: sha256(path.read_bytes()).hexdigest() for path in paths},
            )


if __name__ == "__main__":
    unittest.main()
