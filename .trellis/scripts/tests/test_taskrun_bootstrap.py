from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from taskrun import (
    RESULT_FIELDS,
    InvalidTransition,
    OperationConflict,
    TaskRun,
    bootstrap_task_run,
)


def task_json() -> dict[str, object]:
    return {
        "id": "bootstrap-fixture",
        "name": "bootstrap-fixture",
        "status": "planning",
        "title": "Bootstrap Fixture",
        "meta": {"workflow_mode": "harness_state_machine"},
    }


def envelope() -> dict[str, object]:
    return {
        "actions": [
            {
                "action_id": "action-a",
                "checks": ["python3 focused.py"],
                "dependencies": [],
                "problem_id": "problem-a",
                "requirement_ids": ["REQ-A"],
                "result_schema": sorted(RESULT_FIELDS),
                "touches": ["src/**"],
            }
        ],
        "allowed_effects": ["workspace_write"],
        "approval_refs": ["approval:start:1"],
        "authorization_ref": "user-response:start:1",
        "budgets": {
            "attempts": 4,
            "concurrency": 1,
            "cost": 0,
            "providers": 1,
            "reviewers": 1,
            "workers": 1,
        },
        "context_digest": "a" * 64,
        "prohibited_effects": ["git_commit", "push"],
        "providers": ["none"],
        "reviewers": ["reviewer-a"],
        "strategy_revision": "strategy-v1",
        "workers": ["worker-a"],
    }


class TaskRunBootstrapTests(unittest.TestCase):
    def test_exact_replay_creates_one_task_run_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = bootstrap_task_run(
                root,
                "07-21-bootstrap-fixture",
                task_json(),
                actor="operator",
                strategy="single",
                start_envelope=envelope(),
            )
            replay = bootstrap_task_run(
                root,
                "07-21-bootstrap-fixture",
                task_json(),
                actor="operator",
                strategy="single",
                start_envelope=envelope(),
            )

            self.assertEqual(first.task_run_id, replay.task_run_id)
            self.assertEqual(first.snapshot(), replay.snapshot())
            self.assertEqual(first.snapshot()["status"], "running")
            self.assertEqual(first.snapshot()["execution"]["config"]["strategy"], "single")
            self.assertEqual(len(first.events()), 2)
            self.assertEqual(
                len(list((root / ".trellis/.runtime/taskrun/runs").glob("*/authority.sqlite3"))),
                1,
            )

    def test_stale_or_rebound_bootstrap_does_not_create_an_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_task_run(
                root,
                "07-21-bootstrap-fixture",
                task_json(),
                actor="operator",
                strategy="single",
                start_envelope=envelope(),
            )
            changed = envelope()
            changed["context_digest"] = "b" * 64
            with self.assertRaises(OperationConflict):
                bootstrap_task_run(
                    root,
                    "07-21-bootstrap-fixture",
                    task_json(),
                    actor="operator",
                    strategy="single",
                    start_envelope=changed,
                )

            rebound = task_json()
            rebound["id"] = "different-task"
            rebound["name"] = "different-task"
            with self.assertRaises(OperationConflict):
                bootstrap_task_run(
                    root,
                    "07-21-bootstrap-fixture",
                    rebound,
                    actor="operator",
                    strategy="single",
                    start_envelope=envelope(),
                )
            with self.assertRaises(OperationConflict):
                bootstrap_task_run(
                    root,
                    "07-21-renamed-fixture",
                    task_json(),
                    actor="operator",
                    strategy="single",
                    start_envelope=envelope(),
                )
            self.assertEqual(
                len(list((root / ".trellis/.runtime/taskrun/runs").glob("*/authority.sqlite3"))),
                1,
            )

    def test_bootstrapped_completion_requires_candidate_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = bootstrap_task_run(
                Path(tmp),
                "07-21-bootstrap-fixture",
                task_json(),
                actor="operator",
                strategy="single",
                start_envelope=envelope(),
            )
            with self.assertRaises(InvalidTransition):
                run.record_terminal(
                    operation_id="terminal:early",
                    actor="operator",
                    disposition="completed",
                    authorization_ref="user-response:final:1",
                    evidence={},
                )
            self.assertEqual(run.snapshot()["status"], "running")

    def test_legacy_task_run_lifecycle_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = copy.deepcopy(task_json())
            run = TaskRun.initialize(
                Path(tmp),
                "legacy-run",
                actor="operator",
                task_dir_name="07-21-bootstrap-fixture",
                task_json=legacy,
            )
            run.record_started(operation_id="start:legacy-run", actor="operator")
            run.record_terminal(
                operation_id="terminal:legacy-run",
                actor="operator",
                disposition="completed",
                authorization_ref="user-response:legacy",
                evidence={},
            )
            self.assertEqual(run.snapshot()["status"], "completed")
            self.assertIsNone(run.snapshot()["execution"])


if __name__ == "__main__":
    unittest.main()
