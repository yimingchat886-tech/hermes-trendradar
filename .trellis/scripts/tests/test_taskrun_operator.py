from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import taskrun
from common.task_activity import classify_task_activity
from taskrun import operator as operator_module
from taskrun import (
    InjectedFailure,
    MaterialDriftError,
    OperationConflict,
    OperatorError,
    TaskRunError,
    TaskRunOperator,
)
from taskrun.operator import INDEPENDENT_GATES


TASK = "07-31-operator-fixture"
SUCCESSOR = "07-31-operator-successor"
OTHER_PREDECESSOR = "07-31-operator-other-predecessor"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_task(root: Path, name: str, task_id: str) -> None:
    task_dir = root / ".trellis/tasks" / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "id": task_id,
                "name": task_id,
                "status": "in_progress",
                "tier": "child",
                "title": f"Fixture {task_id}",
                "touches": ["src/**"],
                "meta": {
                    "workflow_mode": "harness_state_machine",
                    "state_machine": {
                        "current_state": "child_waiting_completion_signal",
                        "kind": "child",
                        "schema_version": 2,
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (task_dir / "prd.md").write_text(
        """# Fixture

## Requirements

- `FIXTURE-REQ-001` [owner: codex]: Complete the bounded change.

## Verification Commands

- `python3 -m unittest fixture`

## Out

- External effects.
""",
        encoding="utf-8",
    )
    (task_dir / "state-events.jsonl").write_bytes(b'{"event":"hsm-fixture"}\n')
    (task_dir / "implement.jsonl").write_bytes(b"")
    (task_dir / "check.jsonl").write_bytes(b"")


def prepare_repo(root: Path) -> None:
    (root / ".trellis/scripts/taskrun").mkdir(parents=True)
    (root / ".trellis/scripts/common").mkdir()
    (root / ".trellis/spec/project").mkdir(parents=True)
    (root / "src").mkdir()
    (root / ".trellis/scripts/taskrun/runtime.py").write_text(
        'RUNTIME = "fixture"\n', encoding="utf-8"
    )
    (root / ".trellis/scripts/common/io.py").write_text(
        "JSON_BYTES_VERSION = 1\n", encoding="utf-8"
    )
    (root / ".trellis/spec/project/taskrun-runtime.md").write_text(
        "# Fixture TaskRun Contract\n", encoding="utf-8"
    )
    (root / "src/input.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "BOARD.md").write_bytes(b"# Fixture Board\n")
    (root / ".gitignore").write_text(".trellis/.runtime/\n", encoding="utf-8")
    write_task(root, TASK, "operator-fixture")
    write_task(root, SUCCESSOR, "operator-successor")
    write_task(root, OTHER_PREDECESSOR, "operator-other-predecessor")
    git(root, "init", "-q")
    git(root, "config", "user.name", "TaskRun Fixture")
    git(root, "config", "user.email", "taskrun@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture")


def admit(
    root: Path,
    task: str = TASK,
    *,
    action_risk: str = "low",
    attempts: int = 4,
) -> TaskRunOperator:
    return TaskRunOperator.admit_single(
        root,
        task,
        actor="operator",
        authorization_ref="user-execution-signal:1",
        worker_id="worker-a",
        reviewer_id="reviewer-a",
        provider_id="local",
        action_risk=action_risk,
        low_risk_mode="aggregate",
        attempts=attempts,
    )


def result(packet: dict[str, object], *, passed: bool = True) -> dict[str, object]:
    intent = packet["intent"]
    assert isinstance(intent, dict)
    return {
        "action_id": intent["action_id"],
        "actual_effects": [],
        "actual_touches": ["src/output.py"],
        "artifact_digests": {},
        "attempt": intent["attempt"],
        "candidate_digest": sha256(
            f"{intent['task_run_id']}:{intent['attempt']}".encode("utf-8")
        ).hexdigest(),
        "checks": {
            command: "passed" if passed else "failed" for command in intent["checks"]
        },
        "diff_digest": "working-tree-diff",
        "effect_receipts": {},
        "findings": [],
        "freshness": copy.deepcopy(intent["freshness"]),
        "input_digest": intent["input_digest"],
        "provider_id": packet["provider_id"],
        "requirement_ids": copy.deepcopy(intent["requirement_ids"]),
        "risks": [],
        "task_run_id": intent["task_run_id"],
        "transport_id": "local:fixture",
        "tree_digest": "working-tree-candidate",
        "unknowns": [],
        "worker_id": packet["worker_id"],
    }


def review(packet: dict[str, object], candidate: str) -> dict[str, object]:
    intent = packet["intent"]
    assert isinstance(intent, dict)
    return {
        "action_id": intent["action_id"],
        "attempt": intent["attempt"],
        "candidate_digest": candidate,
        "findings": [],
        "input_digest": intent["input_digest"],
        "reviewer_id": "reviewer-a",
        "task_run_id": intent["task_run_id"],
        "verdict": "accepted",
    }


def gate_receipt(request: dict[str, object]) -> dict[str, object]:
    receipt = {
        "decision": "accepted",
        "final_request_digest": request["request_digest"],
        "final_request_event_digest": request["authority_event_digest"],
        "receipt_audit_at": "2026-07-31T00:00:00Z",
        "receipt_audit_id": "host-direct-user-gate:1",
        "receipt_id": "host-receipt:1",
        "task_run_id": request["task_run_id"],
    }
    return rebind(receipt)


def commit_reconciliation_receipt(
    request: dict[str, object],
    *,
    authorization_ref: str = "user-commit-reconciliation:1",
) -> dict[str, object]:
    return rebind(
        {
            "authorization_ref": authorization_ref,
            "decision": "accepted",
            "receipt_audit_at": "2026-08-02T00:00:00Z",
            "receipt_audit_id": "host-direct-user-commit-audit:1",
            "receipt_id": "host-direct-user-commit:1",
            "request_digest": request["request_digest"],
            "request_event_digest": request["authority_event_digest"],
            "task_run_id": request["task_run_id"],
        }
    )


def rebind(receipt: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(receipt)
    value.pop("receipt_digest", None)
    value["receipt_digest"] = sha256(
        json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return value


def ready_operator(root: Path) -> tuple[TaskRunOperator, dict[str, object]]:
    operator = admit(root)
    packet = operator.next_action(worker_id="worker-a")
    assert packet is not None
    proposal = result(packet)
    operator.submit_result(proposal)
    operator.submit_review(review(packet, proposal["candidate_digest"]))
    return operator, operator.final_request()


class TaskRunOperatorTests(unittest.TestCase):
    def test_bootstrap_reopen_and_jit_packet_use_only_taskrun_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            task_dir = root / ".trellis/tasks" / TASK
            hsm = (task_dir / "state-events.jsonl").read_bytes()
            manifests = {
                name: (task_dir / name).read_bytes()
                for name in ("implement.jsonl", "check.jsonl")
            }

            operator = admit(root)
            packet = operator.next_action(worker_id="worker-a")
            self.assertIsNotNone(packet)
            replay = admit(root).next_action(worker_id="worker-a")

            self.assertEqual(packet, replay)
            self.assertEqual(
                packet["intent"]["requirement_ids"], ["FIXTURE-REQ-001"]
            )
            self.assertEqual(packet["intent"]["touches"], ["src/**"])
            self.assertEqual(
                packet["intent"]["freshness"]["identities"], packet["identities"]
            )
            self.assertEqual((task_dir / "state-events.jsonl").read_bytes(), hsm)
            self.assertEqual(
                {
                    name: (task_dir / name).read_bytes()
                    for name in ("implement.jsonl", "check.jsonl")
                },
                manifests,
            )
            self.assertFalse((root / ".trellis/.runtime/loop-v1").exists())

    def test_operator_envelope_cannot_bootstrap_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            envelope = operator.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]
            before = len(operator.run.events())
            task = json.loads(
                (root / ".trellis/tasks" / SUCCESSOR / "task.json").read_text(
                    encoding="utf-8"
                )
            )

            with self.assertRaisesRegex(
                taskrun.ExecutionError, "operator start envelope requires the single"
            ):
                taskrun.bootstrap_task_run(
                    root,
                    SUCCESSOR,
                    task,
                    actor="operator",
                    strategy="loop",
                    start_envelope=envelope,
                )
            self.assertEqual(
                len(
                    list(
                        (root / ".trellis/.runtime/taskrun/runs").glob(
                            "*/authority.sqlite3"
                        )
                    )
                ),
                1,
            )
            self.assertEqual(len(operator.run.events()), before)

    def test_review_policy_is_immutable_and_final_review_stays_independent(
        self,
    ) -> None:
        for risk, action_mode, required_by in (
            ("low", "aggregate", ["final_candidate"]),
            ("high", "independent", ["high_risk", "final_candidate"]),
        ):
            with self.subTest(risk=risk), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                prepare_repo(root)
                operator = admit(root, action_risk=risk)
                packet = operator.next_action(worker_id="worker-a")
                self.assertEqual(packet["review"]["action_mode"], action_mode)
                self.assertEqual(packet["review"]["required_mode"], "independent")
                self.assertEqual(packet["review"]["required_by"], required_by)
                self.assertEqual(
                    tuple(packet["review"]["independent_gates"]), INDEPENDENT_GATES
                )
                with self.assertRaises(OperationConflict):
                    admit(root, action_risk="high" if risk == "low" else "low")

    def test_operator_final_gate_public_entry_negative_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator, request = ready_operator(root)
            before = len(operator.run.events())

            for raw in (
                {"accepted": True},
                {"responder_id": "invented", "accepted": True},
                True,
                "accepted",
            ):
                with self.subTest(raw=repr(raw)), self.assertRaises(OperatorError):
                    operator.complete(raw)
                self.assertEqual(len(operator.run.events()), before)

            invalid_receipts = []
            invented = gate_receipt(request)
            invented["responder_id"] = "invented"
            invalid_receipts.append(rebind(invented))
            stale = gate_receipt(request)
            stale["final_request_digest"] = "f" * 64
            invalid_receipts.append(rebind(stale))
            foreign = gate_receipt(request)
            foreign["task_run_id"] = f"task-{'e' * 64}"
            invalid_receipts.append(rebind(foreign))
            for receipt in invalid_receipts:
                with self.assertRaises(TaskRunError):
                    operator.complete(lambda _request, value=receipt: value)
                self.assertEqual(len(operator.run.events()), before)

            public_alias = taskrun.TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            with self.assertRaises(OperatorError):
                public_alias.complete({"accepted": True})
            self.assertEqual(len(operator.run.events()), before)

            completed = operator.complete(gate_receipt)
            event_count = len(operator.run.events())
            replay = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            ).complete(lambda _request: self.fail("terminal replay called producer"))
            self.assertEqual(completed, replay)
            self.assertEqual(len(operator.run.events()), event_count)

    def test_single_reconciles_exact_commit_after_candidate_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            task_path = root / ".trellis/tasks" / TASK / "task.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            task["meta"] = {"workflow_mode": "taskrun_v1"}
            task_path.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
            git(root, "add", str(task_path.relative_to(root)))
            git(root, "commit", "-q", "-m", "taskrun fixture")
            operator = admit(root)
            packet = operator.next_action(worker_id="worker-a")
            proposal = result(packet)
            operator.submit_result(proposal)
            operator.submit_review(review(packet, proposal["candidate_digest"]))
            (root / "src/output.py").write_text("DONE = True\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "candidate")
            head = git(root, "rev-parse", "HEAD")

            with self.assertRaises(MaterialDriftError):
                operator.final_request()
            producer = mock.Mock(side_effect=commit_reconciliation_receipt)
            reconciliation = operator.reconcile_local_commit(producer)
            request = reconciliation["request"]
            self.assertEqual(request["head_commit"], head)
            self.assertIn("src/output.py", request["changed_paths"])
            self.assertEqual(producer.call_count, 1)

            replay = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            ).reconcile_local_commit(
                lambda _request: self.fail("reconciliation replay called producer")
            )
            self.assertEqual(reconciliation, replay)
            final_request = operator.final_request()
            self.assertEqual(
                final_request["commit_reconciliation"]["head_commit"], head
            )
            operator.complete(gate_receipt)
            operator.close()
            projection = json.loads(
                (root / ".trellis/tasks" / TASK / "task.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(projection["commit"], head)
            event_types = [event["event_type"] for event in operator.run.events()]
            self.assertEqual(event_types.count("commit_reconciliation_requested"), 1)
            self.assertEqual(event_types.count("commit_reconciliation_recorded"), 1)

    def test_single_reconciliation_before_action_refreshes_packet_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            (root / "src/output.py").write_text("DONE = True\n", encoding="utf-8")
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "committed"\n', encoding="utf-8"
            )
            (root / ".trellis/spec/project/taskrun-runtime.md").write_text(
                "# Committed TaskRun Contract\n", encoding="utf-8"
            )
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "implementation")

            reconciliation = operator.reconcile_local_commit(
                commit_reconciliation_receipt
            )
            observed = reconciliation["request"]["observed_identities"]
            packet = operator.next_action(worker_id="worker-a")
            self.assertEqual(
                packet["intent"]["freshness"]["context_digest"],
                observed["context"],
            )
            self.assertEqual(
                packet["intent"]["freshness"]["identities"],
                {key: value for key, value in observed.items() if key != "context"},
            )
            proposal = result(packet)
            operator.submit_result(proposal)
            operator.submit_review(review(packet, proposal["candidate_digest"]))
            operator.final_request()
            operator.complete(gate_receipt)
            operator.close()

    def test_commit_reconciliation_rejects_bypass_dirty_tree_and_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            before = len(operator.run.events())
            with self.assertRaises(OperatorError):
                operator.reconcile_local_commit({"decision": "accepted"})
            self.assertEqual(len(operator.run.events()), before)

            (root / "src/output.py").write_text("DONE = True\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "implementation")
            (root / "src/input.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(OperatorError, "clean repository worktree"):
                operator.reconcile_local_commit(commit_reconciliation_receipt)
            self.assertEqual(len(operator.run.events()), before)
            git(root, "restore", "src/input.py")

            with self.assertRaises(TaskRunError):
                operator.reconcile_local_commit(
                    lambda request: commit_reconciliation_receipt(
                        request,
                        authorization_ref="user-execution-signal:1",
                    )
                )
            self.assertEqual(
                [event["event_type"] for event in operator.run.events()].count(
                    "commit_reconciliation_requested"
                ),
                1,
            )
            operator.reconcile_local_commit(commit_reconciliation_receipt)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            loop = TaskRunOperator.admit_loop(
                root,
                TASK,
                actor="operator",
                authorization_ref="user-loop:1",
                actions=[
                    {
                        "action_id": "loop",
                        "checks": ["python3 -m unittest loop"],
                        "dependencies": [],
                        "problem_id": "loop",
                        "requirement_ids": ["FIXTURE-REQ-001"],
                        "resources": ["resource:loop"],
                        "result_schema": sorted(taskrun.RESULT_FIELDS),
                        "touches": ["src/**"],
                    }
                ],
                worker_ids=["worker-a"],
                reviewer_id="reviewer-a",
                concurrency=1,
            )
            (root / "src/output.py").write_text("DONE = True\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "loop implementation")
            before = len(loop.run.events())
            with self.assertRaisesRegex(OperatorError, "running single TaskRun"):
                loop.reconcile_local_commit(commit_reconciliation_receipt)
            self.assertEqual(len(loop.run.events()), before)

    def test_final_gate_callback_drift_cannot_record_a_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator, _ = ready_operator(root)
            before = len(operator.run.events())

            def drift_then_accept(request: dict[str, object]) -> dict[str, object]:
                (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                    'RUNTIME = "drifted"\n', encoding="utf-8"
                )
                return gate_receipt(request)

            with self.assertRaises(MaterialDriftError):
                operator.complete(drift_then_accept)
            self.assertEqual(len(operator.run.events()), before)
            state = operator.run.snapshot()
            self.assertIsNone(state["execution"]["final_response"])
            self.assertIsNone(state["terminal"])

    def test_base_runtime_dependency_and_contract_drift_block_dispatch_without_writes(
        self,
    ) -> None:
        for source, identity in (
            ("base", "base"),
            ("runtime", "runtime"),
            ("runtime_dependency", "runtime"),
            ("contract", "contract"),
        ):
            with (
                self.subTest(source=source),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                prepare_repo(root)
                operator = admit(root)
                before = len(operator.run.events())
                if source == "base":
                    (root / "base.txt").write_text("changed\n", encoding="utf-8")
                    git(root, "add", "base.txt")
                    git(root, "commit", "-q", "-m", "base drift")
                elif source == "runtime":
                    (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                        'RUNTIME = "drifted"\n', encoding="utf-8"
                    )
                elif source == "runtime_dependency":
                    (root / ".trellis/scripts/common/io.py").write_text(
                        "JSON_BYTES_VERSION = 2\n", encoding="utf-8"
                    )
                else:
                    (root / ".trellis/spec/project/taskrun-runtime.md").write_text(
                        "# Drifted Contract\n", encoding="utf-8"
                    )

                with self.assertRaises(MaterialDriftError) as raised:
                    operator.next_action(worker_id="worker-a")
                self.assertIn(identity, raised.exception.changed)
                self.assertEqual(len(operator.run.events()), before)

    def test_concurrent_completion_calls_the_host_gate_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator, _ = ready_operator(root)
            peer = TaskRunOperator.reopen(root, operator.task_run_id, actor="operator")

            def produce(request: dict[str, object]) -> dict[str, object]:
                time.sleep(0.1)
                return gate_receipt(request)

            producer = mock.Mock(side_effect=produce)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [
                    future.result()
                    for future in (
                        pool.submit(operator.complete, producer),
                        pool.submit(peer.complete, producer),
                    )
                ]
            self.assertEqual(results[0], results[1])
            self.assertEqual(producer.call_count, 1)
            event_types = [event["event_type"] for event in operator.run.events()]
            self.assertEqual(event_types.count("final_response_recorded"), 1)
            self.assertEqual(event_types.count("terminal_recorded"), 1)

    def test_concurrent_close_recovers_one_terminal_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator, _ = ready_operator(root)
            with mock.patch.object(
                operator,
                "_ensure_terminal_projection",
                side_effect=RuntimeError("injected projection interruption"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "injected projection interruption"
                ):
                    operator.complete(gate_receipt)
            self.assertIsNotNone(operator.run.snapshot()["terminal"])

            first = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            second = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            original_publish = operator_module._publish_task_projection

            def slow_publish(*args: object, **kwargs: object) -> object:
                time.sleep(0.1)
                return original_publish(*args, **kwargs)

            with mock.patch.object(
                operator_module,
                "_publish_task_projection",
                side_effect=slow_publish,
            ) as publish:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [
                        future.result()
                        for future in (
                            pool.submit(first.close),
                            pool.submit(second.close),
                        )
                    ]

            self.assertEqual(results[0], results[1])
            self.assertEqual(publish.call_count, 1)
            event_types = [event["event_type"] for event in operator.run.events()]
            self.assertEqual(event_types.count("closed"), 1)
            self.assertEqual(event_types.count("projection_checkpointed"), 1)

    def test_concurrent_dispatch_replays_one_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            peer = TaskRunOperator.reopen(root, operator.task_run_id, actor="operator")
            original_issue = operator_module.issue_strategy_actions

            def slow_issue(*args: object, **kwargs: object) -> object:
                time.sleep(0.1)
                return original_issue(*args, **kwargs)

            with mock.patch.object(
                operator_module,
                "issue_strategy_actions",
                side_effect=slow_issue,
            ) as issue:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    packets = [
                        future.result()
                        for future in (
                            pool.submit(operator.next_action, worker_id="worker-a"),
                            pool.submit(peer.next_action, worker_id="worker-a"),
                        )
                    ]

            self.assertIsNotNone(packets[0])
            self.assertEqual(packets[0], packets[1])
            self.assertEqual(issue.call_count, 1)
            event_types = [event["event_type"] for event in operator.run.events()]
            self.assertEqual(event_types.count("action_planned"), 1)

    def test_material_drift_supersedes_once_without_resetting_repair_budget(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            first = operator.next_action(worker_id="worker-a")
            self.assertEqual(
                operator.submit_result(result(first, passed=False))["kind"],
                "retry_exact_slice",
            )
            second = operator.next_action(worker_id="worker-a")
            self.assertEqual(second["intent"]["attempt"], 2)
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )

            successor = operator.supersede(
                SUCCESSOR, authorization_ref="user-supersession:1"
            )
            predecessor_events = len(operator.run.events())
            terminal = operator.run.snapshot()["terminal"]
            evidence = terminal["evidence"]
            self.assertEqual(terminal["disposition"], "cancelled")
            self.assertEqual(evidence["superseded_by"], successor.task_run_id)
            self.assertEqual(
                evidence["repair_budget"],
                {"consumed": 2, "remaining": 2, "total": 4},
            )
            self.assertEqual(json.dumps(evidence).count('"superseded_by"'), 1)
            packet = successor.next_action(worker_id="worker-a")
            self.assertEqual(packet["intent"]["attempt"], 1)
            self.assertEqual(packet["repair_budget"]["attempt_number"], 3)
            self.assertEqual(packet["repair_budget"]["total"], 4)

            replay = operator.supersede(
                SUCCESSOR, authorization_ref="user-supersession:1"
            )
            self.assertEqual(replay.task_run_id, successor.task_run_id)
            self.assertEqual(len(operator.run.events()), predecessor_events)

    def test_material_drift_rejects_a_widened_successor_before_terminal_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root)
            before = len(operator.run.events())
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )
            successor_prd = root / ".trellis/tasks" / SUCCESSOR / "prd.md"
            successor_prd.write_text(
                successor_prd.read_text(encoding="utf-8").replace(
                    "- `FIXTURE-REQ-001` [owner: codex]: Complete the bounded change.",
                    "- `FIXTURE-REQ-001` [owner: codex]: Complete the bounded change.\n"
                    "- `FIXTURE-REQ-002` [owner: codex]: Widen the successor.",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                OperatorError, "changes the frozen execution contract"
            ):
                operator.supersede(
                    SUCCESSOR, authorization_ref="user-supersession:widened"
                )

            self.assertIsNone(operator.run.snapshot()["terminal"])
            self.assertEqual(len(operator.run.events()), before)

    def test_exhausted_drift_cancels_without_admitting_a_successor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit(root, attempts=1)
            packet = operator.next_action(worker_id="worker-a")
            self.assertEqual(
                operator.submit_result(result(packet, passed=False))["kind"], "pause"
            )
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )

            self.assertIsNone(
                operator.supersede(
                    SUCCESSOR, authorization_ref="user-supersession:exhausted"
                )
            )
            event_count = len(operator.run.events())
            terminal = operator.run.snapshot()["terminal"]
            evidence = terminal["evidence"]
            self.assertEqual(terminal["disposition"], "cancelled")
            self.assertEqual(
                evidence["repair_budget"],
                {"consumed": 1, "remaining": 0, "total": 1},
            )
            self.assertEqual(json.dumps(evidence).count('"superseded_by"'), 1)
            successor_path = (
                root
                / ".trellis/.runtime/taskrun/runs"
                / evidence["superseded_by"]
                / "authority.sqlite3"
            )
            self.assertFalse(successor_path.exists())
            with self.assertRaisesRegex(
                OperationConflict, "successor repair budget is exhausted"
            ):
                admit(root, SUCCESSOR)
            self.assertFalse(successor_path.exists())
            successor_alias = "07-31-exhausted-successor-alias"
            write_task(root, successor_alias, "operator-successor")
            alias_task_path = (
                root / ".trellis/tasks" / successor_alias / "task.json"
            )
            alias_task = json.loads(alias_task_path.read_bytes())
            alias_task["id"] = "renamed-exhausted-successor-id"
            alias_task_path.write_text(
                json.dumps(alias_task, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                OperationConflict, "supersession reservation is invalid"
            ):
                admit(root, successor_alias)
            self.assertIsNone(
                operator.supersede(
                    SUCCESSOR, authorization_ref="user-supersession:exhausted"
                )
            )
            self.assertEqual(len(operator.run.events()), event_count)

    def test_concurrent_successor_admission_cannot_half_cancel_predecessor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            predecessor = admit(root)
            before = len(predecessor.run.events())
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )
            admission_paused = threading.Event()
            release_admission = threading.Event()
            original_projection = TaskRunOperator._ensure_running_projection

            def pause_successor_projection(
                instance: TaskRunOperator, current: bytes | None = None
            ) -> None:
                if instance.run.snapshot()["task_dir_name"] == SUCCESSOR:
                    admission_paused.set()
                    if not release_admission.wait(2):
                        raise RuntimeError("successor admission was not released")
                original_projection(instance, current)

            with mock.patch.object(
                TaskRunOperator,
                "_ensure_running_projection",
                new=pause_successor_projection,
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    admission = pool.submit(
                        TaskRunOperator.admit_single,
                        root,
                        SUCCESSOR,
                        actor="other-operator",
                        authorization_ref="other-admission:1",
                        worker_id="worker-a",
                        reviewer_id="reviewer-a",
                        action_risk="high",
                    )
                    self.assertTrue(admission_paused.wait(2))
                    supersession = pool.submit(
                        predecessor.supersede,
                        SUCCESSOR,
                        authorization_ref="user-supersession:race",
                    )
                    try:
                        time.sleep(0.1)
                        self.assertFalse(supersession.done())
                    finally:
                        release_admission.set()
                    admission.result()
                    with self.assertRaises(OperationConflict):
                        supersession.result()

            self.assertIsNone(predecessor.run.snapshot()["terminal"])
            self.assertEqual(len(predecessor.run.events()), before)

    def test_crashed_supersession_reservation_rejects_competing_admission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            predecessor = admit(root)
            other_predecessor = admit(root, OTHER_PREDECESSOR)
            packet = predecessor.next_action(worker_id="worker-a")
            predecessor.submit_result(result(packet, passed=False))
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )

            with mock.patch.object(
                predecessor,
                "_bootstrap_successor",
                side_effect=RuntimeError("injected successor bootstrap crash"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "injected successor bootstrap crash"
                ):
                    predecessor.supersede(
                        SUCCESSOR, authorization_ref="user-supersession:crash"
                    )

            terminal = predecessor.run.snapshot()["terminal"]
            successor_path = (
                root
                / ".trellis/.runtime/taskrun/runs"
                / terminal["evidence"]["superseded_by"]
                / "authority.sqlite3"
            )
            self.assertFalse(successor_path.exists())
            successor_task_path = (
                root / ".trellis/tasks" / SUCCESSOR / "task.json"
            )
            successor_task_bytes = successor_task_path.read_bytes()
            renamed_task = json.loads(successor_task_bytes)
            renamed_task["id"] = "renamed-successor"
            renamed_task["name"] = "renamed-successor"
            successor_task_path.write_text(
                json.dumps(renamed_task, indent=2) + "\n", encoding="utf-8"
            )
            database_count = len(
                list(
                    (root / ".trellis/.runtime/taskrun/runs").glob(
                        "*/authority.sqlite3"
                    )
                )
            )
            with self.assertRaisesRegex(
                OperationConflict, "supersession reservation is invalid"
            ):
                admit(root, SUCCESSOR)
            self.assertEqual(
                len(
                    list(
                        (root / ".trellis/.runtime/taskrun/runs").glob(
                            "*/authority.sqlite3"
                        )
                    )
                ),
                database_count,
            )
            successor_task_path.write_bytes(successor_task_bytes)

            successor_alias = "07-31-operator-successor-alias"
            write_task(root, successor_alias, "operator-successor")
            with self.assertRaisesRegex(
                OperationConflict, "supersession reservation is invalid"
            ):
                admit(root, successor_alias)
            self.assertEqual(
                len(
                    list(
                        (root / ".trellis/.runtime/taskrun/runs").glob(
                            "*/authority.sqlite3"
                        )
                    )
                ),
                database_count,
            )
            alias_task_path = (
                root / ".trellis/tasks" / successor_alias / "task.json"
            )
            alias_task = json.loads(alias_task_path.read_bytes())
            alias_task["id"] = "renamed-successor-id"
            alias_task_path.write_text(
                json.dumps(alias_task, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                OperationConflict, "supersession reservation is invalid"
            ):
                admit(root, successor_alias)
            self.assertEqual(
                len(
                    list(
                        (root / ".trellis/.runtime/taskrun/runs").glob(
                            "*/authority.sqlite3"
                        )
                    )
                ),
                database_count,
            )
            with self.assertRaisesRegex(
                OperationConflict, "bootstrap does not match supersession reservation"
            ):
                admit(root, SUCCESSOR, action_risk="high")
            self.assertFalse(successor_path.exists())
            with self.assertRaisesRegex(
                OperationConflict, "successor already has a supersession reservation"
            ):
                other_predecessor.supersede(
                    SUCCESSOR, authorization_ref="other-supersession:1"
                )
            self.assertIsNone(other_predecessor.run.snapshot()["terminal"])

            successor = predecessor.supersede(
                SUCCESSOR, authorization_ref="user-supersession:crash"
            )
            self.assertIsNotNone(successor)
            recovered = successor.next_action(worker_id="worker-a")
            self.assertEqual(recovered["repair_budget"]["attempt_number"], 2)
            self.assertEqual(recovered["repair_budget"]["total"], 4)

    def test_disposable_single_recovers_response_and_status_only_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            head = git(root, "rev-parse", "HEAD")
            task_dir = root / ".trellis/tasks" / TASK
            stable = {
                "board": (root / "BOARD.md").read_bytes(),
                "events": (task_dir / "state-events.jsonl").read_bytes(),
                "implement": (task_dir / "implement.jsonl").read_bytes(),
                "check": (task_dir / "check.jsonl").read_bytes(),
            }
            operator, _ = ready_operator(root)
            producer = mock.Mock(side_effect=gate_receipt)
            with mock.patch.object(
                operator.run,
                "record_terminal",
                side_effect=RuntimeError("injected interruption"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected interruption"):
                    operator.complete(producer)
            self.assertEqual(producer.call_count, 1)

            reopened = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )

            def interrupt_projection(
                path: Path,
                *,
                preimage_digest: str,
                final_projection: bytes,
                operation_id: str,
            ) -> None:
                del preimage_digest, final_projection
                digest = sha256(operation_id.encode("utf-8")).hexdigest()
                path.rename(path.with_name(f".{path.name}.{digest}.close-claim"))
                raise RuntimeError("projection interruption")

            with mock.patch(
                "taskrun.operator._publish_task_projection",
                side_effect=interrupt_projection,
            ):
                with self.assertRaisesRegex(RuntimeError, "projection interruption"):
                    reopened.complete()
            reopened = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            reopened.complete(lambda _request: self.fail("recovery called producer"))
            self.assertEqual(producer.call_count, 1)
            with self.assertRaises(InjectedFailure):
                reopened.close(fault_after="authority_committed")
            recovered = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            closed = recovered.close()
            event_count = len(recovered.run.events())
            self.assertEqual(closed, recovered.close())

            events = recovered.run.events()
            projected = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(len(events), event_count)
            self.assertEqual(
                [event["event_type"] for event in events].count("closed"), 1
            )
            self.assertEqual(
                [event["event_type"] for event in events].count(
                    "projection_checkpointed"
                ),
                1,
            )
            self.assertEqual(projected["status"], "in_progress")
            self.assertEqual(projected["meta"]["task_run"]["state"], "closed")
            self.assertNotIn("completedAt", projected)
            self.assertNotIn("commit", projected)
            self.assertNotIn("archive_path", projected["meta"]["task_run"])
            self.assertTrue(task_dir.is_dir())
            self.assertEqual(git(root, "rev-parse", "HEAD"), head)
            self.assertEqual((root / "BOARD.md").read_bytes(), stable["board"])
            self.assertEqual(
                (task_dir / "state-events.jsonl").read_bytes(), stable["events"]
            )
            self.assertEqual(
                (task_dir / "implement.jsonl").read_bytes(), stable["implement"]
            )
            self.assertEqual((task_dir / "check.jsonl").read_bytes(), stable["check"])
            activity = classify_task_activity(task_dir, root)
            self.assertTrue(activity.active)
            self.assertEqual(activity.state, "child_waiting_completion_signal")
            self.assertFalse((root / ".trellis/.runtime/loop-v1").exists())
            self.assertEqual(
                recovered.run.snapshot()["execution"]["final_request"][
                    "actual_effects"
                ],
                [],
            )


if __name__ == "__main__":
    unittest.main()
