from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from taskrun import (
    EventConflict,
    InvalidTransition,
    OperationConflict,
    TaskRun,
    TaskRunError,
    read_legacy_task_evidence,
    taskrun_path,
)


def task_json() -> dict[str, object]:
    return {
        "id": "fixture-task",
        "name": "fixture-task",
        "title": "Fixture Task",
        "status": "planning",
        "tier": "child",
        "meta": {"workflow_mode": "taskrun_v1"},
    }


def initialize(root: Path, run_id: str = "fixture-run") -> TaskRun:
    return TaskRun.initialize(
        root,
        run_id,
        actor="fixture-actor",
        task_dir_name="07-21-fixture-task",
        task_json=task_json(),
    )


class TaskRunAuthorityTests(unittest.TestCase):
    def test_one_database_schema_and_initialization_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = initialize(root)
            replayed = initialize(root)

            self.assertEqual(run.path, taskrun_path(root, "fixture-run"))
            self.assertEqual(replayed.path, run.path)
            self.assertTrue(run.path.is_file())
            self.assertEqual(run.reference()["read_only"], True)
            self.assertEqual(
                len(list((root / ".trellis" / ".runtime").rglob("authority.sqlite3"))),
                1,
            )

            connection = sqlite3.connect(run.path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "events",
                        "operations",
                        "projection_checkpoints",
                        "schema_meta",
                        "task_runs",
                    }.issubset(tables)
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            finally:
                connection.close()

            with self.assertRaises(OperationConflict):
                TaskRun.initialize(
                    root,
                    "fixture-run",
                    actor="different-actor",
                    task_dir_name="07-21-fixture-task",
                    task_json=task_json(),
                )
            with self.assertRaises(TaskRunError):
                taskrun_path(root, "../unsafe")

    def test_stable_operations_ordered_events_and_deterministic_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = initialize(Path(tmp))
            started = run.record_started(operation_id="start:fixture-run", actor="operator")
            replayed = run.record_started(operation_id="start:fixture-run", actor="operator")
            self.assertEqual(started, replayed)

            with self.assertRaises(OperationConflict):
                run.record_started(operation_id="start:fixture-run", actor="other")

            terminal = run.record_terminal(
                operation_id="terminal:fixture-run",
                actor="operator",
                disposition="completed",
                authorization_ref="user-response:1",
                evidence={"verification_digest": "a" * 64},
            )
            self.assertEqual(
                terminal,
                run.record_terminal(
                    operation_id="terminal:fixture-run",
                    actor="operator",
                    disposition="completed",
                    authorization_ref="user-response:1",
                    evidence={"verification_digest": "a" * 64},
                ),
            )
            with self.assertRaises(InvalidTransition):
                run.record_terminal(
                    operation_id="terminal:second",
                    actor="operator",
                    disposition="cancelled",
                    authorization_ref="user-response:2",
                    evidence={},
                )

            events = run.events()
            self.assertEqual([event["position"] for event in events], [1, 2, 3])
            self.assertIsNone(events[0]["previous_digest"])
            self.assertEqual(events[1]["previous_digest"], events[0]["event_digest"])
            self.assertEqual(events[2]["previous_digest"], events[1]["event_digest"])
            first = run.task_projection_bytes()
            second = run.task_projection_bytes()
            self.assertEqual(first, second)
            projected = json.loads(first)
            self.assertEqual(projected["status"], "completed")
            self.assertEqual(projected["completedAt"], terminal["created_at"][:10])
            self.assertEqual(projected["meta"]["task_run"]["id"], "fixture-run")
            self.assertEqual(run.snapshot()["status"], "completed")

            reopened = TaskRun.open(Path(tmp), "fixture-run")
            self.assertEqual(reopened.snapshot(), run.snapshot())
            self.assertEqual(reopened.authority_digest(), run.authority_digest())

    def test_terminal_hsm_host_projection_preserves_outer_lifecycle(self) -> None:
        for disposition in ("completed", "cancelled"):
            with self.subTest(disposition=disposition), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                seed = {
                    "id": "hsm-fixture-task",
                    "name": "hsm-fixture-task",
                    "status": "in_progress",
                    "tier": "child",
                    "meta": {
                        "workflow_mode": "harness_state_machine",
                        "state_machine": {
                            "current_state": "child_waiting_completion_signal",
                            "kind": "child",
                            "schema_version": 2,
                        },
                    },
                }
                run = TaskRun.initialize(
                    root,
                    f"hsm-fixture-{disposition}",
                    actor="fixture-actor",
                    task_dir_name="08-01-hsm-fixture-task",
                    task_json=seed,
                )
                run.record_started(
                    operation_id=f"start:{disposition}", actor="operator"
                )
                run.record_terminal(
                    operation_id=f"terminal:{disposition}",
                    actor="operator",
                    disposition=disposition,
                    authorization_ref="user-response:1",
                    evidence={},
                )
                operation_id = f"close:hsm-fixture-{disposition}:1"
                run.prepare_close(
                    operation_id=operation_id,
                    intent={
                        "actor": "operator",
                        "task_path": ".trellis/tasks/08-01-hsm-fixture-task",
                        "task_projection_preimage_digest": sha256(
                            run.task_projection_bytes()
                        ).hexdigest(),
                        "terminal_disposition": disposition,
                    },
                )
                run.commit_close(operation_id=operation_id)
                projection_digest = sha256(run.task_projection_bytes()).hexdigest()
                run.mark_projected(
                    operation_id=operation_id,
                    projection_digest=projection_digest,
                )

                projected = run.task_projection()
                self.assertEqual(projected["status"], seed["status"])
                self.assertEqual(
                    projected["meta"]["state_machine"],
                    seed["meta"]["state_machine"],
                )
                self.assertNotIn("completedAt", projected)
                self.assertNotIn("commit", projected)
                self.assertEqual(projected["meta"]["task_run"]["state"], "closed")
                self.assertEqual(run.snapshot()["status"], disposition)
                self.assertTrue(run.snapshot()["closed"])

    def test_non_active_or_invalid_hsm_seed_keeps_existing_projection(self) -> None:
        cases = (
            (
                "terminal_status",
                "completed",
                {
                    "current_state": "child_completed",
                    "kind": "child",
                    "schema_version": 2,
                },
            ),
            ("missing_identity", "in_progress", {}),
            (
                "terminal_state",
                "in_progress",
                {
                    "current_state": "child_completed",
                    "kind": "child",
                    "schema_version": 2,
                },
            ),
            (
                "unhashable_state",
                "in_progress",
                {"current_state": [], "kind": "child", "schema_version": 2},
            ),
        )
        for name, status, machine in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                seed = {
                    "id": "non-active-hsm-seed",
                    "name": "non-active-hsm-seed",
                    "status": status,
                    "tier": "child",
                    "meta": {
                        "workflow_mode": "harness_state_machine",
                        "state_machine": machine,
                    },
                }
                run = TaskRun.initialize(
                    root,
                    "non-active-hsm-run",
                    actor="fixture-actor",
                    task_dir_name="08-01-non-active-hsm-seed",
                    task_json=seed,
                )
                run.record_started(
                    operation_id="start:non-active-hsm", actor="operator"
                )
                run.record_terminal(
                    operation_id="terminal:non-active-hsm",
                    actor="operator",
                    disposition="cancelled",
                    authorization_ref="user-response:1",
                    evidence={},
                )

                projected = run.task_projection()
                self.assertEqual(projected["status"], "cancelled")
                self.assertEqual(projected["meta"]["task_run"]["state"], "cancelled")

    def test_reopen_fails_on_event_or_operation_tampering(self) -> None:
        cases = ("event", "operation_input", "operation_outcome")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run = initialize(root)
                connection = sqlite3.connect(run.path)
                try:
                    if case == "event":
                        connection.execute(
                            "UPDATE events SET payload_json = ? WHERE position = 1",
                            ('{"changed":true}',),
                        )
                    elif case == "operation_input":
                        connection.execute(
                            "UPDATE operations SET input_digest = ? WHERE operation_id = ?",
                            ("0" * 64, "initialize:fixture-run"),
                        )
                    else:
                        connection.execute(
                            "UPDATE operations SET outcome_json = ? WHERE operation_id = ?",
                            ('{"changed":true}', "initialize:fixture-run"),
                        )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises((EventConflict, OperationConflict)):
                    TaskRun.open(root, "fixture-run")

    def test_status_only_close_authority_rejects_physical_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = initialize(root)
            run.record_started(operation_id="start:fixture-run", actor="operator")
            run.record_terminal(
                operation_id="terminal:fixture-run",
                actor="operator",
                disposition="completed",
                authorization_ref="user-response:1",
                evidence={},
            )
            operation_id = "close:fixture-run:1"
            intent = {
                "actor": "operator",
                "task_path": ".trellis/tasks/07-21-fixture-task",
                "task_projection_preimage_digest": sha256(
                    run.task_projection_bytes()
                ).hexdigest(),
                "terminal_disposition": "completed",
            }
            with self.assertRaises(InvalidTransition):
                run.prepare_close(
                    operation_id=operation_id,
                    intent={key: value for key, value in intent.items() if key != "task_path"},
                )

            run.prepare_close(operation_id=operation_id, intent=intent)
            with self.assertRaises(InvalidTransition):
                run.observe_close_effect(
                    operation_id=operation_id,
                    effect="pointer_cleared",
                    observation={"cleared": 1, "remaining": 0},
                )
            with self.assertRaises(InvalidTransition):
                run.mark_close_unknown(
                    operation_id=operation_id,
                    reason="physical_effect_unknown",
                    detail_digest="a" * 64,
                )

            committed = run.commit_close(operation_id=operation_id)
            self.assertEqual(committed["phase"], "authority_committed")
            self.assertEqual(committed["outcome"], {"closed": True})
            projection_digest = sha256(run.task_projection_bytes()).hexdigest()
            with self.assertRaises(InvalidTransition):
                run.mark_projected(
                    operation_id=operation_id,
                    projection_digest="f" * 64,
                )
            self.assertEqual(
                run.mark_projected(
                    operation_id=operation_id,
                    projection_digest=projection_digest,
                )["phase"],
                "projected",
            )

            reopened = TaskRun.open(root, "fixture-run")
            projected = reopened.task_projection()
            self.assertTrue(reopened.snapshot()["closed"])
            self.assertEqual(reopened.snapshot()["close"]["effects"], {})
            self.assertNotIn("archive_path", projected["meta"]["task_run"])
            self.assertNotIn("commit", projected)
            self.assertEqual(reopened.projection_status("board")["status"], "missing")

    def test_legacy_physical_close_events_remain_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = initialize(root)
            run.record_started(operation_id="start:fixture-run", actor="operator")
            run.record_terminal(
                operation_id="terminal:fixture-run",
                actor="operator",
                disposition="completed",
                authorization_ref="user-response:1",
                evidence={},
            )
            operation_id = "close:fixture-run:legacy"
            intent = {
                "actor": "operator",
                "archive_path": ".trellis/tasks/archive/2026-07/07-21-fixture-task",
                "git_evidence": {
                    "authorization_ref": "user-commit:1",
                    "commit": "a" * 40,
                    "tree": "b" * 40,
                },
                "source_path": ".trellis/tasks/07-21-fixture-task",
                "task_projection_digest": sha256(
                    run.task_projection_bytes()
                ).hexdigest(),
                "terminal_disposition": "completed",
            }
            run.prepare_close(operation_id=operation_id, intent=intent)
            run.observe_close_effect(
                operation_id=operation_id,
                effect="pointer_cleared",
                observation={"cleared": 1, "remaining": 0},
            )
            run.observe_close_effect(
                operation_id=operation_id,
                effect="directory_archived",
                observation={
                    "archive_path": intent["archive_path"],
                    "source_absent": True,
                    "task_projection_digest": intent["task_projection_digest"],
                },
            )
            run.observe_close_effect(
                operation_id=operation_id,
                effect="task_projection_rebuilt",
                observation={"digest": "d" * 64},
            )
            run.observe_close_effect(
                operation_id=operation_id,
                effect="board_rebuilt",
                observation={"digest": "e" * 64},
            )
            run.commit_close(operation_id=operation_id)
            expected = sha256(
                json.dumps(
                    {"board": "e" * 64, "task_json": "d" * 64},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            run.mark_projected(operation_id=operation_id, projection_digest=expected)

            reopened = TaskRun.open(root, "fixture-run")
            projected = reopened.task_projection()
            self.assertEqual(reopened.get_operation(operation_id)["phase"], "projected")
            self.assertEqual(
                projected["meta"]["task_run"]["archive_path"],
                intent["archive_path"],
            )
            self.assertEqual(projected["commit"], intent["git_evidence"]["commit"])

    def test_legacy_evidence_reader_is_digest_only_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "legacy-task"
            task_dir.mkdir()
            task_path = task_dir / "task.json"
            events_path = task_dir / "state-events.jsonl"
            task_path.write_text(
                json.dumps(
                    {
                        "id": "legacy-task",
                        "status": "in_progress",
                        "meta": {"workflow_mode": "harness_state_machine"},
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            events_path.write_text('{"event":"init"}\n', encoding="utf-8")
            before = (task_path.read_bytes(), events_path.read_bytes())

            evidence = read_legacy_task_evidence(task_dir)

            self.assertEqual(evidence["kind"], "legacy_task")
            self.assertEqual(evidence["read_only"], True)
            self.assertEqual(evidence["task_id"], "legacy-task")
            self.assertNotIn("task_json", evidence)
            self.assertEqual(before, (task_path.read_bytes(), events_path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
