from __future__ import annotations

import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from loop_v1 import (
    ControlError,
    DirtOverlapError,
    FreshnessError,
    InterventionRequired,
    OperationConflict,
    ProblemBudgetError,
    WriterFenceError,
    acknowledge_integration,
    advance_integration_ref,
    build_integration_candidate,
    cancel_parent,
    cancel_safe_point_status,
    commit_reviewed_candidate,
    issue_child_packet,
    open_recovery_generation,
    pause_parent,
    record_precommit_review,
    record_problem_attempt,
    reconcile_for_cancel,
    resume_parent,
)
import loop_v1.integration as integration_module
from loop_v1.integration import (
    prepare_replacement_revision,
    resume_safe_point_status,
)
from test_loop_v1_integration import (
    INTEGRATION_REF,
    committed_runtime,
    prepare,
)
from test_loop_v1_worker_commit import (
    accepted_result,
    child_state,
    git,
    setup_runtime,
    validate,
)


def parent_status(runtime: dict[str, object]) -> str:
    connection = sqlite3.connect(runtime["ledger"].reference()["sqlite_uri"], uri=True)
    try:
        return connection.execute("SELECT status FROM parent_runs").fetchone()[0]
    finally:
        connection.close()


def resume(runtime: dict[str, object], *, operation_id: str = "resume-1"):
    return resume_parent(
        runtime["ledger"],
        runtime["lease"],
        operation_id=operation_id,
        new_writer_id="recovery-writer",
        authority_identity="user:resume-1",
        direct_user_action=True,
        tool_receipt="receipt-worker-commit-fixture",
        available_resources=["repo"],
    )


def integrate(runtime: dict[str, object]) -> dict[str, object]:
    prepare(runtime)
    candidate = build_integration_candidate(
        runtime["ledger"], runtime["lease"], integration_id="integration-1"
    )
    advance_integration_ref(
        runtime["ledger"], runtime["lease"], integration_id="integration-1"
    )
    acknowledge_integration(
        runtime["ledger"], runtime["lease"], integration_id="integration-1"
    )
    return candidate


class LoopV1RecoveryTests(unittest.TestCase):
    def test_prepared_candidate_recovers_as_no_effect_under_new_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime)
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="inject crash after prepare",
                requested_by="test",
                requires_human_resume=True,
            )

            outcome, new_lease = resume(runtime)

            self.assertEqual(outcome["status"], "resumed")
            self.assertEqual(
                outcome["reconciliation"]["actions"][0]["result"], "no_effect"
            )
            self.assertEqual(new_lease.epoch, runtime["lease"].epoch + 1)
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                runtime["base_head"],
            )
            self.assertEqual(parent_status(runtime), "authorized")
            self.assertEqual(runtime["ledger"].projection_status()["status"], "current")
            with self.assertRaises(WriterFenceError):
                pause_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="stale-writer-pause",
                    reason="old writer must be fenced",
                    requested_by="test",
                    requires_human_resume=False,
                )

    def test_green_intent_and_advanced_ref_each_reconcile_without_duplicate_merge(
        self,
    ) -> None:
        for window in ("before_ref", "after_ref"):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as tmp:
                runtime = committed_runtime(Path(tmp))
                prepare(runtime)
                candidate = build_integration_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    integration_id="integration-1",
                )
                if window == "after_ref":
                    advance_integration_ref(
                        runtime["ledger"],
                        runtime["lease"],
                        integration_id="integration-1",
                    )
                pause_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="pause-1",
                    reason=f"inject crash {window}",
                    requested_by="test",
                    requires_human_resume=False,
                )

                outcome, _ = resume(runtime)

                self.assertEqual(outcome["status"], "resumed")
                self.assertEqual(child_state(runtime), "integrated")
                self.assertEqual(
                    git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                    candidate["candidate_head"],
                )
                self.assertEqual(
                    git(
                        runtime["repo"],
                        "rev-list",
                        "--count",
                        candidate["candidate_head"],
                    ),
                    "3",
                )

    def test_child_commit_effect_is_discovered_and_acknowledged_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "commit recovery\n", encoding="utf-8"
            )
            validation = validate(runtime, accepted_result(runtime))
            review = record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )
            with (
                patch(
                    "loop_v1.worker_commit._record_git_authority",
                    side_effect=RuntimeError("crash after commit effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after commit effect"),
            ):
                commit_reviewed_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="commit-1",
                    review_id=review["review_id"],
                    message="child candidate",
                    author_name="Loop Parent",
                    author_email="parent@example.test",
                )
            effect_head = git(runtime["worktree"], "rev-parse", "HEAD")
            self.assertEqual(child_state(runtime), "reviewed")
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="commit effect lacks authority acknowledgement",
                requested_by="test",
                requires_human_resume=False,
            )

            outcome, _ = resume(runtime)

            self.assertEqual(outcome["status"], "resumed")
            self.assertEqual(child_state(runtime), "committed")
            self.assertEqual(git(runtime["worktree"], "rev-parse", "HEAD"), effect_head)

    def test_review_without_commit_is_invalidated_instead_of_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "reviewed only\n", encoding="utf-8"
            )
            validation = validate(runtime, accepted_result(runtime))
            record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="crash after review",
                requested_by="test",
                requires_human_resume=False,
            )

            outcome, _ = resume(runtime)

            self.assertEqual(outcome["stale_children"], ["child-a"])
            self.assertEqual(child_state(runtime), "stale")
            self.assertEqual(
                git(runtime["worktree"], "rev-parse", "HEAD"), runtime["base_head"]
            )

    def test_acknowledged_authority_rebuilds_projection_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime)
            candidate = build_integration_candidate(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            advance_integration_ref(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            with (
                patch.object(
                    runtime["ledger"],
                    "rebuild_projection",
                    side_effect=RuntimeError("crash before projection"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash before projection"),
            ):
                acknowledge_integration(
                    runtime["ledger"],
                    runtime["lease"],
                    integration_id="integration-1",
                )
            self.assertEqual(child_state(runtime), "integrated")
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="rebuild missing projection",
                requested_by="test",
                requires_human_resume=False,
            )

            outcome, _ = resume(runtime)

            self.assertEqual(outcome["status"], "resumed")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                candidate["candidate_head"],
            )
            self.assertEqual(runtime["ledger"].projection_status()["status"], "current")

    def test_problem_budget_is_stable_and_exhaustion_waits_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            common = {
                "problem_id": "problem-1",
                "operation_phase": "integration_candidate",
                "root_condition": "integration check remains red",
                "requirement_ids": ["REQ-1"],
                "diagnosis": "the required marker is absent",
                "action": "add and verify the marker",
                "commands": [
                    {
                        "command": "test -f marker",
                        "exit_code": 1,
                        "output_digest": "check-failed",
                        "status": "failed",
                    }
                ],
                "artifact_ids": ["tree-1"],
                "result": "failed",
            }
            initial = record_problem_attempt(
                runtime["ledger"],
                runtime["lease"],
                round_number=0,
                **common,
            )
            reset = {**common, "problem_id": "replacement-child-problem"}
            replay = record_problem_attempt(
                runtime["ledger"],
                runtime["lease"],
                round_number=0,
                **reset,
            )
            self.assertEqual(replay, initial)
            self.assertTrue(initial["problem_id"].startswith("problem-"))
            self.assertNotEqual(initial["problem_id"], common["problem_id"])
            for round_number in range(1, 4):
                record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    round_number=round_number,
                    **reset,
                )

            self.assertEqual(parent_status(runtime), "recovery_waiting")
            with self.assertRaises(ProblemBudgetError):
                record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    round_number=4,
                    **common,
                )

    def test_problem_budget_preserves_a_legacy_durable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            common = {
                "problem_id": "caller-selected-id",
                "operation_phase": "integration_candidate",
                "root_condition": "legacy integration failure",
                "requirement_ids": ["REQ-1"],
                "diagnosis": "the legacy attempt remains unresolved",
                "action": "continue the same repair budget",
                "commands": [],
                "artifact_ids": ["legacy-tree"],
                "result": "failed",
            }
            with patch(
                "loop_v1.integration._stable_problem_id",
                return_value="legacy-problem-id",
            ):
                initial = record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    round_number=0,
                    **common,
                )

            repaired = record_problem_attempt(
                runtime["ledger"],
                runtime["lease"],
                round_number=1,
                **{**common, "problem_id": "replacement-caller-id"},
            )

            self.assertEqual(initial["problem_id"], "legacy-problem-id")
            self.assertEqual(repaired["problem_id"], "legacy-problem-id")

    def test_recovery_generation_open_is_atomic_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            common = {
                "problem_id": "problem-generation",
                "operation_phase": "integration_candidate",
                "root_condition": "stable recovery failure",
                "requirement_ids": ["REQ-1"],
                "diagnosis": "generation one exhausted",
                "action": "try a different bounded repair",
                "commands": [],
                "artifact_ids": ["tree-1"],
                "result": "failed",
            }
            problem = None
            for round_number in range(4):
                problem = record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    round_number=round_number,
                    **common,
                )
            self.assertIsNotNone(problem)
            integration_head = git(
                runtime["repo"],
                "rev-parse",
                INTEGRATION_REF,
            )
            integration_tree = git(
                runtime["repo"],
                "rev-parse",
                f"{integration_head}^{{tree}}",
            )
            source_context_digest = max(
                runtime["ledger"].authority_snapshot()["tables"]["context_revisions"],
                key=lambda row: int(row["sequence"]),
            )["digest"]
            with self.assertRaisesRegex(
                ProblemBudgetError,
                "affected children differ",
            ):
                open_recovery_generation(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="open-wrong-scope",
                    problem_id=str(problem["problem_id"]),
                    diagnosis="new in-scope diagnosis",
                    action="new bounded action",
                    integration_ref=INTEGRATION_REF,
                    integration_head=integration_head,
                    integration_tree_id=integration_tree,
                    source_context_digest=source_context_digest,
                    affected_child_ids=["other-child"],
                )
            self.assertEqual(parent_status(runtime), "recovery_waiting")
            self.assertIsNone(runtime["ledger"].get_operation("open-wrong-scope"))
            real_insert = integration_module._insert_committed_operation

            def crash_after_insert(*args, **kwargs):
                real_insert(*args, **kwargs)
                raise RuntimeError("crash before generation commit")

            with (
                patch.object(
                    integration_module,
                    "_insert_committed_operation",
                    side_effect=crash_after_insert,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "crash before generation commit",
                ),
            ):
                open_recovery_generation(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="open-generation-2",
                    problem_id=str(problem["problem_id"]),
                    diagnosis="new in-scope diagnosis",
                    action="new bounded action",
                    integration_ref=INTEGRATION_REF,
                    integration_head=integration_head,
                    integration_tree_id=integration_tree,
                    source_context_digest=source_context_digest,
                    affected_child_ids=["child-a"],
                )
            self.assertEqual(parent_status(runtime), "recovery_waiting")
            self.assertIsNone(
                runtime["ledger"].get_operation("open-generation-2")
            )

            opened = open_recovery_generation(
                runtime["ledger"],
                runtime["lease"],
                operation_id="open-generation-2",
                problem_id=str(problem["problem_id"]),
                diagnosis="new in-scope diagnosis",
                action="new bounded action",
                integration_ref=INTEGRATION_REF,
                integration_head=integration_head,
                integration_tree_id=integration_tree,
                source_context_digest=source_context_digest,
                affected_child_ids=["child-a"],
            )
            replay = open_recovery_generation(
                runtime["ledger"],
                runtime["lease"],
                operation_id="open-generation-2",
                problem_id=str(problem["problem_id"]),
                diagnosis="new in-scope diagnosis",
                action="new bounded action",
                integration_ref=INTEGRATION_REF,
                integration_head=integration_head,
                integration_tree_id=integration_tree,
                source_context_digest=source_context_digest,
                affected_child_ids=["child-a"],
            )

            self.assertEqual(opened, replay)
            self.assertEqual(opened["recovery_generation"], 2)
            self.assertEqual(parent_status(runtime), "authorized")
            with self.assertRaises(OperationConflict):
                open_recovery_generation(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="open-generation-2",
                    problem_id=str(problem["problem_id"]),
                    diagnosis="different diagnosis",
                    action="new bounded action",
                    integration_ref=INTEGRATION_REF,
                    integration_head=integration_head,
                    integration_tree_id=integration_tree,
                    source_context_digest=source_context_digest,
                    affected_child_ids=["child-a"],
                )

    def test_replacement_replays_after_crash_and_preserves_old_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            problem = {
                "problem_id": "caller-selected-id",
                "operation_phase": "worker_result",
                "root_condition": "worker result cannot be accepted",
                "requirement_ids": ["REQ-1"],
                "diagnosis": "the dispatched child is stale",
                "action": "replace the stale child inside the envelope",
                "commands": [
                    {
                        "command": "git diff --check",
                        "exit_code": 1,
                        "output_digest": "worker-result-failed",
                        "status": "failed",
                    }
                ],
                "artifact_ids": ["packet-1"],
                "result": "failed",
            }
            initial = record_problem_attempt(
                runtime["ledger"],
                runtime["lease"],
                round_number=0,
                **problem,
            )
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-replacement",
                reason="replace stale dispatched work",
                requested_by="test",
                requires_human_resume=False,
            )

            resumed, new_lease = resume(runtime, operation_id="resume-replacement")

            self.assertEqual(resumed["replacement"]["status"], "available")
            self.assertEqual(
                resumed["replacement"]["eligible_child_ids"], ["child-a"]
            )
            self.assertEqual(resumed["replacement"]["requirement_ids"], ["REQ-1"])
            runtime["lease"] = new_lease
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-replacement-again",
                reason="verify durable replacement opportunity",
                requested_by="test",
                requires_human_resume=False,
            )
            resumed_again, new_lease = resume(
                runtime, operation_id="resume-replacement-again"
            )
            self.assertEqual(resumed_again["stale_children"], [])
            self.assertEqual(resumed_again["replacement"], resumed["replacement"])
            resumed = resumed_again
            runtime["lease"] = new_lease
            repaired = record_problem_attempt(
                runtime["ledger"],
                runtime["lease"],
                round_number=1,
                **{**problem, "problem_id": "replacement-worker-id"},
            )
            self.assertEqual(repaired["problem_id"], initial["problem_id"])

            replacement_graph = copy.deepcopy(runtime["context"]["graph"])
            replacement_graph[0]["child_id"] = "child-a-repair"
            invalid_graph = copy.deepcopy(replacement_graph)
            invalid_graph[0]["touches"] = ["outside/**"]
            with self.assertRaises(InterventionRequired):
                prepare_replacement_revision(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id="start-1",
                    problem_id=initial["problem_id"],
                    source_context_digest=resumed["replacement"][
                        "source_context_digest"
                    ],
                    replaced_child_ids=["child-a"],
                    replacement_graph=invalid_graph,
                )

            def durable_counts() -> tuple[int, int, int, int, int]:
                connection = sqlite3.connect(
                    runtime["ledger"].reference()["sqlite_uri"], uri=True
                )
                try:
                    return tuple(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                            0
                        ]
                        for table in (
                            "context_revisions",
                            "graph_revisions",
                            "operations",
                            "child_packets",
                            "child_operations",
                        )
                    )
                finally:
                    connection.close()

            real_record = integration_module.record_context_revision

            def crash_after_commit(*args, **kwargs):
                real_record(*args, **kwargs)
                raise RuntimeError("crash after replacement authority")

            with (
                patch.object(
                    integration_module,
                    "record_context_revision",
                    side_effect=crash_after_commit,
                ),
                self.assertRaisesRegex(
                    RuntimeError, "crash after replacement authority"
                ),
            ):
                prepare_replacement_revision(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id="start-1",
                    problem_id=initial["problem_id"],
                    source_context_digest=resumed["replacement"][
                        "source_context_digest"
                    ],
                    replaced_child_ids=["child-a"],
                    replacement_graph=replacement_graph,
                )
            after_crash = durable_counts()

            replacement = prepare_replacement_revision(
                runtime["ledger"],
                runtime["lease"],
                request_id="start-1",
                problem_id=initial["problem_id"],
                source_context_digest=resumed["replacement"][
                    "source_context_digest"
                ],
                replaced_child_ids=["child-a"],
                replacement_graph=replacement_graph,
            )

            self.assertEqual(durable_counts(), after_crash)
            self.assertEqual(replacement["replacement_child_ids"], ["child-a-repair"])
            repair_assignment = {
                "packet_id": "packet-repair",
                "child_id": "child-a-repair",
                "requirements": ["REQ-1"],
                "forbidden_touches": ["src/forbidden.txt"],
                "tests": ["git diff --check"],
                "context_slice_ids": ["slice-public"],
                "parent_contact": "channel:worker-commit-run",
                "result_deadline": "2026-07-13T21:00:00Z",
                "result_lease": "result-lease-repair",
                "attempt": 2,
                "round": 2,
            }
            replacement_packet = issue_child_packet(
                runtime["ledger"],
                runtime["lease"],
                repair_assignment,
            )
            packet_counts = durable_counts()
            packet_replay = issue_child_packet(
                runtime["ledger"],
                runtime["lease"],
                repair_assignment,
            )
            self.assertEqual(packet_replay, replacement_packet)
            self.assertEqual(durable_counts(), packet_counts)
            connection = sqlite3.connect(
                runtime["ledger"].reference()["sqlite_uri"], uri=True
            )
            try:
                states = dict(
                    connection.execute(
                        "SELECT child_id, state FROM child_operations ORDER BY child_id"
                    ).fetchall()
                )
            finally:
                connection.close()
            self.assertEqual(states["child-a"], "stale")
            self.assertEqual(states["child-a-repair"], "dispatched")

    def test_resume_stales_old_child_and_rejects_its_late_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="pause dispatched child",
                requested_by="test",
                requires_human_resume=False,
            )
            with self.assertRaises(FreshnessError):
                accepted_result(runtime)

            outcome, new_lease = resume(runtime)

            self.assertEqual(outcome["stale_children"], ["child-a"])
            self.assertEqual(child_state(runtime), "stale")
            runtime["lease"] = new_lease
            with self.assertRaises(FreshnessError):
                accepted_result(runtime)

    def test_resume_recovers_same_attempt_after_fence_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-fence-crash",
                reason="inject crash after fence rotation",
                requested_by="test",
                requires_human_resume=False,
            )
            original = integration_module.reconcile_parent
            with patch.object(
                integration_module,
                "reconcile_parent",
                side_effect=RuntimeError("crash after fence"),
            ), self.assertRaisesRegex(RuntimeError, "crash after fence"):
                resume(runtime, operation_id="resume-fence-crash")

            resumed, lease = resume(runtime, operation_id="resume-fence-crash")

            self.assertEqual(resumed["status"], "resumed")
            self.assertEqual(lease.epoch, runtime["lease"].epoch + 1)
            attempts = [
                row
                for row in runtime["ledger"].authority_snapshot()["tables"][
                    "operations"
                ]
                if row["kind"] == "resume_attempt"
            ]
            rotations = [
                row
                for row in runtime["ledger"].authority_snapshot()["tables"][
                    "operations"
                ]
                if row["kind"] == "writer_rotation"
            ]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(len(rotations), 1)
            self.assertIs(original, integration_module.reconcile_parent)

    def test_resume_retries_unresolved_without_rotating_and_then_waits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-unresolved-retry",
                reason="repeat one reconciliation root",
                requested_by="test",
                requires_human_resume=False,
            )
            unresolved = {
                "actions": [],
                "epoch": runtime["lease"].epoch + 1,
                "status": "unresolved",
                "unresolved": [
                    {
                        "operation_id": "integration:blocked",
                        "reason": "same root condition",
                    }
                ],
            }
            with patch.object(
                integration_module,
                "reconcile_parent",
                return_value=unresolved,
            ) as reconcile:
                first, first_lease = resume(
                    runtime, operation_id="resume-unresolved-retry"
                )
                second, second_lease = resume(
                    runtime, operation_id="resume-unresolved-retry"
                )
                replay, replay_lease = resume(
                    runtime, operation_id="resume-unresolved-retry"
                )

            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["failure_count"], 1)
            self.assertEqual(second["status"], "recovery_waiting")
            self.assertEqual(second["failure_count"], 2)
            self.assertEqual(replay, second)
            self.assertEqual(
                {first_lease.epoch, second_lease.epoch, replay_lease.epoch},
                {runtime["lease"].epoch + 1},
            )
            self.assertEqual(reconcile.call_count, 2)
            self.assertEqual(parent_status(runtime), "recovery_waiting")

    def test_resume_stales_children_once_across_terminal_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-stale-replay",
                reason="prove stale transition is exactly once",
                requested_by="test",
                requires_human_resume=False,
            )

            first, first_lease = resume(runtime, operation_id="resume-stale-replay")
            replay, replay_lease = resume(runtime, operation_id="resume-stale-replay")

            self.assertEqual(first, replay)
            self.assertEqual(first["stale_children"], ["child-a"])
            self.assertEqual(first_lease.epoch, replay_lease.epoch)
            events = runtime["ledger"].authority_snapshot()["tables"][
                "ledger_events"
            ]
            self.assertEqual(
                sum(
                    row["event_type"] == "resume_reconciling"
                    for row in events
                ),
                1,
            )

    def test_resume_revalidates_authority_receipt_resources_and_dirt(self) -> None:
        cases = (
            ("authority", ControlError),
            ("receipt", FreshnessError),
            ("resources", FreshnessError),
            ("dirt", DirtOverlapError),
        )
        for case, error in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                runtime = setup_runtime(Path(tmp))
                pause_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="pause-1",
                    reason=f"validate resume {case}",
                    requested_by="test",
                    requires_human_resume=case == "authority",
                )
                if case == "dirt":
                    (runtime["repo"] / "src/late-user-file.txt").write_text(
                        "late user dirt\n", encoding="utf-8"
                    )
                with self.assertRaises(error):
                    resume_parent(
                        runtime["ledger"],
                        runtime["lease"],
                        operation_id="resume-1",
                        new_writer_id="recovery-writer",
                        authority_identity="user:resume-1",
                        direct_user_action=case != "authority",
                        tool_receipt=(
                            "wrong-receipt"
                            if case == "receipt"
                            else "receipt-worker-commit-fixture"
                        ),
                        available_resources=[] if case == "resources" else ["repo"],
                    )
                self.assertEqual(parent_status(runtime), "paused")

    def test_resume_accepts_retained_integrated_main_drift_at_safe_point(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            candidate = integrate(runtime)
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="published integration retained on main",
                requested_by="test",
                requires_human_resume=False,
            )
            git(
                runtime["repo"],
                "merge",
                "--no-ff",
                candidate["candidate_head"],
                "-m",
                "publish integration",
            )

            outcome, _ = resume(runtime)

            self.assertEqual(outcome["status"], "resumed")
            self.assertEqual(
                outcome["retained_integration_head"],
                candidate["candidate_head"],
            )
            self.assertEqual(
                outcome["main_head"],
                git(runtime["repo"], "rev-parse", "HEAD"),
            )

    def test_resume_safe_point_status_names_failed_predicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            candidate = integrate(runtime)
            runtime["ledger"].prepare_operation(
                runtime["lease"],
                operation_id="pending-effect",
                kind="local-effect",
                input_fingerprint="pending-effect",
                intent={"case": "unresolved"},
            )
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-1",
                reason="diagnose unresolved safe point",
                requested_by="test",
                requires_human_resume=False,
            )
            git(
                runtime["repo"],
                "merge",
                "--no-ff",
                candidate["candidate_head"],
                "-m",
                "publish integration",
            )
            digest = runtime["ledger"].authority_digest()

            status = resume_safe_point_status(runtime["ledger"])

            self.assertFalse(status["eligible"])
            self.assertEqual(
                status["failed_predicates"],
                ["unresolved_operations_reconcilable"],
            )
            self.assertEqual(
                status["unresolved_operations"],
                [
                    {
                        "epoch": 1,
                        "kind": "local-effect",
                        "operation_id": "pending-effect",
                        "phase": "prepared",
                    }
                ],
            )
            self.assertEqual(runtime["ledger"].authority_digest(), digest)

    def test_resume_rejects_unproved_or_unsafe_overlapping_main_drift(self) -> None:
        cases = (
            "unfinished",
            "unretained",
            "user_dirt",
            "unresolved",
            "not_ready",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                runtime = (
                    setup_runtime(Path(tmp))
                    if case == "unfinished"
                    else committed_runtime(Path(tmp))
                )
                candidate = None if case == "unfinished" else integrate(runtime)
                if case == "unresolved":
                    runtime["ledger"].prepare_operation(
                        runtime["lease"],
                        operation_id="pending-effect",
                        kind="local-effect",
                        input_fingerprint="pending-effect",
                        intent={"case": case},
                    )
                pause_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="pause-1",
                    reason=f"reject unsafe drift: {case}",
                    requested_by="test",
                    requires_human_resume=False,
                )
                if case in {"user_dirt", "unresolved", "not_ready"}:
                    git(
                        runtime["repo"],
                        "merge",
                        "--no-ff",
                        candidate["candidate_head"],
                        "-m",
                        "publish integration",
                    )
                else:
                    (runtime["repo"] / "src/main-drift.txt").write_text(
                        f"{case}\n", encoding="utf-8"
                    )
                    git(runtime["repo"], "add", "src/main-drift.txt")
                    git(runtime["repo"], "commit", "-m", f"main drift: {case}")
                if case == "user_dirt":
                    (runtime["repo"] / "src/user-dirt.txt").write_text(
                        "user dirt\n", encoding="utf-8"
                    )
                digest = runtime["ledger"].authority_digest()
                if case == "not_ready":
                    with (
                        patch.object(
                            integration_module,
                            "requirement_progress",
                            return_value={"final_ready": False},
                        ),
                        self.assertRaises(DirtOverlapError),
                    ):
                        resume(runtime)
                else:
                    with self.assertRaises(DirtOverlapError):
                        resume(runtime)
                self.assertEqual(runtime["ledger"].authority_digest(), digest)
                self.assertEqual(parent_status(runtime), "paused")

    def test_cancel_is_human_only_terminal_and_preserves_integrated_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            candidate = integrate(runtime)
            authority = f"sha256:{runtime['ledger'].authority_digest()}"
            with self.assertRaises(ControlError):
                cancel_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="cancel-1",
                    reason="superseded",
                    actor="automation",
                    requested_at="2026-07-13T19:30:00Z",
                    direct_user_action=False,
                    expected_authority_digest=authority,
                )

            cancelled = cancel_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="cancel-1",
                reason="superseded",
                actor="user:jym",
                requested_at="2026-07-13T19:30:00Z",
                direct_user_action=True,
                expected_authority_digest=authority,
                superseded_by="next-parent",
            )
            replay = cancel_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="cancel-1",
                reason="superseded",
                actor="user:jym",
                requested_at="2026-07-13T19:30:00Z",
                direct_user_action=True,
                expected_authority_digest=authority,
                superseded_by="next-parent",
            )

            self.assertEqual(cancelled, replay)
            self.assertFalse(cancelled["cleanup_performed"])
            self.assertFalse(cancelled["archive_as_completed"])
            self.assertEqual(cancelled["integrated_children"], ["child-a"])
            self.assertEqual(cancelled["cancelled_children"], [])
            self.assertEqual(parent_status(runtime), "cancelled")
            self.assertEqual(child_state(runtime), "integrated")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                candidate["candidate_head"],
            )
            self.assertTrue(Path(candidate["candidate_worktree"]).exists())

        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            commit_id = runtime["commit"]["commit_id"]
            authority = f"sha256:{runtime['ledger'].authority_digest()}"
            cancelled = cancel_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="cancel-unfinished",
                reason="stop before integration",
                actor="user:jym",
                requested_at="2026-07-13T19:31:00Z",
                direct_user_action=True,
                expected_authority_digest=authority,
            )

            self.assertEqual(cancelled["cancelled_children"], ["child-a"])
            self.assertEqual(child_state(runtime), "cancelled")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", "refs/heads/loop-v1/child-a"),
                commit_id,
            )
            self.assertTrue(cancelled["retained_git"])

    def test_cancel_safe_point_is_read_only_and_requires_public_reconciliation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime)
            authority = runtime["ledger"].authority_digest()

            safe_point = cancel_safe_point_status(runtime["ledger"])

            self.assertEqual(
                safe_point["status"],
                "reconciliation_required",
            )
            self.assertEqual(
                safe_point["operations"][0]["disposition"],
                "reconcilable_for_completion",
            )
            self.assertEqual(runtime["ledger"].authority_digest(), authority)
            reconciled = reconcile_for_cancel(
                runtime["ledger"],
                runtime["lease"],
                expected_authority_digest=f"sha256:{authority}",
            )
            self.assertEqual(reconciled["status"], "reconciled")
            self.assertEqual(
                reconciled["actions"][0]["result"],
                "no_effect",
            )
            safe_after = cancel_safe_point_status(runtime["ledger"])
            self.assertEqual(safe_after["status"], "cancel_safe")

    def test_cancel_atomically_preserves_absent_local_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            missing = Path(tmp) / "missing-worktree"
            runtime["ledger"].prepare_operation(
                runtime["lease"],
                operation_id="worktree-intent-without-effect",
                kind="child_worktree_create",
                input_fingerprint="intent-fingerprint",
                intent={
                    "branch": "loop-v1/missing-worktree",
                    "worktree": str(missing),
                },
            )
            safe_point = cancel_safe_point_status(runtime["ledger"])
            self.assertEqual(
                safe_point["operations"][0]["disposition"],
                "abandonable_preserved",
            )

            cancelled = cancel_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="cancel-absent-local-intent",
                reason="preserve absent local intent",
                actor="user:jym",
                requested_at="2026-07-29T18:00:00Z",
                direct_user_action=True,
                expected_authority_digest=safe_point["authority_digest"],
            )

            self.assertEqual(
                cancelled["abandoned_operations"],
                [
                    {
                        "kind": "child_worktree_create",
                        "operation_id": "worktree-intent-without-effect",
                        "status": "abandoned_preserved",
                    }
                ],
            )
            abandoned = runtime["ledger"].get_operation(
                "worktree-intent-without-effect"
            )
            self.assertEqual(abandoned["phase"], "authority_committed")
            self.assertEqual(
                abandoned["outcome"]["status"],
                "abandoned_preserved",
            )


if __name__ == "__main__":
    unittest.main()
