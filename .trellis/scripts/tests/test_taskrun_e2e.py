from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from downstream_deployer import transaction
from loop_v1.qualification import (
    generate_artifact_qualification_receipt,
    generate_local_runtime_receipt,
    verify_artifact_qualification_receipt,
    verify_local_runtime_receipt,
)
from taskrun import (
    FAULT_BOUNDARIES,
    InjectedFailure,
    InvalidTransition,
    TaskRunOperator,
    close_task_run,
    ingest_action_result,
    issue_final_request,
    issue_strategy_actions,
    record_action_review_and_reduce,
    record_final_acceptance,
    record_final_candidate_review,
)
from test_downstream_deployer_plan import prepare_repositories, qualified
from test_downstream_deployer_transaction import apply_fixture, checks, create_plan
from test_execution_strategies import (
    action,
    bootstrap,
    outer_final_gate_receipt,
    result,
    review,
)
from test_loop_v1_qualification import prepare_receipt_repo, qualification_result
from test_taskrun_loop_integration import (
    accept as accept_loop,
    action as loop_action,
    final_review as loop_final_review,
)
from test_taskrun_operator import (
    gate_receipt,
    prepare_repo,
    result as operator_result,
    review as operator_review,
)
from test_upstream_release import _fixture as upstream_fixture
from upstream_release.core import plan_candidate, verify_candidate


PARENT = "08-01-parent-settlement-fixture"
SINGLE_CHILD = "08-01-single-settlement-fixture"
SINGLE_SUCCESSOR = "08-01-single-settlement-successor"
LOOP_CHILD = "08-01-loop-settlement-fixture"


def write_taskrun_plan(
    root: Path,
    name: str,
    requirement_ids: list[str],
    touches: list[str],
    *,
    tier: str,
    strategy: str,
    parent: str | None = None,
    children: list[str] | None = None,
) -> None:
    task_dir = root / ".trellis/tasks" / name
    task_dir.mkdir()
    task = {
        "children": children or [],
        "id": name,
        "meta": {
            "taskrun_strategy": strategy,
            "workflow_mode": "taskrun_v2",
        },
        "name": name,
        "parent": parent,
        "scope": "settlement-fixture",
        "status": "planning",
        "tier": tier,
        "title": name,
        "touches": touches,
    }
    (task_dir / "task.json").write_text(
        json.dumps(task, indent=2) + "\n", encoding="utf-8"
    )
    requirements = "\n".join(
        f"- `{requirement}` [owner: codex]: Complete {requirement}."
        for requirement in requirement_ids
    )
    (task_dir / "prd.md").write_text(
        "# Settlement fixture\n\n"
        f"## Requirements\n\n{requirements}\n\n"
        "## Verification Commands\n\n"
        "- `python3 -m unittest fixture`\n",
        encoding="utf-8",
    )


def completed_run(root: Path, strategy: str):
    root.mkdir()
    actions = (
        [action("single", "E2E-SINGLE", "src/single/**")]
        if strategy == "single"
        else [
            action("first", "E2E-LOOP-A", "src/first/**"),
            action("second", "E2E-LOOP-B", "src/second/**"),
        ]
    )
    run = bootstrap(root, strategy, actions, concurrency=len(actions))
    intents = {
        item["action_id"]: item
        for item in issue_strategy_actions(run, actor="operator")
    }
    proposals: dict[str, dict[str, object]] = {}
    for action_id, intent in intents.items():
        proposals[action_id] = result(intent)
        ingest_action_result(run, actor="operator", payload=proposals[action_id])
        record_action_review_and_reduce(
            run,
            actor="operator",
            payload=review(intent, proposals[action_id]["candidate_digest"]),
        )
    if strategy == "loop":
        record_final_candidate_review(
            run,
            actor="operator",
            payload={
                "candidate_digest": "e" * 64,
                "component_candidates": [
                    {
                        "action_id": action_id,
                        "attempt": intents[action_id]["attempt"],
                        "candidate_digest": proposals[action_id]["candidate_digest"],
                    }
                    for action_id in ("first", "second")
                ],
                "findings": [],
                "review_id": "e2e-final-review",
                "reviewer_id": "reviewer-a",
                "task_run_id": run.task_run_id,
                "verdict": "accepted",
            },
        )
    final_request = issue_final_request(run, actor="operator")
    terminal_binding = record_final_acceptance(
        run,
        actor="operator",
        receipt=outer_final_gate_receipt(
            final_request,
            receipt_id=f"e2e-{strategy}-receipt",
            receipt_audit_id=f"e2e-{strategy}-audit",
        ),
    )
    run.record_terminal(
        operation_id=f"terminal:{strategy}",
        actor="operator",
        disposition="completed",
        authorization_ref=terminal_binding["final_response_event_digest"],
        evidence=terminal_binding,
    )
    task_name = "07-21-execution-fixture"
    source = root / ".trellis/tasks" / task_name
    source.mkdir(parents=True)
    (source / "task.json").write_bytes(run.task_projection_bytes())
    (source / "prd.md").write_text("# Disposable E2E\n", encoding="utf-8")
    session = root / ".trellis/.runtime/sessions/e2e.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        json.dumps({"current_task": f".trellis/tasks/{task_name}"}) + "\n",
        encoding="utf-8",
    )
    board = root / "BOARD.md"
    board.write_bytes(b"# Stable E2E Board\n")
    return run, source, session, board


def close(run, task_dir: Path, *, fault_after: str | None = None):
    return close_task_run(
        run,
        operation_id=f"close:{run.task_run_id}:1",
        task_dir=task_dir,
        actor="operator",
        fault_after=fault_after,
    )


class TaskRunEndToEndTests(unittest.TestCase):
    def test_disposable_golden_path_completes_both_strategies_and_exactly_replays_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for strategy in ("single", "loop"):
                with self.subTest(strategy=strategy):
                    run, task_dir, session, board = completed_run(
                        base / strategy,
                        strategy,
                    )
                    pointer = session.read_bytes()
                    board_bytes = board.read_bytes()
                    first = close(run, task_dir)
                    event_count = len(run.events())
                    replay = close(run, task_dir)

                    self.assertEqual(first, replay)
                    self.assertEqual(first["status"], "closed")
                    self.assertTrue(task_dir.is_dir())
                    self.assertEqual(session.read_bytes(), pointer)
                    self.assertEqual(board.read_bytes(), board_bytes)
                    self.assertEqual(len(run.events()), event_count)
                    self.assertEqual(run.snapshot()["status"], "completed")
                    self.assertTrue(run.snapshot()["closed"])
                    projected = json.loads(
                        (task_dir / "task.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(projected["meta"]["task_run"]["state"], "closed")
                    self.assertNotIn("archive_path", projected["meta"]["task_run"])
                    self.assertNotIn("commit", projected)
                    self.assertNotIn(
                        "delivery_settlement",
                        run.snapshot()["execution"]["final_request"],
                    )
                    self.assertNotIn(
                        "delivery_settlement", run.snapshot()["execution"]
                    )

    def test_parent_final_request_consumes_single_and_loop_terminal_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            write_taskrun_plan(
                root,
                SINGLE_CHILD,
                ["SETTLEMENT-REQ-001"],
                ["src/single/**"],
                tier="child",
                strategy="single",
                parent=PARENT,
            )
            write_taskrun_plan(
                root,
                LOOP_CHILD,
                ["SETTLEMENT-REQ-002"],
                ["src/loop/**"],
                tier="child",
                strategy="loop",
                parent=PARENT,
            )
            write_taskrun_plan(
                root,
                SINGLE_SUCCESSOR,
                ["SETTLEMENT-REQ-001"],
                ["src/single/**"],
                tier="child",
                strategy="single",
                parent=PARENT,
            )
            write_taskrun_plan(
                root,
                PARENT,
                ["SETTLEMENT-REQ-001", "SETTLEMENT-REQ-002"],
                ["src/**"],
                tier="parent",
                strategy="single",
                children=[SINGLE_CHILD, LOOP_CHILD],
            )
            parent = TaskRunOperator.admit_single(
                root,
                PARENT,
                actor="operator",
                authorization_ref="parent-start:1",
                worker_id="worker-a",
                reviewer_id="reviewer-a",
            )
            parent_packet = parent.next_action(worker_id="worker-a")
            parent_result = operator_result(parent_packet)
            parent.submit_result(parent_result)
            parent.submit_review(
                operator_review(parent_packet, parent_result["candidate_digest"])
            )

            parent_event_count = len(parent.run.events())
            with self.assertRaisesRegex(
                InvalidTransition, "TaskRun authority is unavailable"
            ):
                parent.final_request()
            self.assertEqual(len(parent.run.events()), parent_event_count)

            predecessor = TaskRunOperator.admit_single(
                root,
                SINGLE_CHILD,
                actor="operator",
                authorization_ref="single-start:1",
                worker_id="worker-a",
                reviewer_id="reviewer-a",
            )
            predecessor_prd = root / ".trellis/tasks" / SINGLE_CHILD / "prd.md"
            predecessor_prd.write_text(
                predecessor_prd.read_text(encoding="utf-8")
                + "\n<!-- material drift -->\n",
                encoding="utf-8",
            )
            single = predecessor.supersede(
                SINGLE_SUCCESSOR,
                authorization_ref="single-successor:1",
            )
            self.assertIsNotNone(single)
            single_packet = single.next_action(worker_id="worker-a")
            single_result = operator_result(single_packet)
            single_result["actual_touches"] = ["src/single/output.py"]
            single.submit_result(single_result)
            single.submit_review(
                operator_review(single_packet, single_result["candidate_digest"])
            )
            single.final_request()
            single.complete(gate_receipt)

            loop_action_value = loop_action(
                "loop-slot", "src/loop/**", "resource:loop-slot"
            )
            loop_action_value["requirement_ids"] = ["SETTLEMENT-REQ-002"]
            loop = TaskRunOperator.admit_loop(
                root,
                LOOP_CHILD,
                actor="operator",
                authorization_ref="loop-start:1",
                actions=[loop_action_value],
                worker_ids=["worker-a"],
                reviewer_id="reviewer-a",
                concurrency=1,
            )
            loop_packet = loop.next_actions()[0]
            accept_loop(loop, loop_packet)
            loop.submit_final_review(loop_final_review(loop))
            loop.final_request()
            loop.complete(gate_receipt)

            replayed_parent = TaskRunOperator.admit_single(
                root,
                PARENT,
                actor="operator",
                authorization_ref="parent-start:1",
                worker_id="worker-a",
                reviewer_id="reviewer-a",
            )
            self.assertEqual(replayed_parent.task_run_id, parent.task_run_id)
            final_request = parent.final_request()
            settlement = final_request["delivery_settlement"]
            settled_slots = settlement["slots"]
            self.assertEqual(
                [
                    [attempt["strategy"] for attempt in slot["attempts"]]
                    for slot in settled_slots
                ],
                [["single", "single"], ["loop"]],
            )
            self.assertEqual(
                [slot["fulfilled_by"] for slot in settled_slots],
                [single.task_run_id, loop.task_run_id],
            )
            self.assertEqual(
                settled_slots[0]["attempts"][0]["successor_task_run_id"],
                single.task_run_id,
            )
            self.assertEqual(
                settled_slots[0]["attempts"][0]["disposition"], "cancelled"
            )
            terminal_digests = {
                operator.task_run_id: next(
                    event["event_digest"]
                    for event in operator.run.events()
                    if event["event_type"] == "terminal_recorded"
                )
                for operator in (single, loop)
            }
            self.assertEqual(
                [
                    slot["attempts"][-1]["terminal_event_digest"]
                    for slot in settled_slots
                ],
                [terminal_digests[single.task_run_id], terminal_digests[loop.task_run_id]],
            )
            event_count = len(parent.run.events())
            self.assertEqual(parent.final_request(), final_request)
            self.assertEqual(len(parent.run.events()), event_count)

    def test_every_taskrun_close_boundary_recovers_without_false_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for index, boundary in enumerate(FAULT_BOUNDARIES):
                with self.subTest(boundary=boundary):
                    run, task_dir, session, board = completed_run(
                        base / f"fault-{index}",
                        "single",
                    )
                    pointer = session.read_bytes()
                    board_bytes = board.read_bytes()
                    with self.assertRaises(InjectedFailure):
                        close(
                            run,
                            task_dir,
                            fault_after=boundary,
                        )
                    self.assertEqual(
                        run.snapshot()["closed"],
                        boundary != "close_prepared",
                    )
                    recovered = close(run, task_dir)
                    self.assertEqual(recovered["status"], "closed")
                    self.assertTrue(run.snapshot()["closed"])
                    self.assertTrue(task_dir.is_dir())
                    self.assertEqual(session.read_bytes(), pointer)
                    self.assertEqual(board.read_bytes(), board_bytes)

    def test_disposable_sync_qualification_and_upstream_candidate_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sync_root = base / "sync"
            sync_root.mkdir()
            source, target, scratch = prepare_repositories(sync_root)
            recovery = sync_root / "recovery"
            recovery.mkdir()
            candidate, plan = create_plan(source, target, scratch, checks())
            applied = apply_fixture(
                source,
                target,
                candidate,
                plan,
                verification_commands=checks(),
                recovery_root=recovery,
            )
            with mock.patch(
                "downstream_deployer.planning.configured_qualification",
                return_value=qualified(),
            ):
                verified = transaction.verify_transaction(
                    source,
                    target,
                    plan,
                    applied["final_receipt"],
                    verification_commands=checks(),
                )
            self.assertEqual(applied["status"], "succeeded")
            self.assertEqual(verified["status"], "verified")

            qualification_root = base / "qualification"
            qualification_root.mkdir()
            runtime = qualification_root / "runtime"
            runtime.mkdir()
            prepare_receipt_repo(runtime)
            artifact = generate_artifact_qualification_receipt(
                runtime,
                qualification_result(runtime),
                qualification_root / "artifact-receipts",
            )
            local = generate_local_runtime_receipt(
                runtime,
                artifact,
                qualification_root / "local-receipts",
            )
            self.assertTrue(verify_artifact_qualification_receipt(runtime, artifact)["valid"])
            self.assertTrue(verify_local_runtime_receipt(runtime, artifact, local)["valid"])

            upstream_root = base / "upstream"
            upstream_root.mkdir()
            upstream_source, pin, upstream_scratch = upstream_fixture(upstream_root)
            upstream_candidate = upstream_scratch / "candidate"
            release_plan = plan_candidate(
                upstream_source,
                pin,
                upstream_scratch,
                candidate_output=upstream_candidate,
            )
            release_verification = verify_candidate(
                upstream_source,
                pin,
                upstream_candidate,
                release_plan,
            )
            self.assertEqual(release_verification["status"], "verified")


if __name__ == "__main__":
    unittest.main()
