from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from loop_v1 import (
    InterventionRequired,
    IntegrationCASConflict,
    ParentValidationError,
    acknowledge_integration,
    advance_integration_ref,
    approve_start_request,
    build_integration_candidate,
    commit_reviewed_candidate,
    create_start_request,
    freshness_token,
    issue_child_packet,
    prepare_integration_candidate,
    reconcile_integration_history,
    record_context_revision,
    record_problem_attempt,
    record_precommit_review,
)
from loop_v1.integration import (
    integrate_reviewed_zero_diff_candidate,
    prepare_replacement_revision,
)
from test_loop_v1_context import canonical_context, initialize, start_envelope
from test_loop_v1_worker_commit import (
    accepted_result,
    child_state,
    git,
    setup_runtime,
    validate,
)


INTEGRATION_REF = "refs/heads/loop-v1/integration"


def committed_runtime(root: Path) -> dict[str, object]:
    runtime = setup_runtime(root)
    (runtime["worktree"] / "src/app.txt").write_text(
        "integrated candidate\n", encoding="utf-8"
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
    commit = commit_reviewed_candidate(
        runtime["ledger"],
        runtime["lease"],
        operation_id="commit-1",
        review_id=review["review_id"],
        message="child candidate",
        author_name="Loop Parent",
        author_email="parent@example.test",
    )
    git(runtime["repo"], "update-ref", INTEGRATION_REF, runtime["base_head"])
    runtime.update({"commit": commit, "integration_ref": INTEGRATION_REF})
    return runtime


def prepare(runtime: dict[str, object], *, checks: list[str] | None = None):
    return prepare_integration_candidate(
        runtime["ledger"],
        runtime["lease"],
        integration_id="integration-1",
        child_id="child-a",
        integration_ref=INTEGRATION_REF,
        candidate_worktree=Path(runtime["repo"]).parent / "integration",
        candidate_branch="loop-v1/candidate-1",
        checks=checks or ["git diff --check"],
        author_name="Loop Integrator",
        author_email="integrator@example.test",
    )


def ledger_rows(runtime: dict[str, object], query: str):
    connection = sqlite3.connect(runtime["ledger"].reference()["sqlite_uri"], uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query).fetchall()]
    finally:
        connection.close()


def commit_operation(
    runtime: dict[str, object],
    operation_id: str,
    kind: str,
    outcome: dict[str, object],
) -> None:
    runtime["ledger"].prepare_operation(
        runtime["lease"],
        operation_id=operation_id,
        kind=kind,
        input_fingerprint=f"input:{operation_id}",
        intent=outcome,
    )
    runtime["ledger"].advance_operation(
        runtime["lease"],
        operation_id=operation_id,
        expected_phase="prepared",
        phase="effect_observed",
        output_fingerprint=f"observed:{operation_id}",
        outcome=outcome,
    )
    runtime["ledger"].advance_operation(
        runtime["lease"],
        operation_id=operation_id,
        expected_phase="effect_observed",
        phase="authority_committed",
        output_fingerprint=f"committed:{operation_id}",
        outcome=outcome,
    )


def zero_diff_recovery_runtime(root: Path) -> dict[str, object]:
    runtime = setup_runtime(root)
    validation = validate(runtime, accepted_result(runtime))
    review = record_precommit_review(
        runtime["ledger"],
        runtime["lease"],
        review_id="review-zero-diff",
        validation_id=validation["validation_id"],
        reviewer_identity="model:codex",
        verdict="passed",
        required_findings=[],
        advisory_findings=[],
        dispositions=[],
    )
    failed_operation_id = "final-checks-failed"
    failed = {
        "checks_result": [
            {
                "command": "test -f missing-tool",
                "exit_code": 127,
                "output_digest": "sha256:missing-tool",
                "status": "failed",
            }
        ],
        "integration_head": runtime["base_head"],
        "integration_tree_id": runtime["base_tree"],
        "root_condition": "final_integration_check_failed",
        "status": "failed",
    }
    commit_operation(runtime, failed_operation_id, "final_integration_checks", failed)
    problem = record_problem_attempt(
        runtime["ledger"],
        runtime["lease"],
        problem_id="ignored-derived-id",
        round_number=0,
        operation_phase="final_integration_checks",
        root_condition="final_integration_check_failed",
        requirement_ids=["REQ-1"],
        diagnosis="the final-check environment lacked its approved tool",
        action="retry ordinary final checks after no-op integration",
        commands=failed["checks_result"],
        artifact_ids=[failed_operation_id, "child-a"],
        result="failed",
    )
    recovery_operation_id = "recover-final-checks-zero-diff"
    commit_operation(
        runtime,
        recovery_operation_id,
        "final_check_recovery_guidance",
        {
            "action": "integrate the reviewed zero-diff replacement",
            "affected_child_ids": ["child-a"],
            "diagnosis": "only the final-check environment changed",
            "direct_user_action": True,
            "failed_operation_id": failed_operation_id,
            "operation_id": recovery_operation_id,
            "requirement_ids": ["REQ-1"],
            "status": "recorded",
        },
    )
    runtime.update(
        {
            "problem": problem,
            "recovery_operation_id": recovery_operation_id,
            "review": review,
        }
    )
    return runtime


def replacement_runtime(root: Path, *, child_state: str) -> dict[str, object]:
    ledger, lease = initialize(root)
    request = create_start_request(
        ledger,
        lease,
        request_id="start-request-1",
        envelope=start_envelope(),
    )
    approve_start_request(
        ledger,
        lease,
        request_id="start-request-1",
        request_digest=request["request_digest"],
        response_identity="user-response-1",
        response_at="2026-07-16T18:00:00Z",
        direct_user_action=True,
    )
    context = canonical_context()
    context["dependency_state"]["REQ-2"] = "ready"
    context["graph"] = [
        {
            "child_id": "child-a",
            "depends_on": [],
            "requirements": ["REQ-1", "REQ-2"],
            "resources": ["canonical-repo"],
            "touches": [
                ".trellis/scripts/loop_v1/**",
                ".trellis/spec/project/loop-v1-runtime.md",
            ],
        }
    ]
    record_context_revision(
        ledger,
        lease,
        request_id="start-request-1",
        revision_id="context-1",
        reason="multi-requirement replacement fixture",
        context=context,
    )
    issue_child_packet(
        ledger,
        lease,
        {
            "packet_id": "packet-1",
            "child_id": "child-a",
            "requirements": ["REQ-1", "REQ-2"],
            "forbidden_touches": [],
            "tests": ["python3 focused-test.py"],
            "context_slice_ids": ["slice-public"],
            "parent_contact": "channel:parent-run",
            "result_deadline": "2026-07-16T20:00:00Z",
            "result_lease": "result-lease-1",
            "attempt": 1,
            "round": 1,
        },
    )
    if child_state != "dispatched":
        with ledger._write_transaction(lease) as connection:
            connection.execute(
                "UPDATE child_operations SET state = ? WHERE child_id = 'child-a'",
                (child_state,),
            )
    return {"context": context, "ledger": ledger, "lease": lease}


class LoopV1IntegrationTests(unittest.TestCase):
    def test_reviewed_zero_diff_final_check_recovery_integrates_without_git(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = zero_diff_recovery_runtime(Path(tmp))
            (runtime["repo"] / "src/app.txt").write_text(
                "later canonical change\n", encoding="utf-8"
            )
            git(runtime["repo"], "add", "src/app.txt")
            git(runtime["repo"], "commit", "-m", "later canonical change")
            canonical_head = git(runtime["repo"], "rev-parse", "HEAD")
            outcome = integrate_reviewed_zero_diff_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="zero-diff-integration:child-a",
                review_id=runtime["review"]["review_id"],
                recovery_operation_id=runtime["recovery_operation_id"],
                problem_id=runtime["problem"]["problem_id"],
            )
            replay = integrate_reviewed_zero_diff_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="zero-diff-integration:child-a",
                review_id=runtime["review"]["review_id"],
                recovery_operation_id=runtime["recovery_operation_id"],
                problem_id=runtime["problem"]["problem_id"],
            )

            self.assertEqual(outcome, replay)
            self.assertEqual(outcome["git_effect"], "none")
            self.assertEqual(child_state(runtime), "integrated")
            self.assertEqual(
                ledger_rows(
                    runtime,
                    "SELECT * FROM git_operations "
                    "WHERE phase IN ('committed', 'integrated')",
                ),
                [],
            )
            context_row = ledger_rows(
                runtime,
                "SELECT context_json FROM context_revisions "
                "ORDER BY sequence DESC LIMIT 1",
            )[0]
            context = json.loads(context_row["context_json"])
            self.assertEqual(context["requirements"][0]["coverage_state"], "covered")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", "HEAD"), canonical_head
            )

    def test_ordinary_reviewed_zero_diff_candidate_still_rejects_empty_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            validation = validate(runtime, accepted_result(runtime))
            review = record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-zero-diff",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )
            before = runtime["ledger"].authority_digest()
            with self.assertRaisesRegex(
                ParentValidationError, "validated child commit scope is empty"
            ):
                commit_reviewed_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="commit-zero-diff",
                    review_id=review["review_id"],
                    message="must not fabricate a commit",
                    author_name="Loop Parent",
                    author_email="parent@example.test",
                )
            self.assertEqual(runtime["ledger"].authority_digest(), before)
            self.assertEqual(child_state(runtime), "reviewed")

    def test_green_candidate_advances_by_cas_and_acknowledges_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepared = prepare(runtime)
            self.assertEqual(prepared["status"], "prepared")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                runtime["base_head"],
            )

            candidate = build_integration_candidate(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            self.assertEqual(candidate["status"], "candidate_green")
            self.assertEqual(
                git(
                    runtime["repo"],
                    "rev-list",
                    "--parents",
                    "-n",
                    "1",
                    candidate["candidate_head"],
                ).split(),
                [
                    candidate["candidate_head"],
                    runtime["base_head"],
                    runtime["commit"]["commit_id"],
                ],
            )
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                runtime["base_head"],
            )

            advanced = advance_integration_ref(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            self.assertTrue(advanced["advanced"])
            acknowledged = acknowledge_integration(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            replay = acknowledge_integration(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )

            self.assertEqual(acknowledged, replay)
            self.assertTrue(acknowledged["archive_eligible"])
            self.assertEqual(child_state(runtime), "integrated")
            self.assertEqual(
                len(
                    ledger_rows(
                        runtime,
                        "SELECT * FROM git_operations WHERE phase = 'integrated'",
                    )
                ),
                1,
            )
            contexts = ledger_rows(
                runtime,
                "SELECT context_json FROM context_revisions ORDER BY sequence DESC LIMIT 1",
            )
            context = json.loads(contexts[0]["context_json"])
            self.assertEqual(
                context["integration"]["integration_head"],
                candidate["candidate_head"],
            )
            self.assertEqual(context["requirements"][0]["coverage_state"], "covered")
            self.assertEqual(runtime["ledger"].projection_status()["status"], "current")

    def test_failed_candidate_preserves_ref_and_records_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime, checks=["test -f integration-marker"])
            candidate = build_integration_candidate(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )

            self.assertEqual(candidate["status"], "candidate_failed")
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                runtime["base_head"],
            )
            self.assertEqual(child_state(runtime), "committed")
            problems = ledger_rows(
                runtime,
                "SELECT outcome_json FROM operations WHERE kind = 'problem_attempt'",
            )
            self.assertEqual(len(problems), 1)
            self.assertEqual(json.loads(problems[0]["outcome_json"])["round"], 0)

    def test_check_side_effect_fails_the_candidate_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime, checks=["touch leaked-check-file"])

            candidate = build_integration_candidate(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )

            self.assertEqual(candidate["status"], "candidate_failed")
            self.assertEqual(
                candidate["root_condition"], "candidate_effect_boundary_failed"
            )
            self.assertFalse(candidate["effect_boundary_clean"])
            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF),
                runtime["base_head"],
            )

    def test_unexpected_ref_pauses_without_rewriting_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            prepare(runtime)
            candidate = build_integration_candidate(
                runtime["ledger"], runtime["lease"], integration_id="integration-1"
            )
            unrelated = git(
                runtime["repo"],
                "commit-tree",
                runtime["base_tree"],
                "-p",
                runtime["base_head"],
            )
            git(runtime["repo"], "update-ref", INTEGRATION_REF, unrelated)

            with self.assertRaises(IntegrationCASConflict):
                advance_integration_ref(
                    runtime["ledger"],
                    runtime["lease"],
                    integration_id="integration-1",
                )

            self.assertEqual(
                git(runtime["repo"], "rev-parse", INTEGRATION_REF), unrelated
            )
            self.assertEqual(
                git(runtime["repo"], "rev-parse", candidate["candidate_branch"]),
                candidate["candidate_head"],
            )
            parent = ledger_rows(runtime, "SELECT status FROM parent_runs")[0]
            self.assertEqual(parent["status"], "paused")

    def test_removed_integration_history_invalidates_coverage_but_keeps_git_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
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
            git(
                runtime["repo"],
                "update-ref",
                INTEGRATION_REF,
                runtime["base_head"],
                candidate["candidate_head"],
            )

            outcome = reconcile_integration_history(
                runtime["ledger"], runtime["lease"], integration_ref=INTEGRATION_REF
            )

            self.assertEqual(outcome["invalidated_children"], ["child-a"])
            self.assertEqual(child_state(runtime), "invalidated")
            self.assertEqual(
                len(
                    ledger_rows(
                        runtime,
                        "SELECT * FROM git_operations WHERE phase = 'integrated'",
                    )
                ),
                1,
            )
            context = json.loads(
                ledger_rows(
                    runtime,
                    "SELECT context_json FROM context_revisions ORDER BY sequence DESC LIMIT 1",
                )[0]["context_json"]
            )
            self.assertEqual(context["requirements"][0]["coverage_state"], "uncovered")

    def test_non_final_replacement_requires_exact_problem_coverage(self) -> None:
        cases = {
            "worker_result": "dispatched",
            "worker_validation": "result_validated",
            "precommit_review": "review_blocked",
            "integration_candidate": "committed",
            "resume_stale": "stale",
        }
        for phase, state in cases.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                runtime = replacement_runtime(Path(tmp), child_state=state)
                problem = record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    problem_id=f"ignored-{phase}",
                    round_number=0,
                    operation_phase=phase,
                    root_condition=f"{phase}-scope-regression",
                    requirement_ids=["REQ-2"],
                    diagnosis="one symptom must not narrow the complete child contract",
                    action="reject the incomplete non-final recovery scope",
                    commands=[],
                    artifact_ids=["child-a"],
                    result="failed",
                )
                replacement_graph = copy.deepcopy(runtime["context"]["graph"])
                replacement_graph[0]["child_id"] = "child-a-repair-1"
                before = len(ledger_rows(runtime, "SELECT 1 FROM context_revisions"))

                with self.assertRaisesRegex(
                    InterventionRequired,
                    "replacement coverage must match the recorded problem",
                ):
                    prepare_replacement_revision(
                        runtime["ledger"],
                        runtime["lease"],
                        request_id="start-request-1",
                        problem_id=problem["problem_id"],
                        source_context_digest=freshness_token(runtime["ledger"])[
                            "context_digest"
                        ],
                        replaced_child_ids=["child-a"],
                        replacement_graph=replacement_graph,
                    )

                self.assertEqual(
                    len(ledger_rows(runtime, "SELECT 1 FROM context_revisions")),
                    before,
                )
                self.assertEqual(
                    ledger_rows(
                        runtime,
                        "SELECT state FROM child_operations WHERE child_id = 'child-a'",
                    )[0]["state"],
                    state,
                )

    def test_final_review_problem_scope_must_be_nonempty_subset_without_revision(
        self,
    ) -> None:
        for label, requirements in (
            ("empty", []),
            ("outside", ["REQ-OUTSIDE"]),
        ):
            with self.subTest(scope=label), tempfile.TemporaryDirectory() as tmp:
                runtime = replacement_runtime(Path(tmp), child_state="integrated")
                problem = record_problem_attempt(
                    runtime["ledger"],
                    runtime["lease"],
                    problem_id=f"ignored-final-{label}",
                    round_number=0,
                    operation_phase="final_review",
                    root_condition=f"final-review-{label}-scope",
                    requirement_ids=requirements,
                    diagnosis="invalid final-review scope must fail closed",
                    action="reject the invalid final-review recovery scope",
                    commands=[],
                    artifact_ids=["child-a"],
                    result="failed",
                )
                replacement_graph = copy.deepcopy(runtime["context"]["graph"])
                replacement_graph[0]["child_id"] = "child-a-repair-1"
                before = len(ledger_rows(runtime, "SELECT 1 FROM context_revisions"))

                with self.assertRaisesRegex(
                    InterventionRequired,
                    "final-review problem coverage must be a non-empty subset",
                ):
                    prepare_replacement_revision(
                        runtime["ledger"],
                        runtime["lease"],
                        request_id="start-request-1",
                        problem_id=problem["problem_id"],
                        source_context_digest=freshness_token(runtime["ledger"])[
                            "context_digest"
                        ],
                        replaced_child_ids=["child-a"],
                        replacement_graph=replacement_graph,
                    )

                self.assertEqual(
                    len(ledger_rows(runtime, "SELECT 1 FROM context_revisions")),
                    before,
                )
                self.assertEqual(
                    ledger_rows(
                        runtime,
                        "SELECT state FROM child_operations WHERE child_id = 'child-a'",
                    )[0]["state"],
                    "integrated",
                )


if __name__ == "__main__":
    unittest.main()
