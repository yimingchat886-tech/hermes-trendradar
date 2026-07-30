"""Serial integration, crash reconciliation, and parent control for Loop v1."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from .context import (
    ContextError,
    FreshnessError,
    InterventionRequired,
    _approved_envelope,
    _compare_freshness,
    _context_record,
    _digest_json,
    _freshness_token,
    _insert_committed_operation,
    _latest_context_row,
    _required_text,
    _text_list,
    _validate_graph,
    record_context_revision,
)
from .ledger import (
    OperationConflict,
    ParentLedger,
    WriterLease,
    _canonical_json,
    _now,
)
from .scheduler import deterministic_integration_selection, requirement_progress
from .worker_commit import (
    DirtOverlapError,
    GitStateError,
    ParentValidationError,
    _assert_main_isolated,
    _assert_review_artifact,
    _assert_same_repository,
    _assert_worktree_target,
    _git,
    _git_text,
    _main_state,
    observe_worker_candidate,
    _repository_path,
    _run_parent_check,
    scan_repository_dirt,
)


class IntegrationError(ContextError):
    """Base error for serial integration and recovery failures."""


class IntegrationStateError(IntegrationError):
    """Raised when ledger/Git state cannot begin or continue integration."""


class IntegrationCASConflict(IntegrationError):
    """Raised when the authoritative integration ref differs from expected-old."""


class RecoveryError(IntegrationError):
    """Raised when crash reconciliation cannot prove one exact outcome."""


class ProblemBudgetError(IntegrationError):
    """Raised when a problem attempt violates its durable 0+3 budget."""


class ControlError(IntegrationError):
    """Raised when pause, resume, or cancel authority is invalid."""


MAX_REPAIR_ROUNDS = 3
_INTEGRATION_KIND = "integration_candidate"
_FINAL_INTEGRATION_KIND = "final_integration_checks"
_ZERO_DIFF_INTEGRATION_KIND = "reviewed_zero_diff_integration"
_EMPTY_DIFF_IDENTITY = (
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
_RECOVERABLE_OPERATION_KINDS = frozenset(
    {_INTEGRATION_KIND, "child_commit", "final_local_merge"}
)
_PROBLEM_KIND = "problem_attempt"
_RECOVERY_GENERATION_KIND = "recovery_generation_opened"
_REPLACEMENT_GUIDANCE_KIND = "replacement_guidance"
_PROBLEM_FIELDS = frozenset(
    {"action", "diagnosis", "problem_id", "root_condition", "round"}
)


def integrate_reviewed_zero_diff_candidate(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    review_id: str,
    recovery_operation_id: str,
    problem_id: str,
) -> dict[str, object]:
    """Integrate one exact final-check recovery result without a Git effect."""
    operation_id = _required_text(operation_id, "operation_id")
    review_id = _required_text(review_id, "review_id")
    recovery_operation_id = _required_text(
        recovery_operation_id, "recovery_operation_id"
    )
    problem_id = _required_text(problem_id, "problem_id")
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        outcome = existing["outcome"]
        if (
            existing["kind"] != _ZERO_DIFF_INTEGRATION_KIND
            or existing["phase"] != "authority_committed"
            or outcome.get("review_id") != review_id
            or outcome.get("recovery_operation_id") != recovery_operation_id
            or outcome.get("problem_id") != problem_id
        ):
            raise OperationConflict("zero-diff integration operation ID was reused")
        _ensure_integration_context(ledger, lease, outcome)
        return outcome

    connection = ledger._connect(read_only=True)
    try:
        evidence = _reviewed_zero_diff_evidence(
            connection,
            ledger,
            lease,
            review_id=review_id,
            recovery_operation_id=recovery_operation_id,
            problem_id=problem_id,
        )
    finally:
        connection.close()
    observation = observe_worker_candidate(
        Path(evidence["review"]["worktree"]),
        base_head=str(evidence["review"]["execution_base"]["head"]),
    )
    _assert_review_artifact(evidence["validation"], observation)
    outcome = _zero_diff_integration_outcome(
        operation_id, evidence, problem_id, recovery_operation_id
    )
    input_fingerprint = _digest_json(
        {
            "freshness": evidence["review"]["freshness"],
            "outcome": outcome,
        }
    )

    with ledger._write_transaction(lease) as connection:
        current = _reviewed_zero_diff_evidence(
            connection,
            ledger,
            lease,
            review_id=review_id,
            recovery_operation_id=recovery_operation_id,
            problem_id=problem_id,
        )
        current_observation = observe_worker_candidate(
            Path(current["review"]["worktree"]),
            base_head=str(current["review"]["execution_base"]["head"]),
        )
        _assert_review_artifact(current["validation"], current_observation)
        if _zero_diff_integration_outcome(
            operation_id, current, problem_id, recovery_operation_id
        ) != outcome:
            raise FreshnessError("zero-diff integration evidence changed")
        if connection.execute(
            "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone():
            raise OperationConflict("zero-diff integration operation was concurrently reused")
        now = _now()
        child = connection.execute(
            "UPDATE child_operations SET state = 'integrated', updated_at = ? "
            "WHERE child_id = ? AND state = 'reviewed' AND epoch = ?",
            (now, outcome["child_id"], lease.epoch),
        )
        if child.rowcount != 1:
            raise FreshnessError("zero-diff child is not awaiting integration")
        connection.execute(
            "UPDATE resource_claims SET state = 'released', updated_at = ? "
            "WHERE child_id = ? AND state = 'acquired'",
            (now, outcome["child_id"]),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_ZERO_DIFF_INTEGRATION_KIND,
            epoch=lease.epoch,
            input_fingerprint=input_fingerprint,
            outcome=outcome,
            event_type="reviewed_zero_diff_integrated",
            created_at=now,
        )
    _ensure_integration_context(ledger, lease, outcome)
    return outcome


def _reviewed_zero_diff_evidence(
    connection: sqlite3.Connection,
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    review_id: str,
    recovery_operation_id: str,
    problem_id: str,
) -> dict[str, object]:
    runtime = _runtime_state(connection, ledger.run_id, require_authorized=True)
    review = _authority_outcome(
        connection, f"precommit-review:{review_id}", "precommit_review_recorded"
    )
    validation = _authority_outcome(
        connection,
        f"candidate-validation:{review['validation_id']}",
        "candidate_validated",
    )
    guidance = _authority_outcome(
        connection, recovery_operation_id, "final_check_recovery_guidance"
    )
    attempts = [
        json.loads(row["outcome_json"])
        for row in connection.execute(
            "SELECT outcome_json FROM operations "
            "WHERE kind = 'problem_attempt' AND phase = 'authority_committed' "
            "ORDER BY created_at, operation_id"
        ).fetchall()
        if json.loads(row["outcome_json"]).get("problem_id") == problem_id
    ]
    if not attempts:
        raise FreshnessError("zero-diff integration recovery problem is missing")
    problem = attempts[-1]
    failed_operation_id = str(guidance.get("failed_operation_id", ""))
    failed = _authority_outcome(
        connection, failed_operation_id, _FINAL_INTEGRATION_KIND
    )
    child_id = str(review["child_id"])
    affected = guidance.get("affected_child_ids")
    recovery_child_id = (
        str(affected[0])
        if isinstance(affected, list) and len(affected) == 1
        else ""
    )
    coverage = sorted(str(item) for item in review["coverage"])
    child = _child_row(connection, child_id)
    node = _graph_node(runtime["context"]["graph"], child_id)
    _compare_freshness("zero-diff integration", review["freshness"], runtime["token"])
    if (
        review["verdict"] != "passed"
        or review["required_findings"]
        or review["actual_touches"]
        or validation["actual_touches"]
        or validation["untracked_files"]
        or review["diff_identity"] != _EMPTY_DIFF_IDENTITY
        or validation["diff_identity"] != _EMPTY_DIFF_IDENTITY
        or review["tree_id"] != validation["tree_id"]
        or review["tree_id"] != review["execution_base"]["tree_id"]
        or review["coverage"] != validation["coverage"]
        or not validation["parent_checks"]
        or any(item["status"] != "passed" for item in validation["parent_checks"])
        or int(child["epoch"]) != lease.epoch
        or child["state"] != "reviewed"
        or sorted(str(item) for item in node["requirements"]) != coverage
        or runtime["context"]["integration"]["integration_head"]
        != review["execution_base"]["head"]
        or runtime["context"]["integration"]["integration_tree_id"]
        != review["execution_base"]["tree_id"]
        or guidance.get("direct_user_action") is not True
        or not _replacement_maps_to_child(
            connection,
            problem_id=problem_id,
            source_child_id=recovery_child_id,
            current_child_id=child_id,
        )
        or sorted(str(item) for item in guidance.get("requirement_ids", []))
        != coverage
        or problem.get("operation_phase") != _FINAL_INTEGRATION_KIND
        or problem.get("result") != "failed"
        or problem.get("exhausted") is not False
        or sorted(str(item) for item in problem.get("requirement_ids", []))
        != coverage
        or recovery_child_id not in problem.get("artifact_ids", [])
        or failed_operation_id not in problem.get("artifact_ids", [])
        or failed.get("status") != "failed"
    ):
        raise IntegrationStateError(
            "reviewed zero-diff child lacks exact final-check recovery evidence"
        )
    return {
        "guidance": guidance,
        "problem": problem,
        "review": review,
        "runtime": runtime,
        "validation": validation,
    }


def _replacement_maps_to_child(
    connection: sqlite3.Connection,
    *,
    problem_id: str,
    source_child_id: str,
    current_child_id: str,
) -> bool:
    if not source_child_id:
        return False
    if source_child_id == current_child_id:
        return True
    reason = f"recovery replacement for {problem_id}: {source_child_id}"
    matches = []
    for row in connection.execute(
        "SELECT previous_digest, context_json FROM context_revisions "
        "WHERE reason = ? ORDER BY sequence",
        (reason,),
    ).fetchall():
        source = connection.execute(
            "SELECT context_json FROM context_revisions WHERE digest = ?",
            (row["previous_digest"],),
        ).fetchall()
        if len(source) != 1:
            continue
        source_ids = {
            str(node["child_id"])
            for node in json.loads(source[0]["context_json"])["graph"]
        }
        current_ids = {
            str(node["child_id"])
            for node in json.loads(row["context_json"])["graph"]
        }
        if source_child_id in source_ids and current_ids - source_ids == {
            current_child_id
        }:
            matches.append(row)
    if len(matches) > 1:
        raise FreshnessError("zero-diff recovery has ambiguous replacement lineage")
    return bool(matches)


def _zero_diff_integration_outcome(
    operation_id: str,
    evidence: Mapping[str, object],
    problem_id: str,
    recovery_operation_id: str,
) -> dict[str, object]:
    review = evidence["review"]
    integration = evidence["runtime"]["context"]["integration"]
    return {
        "actual_touches": [],
        "candidate_head": integration["integration_head"],
        "candidate_tree_id": integration["integration_tree_id"],
        "child_commit_id": None,
        "child_id": review["child_id"],
        "coverage": sorted(review["coverage"]),
        "diff_identity": _EMPTY_DIFF_IDENTITY,
        "disposition": "reviewed_zero_diff",
        "expected_old": integration["integration_head"],
        "freshness": review["freshness"],
        "git_effect": "none",
        "integration_id": f"zero-diff-{review['child_id']}",
        "operation_id": operation_id,
        "problem_id": problem_id,
        "recovery_child_id": evidence["guidance"]["affected_child_ids"][0],
        "recovery_operation_id": recovery_operation_id,
        "result_digest": review["result_digest"],
        "review_id": review["review_id"],
        "status": "integrated",
        "validation_id": review["validation_id"],
    }


def _canonical_recovery_observations(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    return sorted((dict(item) for item in observations), key=_canonical_json)


def prepare_integration_candidate(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_id: str,
    child_id: str,
    integration_ref: str,
    candidate_worktree: Path,
    candidate_branch: str,
    checks: Sequence[str],
    author_name: str,
    author_email: str,
    problem: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Persist one deterministic child/candidate/ref intent before Git effects."""
    integration_id = _required_text(integration_id, "integration_id")
    child_id = _required_text(child_id, "child_id")
    integration_ref = _integration_ref(integration_ref)
    candidate_branch = _required_text(candidate_branch, "candidate_branch")
    commands = _text_list(list(checks), "checks")
    if not commands:
        raise IntegrationStateError("integration requires at least one check")
    author_name = _required_text(author_name, "author_name")
    author_email = _required_text(author_email, "author_email")
    if "@" not in author_email:
        raise IntegrationStateError("author_email must contain '@'")
    problem_value = _problem_input(problem)
    operation_id = f"integration:{integration_id}"
    existing = ledger.get_operation(operation_id)

    history = reconcile_integration_history(
        ledger,
        lease,
        integration_ref=integration_ref,
    )
    if history["invalidated_children"]:
        raise IntegrationStateError(
            "integration history changed; invalidated evidence must be repaired first"
        )

    canonical = ledger.repo_root.resolve()
    target = Path(candidate_worktree).resolve()
    protected = _registered_worktrees(canonical)
    _assert_worktree_target(
        canonical,
        target,
        protected,
        allow_registered=existing is not None,
    )
    _git(canonical, "check-ref-format", "--branch", candidate_branch)
    _assert_ref_unattached(canonical, integration_ref)

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id, require_authorized=True)
        unresolved = connection.execute(
            """
            SELECT operation_id FROM operations
            WHERE kind = ? AND phase != 'authority_committed' AND operation_id != ?
            ORDER BY operation_id
            """,
            (_INTEGRATION_KIND, operation_id),
        ).fetchall()
        if unresolved:
            raise IntegrationStateError(
                f"another integration is unresolved: {unresolved[0]['operation_id']}"
            )
        selection = deterministic_integration_selection(ledger)
        if selection["status"] != "ready" or not selection["selected"]:
            raise IntegrationStateError("no committed child is ready for integration")
        first_selected = selection["selected"][0]
        if _child_row(connection, first_selected)["state"] != "committed":
            raise IntegrationStateError("deterministic next child is not committed")
        if first_selected != child_id:
            raise IntegrationStateError(
                f"deterministic next child is {first_selected}, not {child_id}"
            )
        child = _child_row(connection, child_id)
        if child["state"] != "committed":
            raise IntegrationStateError("only a parent-committed child may integrate")
        child_git = connection.execute(
            "SELECT * FROM git_operations WHERE git_operation_id = ?",
            (f"child-commit:{child_id}",),
        ).fetchone()
        if child_git is None or child_git["phase"] != "committed":
            raise IntegrationStateError("child commit lacks durable Git authority")
        child_outcome = json.loads(child_git["outcome_json"])
        review = _authority_outcome(
            connection,
            f"precommit-review:{child_outcome['review_id']}",
            "precommit_review_recorded",
        )
        node = _graph_node(runtime["context"]["graph"], child_id)
        if sorted(node["requirements"]) != sorted(review["coverage"]):
            raise FreshnessError(
                "committed child coverage differs from the current graph assignment"
            )
        if (
            review["freshness"]["envelope_digest"]
            != runtime["token"]["envelope_digest"]
        ):
            raise FreshnessError(
                "committed child is stale at integration boundary: envelope_digest"
            )
        source_base = review["source_base"]
        context_source = runtime["context"]["integration"]
        if (
            source_base["head"] != context_source["base_head"]
            or source_base["tree_id"] != context_source["base_tree_id"]
        ):
            raise FreshnessError(
                "committed child source base differs from canonical context"
            )
    finally:
        connection.close()

    expected_old = _git_text(canonical, "rev-parse", "--verify", integration_ref)
    expected_tree = _git_text(canonical, "rev-parse", f"{expected_old}^{{tree}}")
    if (
        review["execution_base"] != {
            "head": source_base["head"],
            "tree_id": source_base["tree_id"],
        }
        and review["execution_base"]
        != {"head": expected_old, "tree_id": expected_tree}
    ):
        raise FreshnessError(
            "committed child execution base differs from current integration"
        )
    context_integration = runtime["context"]["integration"]
    if (
        context_integration["integration_head"] != expected_old
        or context_integration["integration_tree_id"] != expected_tree
    ):
        raise RecoveryError(
            "canonical context and integration ref differ; reconcile before integrating"
        )
    _assert_main_isolated(
        _main_state(canonical, str(source_base["head"])), node["touches"]
    )

    intent = {
        "author_email": author_email,
        "author_name": author_name,
        "candidate_branch": candidate_branch,
        "candidate_worktree": str(target),
        "checks": commands,
        "child_commit_id": child_git["commit_id"],
        "child_id": child_id,
        "child_tree_id": child_git["tree_id"],
        "coverage": sorted(node["requirements"]),
        "expected_old": expected_old,
        "expected_old_tree": expected_tree,
        "freshness": runtime["token"],
        "integration_id": integration_id,
        "integration_ref": integration_ref,
        "operation_id": operation_id,
        "problem": problem_value,
        "status": "prepared",
    }
    prepared = ledger.prepare_operation(
        lease,
        operation_id=operation_id,
        kind=_INTEGRATION_KIND,
        input_fingerprint=_digest_json(intent),
        intent=intent,
    )
    return prepared["outcome"]


def build_integration_candidate(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_id: str,
) -> dict[str, object]:
    """Build and check the no-ff candidate without advancing integration ref."""
    integration_id = _required_text(integration_id, "integration_id")
    operation_id = f"integration:{integration_id}"
    operation = _operation(ledger, operation_id, _INTEGRATION_KIND)
    if operation["phase"] == "authority_committed":
        outcome = operation["outcome"]
        if outcome.get("status") == "candidate_failed":
            _ensure_problem_from_candidate(ledger, lease, outcome)
        return outcome
    if operation["phase"] == "effect_observed":
        _ensure_candidate_verification(ledger, lease, operation["outcome"])
        return operation["outcome"]
    if operation["phase"] != "prepared" or operation["epoch"] != lease.epoch:
        raise RecoveryError("candidate preparation belongs to another recovery epoch")
    intent = operation["outcome"]
    _assert_parent_status(ledger, "authorized", "candidate build")
    canonical = ledger.repo_root.resolve()
    actual_ref = _git_text(
        canonical, "rev-parse", "--verify", intent["integration_ref"]
    )
    if actual_ref != intent["expected_old"]:
        raise IntegrationCASConflict(
            "integration ref changed before candidate construction"
        )

    target = Path(intent["candidate_worktree"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        _git(
            canonical,
            "worktree",
            "add",
            "-b",
            intent["candidate_branch"],
            str(target),
            intent["expected_old"],
        )
    _assert_same_repository(canonical, target)
    if (
        _git_text(target, "symbolic-ref", "--short", "HEAD")
        != intent["candidate_branch"]
    ):
        raise IntegrationStateError("candidate worktree owns another branch")

    merge_result: dict[str, object]
    head = _git_text(target, "rev-parse", "HEAD")
    if head == intent["expected_old"]:
        process = _git_process(
            target,
            "merge",
            "--no-ff",
            "--no-edit",
            intent["child_commit_id"],
            env=_identity_env(intent),
        )
        merge_result = _process_evidence("git merge --no-ff", process)
    else:
        merge_result = {
            "command": "git merge --no-ff",
            "exit_code": 0,
            "output_digest": "replayed-existing-candidate",
            "status": "passed",
        }

    candidate_head = _git_text(target, "rev-parse", "HEAD")
    parents_ok = _merge_parents(target, candidate_head) == [
        intent["expected_old"],
        intent["child_commit_id"],
    ]
    if merge_result["status"] != "passed" or not parents_ok:
        outcome = {
            **intent,
            "candidate_head": candidate_head,
            "candidate_tree_id": _git_text(
                target, "rev-parse", f"{candidate_head}^{{tree}}"
            ),
            "checks_result": [],
            "merge_result": merge_result,
            "root_condition": "candidate_merge_failed",
            "status": "candidate_failed",
        }
        return _record_candidate_outcome(ledger, lease, operation_id, outcome)

    checks_result = [_run_parent_check(target, command) for command in intent["checks"]]
    effect_boundary_clean = _candidate_worktree_clean(target)
    failed = [item for item in checks_result if item["status"] != "passed"]
    candidate_tree = _git_text(target, "rev-parse", f"{candidate_head}^{{tree}}")
    outcome = {
        **intent,
        "candidate_head": candidate_head,
        "candidate_tree_id": candidate_tree,
        "checks_result": checks_result,
        "merge_result": merge_result,
        "effect_boundary_clean": effect_boundary_clean,
        "root_condition": (
            "candidate_effect_boundary_failed"
            if not effect_boundary_clean
            else "candidate_check_failed"
            if failed
            else None
        ),
        "status": (
            "candidate_failed"
            if failed or not effect_boundary_clean
            else "candidate_green"
        ),
    }
    return _record_candidate_outcome(ledger, lease, operation_id, outcome)


def advance_integration_ref(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_id: str,
) -> dict[str, object]:
    """Advance the unattached integration ref once using expected-old CAS."""
    integration_id = _required_text(integration_id, "integration_id")
    operation = _operation(ledger, f"integration:{integration_id}", _INTEGRATION_KIND)
    outcome = operation["outcome"]
    if operation["phase"] == "authority_committed":
        if outcome.get("status") != "integrated":
            raise IntegrationStateError("failed candidate cannot advance a ref")
        actual = _git_text(
            ledger.repo_root, "rev-parse", "--verify", outcome["integration_ref"]
        )
        if actual != outcome["candidate_head"] and not _is_ancestor(
            ledger.repo_root, outcome["candidate_head"], actual
        ):
            raise RecoveryError("acknowledged integration is absent from current ref")
        return {
            "actual_ref": actual,
            "advanced": False,
            "integration_id": integration_id,
            "replayed": True,
        }
    if (
        operation["phase"] != "effect_observed"
        or outcome["status"] != "candidate_green"
    ):
        raise IntegrationStateError("integration requires one green durable candidate")
    if operation["epoch"] != lease.epoch:
        raise RecoveryError("stale integration intent requires resume reconciliation")
    _assert_parent_status(ledger, "authorized", "integration ref advance")
    _assert_candidate_green(ledger, outcome, rerun_checks=True)

    canonical = ledger.repo_root.resolve()
    _assert_ref_unattached(canonical, outcome["integration_ref"])
    actual = _git_text(canonical, "rev-parse", "--verify", outcome["integration_ref"])
    if actual == outcome["candidate_head"]:
        return {
            "actual_ref": actual,
            "advanced": False,
            "integration_id": integration_id,
            "replayed": True,
        }
    if actual != outcome["expected_old"]:
        _pause_for_conflict(
            ledger,
            lease,
            integration_id=integration_id,
            expected=outcome["expected_old"],
            actual=actual,
        )
        raise IntegrationCASConflict("integration ref compare-and-swap failed")
    try:
        _git(
            canonical,
            "update-ref",
            outcome["integration_ref"],
            outcome["candidate_head"],
            outcome["expected_old"],
        )
    except GitStateError as exc:
        actual = _git_text(
            canonical, "rev-parse", "--verify", outcome["integration_ref"]
        )
        _pause_for_conflict(
            ledger,
            lease,
            integration_id=integration_id,
            expected=outcome["expected_old"],
            actual=actual,
        )
        raise IntegrationCASConflict(
            "integration ref CAS outcome is unresolved"
        ) from exc
    actual = _git_text(canonical, "rev-parse", "--verify", outcome["integration_ref"])
    if actual != outcome["candidate_head"]:
        raise RecoveryError("integration ref did not reach the intended candidate")
    return {
        "actual_ref": actual,
        "advanced": True,
        "integration_id": integration_id,
        "replayed": False,
    }


def acknowledge_integration(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_id: str,
) -> dict[str, object]:
    """Acknowledge an exact ref advance and make the child archive-eligible."""
    integration_id = _required_text(integration_id, "integration_id")
    operation_id = f"integration:{integration_id}"
    operation = _operation(ledger, operation_id, _INTEGRATION_KIND)
    if operation["phase"] == "authority_committed":
        outcome = operation["outcome"]
        if outcome.get("status") != "integrated":
            raise IntegrationStateError(
                "failed candidate has no integration acknowledgement"
            )
        _assert_integration_authority(ledger, outcome)
        _ensure_integration_context(ledger, lease, outcome)
        _ensure_problem_resolution(ledger, lease, outcome)
        ledger.rebuild_projection(lease)
        return outcome
    if operation["phase"] != "effect_observed":
        raise IntegrationStateError("candidate effect has not been observed")
    outcome = operation["outcome"]
    if outcome["status"] != "candidate_green":
        raise IntegrationStateError("failed candidate cannot be acknowledged")
    if operation["epoch"] != lease.epoch:
        raise RecoveryError("stale integration acknowledgement requires reconciliation")
    _assert_parent_status(ledger, "authorized", "integration acknowledgement")
    _assert_candidate_green(ledger, outcome, rerun_checks=False)
    actual = _git_text(
        ledger.repo_root, "rev-parse", "--verify", outcome["integration_ref"]
    )
    if actual != outcome["candidate_head"]:
        raise RecoveryError("integration ref has not reached the green candidate")
    acknowledged = _commit_integration_authority(
        ledger,
        lease,
        operation_id=operation_id,
        source=outcome,
        recovered=False,
    )
    _ensure_integration_context(ledger, lease, acknowledged)
    _ensure_problem_resolution(ledger, lease, acknowledged)
    ledger.rebuild_projection(lease)
    return acknowledged


def verify_final_integration_checks(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    verification_id: str,
    integration_ref: str,
    worktree: Path,
    checks: Sequence[str],
) -> dict[str, object]:
    """Run one durable final check set for an exact complete integration."""
    verification_id = _required_text(verification_id, "verification_id")
    integration_ref = _integration_ref(integration_ref)
    commands = _text_list(list(checks), "checks")
    if not commands:
        raise IntegrationStateError("final integration requires at least one check")
    if len(commands) != len(set(commands)):
        raise IntegrationStateError("final integration checks must not contain duplicates")
    if not requirement_progress(ledger)["final_ready"]:
        raise IntegrationStateError("final integration checks require final-ready coverage")

    canonical = ledger.repo_root.resolve()
    target = _repository_path(Path(worktree), "final integration")
    _assert_same_repository(canonical, target)
    _assert_ref_unattached(canonical, integration_ref)

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id, require_authorized=True)
        graph_children = [
            _required_text(node["child_id"], "child_id")
            for node in runtime["context"]["graph"]
        ]
        incomplete = [
            child_id
            for child_id in graph_children
            if _child_row(connection, child_id)["state"] != "integrated"
        ]
        if incomplete:
            raise IntegrationStateError(
                "final integration checks require every current child integrated"
            )
    finally:
        connection.close()

    integration_head = _git_text(
        canonical, "rev-parse", "--verify", integration_ref
    )
    integration_tree = _git_text(
        canonical, "rev-parse", f"{integration_head}^{{tree}}"
    )
    context_integration = runtime["context"]["integration"]
    if (
        context_integration["integration_head"] != integration_head
        or context_integration["integration_tree_id"] != integration_tree
    ):
        raise RecoveryError(
            "canonical context and integration ref differ at final verification"
        )
    if _git_text(target, "rev-parse", "HEAD") != integration_head:
        raise RecoveryError("final integration worktree HEAD differs from authority")
    if (
        _git_text(target, "rev-parse", f"{integration_head}^{{tree}}")
        != integration_tree
    ):
        raise RecoveryError("final integration worktree tree differs from authority")

    operation_id = f"final-integration-checks:{verification_id}"
    intent = {
        "checks": commands,
        "freshness": runtime["token"],
        "integration_head": integration_head,
        "integration_ref": integration_ref,
        "integration_tree_id": integration_tree,
        "operation_id": operation_id,
        "policy_digest": _digest_json(commands),
        "status": "prepared",
        "verification_id": verification_id,
        "worktree": str(target),
    }
    operation = ledger.prepare_operation(
        lease,
        operation_id=operation_id,
        kind=_FINAL_INTEGRATION_KIND,
        input_fingerprint=_digest_json(intent),
        intent=intent,
    )
    if operation["phase"] == "authority_committed":
        return operation["outcome"]

    if operation["phase"] == "prepared":
        results = []
        for command in commands:
            try:
                results.append(_run_parent_check(target, command))
            except ParentValidationError as exc:
                results.append(
                    {
                        "command": command,
                        "exit_code": -1,
                        "output_digest": (
                            f"sha256:{sha256(str(exc).encode('utf-8')).hexdigest()}"
                        ),
                        "status": "failed",
                    }
                )

        observed_ref = _git_text(
            canonical, "rev-parse", "--verify", integration_ref
        )
        observed_head = _git_text(target, "rev-parse", "HEAD")
        observed_tree = _git_text(target, "rev-parse", f"{observed_head}^{{tree}}")
        effect_boundary_clean = _candidate_worktree_clean(target)
        connection = ledger._connect(read_only=True)
        try:
            current = _runtime_state(
                connection, ledger.run_id, require_authorized=True
            )
        finally:
            connection.close()
        freshness_matches = current["token"] == intent["freshness"]
        identity_matches = (
            observed_ref == integration_head
            and observed_head == integration_head
            and observed_tree == integration_tree
            and current["context"]["integration"] == context_integration
        )
        if any(item["status"] != "passed" for item in results):
            root_condition = "final_integration_check_failed"
        elif not effect_boundary_clean:
            root_condition = "final_integration_effect_boundary_dirty"
        elif not identity_matches:
            root_condition = "final_integration_identity_changed"
        elif not freshness_matches:
            root_condition = "final_integration_freshness_changed"
        else:
            root_condition = None
        outcome = {
            **intent,
            "checks_result": results,
            "effect_boundary_clean": effect_boundary_clean,
            "freshness_matches": freshness_matches,
            "identity_matches": identity_matches,
            "observed_head": observed_head,
            "observed_ref": observed_ref,
            "observed_tree_id": observed_tree,
            "root_condition": root_condition,
            "status": "passed" if root_condition is None else "failed",
        }
        fingerprint = _digest_json(outcome)
        operation = ledger.advance_operation(
            lease,
            operation_id=operation_id,
            expected_phase="prepared",
            phase="effect_observed",
            output_fingerprint=fingerprint,
            outcome=outcome,
        )

    if operation["phase"] != "effect_observed":
        raise RecoveryError("final integration check operation has an invalid phase")
    outcome = operation["outcome"]
    ledger.advance_operation(
        lease,
        operation_id=operation_id,
        expected_phase="effect_observed",
        phase="authority_committed",
        output_fingerprint=_digest_json(outcome),
        outcome=outcome,
    )
    ledger.rebuild_projection(lease)
    return outcome


def record_problem_attempt(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    problem_id: str,
    round_number: int,
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
    diagnosis: str,
    action: str,
    commands: Sequence[Mapping[str, object]],
    artifact_ids: Sequence[str],
    result: str,
) -> dict[str, object]:
    """Record one stable initial failure or one of three repair rounds."""
    _required_text(problem_id, "problem_id")
    if isinstance(round_number, bool) or not isinstance(round_number, int):
        raise ProblemBudgetError("problem round must be an integer")
    if round_number < 0 or round_number > MAX_REPAIR_ROUNDS:
        raise ProblemBudgetError("problem round must be initial 0 or repair 1..3")
    operation_phase = _required_text(operation_phase, "operation_phase")
    root_condition = _required_text(root_condition, "root_condition")
    requirements = sorted(_text_list(list(requirement_ids), "requirement_ids"))
    derived_problem_id = _stable_problem_id(
        operation_phase, root_condition, requirements
    )
    diagnosis = _required_text(diagnosis, "diagnosis")
    action = _required_text(action, "action")
    artifacts = sorted(_text_list(list(artifact_ids), "artifact_ids"))
    if result not in {"passed", "failed"}:
        raise ProblemBudgetError("problem result must be passed or failed")
    command_rows = [_command_evidence(item) for item in commands]
    with ledger._write_transaction(lease) as connection:
        matching = [
            attempt
            for attempt in _problem_attempts(connection, None)
            if attempt["operation_phase"] == operation_phase
            and attempt["root_condition"] == root_condition
            and attempt["requirement_ids"] == requirements
        ]
        historical_ids = {attempt["problem_id"] for attempt in matching}
        if len(historical_ids) > 1:
            raise ProblemBudgetError("problem lineage has conflicting durable identities")
        problem_id = next(iter(historical_ids), derived_problem_id)
        generations = []
        for row in connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = ? AND phase = 'authority_committed'
            ORDER BY created_at, operation_id
            """,
            (_RECOVERY_GENERATION_KIND,),
        ).fetchall():
            generation_outcome = json.loads(row["outcome_json"])
            if generation_outcome.get("problem_id") == problem_id:
                generations.append(generation_outcome)
        generation = max(
            (int(item["recovery_generation"]) for item in generations),
            default=1,
        )
        if generation > 1:
            opened = [
                item
                for item in generations
                if int(item["recovery_generation"]) == generation
            ][-1]
            diagnosis = str(opened["diagnosis"])
            action = str(opened["action"])
        supplied = {
            "action": action,
            "artifact_ids": artifacts,
            "commands": command_rows,
            "diagnosis": diagnosis,
            "operation_phase": operation_phase,
            "problem_id": problem_id,
            "requirement_ids": requirements,
            "result": result,
            "root_condition": root_condition,
            "round": round_number,
        }
        if generation > 1:
            supplied["recovery_generation"] = generation
        operation_id = (
            f"problem:{problem_id}:round:{round_number}"
            if generation == 1
            else f"problem:{problem_id}:generation:{generation}:round:{round_number}"
        )
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            return _replay_committed(existing, _PROBLEM_KIND, supplied)
        parent = _parent_row(connection, ledger.run_id)
        recovery_resolution = parent["status"] == "paused" and result == "passed"
        if parent["status"] != "authorized" and not recovery_resolution:
            raise ProblemBudgetError(
                f"problem attempt blocked by parent status: {parent['status']}"
            )
        previous = [
            attempt
            for attempt in _problem_attempts(connection, problem_id)
            if int(attempt.get("recovery_generation", 1)) == generation
        ]
        if not previous:
            if round_number != 0 or result != "failed":
                raise ProblemBudgetError("a problem must begin with round 0 failure")
        else:
            latest = previous[-1]
            if latest["result"] == "passed" or latest["exhausted"]:
                raise ProblemBudgetError("problem is already terminal")
            if round_number != latest["round"] + 1:
                raise ProblemBudgetError("problem rounds must be contiguous")
            if (
                latest["operation_phase"] != operation_phase
                or latest["root_condition"] != root_condition
                or latest["requirement_ids"] != requirements
            ):
                raise ProblemBudgetError(
                    "worker/wording changes cannot reset problem identity"
                )
        exhausted = result == "failed" and round_number == MAX_REPAIR_ROUNDS
        outcome = {**supplied, "exhausted": exhausted, "status": "recorded"}
        now = _now()
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_PROBLEM_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="problem_attempt_recorded",
            created_at=now,
        )
        if exhausted:
            connection.execute(
                "UPDATE parent_runs SET status = 'recovery_waiting', updated_at = ? "
                "WHERE run_id = ?",
                (now, ledger.run_id),
            )
        return outcome


def open_recovery_generation(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    problem_id: str,
    diagnosis: str,
    action: str,
    integration_ref: str,
    integration_head: str,
    integration_tree_id: str,
    source_context_digest: str,
    affected_child_ids: Sequence[str],
) -> dict[str, object]:
    """Open the next bounded generation inside one exhausted parent run."""
    operation_id = _required_text(operation_id, "operation_id")
    problem_id = _required_text(problem_id, "problem_id")
    diagnosis = _required_text(diagnosis, "diagnosis")
    action = _required_text(action, "action")
    integration_ref = _integration_ref(integration_ref)
    integration_head = _required_text(integration_head, "integration_head")
    integration_tree_id = _required_text(integration_tree_id, "integration_tree_id")
    source_context_digest = _required_text(
        source_context_digest, "source_context_digest"
    )
    affected_children = sorted(
        set(_text_list(list(affected_child_ids), "affected_child_ids"))
    )
    if not affected_children:
        raise ProblemBudgetError("continue recovery requires affected children")
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            outcome = json.loads(existing["outcome_json"])
            supplied = {
                "action": action,
                "affected_child_ids": affected_children,
                "diagnosis": diagnosis,
                "from_generation": outcome["from_generation"],
                "integration_head": integration_head,
                "integration_ref": integration_ref,
                "integration_tree_id": integration_tree_id,
                "problem_id": problem_id,
                "recovery_generation": outcome["recovery_generation"],
                "requirement_ids": outcome["requirement_ids"],
                "source_context_digest": source_context_digest,
            }
            return _replay_committed(
                existing,
                _RECOVERY_GENERATION_KIND,
                supplied,
            )
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] != "recovery_waiting":
            raise ProblemBudgetError(
                "continue recovery requires a recovery_waiting parent"
            )
        attempts = _problem_attempts(connection, problem_id)
        if not attempts:
            raise ProblemBudgetError("continue recovery requires durable problem history")
        latest = attempts[-1]
        if latest["result"] != "failed" or latest["exhausted"] is not True:
            raise ProblemBudgetError("continue recovery requires an exhausted problem")
        current = _latest_context_row(connection)
        if current is None or current["digest"] != source_context_digest:
            raise ProblemBudgetError("continue recovery source context is not current")
        context = json.loads(current["context_json"])
        expected_children = sorted(
            str(node["child_id"])
            for node in context["graph"]
            if set(str(item) for item in latest["requirement_ids"]).intersection(
                str(item) for item in node["requirements"]
            )
        )
        if affected_children != expected_children:
            raise ProblemBudgetError(
                "continue recovery affected children differ from the exhausted scope"
            )
        context_integration = context["integration"]
        actual_head = _git_text(
            ledger.repo_root, "rev-parse", "--verify", integration_ref
        )
        actual_tree = _git_text(
            ledger.repo_root, "rev-parse", f"{actual_head}^{{tree}}"
        )
        if (
            integration_head != actual_head
            or integration_tree_id != actual_tree
            or context_integration["integration_head"] != actual_head
            or context_integration["integration_tree_id"] != actual_tree
        ):
            raise ProblemBudgetError(
                "continue recovery integration identity is not current"
            )
        from_generation = int(latest.get("recovery_generation", 1))
        supplied = {
            "action": action,
            "affected_child_ids": affected_children,
            "diagnosis": diagnosis,
            "from_generation": from_generation,
            "integration_head": integration_head,
            "integration_ref": integration_ref,
            "integration_tree_id": integration_tree_id,
            "problem_id": problem_id,
            "recovery_generation": from_generation + 1,
            "requirement_ids": list(latest["requirement_ids"]),
            "source_context_digest": source_context_digest,
        }
        outcome = {**supplied, "status": "opened"}
        now = _now()
        connection.execute(
            "UPDATE parent_runs SET status = 'authorized', updated_at = ? "
            "WHERE run_id = ?",
            (now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_RECOVERY_GENERATION_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="recovery_generation_opened",
            created_at=now,
        )
        return outcome


def record_replacement_guidance(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    problem_id: str,
    problem_round: int,
    failed_result_id: str,
    source_dispatch_id: str,
    problem_attempt_evidence_digest: str,
    source_context_digest: str,
    source_graph_digest: str,
    failed_child_id: str,
    prerequisite_child_ids: Sequence[str],
) -> dict[str, object]:
    """Bind one explicit, path-closed prerequisite slice to a worker failure."""
    operation_id = _required_text(operation_id, "operation_id")
    problem_id = _required_text(problem_id, "problem_id")
    failed_result_id = _required_text(failed_result_id, "failed_result_id")
    source_dispatch_id = _required_text(source_dispatch_id, "source_dispatch_id")
    problem_attempt_evidence_digest = _required_text(
        problem_attempt_evidence_digest, "problem_attempt_evidence_digest"
    )
    source_context_digest = _required_text(
        source_context_digest, "source_context_digest"
    )
    source_graph_digest = _required_text(source_graph_digest, "source_graph_digest")
    failed_child_id = _required_text(failed_child_id, "failed_child_id")
    if isinstance(problem_round, bool) or not isinstance(problem_round, int):
        raise RecoveryError("problem_round must be an integer")
    prerequisites = _text_list(
        list(prerequisite_child_ids), "prerequisite_child_ids"
    )
    if not prerequisites:
        raise RecoveryError("prerequisite_child_ids must not be empty")

    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            stored_prerequisites = list(
                json.loads(existing["outcome_json"]).get(
                    "prerequisite_child_ids", []
                )
            )
            canonical_prerequisites = (
                stored_prerequisites
                if set(stored_prerequisites) == set(prerequisites)
                else prerequisites
            )
            supplied = {
                "failed_child_id": failed_child_id,
                "failed_result_id": failed_result_id,
                "operation_id": operation_id,
                "prerequisite_child_ids": canonical_prerequisites,
                "problem_attempt_evidence_digest": problem_attempt_evidence_digest,
                "problem_id": problem_id,
                "problem_round": problem_round,
                "source_context_digest": source_context_digest,
                "source_dispatch_id": source_dispatch_id,
                "source_graph_digest": source_graph_digest,
            }
            return _replay_committed(existing, _REPLACEMENT_GUIDANCE_KIND, supplied)
        current = _latest_context_row(connection)
        if current is None:
            raise FreshnessError("replacement guidance requires canonical context")
        context = json.loads(current["context_json"])
        graph = list(context["graph"])
        graph_order = {
            str(node["child_id"]): index for index, node in enumerate(graph)
        }
        canonical_prerequisites = sorted(
            prerequisites, key=lambda child_id: graph_order.get(child_id, len(graph))
        )
        supplied = {
            "failed_child_id": failed_child_id,
            "failed_result_id": failed_result_id,
            "operation_id": operation_id,
            "prerequisite_child_ids": canonical_prerequisites,
            "problem_attempt_evidence_digest": problem_attempt_evidence_digest,
            "problem_id": problem_id,
            "problem_round": problem_round,
            "source_context_digest": source_context_digest,
            "source_dispatch_id": source_dispatch_id,
            "source_graph_digest": source_graph_digest,
        }
        prior_guidance = []
        for row in connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = ? AND phase = 'authority_committed'
            """,
            (_REPLACEMENT_GUIDANCE_KIND,),
        ).fetchall():
            outcome = json.loads(row["outcome_json"])
            if (
                outcome.get("problem_id") == problem_id
                and int(outcome.get("problem_round", -1)) == problem_round
                and outcome.get("source_context_digest") == source_context_digest
                and outcome.get("source_graph_digest") == source_graph_digest
            ):
                prior_guidance.append(outcome)
        if prior_guidance:
            raise OperationConflict(
                "replacement failure attempt already has accepted guidance"
            )
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] != "authorized":
            raise RecoveryError(
                f"replacement guidance blocked by parent status: {parent['status']}"
            )
        if (
            current["digest"] != source_context_digest
            or current["graph_digest"] != source_graph_digest
        ):
            raise FreshnessError("replacement guidance source context is stale")
        attempts = _problem_attempts(connection, problem_id)
        if not attempts:
            raise ProblemBudgetError("replacement guidance requires a recorded problem")
        problem = attempts[-1]
        if (
            problem["result"] != "failed"
            or problem["exhausted"]
            or problem["round"] != problem_round
            or problem["operation_phase"] != "worker_result"
            or problem["root_condition"] != "worker_required_test_failed"
        ):
            raise ProblemBudgetError(
                "replacement guidance requires the current worker test failure"
            )
        quiescent = connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = 'worker_dispatch_quiesced'
              AND phase = 'authority_committed'
            """
        ).fetchall()
        quiescent_outcomes = [
            json.loads(row["outcome_json"])
            for row in quiescent
            if json.loads(row["outcome_json"]).get("action_id") == source_dispatch_id
        ]
        if len(quiescent_outcomes) != 1:
            raise RecoveryError("replacement guidance source dispatch is not quiescent")
        observations = []
        for row in connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = 'recovery_failure_observed'
              AND phase = 'authority_committed'
            ORDER BY operation_id
            """
        ).fetchall():
            outcome = json.loads(row["outcome_json"])
            if (
                outcome.get("operation_phase") == problem["operation_phase"]
                and outcome.get("root_condition") == problem["root_condition"]
                and outcome.get("requirement_ids") == problem["requirement_ids"]
                and outcome.get("graph_digest") == source_graph_digest
                and int(outcome.get("problem_round", -1)) == problem_round
                and int(outcome.get("recovery_generation", 1))
                == int(problem.get("recovery_generation", 1))
            ):
                observations.append(outcome)
        observations = _canonical_recovery_observations(observations)
        if _digest_json(observations) != problem_attempt_evidence_digest:
            raise FreshnessError("replacement guidance failure evidence is stale")
        failed_children = sorted(
            {
                str(child_id)
                for outcome in observations
                for child_id in outcome.get("affected_child_ids", [])
            }
        )
        if failed_children != [failed_child_id]:
            raise RecoveryError(
                "replacement guidance problem must bind one current failed child"
            )
        if not any(
            failed_result_id in outcome.get("artifact_ids", [])
            and source_dispatch_id in outcome.get("artifact_ids", [])
            for outcome in observations
        ):
            raise FreshnessError(
                "replacement guidance result or dispatch identity is stale"
            )
        nodes = {str(node["child_id"]): node for node in graph}
        if failed_child_id not in nodes:
            raise FreshnessError("replacement guidance failed child is not current")
        unknown = sorted(set(prerequisites) - set(nodes))
        if unknown:
            raise RecoveryError(
                "replacement guidance references unknown current children: "
                + ", ".join(unknown)
            )
        states = {
            row["child_id"]: row["state"]
            for row in connection.execute(
                "SELECT child_id, state FROM child_operations"
            ).fetchall()
        }
        if states.get(failed_child_id) != "dispatched":
            raise FreshnessError("replacement guidance failed child is not current")
        if any(states.get(child_id) != "integrated" for child_id in prerequisites):
            raise RecoveryError(
                "replacement guidance prerequisites must be current integrated children"
            )
        ancestors: set[str] = set()
        frontier = list(nodes[failed_child_id]["depends_on"])
        while frontier:
            child_id = str(frontier.pop())
            if child_id in ancestors:
                continue
            ancestors.add(child_id)
            frontier.extend(nodes[child_id]["depends_on"])
        if not set(prerequisites).issubset(ancestors):
            raise RecoveryError(
                "replacement guidance prerequisites must be transitive ancestors"
            )
        dependents = {child_id: set() for child_id in nodes}
        for child_id, node in nodes.items():
            for dependency in node["depends_on"]:
                dependents[str(dependency)].add(child_id)
        slice_ids = set(prerequisites) | {failed_child_id}
        for prerequisite in prerequisites:
            descendants: set[str] = set()
            frontier = list(dependents[prerequisite])
            while frontier:
                child_id = frontier.pop()
                if child_id in descendants:
                    continue
                descendants.add(child_id)
                frontier.extend(dependents[child_id])
            if not descendants.issubset(slice_ids):
                raise RecoveryError(
                    "replacement guidance leaves a prerequisite descendant outside the slice"
                )
        coverage = sorted(
            {
                str(requirement)
                for child_id in slice_ids
                for requirement in nodes[child_id]["requirements"]
            }
        )
        for child_id, node in nodes.items():
            if child_id not in slice_ids and set(coverage).intersection(
                str(requirement) for requirement in node["requirements"]
            ):
                raise RecoveryError(
                    "replacement guidance coverage overlaps an unselected child"
                )
        outcome = {
            **supplied,
            "expanded_child_ids": [
                child_id for child_id in graph_order if child_id in slice_ids
            ],
            "expanded_requirement_ids": coverage,
            "repair_round": problem_round + 1,
            "status": "accepted",
        }
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_REPLACEMENT_GUIDANCE_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="replacement_guidance_recorded",
            created_at=_now(),
        )
        return outcome


def prepare_replacement_revision(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    problem_id: str,
    source_context_digest: str,
    replaced_child_ids: Sequence[str],
    replacement_graph: Sequence[Mapping[str, object]],
    expected_replacement_coverage: Sequence[str] | None = None,
) -> dict[str, object]:
    """Record one deterministic in-envelope graph replacement for failed work."""
    request_id = _required_text(request_id, "request_id")
    problem_id = _required_text(problem_id, "problem_id")
    source_context_digest = _required_text(
        source_context_digest, "source_context_digest"
    )
    replaced = sorted(set(_text_list(list(replaced_child_ids), "replaced_child_ids")))
    if not replaced:
        raise RecoveryError("replacement requires at least one prior child")
    graph = _validate_graph(copy.deepcopy(list(replacement_graph)))
    replacement_digest = _digest_json(
        {
            "graph": graph,
            "problem_id": problem_id,
            "replaced_child_ids": replaced,
        }
    )
    revision_id = f"recovery-replacement-{replacement_digest[:32]}"
    reason = f"recovery replacement for {problem_id}: {','.join(replaced)}"

    connection = ledger._connect(read_only=True)
    try:
        existing = connection.execute(
            "SELECT * FROM context_revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if existing is not None:
            existing_context = json.loads(existing["context_json"])
            if (
                existing["previous_digest"] != source_context_digest
                or existing["reason"] != reason
                or existing_context["graph"] != graph
            ):
                raise OperationConflict(
                    "replacement identity belongs to another context revision"
                )
            source = connection.execute(
                "SELECT context_json FROM context_revisions WHERE digest = ?",
                (source_context_digest,),
            ).fetchone()
            if source is None:
                raise RecoveryError("replacement source context evidence is missing")
            source_ids = {
                item["child_id"] for item in json.loads(source["context_json"])["graph"]
            }
            current_ids = {item["child_id"] for item in graph}
            return {
                "context_revision": _context_record(existing),
                "problem_id": problem_id,
                "replaced_child_ids": replaced,
                "replacement_child_ids": sorted(current_ids - source_ids),
                "status": "prepared",
            }

        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] != "authorized":
            raise RecoveryError(
                f"replacement blocked by parent status: {parent['status']}"
            )
        current = _latest_context_row(connection)
        if current is None:
            raise FreshnessError("replacement requires canonical context")
        if current["digest"] != source_context_digest:
            raise FreshnessError("replacement source context is stale")
        attempts = _problem_attempts(connection, problem_id)
        if not attempts:
            raise ProblemBudgetError("replacement requires a recorded problem")
        latest = attempts[-1]
        if latest["result"] != "failed" or latest["exhausted"]:
            raise ProblemBudgetError("replacement problem is not repairable")
        context = json.loads(current["context_json"])
        old_nodes = {item["child_id"]: item for item in context["graph"]}
        new_nodes = {item["child_id"]: item for item in graph}
        missing = sorted(set(replaced) - set(old_nodes))
        if missing:
            raise RecoveryError(
                f"replacement references unknown prior children: {', '.join(missing)}"
            )
        if set(replaced).intersection(new_nodes):
            raise RecoveryError("replacement cannot revive a prior child identity")
        replacements = sorted(set(new_nodes) - set(old_nodes))
        if not replacements:
            raise RecoveryError("replacement graph adds no new child identity")
        if len(graph) != len(context["graph"]) or len(replacements) != len(replaced):
            raise InterventionRequired(
                "replacement must preserve graph shape with one identity per failed child"
            )
        replacement_map: dict[str, str] = {}
        for old_node, new_node in zip(context["graph"], graph, strict=True):
            old_child_id = str(old_node["child_id"])
            new_child_id = str(new_node["child_id"])
            if old_child_id in replaced:
                if new_child_id not in replacements:
                    raise InterventionRequired(
                        "replacement must preserve failed child graph position"
                    )
                replacement_map[old_child_id] = new_child_id
            elif new_child_id != old_child_id:
                raise InterventionRequired(
                    "replacement cannot reorder unaffected children"
                )
        if set(replacement_map) != set(replaced) or set(replacement_map.values()) != set(
            replacements
        ):
            raise InterventionRequired("replacement child mapping is not one-to-one")
        for old_child_id, new_child_id in replacement_map.items():
            old_node = old_nodes[old_child_id]
            new_node = new_nodes[new_child_id]
            if any(
                old_node[field] != new_node[field]
                for field in ("requirements", "touches", "resources")
            ):
                raise InterventionRequired(
                    f"replacement changes failed child contract: {old_child_id}"
                )
            expected_dependencies = {
                replacement_map.get(str(dependency), str(dependency))
                for dependency in old_node["depends_on"]
            }
            if set(new_node["depends_on"]) != expected_dependencies:
                raise InterventionRequired(
                    f"replacement changes failed child dependencies: {old_child_id}"
                )
        retained = sorted(set(old_nodes) - set(replaced))
        missing_retained = [child_id for child_id in retained if child_id not in new_nodes]
        if missing_retained:
            raise InterventionRequired(
                "replacement cannot remove unaffected children: "
                + ", ".join(missing_retained)
            )
        for child_id in retained:
            old_node = old_nodes[child_id]
            new_node = new_nodes[child_id]
            if any(
                old_node[field] != new_node[field]
                for field in ("requirements", "touches", "resources")
            ):
                raise InterventionRequired(
                    f"replacement changes unaffected child contract: {child_id}"
                )
            expected_dependencies = {
                replacement_map.get(str(dependency), str(dependency))
                for dependency in old_node["depends_on"]
            }
            if set(new_node["depends_on"]) != expected_dependencies:
                raise InterventionRequired(
                    f"replacement changes unaffected child dependencies: {child_id}"
                )
        old_coverage = {
            requirement
            for child_id in replaced
            for requirement in old_nodes[child_id]["requirements"]
        }
        new_coverage = {
            requirement
            for child_id in replacements
            for requirement in new_nodes[child_id]["requirements"]
        }
        if old_coverage != new_coverage:
            raise InterventionRequired(
                "replacement child coverage must remain unchanged"
            )
        problem_coverage = set(latest["requirement_ids"])
        guided_coverage = (
            set(
                _text_list(
                    list(expected_replacement_coverage),
                    "expected_replacement_coverage",
                )
            )
            if expected_replacement_coverage is not None
            else None
        )
        if guided_coverage is not None:
            if latest["operation_phase"] != "worker_result":
                raise InterventionRequired(
                    "guided replacement requires a worker-result problem"
                )
            if guided_coverage != old_coverage:
                raise InterventionRequired(
                    "guided replacement coverage differs from accepted guidance"
                )
        elif latest["operation_phase"] == "final_review":
            if not problem_coverage or not problem_coverage.issubset(old_coverage):
                raise InterventionRequired(
                    "final-review problem coverage must be a non-empty subset of "
                    "replacement child coverage"
                )
        elif problem_coverage != old_coverage:
            raise InterventionRequired(
                "replacement coverage must match the recorded problem"
            )
        rows = connection.execute(
            f"SELECT child_id, state FROM child_operations WHERE child_id IN ({','.join('?' for _ in replaced)})",
            replaced,
        ).fetchall()
        states = {row["child_id"]: row["state"] for row in rows}
        if set(states) != set(replaced):
            raise RecoveryError("replacement prior child lacks durable evidence")
        for child_id, state in states.items():
            phase_states = {
                "final_integration_checks": {"integrated"},
                "final_review": {"integrated"},
                "integration_candidate": {"committed"},
                "precommit_review": {"review_blocked"},
                "resume_stale": {"stale"},
                "worker_result": {"dispatched"},
                "worker_validation": {"result_validated"},
            }
            allowed_states = {"stale", "invalidated"} | phase_states.get(
                str(latest["operation_phase"]), set()
            )
            if expected_replacement_coverage is not None:
                allowed_states.add("integrated")
            if state not in allowed_states:
                raise RecoveryError(
                    f"child is not replaceable in state {state}: {child_id}"
                )
        reused = connection.execute(
            f"SELECT child_id FROM child_operations WHERE child_id IN ({','.join('?' for _ in replacements)})",
            replacements,
        ).fetchall()
        if reused:
            raise OperationConflict("replacement child identity already has evidence")
    finally:
        connection.close()

    context["graph"] = graph
    problem_requirements = (
        set(expected_replacement_coverage)
        if expected_replacement_coverage is not None
        else set(latest["requirement_ids"])
    )
    requirement_states = {
        item["requirement_id"]: item["coverage_state"]
        for item in context["requirements"]
    }
    for requirement in context["requirements"]:
        if requirement["requirement_id"] in problem_requirements:
            if requirement["coverage_state"] != "uncovered":
                requirement["coverage_state"] = "uncovered"
                requirement["revision"] = int(requirement["revision"]) + 1
            requirement_states[requirement["requirement_id"]] = "uncovered"
    for requirement in context["requirements"]:
        requirement_id = requirement["requirement_id"]
        if requirement_id not in problem_requirements:
            continue
        context["dependency_state"][requirement_id] = (
            "ready"
            if all(
                requirement_states[dependency] in {"covered", "omitted"}
                for dependency in requirement["dependencies"]
            )
            else "blocked"
        )
    record = record_context_revision(
        ledger,
        lease,
        request_id=request_id,
        revision_id=revision_id,
        reason=reason,
        context=context,
        expected_previous_digest=source_context_digest,
    )
    return {
        "context_revision": record,
        "problem_id": problem_id,
        "replaced_child_ids": replaced,
        "replacement_child_ids": replacements,
        "status": "prepared",
    }


def pause_parent(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    reason: str,
    requested_by: str,
    requires_human_resume: bool,
) -> dict[str, object]:
    """Pause at the current recorded boundary without cleaning any evidence."""
    operation_id = _required_text(operation_id, "operation_id")
    reason = _required_text(reason, "reason")
    requested_by = _required_text(requested_by, "requested_by")
    if not isinstance(requires_human_resume, bool):
        raise ControlError("requires_human_resume must be boolean")
    supplied = {
        "reason": reason,
        "requested_by": requested_by,
        "requires_human_resume": requires_human_resume,
    }
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            return _replay_committed(existing, "parent_pause", supplied)
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] not in {"authorized", "paused"}:
            raise ControlError(f"cannot pause parent in status {parent['status']}")
        in_flight = _unresolved_operations(connection)
        outcome = {
            **supplied,
            "epoch": lease.epoch,
            "in_flight": in_flight,
            "preserved": True,
            "status": "paused",
        }
        now = _now()
        connection.execute(
            "UPDATE parent_runs SET status = 'paused', updated_at = ? WHERE run_id = ?",
            (now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="parent_pause",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="parent_paused",
            created_at=now,
        )
        return outcome


def reconcile_parent(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    allow_ref_advance: bool = True,
    expected_qualification_rotation_proof: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Reconcile old-epoch Git intents under the active recovery fence."""
    _assert_parent_status(ledger, "paused", "reconciliation")
    connection = ledger._connect(read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT * FROM operations
            WHERE phase IN ('prepared', 'effect_observed')
            ORDER BY created_at, operation_id
            """
        ).fetchall()
    finally:
        connection.close()
    actions = []
    unresolved = []
    for raw in rows:
        operation = _operation_dict(raw)
        try:
            if operation["kind"] == _INTEGRATION_KIND:
                action = _recover_integration_operation(
                    ledger,
                    lease,
                    operation,
                    allow_ref_advance=allow_ref_advance,
                )
            elif operation["kind"] == "child_commit":
                action = _recover_child_commit(ledger, lease, operation)
            elif operation["kind"] == "final_local_merge":
                from .acceptance import recover_final_merge

                action = recover_final_merge(
                    ledger,
                    lease,
                    operation,
                    expected_qualification_rotation_proof=(
                        expected_qualification_rotation_proof
                    ),
                )
            else:
                raise RecoveryError(
                    f"unsupported unresolved operation kind: {operation['kind']}"
                )
            actions.append(action)
        except (ContextError, GitStateError, OperationConflict) as exc:
            unresolved.append(
                {"operation_id": operation["operation_id"], "reason": str(exc)}
            )
    if not unresolved:
        ledger.rebuild_projection(lease)
    return {
        "actions": actions,
        "epoch": lease.epoch,
        "status": "reconciled" if not unresolved else "unresolved",
        "unresolved": unresolved,
    }


def _resume_safe_point(
    ledger: ParentLedger, envelope: Mapping[str, object]
) -> dict[str, object]:
    dirt = scan_repository_dirt(ledger.repo_root)
    main = _main_state(ledger.repo_root, envelope["base_head"])
    predicates: dict[str, bool | None] = {
        "canonical_dirt_clear": True,
        "committed_drift_clear": None,
        "current_children_integrated": None,
        "final_ready": None,
        "integration_head_retained": None,
        "unresolved_operations_clear": None,
        "unresolved_operations_reconcilable": None,
    }
    try:
        _assert_main_isolated(
            {**main, "drift_paths": []},
            envelope["allowed_touches"],
        )
    except DirtOverlapError:
        predicates["canonical_dirt_clear"] = False

    closeout_exception_required = False
    integration_head = None
    qualification_rotation_proof = None
    unresolved_operations = []
    if predicates["canonical_dirt_clear"]:
        try:
            _assert_main_isolated(main, envelope["allowed_touches"])
            predicates["committed_drift_clear"] = True
        except DirtOverlapError:
            predicates["committed_drift_clear"] = False
            closeout_exception_required = True
            connection = ledger._connect(read_only=True)
            try:
                runtime = _runtime_state(
                    connection,
                    ledger.run_id,
                    require_authorized=False,
                )
                unresolved = _unresolved_operations(connection)
                child_states = {
                    row["child_id"]: row["state"]
                    for row in connection.execute(
                        "SELECT child_id, state FROM child_operations"
                    ).fetchall()
                }
            finally:
                connection.close()
            unresolved_operations = [
                {
                    "epoch": operation["epoch"],
                    "kind": operation["kind"],
                    "operation_id": operation["operation_id"],
                    "phase": operation["phase"],
                }
                for operation in unresolved
            ]
            predicates["unresolved_operations_clear"] = not unresolved
            predicates["unresolved_operations_reconcilable"] = all(
                _unresolved_operation_reconcilable(
                    ledger,
                    operation,
                    envelope["allowed_touches"],
                )
                for operation in unresolved
            )
            if predicates["unresolved_operations_reconcilable"]:
                proof_states = [
                    _final_merge_recovery_state_for_operation(
                        ledger,
                        operation,
                        envelope["allowed_touches"],
                    )
                    for operation in unresolved
                    if operation["kind"] == "final_local_merge"
                ]
                proofs = [
                    state["qualification_rotation_proof"]
                    for state in proof_states
                    if state["qualification_rotation_proof"] is not None
                ]
                if len(proofs) > 1:
                    predicates["unresolved_operations_reconcilable"] = False
                elif proofs:
                    qualification_rotation_proof = proofs[0]
            if predicates["unresolved_operations_reconcilable"]:
                predicates["current_children_integrated"] = all(
                    child_states.get(node["child_id"]) == "integrated"
                    for node in runtime["context"]["graph"]
                )
                if predicates["current_children_integrated"]:
                    predicates["final_ready"] = requirement_progress(ledger)[
                        "final_ready"
                    ]
                    if predicates["final_ready"]:
                        integration_head = runtime["context"]["integration"][
                            "integration_head"
                        ]
                        predicates["integration_head_retained"] = _is_ancestor(
                            ledger.repo_root, integration_head, main["head"]
                        )

    failed = []
    if not predicates["canonical_dirt_clear"]:
        failed.append("canonical_dirt_clear")
    elif closeout_exception_required:
        failed.extend(
            name
            for name in (
                "unresolved_operations_reconcilable",
                "current_children_integrated",
                "final_ready",
                "integration_head_retained",
            )
            if predicates[name] is False
        )
    return {
        "dirt": dirt,
        "integration_head": integration_head,
        "main": main,
        "projection": {
            "closeout_exception_required": closeout_exception_required,
            "eligible": not failed,
            "failed_predicates": failed,
            "predicates": predicates,
            "qualification_rotation_proof": qualification_rotation_proof,
            "qualification_rotation_proof_digest": (
                qualification_rotation_proof["proof_digest"]
                if qualification_rotation_proof is not None
                else None
            ),
            "unresolved_operations": unresolved_operations,
        },
    }


def _unresolved_operation_reconcilable(
    ledger: ParentLedger,
    operation: Mapping[str, object],
    allowed_touches: Sequence[str],
) -> bool:
    if operation["kind"] not in _RECOVERABLE_OPERATION_KINDS:
        return False
    if operation["kind"] != "final_local_merge":
        return True
    try:
        _final_merge_recovery_state_for_operation(
            ledger,
            operation,
            allowed_touches,
        )
    except (ContextError, GitStateError):
        return False
    return True


def _final_merge_recovery_state_for_operation(
    ledger: ParentLedger,
    operation: Mapping[str, object],
    allowed_touches: Sequence[str],
) -> dict[str, object]:
    from .acceptance import (
        _execution_binding_for_recovery,
        _final_merge_recovery_state,
    )

    full_operation = ledger.get_operation(str(operation["operation_id"]))
    if full_operation is None:
        raise RecoveryError("final merge recovery operation is missing")
    return _final_merge_recovery_state(
        ledger.repo_root,
        full_operation,
        allowed_touches,
        execution_binding=_execution_binding_for_recovery(ledger),
    )


def _record_resume_preflight(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    supplied: Mapping[str, object],
) -> dict[str, object]:
    preflight_id = f"resume-preflight:{operation_id}"
    outcome = {**supplied, "status": "verified"}
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (preflight_id,),
        ).fetchone()
        if existing is not None:
            return _replay_committed(
                existing,
                "parent_resume_preflight",
                supplied,
            )
        now = _now()
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=preflight_id,
            kind="parent_resume_preflight",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(dict(supplied)),
            outcome=outcome,
            event_type="parent_resume_preflight_verified",
            created_at=now,
        )
    return outcome


def resume_parent(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    new_writer_id: str,
    authority_identity: str,
    direct_user_action: bool,
    tool_receipt: str,
    available_resources: Sequence[str],
) -> tuple[dict[str, object], WriterLease]:
    """Continue one durable resume attempt until resumed or recovery waiting."""
    operation_id = _required_text(operation_id, "operation_id")
    new_writer_id = _required_text(new_writer_id, "new_writer_id")
    authority_identity = _required_text(authority_identity, "authority_identity")
    tool_receipt = _required_text(tool_receipt, "tool_receipt")
    if not isinstance(direct_user_action, bool):
        raise ControlError("direct_user_action must be boolean")
    resources = sorted(_text_list(list(available_resources), "available_resources"))
    request = {
        "authority_identity": authority_identity,
        "available_resources": resources,
        "direct_user_action": direct_user_action,
        "new_writer_id": new_writer_id,
        "request_operation_id": operation_id,
        "tool_receipt": tool_receipt,
    }
    existing_attempt = _resume_attempt_for_request(ledger, operation_id)
    if existing_attempt is not None:
        return _continue_resume_attempt(ledger, lease, existing_attempt, request)

    connection = ledger._connect(read_only=True)
    try:
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] != "paused":
            raise ControlError("resume requires a paused parent")
        envelope, _ = _approved_envelope(connection, parent["start_gate_ref"])
        pause = _latest_pause(connection)
    finally:
        connection.close()
    requires_human = pause is None or pause.get("requires_human_resume", True)
    if requires_human and not direct_user_action:
        raise ControlError("this pause requires direct human resume authority")
    if tool_receipt != envelope["conformance_receipt"]:
        raise FreshnessError("resume tool/conformance receipt differs from start")
    approved_resources = sorted(item["resource_key"] for item in envelope["resources"])
    if sorted(item.casefold() for item in resources) != sorted(
        item.casefold() for item in approved_resources
    ):
        raise FreshnessError("resume resources differ from the approved envelope")
    safe_point = _resume_safe_point(ledger, envelope)
    dirt = safe_point["dirt"]
    main = safe_point["main"]
    if not safe_point["projection"]["predicates"]["canonical_dirt_clear"]:
        _assert_main_isolated(
            {**main, "drift_paths": []},
            envelope["allowed_touches"],
        )
    retained_integration_head = None
    if safe_point["projection"]["closeout_exception_required"]:
        if not safe_point["projection"]["eligible"]:
            _assert_main_isolated(main, envelope["allowed_touches"])
        retained_integration_head = safe_point["integration_head"]
    qualification_rotation_proof = safe_point["projection"][
        "qualification_rotation_proof"
    ]
    qualification_rotation_proof_digest = safe_point["projection"][
        "qualification_rotation_proof_digest"
    ]
    unresolved_operations = safe_point["projection"]["unresolved_operations"]
    attempt_identity = {
        "authority_digest": f"sha256:{ledger.authority_digest()}",
        "dirt_digest": dirt["digest"],
        "from_epoch": lease.epoch,
        "main_head": main["head"],
        "main_state_digest": main["digest"],
        "qualification_rotation_proof_digest": qualification_rotation_proof_digest,
        "run_id": ledger.run_id,
        "unresolved_operations_digest": (
            f"sha256:{_digest_json(unresolved_operations)}"
        ),
    }
    attempt_id = f"resume-attempt:{_digest_json(attempt_identity)}"
    attempt = ledger.prepare_operation(
        lease,
        operation_id=attempt_id,
        kind="resume_attempt",
        input_fingerprint=_digest_json({**attempt_identity, "request": request}),
        intent={
            **attempt_identity,
            "attempt_id": attempt_id,
            "failure_count": 0,
            "failure_fingerprint": None,
            "qualification_rotation_proof": qualification_rotation_proof,
            "request": request,
            "retained_integration_head": retained_integration_head,
            "stage": "preflight_verified",
        },
    )
    if qualification_rotation_proof_digest is not None:
        _record_resume_preflight(
            ledger,
            lease,
            operation_id=operation_id,
            supplied={
                "dirt_digest": dirt["digest"],
                "from_epoch": lease.epoch,
                "main_head": main["head"],
                "main_state_digest": main["digest"],
                "qualification_rotation_proof_digest": (
                    qualification_rotation_proof_digest
                ),
                "retained_integration_head": retained_integration_head,
            },
        )
    return _continue_resume_attempt(ledger, lease, attempt, request)


def _resume_attempt_for_request(
    ledger: ParentLedger, request_operation_id: str
) -> dict[str, object] | None:
    connection = ledger._connect(read_only=True)
    try:
        matches = []
        for row in connection.execute(
            "SELECT * FROM operations WHERE kind = 'resume_attempt' "
            "ORDER BY created_at, operation_id"
        ).fetchall():
            operation = _operation_dict(row)
            request = operation["outcome"].get("request")
            if (
                isinstance(request, Mapping)
                and request.get("request_operation_id") == request_operation_id
            ):
                matches.append(operation)
    finally:
        connection.close()
    if len(matches) > 1:
        raise OperationConflict("resume request maps to multiple durable attempts")
    return matches[0] if matches else None


def _active_lease(ledger: ParentLedger) -> WriterLease:
    connection = ledger._connect(read_only=True)
    try:
        return ledger._lease_from_row(ledger._parent_row(connection))
    finally:
        connection.close()


def _resume_attempt_after_rotation(
    ledger: ParentLedger,
    lease: WriterLease,
    attempt: Mapping[str, object],
) -> dict[str, object]:
    operation_id = str(attempt["operation_id"])
    with ledger._write_transaction(lease) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise OperationConflict("durable resume attempt is missing")
        operation = _operation_dict(row)
        if operation["phase"] != "prepared":
            return operation
        outcome = {
            **operation["outcome"],
            "stage": "fence_rotated",
            "to_epoch": lease.epoch,
        }
        now = _now()
        connection.execute(
            """
            UPDATE operations
            SET phase = 'effect_observed', epoch = ?, output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase = 'prepared'
            """,
            (
                lease.epoch,
                _digest_json(outcome),
                _canonical_json(outcome),
                now,
                operation_id,
            ),
        )
        ledger._insert_event(
            connection,
            operation_id=operation_id,
            event_type="resume_fence_rotated",
            phase="effect_observed",
            epoch=lease.epoch,
            payload={"stage": "fence_rotated", "to_epoch": lease.epoch},
            created_at=now,
        )
        return {
            **operation,
            "epoch": lease.epoch,
            "outcome": outcome,
            "phase": "effect_observed",
        }


def _resume_attempt_reconciling(
    ledger: ParentLedger,
    lease: WriterLease,
    attempt: Mapping[str, object],
) -> dict[str, object]:
    if attempt["phase"] != "effect_observed":
        return dict(attempt)
    operation_id = str(attempt["operation_id"])
    with ledger._write_transaction(lease) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise OperationConflict("durable resume attempt is missing")
        operation = _operation_dict(row)
        if operation["phase"] != "effect_observed":
            return operation
        now = _now()
        outcome = {
            **operation["outcome"],
            "stage": "reconciling",
        }
        connection.execute(
            """
            UPDATE operations
            SET phase = 'authority_committed', output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase = 'effect_observed' AND epoch = ?
            """,
            (
                _digest_json(outcome),
                _canonical_json(outcome),
                now,
                operation_id,
                lease.epoch,
            ),
        )
        ledger._insert_event(
            connection,
            operation_id=operation_id,
            event_type="resume_reconciling",
            phase="authority_committed",
            epoch=lease.epoch,
            payload={"stage": "reconciling"},
            created_at=now,
        )
        return {
            **operation,
            "outcome": outcome,
            "phase": "authority_committed",
        }


def _finish_resume_attempt(
    ledger: ParentLedger,
    lease: WriterLease,
    attempt: Mapping[str, object],
    reconciliation: Mapping[str, object],
) -> dict[str, object]:
    operation_id = str(attempt["operation_id"])
    with ledger._write_transaction(lease) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise OperationConflict("durable resume attempt is missing")
        current = _operation_dict(row)
        if current["phase"] == "projected":
            return current["outcome"]
        if current["phase"] != "authority_committed":
            raise OperationConflict("resume attempt is not reconciling")
        prior = current["outcome"]
        stale_children = prior.get("stale_children")
        if not isinstance(stale_children, list):
            stale_rows = connection.execute(
                """
                SELECT child_id FROM child_operations
                WHERE epoch < ?
                  AND state IN (
                      'dispatched', 'result_validated',
                      'candidate_validated', 'reviewed'
                  )
                ORDER BY child_id
                """,
                (lease.epoch,),
            ).fetchall()
            stale_children = [row["child_id"] for row in stale_rows]
            if stale_children:
                placeholders = ",".join("?" for _ in stale_children)
                now = _now()
                connection.execute(
                    f"UPDATE child_operations SET state = 'stale', updated_at = ? "
                    f"WHERE child_id IN ({placeholders})",
                    (now, *stale_children),
                )
                connection.execute(
                    f"UPDATE resource_claims SET state = 'released', updated_at = ? "
                    f"WHERE state = 'acquired' AND child_id IN ({placeholders})",
                    (now, *stale_children),
                )
        unresolved = list(reconciliation["unresolved"])
        failure_fingerprint = (
            f"sha256:{_digest_json(unresolved)}" if unresolved else None
        )
        failure_count = 0
        if failure_fingerprint is not None:
            failure_count = (
                int(prior.get("failure_count", 0)) + 1
                if failure_fingerprint == prior.get("failure_fingerprint")
                else 1
            )
        terminal_status = (
            "resumed"
            if not unresolved
            else ("recovery_waiting" if failure_count >= 2 else None)
        )
        outcome = {
            **prior,
            "failure_count": failure_count,
            "failure_fingerprint": failure_fingerprint,
            "reconciliation": dict(reconciliation),
            "replacement": _replacement_opportunity(connection),
            "stage": terminal_status or "reconciling",
            "stale_children": stale_children,
            "status": terminal_status or "blocked",
        }
        now = _now()
        if terminal_status is None:
            connection.execute(
                """
                UPDATE operations
                SET output_fingerprint = ?, outcome_json = ?, updated_at = ?
                WHERE operation_id = ? AND phase = 'authority_committed'
                """,
                (
                    _digest_json(outcome),
                    _canonical_json(outcome),
                    now,
                    operation_id,
                ),
            )
            ledger._insert_event(
                connection,
                operation_id=operation_id,
                event_type="resume_reconciliation_blocked",
                phase="authority_committed",
                epoch=lease.epoch,
                payload={
                    "failure_count": failure_count,
                    "failure_fingerprint": failure_fingerprint,
                },
                created_at=now,
            )
            return outcome

        parent_status = (
            "authorized" if terminal_status == "resumed" else "recovery_waiting"
        )
        connection.execute(
            "UPDATE parent_runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (parent_status, now, ledger.run_id),
        )
        connection.execute(
            """
            UPDATE operations
            SET phase = 'projected', output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase = 'authority_committed'
            """,
            (
                _digest_json(outcome),
                _canonical_json(outcome),
                now,
                operation_id,
            ),
        )
        request = outcome["request"]
        compatibility = {
            key: outcome[key]
            for key in (
                "dirt_digest",
                "from_epoch",
                "main_head",
                "main_state_digest",
                "qualification_rotation_proof_digest",
                "retained_integration_head",
                "reconciliation",
                "replacement",
                "stale_children",
                "status",
                "to_epoch",
            )
        }
        compatibility.update(
            {
                "authority_identity": request["authority_identity"],
                "available_resources": request["available_resources"],
                "direct_user_action": request["direct_user_action"],
                "new_writer_id": request["new_writer_id"],
                "tool_receipt": request["tool_receipt"],
            }
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=str(request["request_operation_id"]),
            kind="parent_resume",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(compatibility),
            outcome=compatibility,
            event_type=(
                "parent_resumed"
                if terminal_status == "resumed"
                else "resume_recovery_waiting"
            ),
            created_at=now,
        )
        return outcome


def _continue_resume_attempt(
    ledger: ParentLedger,
    lease: WriterLease,
    attempt: Mapping[str, object],
    request: Mapping[str, object],
) -> tuple[dict[str, object], WriterLease]:
    if attempt["outcome"].get("request") != dict(request):
        raise OperationConflict("resume request was reused with new input")
    if attempt["phase"] == "projected":
        return attempt["outcome"], _active_lease(ledger)

    rotation_id = f"resume-fence:{attempt['operation_id']}"
    rotation = ledger.get_operation(rotation_id)
    if rotation is None:
        if lease.epoch != attempt["outcome"]["from_epoch"]:
            raise OperationConflict("resume attempt lost its original writer lease")
        next_lease = ledger.rotate_writer(
            lease,
            new_writer_id=str(request["new_writer_id"]),
            operation_id=rotation_id,
        )
    else:
        next_lease = _active_lease(ledger)
        if (
            rotation["kind"] != "writer_rotation"
            or rotation["phase"] != "authority_committed"
            or rotation["outcome"].get("epoch") != next_lease.epoch
            or rotation["outcome"].get("writer_id") != next_lease.writer_id
        ):
            raise OperationConflict("resume fence rotation is not the active lease")

    attempt = _resume_attempt_after_rotation(ledger, next_lease, attempt)
    attempt = _resume_attempt_reconciling(ledger, next_lease, attempt)
    reconciliation = reconcile_parent(
        ledger,
        next_lease,
        expected_qualification_rotation_proof=attempt["outcome"].get(
            "qualification_rotation_proof"
        ),
    )
    outcome = _finish_resume_attempt(
        ledger,
        next_lease,
        attempt,
        reconciliation,
    )
    if outcome["status"] in {"resumed", "recovery_waiting"}:
        ledger.rebuild_projection(next_lease)
    return outcome, next_lease


def resume_safe_point_status(ledger: ParentLedger) -> dict[str, object]:
    """Return the read-only predicate projection used by paused resume."""
    connection = ledger._connect(read_only=True)
    try:
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] != "paused":
            raise ControlError("resume safe-point status requires a paused parent")
        envelope, _ = _approved_envelope(connection, parent["start_gate_ref"])
    finally:
        connection.close()
    return _resume_safe_point(ledger, envelope)["projection"]


def _cancel_operation_disposition(
    ledger: ParentLedger,
    operation: Mapping[str, object],
    allowed_touches: Sequence[str],
) -> dict[str, object]:
    base = {
        "kind": operation["kind"],
        "operation_id": operation["operation_id"],
        "phase": operation["phase"],
    }
    if operation["kind"] in _RECOVERABLE_OPERATION_KINDS:
        if _unresolved_operation_reconcilable(
            ledger,
            operation,
            allowed_touches,
        ):
            return {**base, "disposition": "reconcilable_for_completion"}
        return {
            **base,
            "disposition": "ambiguous_external_effect",
            "reason": "operation effect cannot be proven from current local state",
        }
    if operation["kind"] == "resume_attempt" and operation["phase"] == "prepared":
        rotation = ledger.get_operation(
            f"resume-fence:{operation['operation_id']}"
        )
        if rotation is None:
            return {**base, "disposition": "abandonable_preserved"}
    if operation["kind"] == "child_worktree_create":
        intent = operation["outcome"]
        worktree = intent.get("worktree")
        branch = intent.get("branch")
        if (
            operation["phase"] == "prepared"
            and isinstance(worktree, str)
            and isinstance(branch, str)
        ):
            ref = f"refs/heads/{branch}"
            ref_probe = _git_process(
                ledger.repo_root,
                "show-ref",
                "--verify",
                "--quiet",
                ref,
            )
            if ref_probe.returncode not in {0, 1}:
                raise GitStateError("git show-ref failed during cancel preflight")
            if not Path(worktree).exists() and ref_probe.returncode == 1:
                return {**base, "disposition": "abandonable_preserved"}
    return {
        **base,
        "disposition": "ambiguous_external_effect",
        "reason": "unresolved operation may have an external effect",
    }


def _cancel_safe_point_from_connection(
    ledger: ParentLedger,
    connection: sqlite3.Connection,
) -> dict[str, object]:
    parent = _parent_row(connection, ledger.run_id)
    allowed_touches: list[str] = []
    if parent["start_gate_ref"] is not None:
        try:
            envelope, _ = _approved_envelope(
                connection,
                parent["start_gate_ref"],
            )
            allowed_touches = list(envelope["allowed_touches"])
        except ContextError:
            allowed_touches = []
    operations = []
    for unresolved in _unresolved_operations(connection):
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (unresolved["operation_id"],),
        ).fetchone()
        if row is None:
            raise OperationConflict("cancel preflight lost an unresolved operation")
        operations.append(
            _cancel_operation_disposition(
                ledger,
                _operation_dict(row),
                allowed_touches,
            )
        )
    ambiguous = [
        item
        for item in operations
        if item["disposition"] == "ambiguous_external_effect"
    ]
    reconcilable = [
        item
        for item in operations
        if item["disposition"] == "reconcilable_for_completion"
    ]
    return {
        "authority_digest": f"sha256:{ledger.authority_digest(connection)}",
        "eligible": not ambiguous and not reconcilable,
        "operations": operations,
        "parent_status": parent["status"],
        "requires_reconciliation": bool(reconcilable),
        "status": (
            "ambiguous_external_effect"
            if ambiguous
            else ("reconciliation_required" if reconcilable else "cancel_safe")
        ),
    }


def cancel_safe_point_status(ledger: ParentLedger) -> dict[str, object]:
    """Return cancellation eligibility without mutating ledger or Git state."""
    connection = ledger._connect(read_only=True)
    try:
        connection.execute("BEGIN")
        result = _cancel_safe_point_from_connection(ledger, connection)
        connection.commit()
        return result
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def reconcile_for_cancel(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    expected_authority_digest: str,
) -> dict[str, object]:
    """Complete only typed local intents before a separately bound cancel."""
    expected = _required_text(
        expected_authority_digest,
        "expected_authority_digest",
    )
    with ledger._write_transaction(lease) as connection:
        safe_point = _cancel_safe_point_from_connection(ledger, connection)
        if safe_point["authority_digest"] != expected:
            raise ControlError("cancel reconciliation authority digest changed")
        if safe_point["status"] == "ambiguous_external_effect":
            raise ControlError("cancel reconciliation has an ambiguous external effect")
        operations = [
            _operation_dict(
                connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (item["operation_id"],),
                ).fetchone()
            )
            for item in safe_point["operations"]
            if item["disposition"] == "reconcilable_for_completion"
        ]

    actions = []
    for operation in operations:
        if operation["kind"] == _INTEGRATION_KIND:
            action = _recover_integration_operation(
                ledger,
                lease,
                operation,
                allow_ref_advance=True,
            )
        elif operation["kind"] == "child_commit":
            action = _recover_child_commit(ledger, lease, operation)
        elif operation["kind"] == "final_local_merge":
            from .acceptance import recover_final_merge

            connection = ledger._connect(read_only=True)
            try:
                parent = _parent_row(connection, ledger.run_id)
                envelope, _ = _approved_envelope(
                    connection,
                    parent["start_gate_ref"],
                )
            finally:
                connection.close()
            recovery = _final_merge_recovery_state_for_operation(
                ledger,
                operation,
                envelope["allowed_touches"],
            )
            action = recover_final_merge(
                ledger,
                lease,
                operation,
                expected_qualification_rotation_proof=recovery[
                    "qualification_rotation_proof"
                ],
            )
        else:
            raise RecoveryError(
                f"unsupported cancel reconciliation kind: {operation['kind']}"
            )
        actions.append(action)
    if actions:
        ledger.rebuild_projection(lease)
    return {
        "actions": actions,
        "authority_digest": f"sha256:{ledger.authority_digest()}",
        "status": "reconciled",
    }


def _abandon_cancel_safe_operations(
    ledger: ParentLedger,
    connection: sqlite3.Connection,
    operations: Sequence[Mapping[str, object]],
    *,
    epoch: int,
) -> list[dict[str, object]]:
    abandoned = []
    for item in operations:
        if item["disposition"] != "abandonable_preserved":
            continue
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (item["operation_id"],),
        ).fetchone()
        if row is None or row["phase"] not in {"prepared", "effect_observed"}:
            raise OperationConflict("cancel-safe operation changed after preflight")
        operation = _operation_dict(row)
        outcome = {
            **operation["outcome"],
            "cancel_disposition": "abandoned_preserved",
            "effect_performed": False,
            "status": "abandoned_preserved",
        }
        now = _now()
        connection.execute(
            """
            UPDATE operations
            SET phase = 'authority_committed', epoch = ?,
                output_fingerprint = ?, outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase = ?
            """,
            (
                epoch,
                _digest_json(outcome),
                _canonical_json(outcome),
                now,
                operation["operation_id"],
                operation["phase"],
            ),
        )
        ledger._insert_event(
            connection,
            operation_id=str(operation["operation_id"]),
            event_type="operation_abandoned_preserved",
            phase="authority_committed",
            epoch=epoch,
            payload={
                "effect_performed": False,
                "status": "abandoned_preserved",
            },
            created_at=now,
        )
        abandoned.append(
            {
                "kind": operation["kind"],
                "operation_id": operation["operation_id"],
                "status": "abandoned_preserved",
            }
        )
    return abandoned


def cancel_parent(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    reason: str,
    actor: str,
    requested_at: str,
    direct_user_action: bool,
    expected_authority_digest: str,
    operator_input_digest: str | None = None,
    pending_action_digest: str | None = None,
    pending_action_id: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, object]:
    """Record terminal human cancellation without cleanup or fake completion."""
    operation_id = _required_text(operation_id, "operation_id")
    reason = _required_text(reason, "reason")
    actor = _required_text(actor, "actor")
    requested_at = _required_text(requested_at, "requested_at")
    expected_authority = _required_text(
        expected_authority_digest,
        "expected_authority_digest",
    )
    if not isinstance(direct_user_action, bool) or not direct_user_action:
        raise ControlError("Loop cancellation requires a direct human action")
    supersession = (
        _required_text(superseded_by, "superseded_by")
        if superseded_by is not None
        else None
    )
    action_id = (
        _required_text(pending_action_id, "pending_action_id")
        if pending_action_id is not None
        else None
    )
    action_digest = (
        _required_text(pending_action_digest, "pending_action_digest")
        if pending_action_digest is not None
        else None
    )
    input_digest = (
        _required_text(operator_input_digest, "operator_input_digest")
        if operator_input_digest is not None
        else None
    )
    if (action_id is None) != (action_digest is None):
        raise ControlError("pending action ID and digest must be supplied together")
    supplied = {
        "actor": actor,
        "direct_user_action": True,
        "expected_authority_digest": expected_authority,
        "operator_input_digest": input_digest,
        "pending_action_digest": action_digest,
        "pending_action_id": action_id,
        "reason": reason,
        "requested_at": requested_at,
        "superseded_by": supersession,
    }
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            return _replay_committed(existing, "parent_cancel", supplied)
        parent = _parent_row(connection, ledger.run_id)
        if parent["status"] not in {
            "initialized",
            "authorized",
            "paused",
            "recovery_waiting",
        }:
            raise ControlError(f"cannot cancel parent in status {parent['status']}")
        safe_point = _cancel_safe_point_from_connection(ledger, connection)
        if safe_point["authority_digest"] != expected_authority:
            raise ControlError("cancel authority digest changed")
        if not safe_point["eligible"]:
            raise ControlError(
                "cancel requires reconciliation and no ambiguous external effect"
            )
        abandoned = _abandon_cancel_safe_operations(
            ledger,
            connection,
            safe_point["operations"],
            epoch=lease.epoch,
        )
        child_rows = connection.execute(
            "SELECT child_id, state FROM child_operations ORDER BY child_id"
        ).fetchall()
        integrated = [
            row["child_id"] for row in child_rows if row["state"] == "integrated"
        ]
        cancelled = [
            row["child_id"] for row in child_rows if row["state"] != "integrated"
        ]
        retained_git = [
            {
                "commit_id": row["commit_id"],
                "git_operation_id": row["git_operation_id"],
                "ref_name": row["ref_name"],
                "tree_id": row["tree_id"],
                "worktree": row["worktree"],
            }
            for row in connection.execute(
                "SELECT * FROM git_operations ORDER BY git_operation_id"
            ).fetchall()
        ]
        now = _now()
        if cancelled:
            placeholders = ",".join("?" for _ in cancelled)
            connection.execute(
                f"UPDATE child_operations SET state = 'cancelled', updated_at = ? "
                f"WHERE child_id IN ({placeholders})",
                (now, *cancelled),
            )
            connection.execute(
                f"UPDATE resource_claims SET state = 'released', updated_at = ? "
                f"WHERE state = 'acquired' AND child_id IN ({placeholders})",
                (now, *cancelled),
            )
        outcome = {
            **supplied,
            "abandoned_operations": abandoned,
            "archive_as_completed": False,
            "cancelled_children": cancelled,
            "cleanup_performed": False,
            "integrated_children": integrated,
            "retained_git": retained_git,
            "status": "cancelled",
        }
        connection.execute(
            "UPDATE parent_runs SET status = 'cancelled', updated_at = ? WHERE run_id = ?",
            (now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="parent_cancel",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="parent_cancelled",
            created_at=now,
        )
        return outcome


def reconcile_integration_history(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_ref: str,
) -> dict[str, object]:
    """Invalidate integrated evidence removed from the current integration ref."""
    integration_ref = _integration_ref(integration_ref)
    canonical = ledger.repo_root.resolve()
    actual = _git_text(canonical, "rev-parse", "--verify", integration_ref)
    actual_tree = _git_text(canonical, "rev-parse", f"{actual}^{{tree}}")
    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id, require_authorized=False)
        rows = connection.execute(
            "SELECT * FROM git_operations WHERE phase = 'integrated' ORDER BY updated_at"
        ).fetchall()
    finally:
        connection.close()
    missing = []
    for row in rows:
        outcome = json.loads(row["outcome_json"])
        if not _is_ancestor(canonical, outcome["child_commit_id"], actual):
            missing.append(outcome["child_id"])
    if not missing:
        context_head = runtime["context"]["integration"]["integration_head"]
        if rows and context_head != actual:
            raise RecoveryError(
                "integration ref/context drift is not explained by removed child evidence"
            )
        return {
            "actual_ref": actual,
            "invalidated_children": [],
            "invalidated_requirements": [],
            "status": "current",
        }

    graph = runtime["context"]["graph"]
    affected = _transitive_dependents(graph, set(missing))
    nodes = {item["child_id"]: item for item in graph}
    requirements = sorted(
        {
            requirement
            for child in affected
            for requirement in nodes[child]["requirements"]
        }
    )
    fingerprint = _digest_json(
        {"actual_ref": actual, "affected": sorted(affected), "missing": sorted(missing)}
    )
    revision_id = f"integration-history-{fingerprint[:16]}"
    operation_id = f"integration-history:{fingerprint[:24]}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return existing["outcome"]

    context = copy.deepcopy(runtime["context"])
    for item in context["requirements"]:
        if item["requirement_id"] in requirements:
            if item["coverage_state"] != "uncovered":
                item["revision"] += 1
            item["coverage_state"] = "uncovered"
            context["dependency_state"][item["requirement_id"]] = "blocked"
    context["integration"]["integration_head"] = actual
    context["integration"]["integration_tree_id"] = actual_tree
    record_context_revision(
        ledger,
        lease,
        request_id=runtime["start_gate_ref"],
        revision_id=revision_id,
        reason=f"integration history invalidated: {','.join(sorted(missing))}",
        context=context,
    )
    supplied = {
        "actual_ref": actual,
        "integration_ref": integration_ref,
        "invalidated_children": sorted(affected),
        "invalidated_requirements": requirements,
        "missing_children": sorted(missing),
        "revision_id": revision_id,
    }
    with ledger._write_transaction(lease) as connection:
        replay = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if replay is not None:
            return _replay_committed(
                replay, "integration_history_invalidated", supplied
            )
        now = _now()
        placeholders = ",".join("?" for _ in affected)
        connection.execute(
            f"UPDATE child_operations SET state = 'invalidated', updated_at = ? "
            f"WHERE child_id IN ({placeholders})",
            (now, *sorted(affected)),
        )
        outcome = {**supplied, "status": "invalidated"}
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="integration_history_invalidated",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="integration_history_invalidated",
            created_at=now,
        )
        return outcome


def _record_candidate_outcome(
    ledger: ParentLedger,
    lease: WriterLease,
    operation_id: str,
    outcome: dict[str, object],
) -> dict[str, object]:
    output = outcome.get("candidate_head") or outcome["expected_old"]
    ledger.advance_operation(
        lease,
        operation_id=operation_id,
        expected_phase="prepared",
        phase="effect_observed",
        output_fingerprint=str(output),
        outcome=outcome,
    )
    _ensure_candidate_verification(ledger, lease, outcome)
    if outcome["status"] == "candidate_green":
        return outcome
    _ensure_problem_from_candidate(ledger, lease, outcome)
    ledger.advance_operation(
        lease,
        operation_id=operation_id,
        expected_phase="effect_observed",
        phase="authority_committed",
        output_fingerprint=str(output),
        outcome=outcome,
    )
    return outcome


def _ensure_candidate_verification(
    ledger: ParentLedger, lease: WriterLease, outcome: Mapping[str, object]
) -> None:
    verification_id = f"integration-check:{outcome['integration_id']}"
    verdict = "passed" if outcome["status"] == "candidate_green" else "failed"
    findings = [] if verdict == "passed" else [outcome["root_condition"]]
    with ledger._write_transaction(lease) as connection:
        row = connection.execute(
            "SELECT * FROM verifications WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
        applicability = {
            "checks": outcome["checks_result"],
            "child_commit_id": outcome["child_commit_id"],
            "coverage": outcome["coverage"],
            "effect_boundary_clean": outcome.get("effect_boundary_clean", False),
            "expected_old": outcome["expected_old"],
            "integration_ref": outcome["integration_ref"],
            "merge": outcome["merge_result"],
        }
        artifact = outcome["candidate_tree_id"]
        if row is not None:
            if (
                row["artifact_digest"] != artifact
                or row["verdict"] != verdict
                or row["findings_json"] != _canonical_json(findings)
                or row["applicability_json"] != _canonical_json(applicability)
            ):
                raise OperationConflict("integration verification identity was reused")
            return
        connection.execute(
            """
            INSERT INTO verifications (
                verification_id, run_id, artifact_digest, actor, verdict,
                findings_json, applicability_json, created_at
            ) VALUES (?, ?, ?, 'parent-integrator', ?, ?, ?, ?)
            """,
            (
                verification_id,
                ledger.run_id,
                artifact,
                verdict,
                _canonical_json(findings),
                _canonical_json(applicability),
                _now(),
            ),
        )


def _ensure_problem_from_candidate(
    ledger: ParentLedger, lease: WriterLease, outcome: Mapping[str, object]
) -> dict[str, object]:
    problem_identity = _digest_json(
        {
            "operation_phase": "integration_candidate",
            "requirements": outcome["coverage"],
            "root_condition": outcome["root_condition"],
        }
    )
    problem = outcome.get("problem") or {
        "action": "build a new isolated candidate after diagnosis",
        "diagnosis": str(outcome["root_condition"]),
        "problem_id": f"integration-{problem_identity[:20]}",
        "root_condition": outcome["root_condition"],
        "round": 0,
    }
    return record_problem_attempt(
        ledger,
        lease,
        problem_id=problem["problem_id"],
        round_number=problem["round"],
        operation_phase="integration_candidate",
        root_condition=problem["root_condition"],
        requirement_ids=outcome["coverage"],
        diagnosis=problem["diagnosis"],
        action=problem["action"],
        commands=outcome["checks_result"],
        artifact_ids=[outcome["candidate_head"], outcome["candidate_tree_id"]],
        result="failed",
    )


def _ensure_problem_resolution(
    ledger: ParentLedger, lease: WriterLease, outcome: Mapping[str, object]
) -> None:
    problem = outcome.get("problem")
    if not problem:
        return
    record_problem_attempt(
        ledger,
        lease,
        problem_id=problem["problem_id"],
        round_number=problem["round"],
        operation_phase="integration_candidate",
        root_condition=problem["root_condition"],
        requirement_ids=outcome["coverage"],
        diagnosis=problem["diagnosis"],
        action=problem["action"],
        commands=outcome["checks_result"],
        artifact_ids=[outcome["candidate_head"], outcome["candidate_tree_id"]],
        result="passed",
    )


def _commit_integration_authority(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    source: Mapping[str, object],
    recovered: bool,
    recovered_from_epoch: int | None = None,
) -> dict[str, object]:
    outcome = {
        **source,
        "archive_eligible": True,
        "phase": "integrated",
        "recovered": recovered,
        "status": "integrated",
    }
    with ledger._write_transaction(lease) as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None or row["phase"] != "effect_observed":
            raise OperationConflict(
                "integration intent is not awaiting acknowledgement"
            )
        now = _now()
        connection.execute(
            """
            UPDATE operations
            SET phase = 'authority_committed', output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase = 'effect_observed'
            """,
            (
                outcome["candidate_head"],
                _canonical_json(outcome),
                now,
                operation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO git_operations (
                git_operation_id, run_id, operation_id, worktree, branch,
                ref_name, expected_old_ref, commit_id, tree_id, phase,
                outcome_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'integrated', ?, ?)
            """,
            (
                f"integration:{source['integration_id']}",
                ledger.run_id,
                operation_id,
                source["candidate_worktree"],
                source["candidate_branch"],
                source["integration_ref"],
                source["expected_old"],
                source["candidate_head"],
                source["candidate_tree_id"],
                _canonical_json(outcome),
                now,
            ),
        )
        child = connection.execute(
            """
            UPDATE child_operations SET state = 'integrated', updated_at = ?
            WHERE child_id = ? AND state = 'committed'
            """,
            (now, source["child_id"]),
        )
        if child.rowcount != 1:
            raise FreshnessError(
                "child state changed before integration acknowledgement"
            )
        connection.execute(
            """
            UPDATE resource_claims SET state = 'released', updated_at = ?
            WHERE child_id = ? AND state = 'acquired'
            """,
            (now, source["child_id"]),
        )
        ledger._insert_event(
            connection,
            operation_id=operation_id,
            event_type=(
                "integration_reconciled"
                if recovered_from_epoch is not None
                else "integration_acknowledged"
            ),
            phase="authority_committed",
            epoch=lease.epoch,
            payload=(
                {
                    "recovered_from_epoch": recovered_from_epoch,
                    "outcome": outcome,
                }
                if recovered_from_epoch is not None
                else outcome
            ),
            created_at=now,
        )
    return outcome


def _ensure_integration_context(
    ledger: ParentLedger, lease: WriterLease, outcome: Mapping[str, object]
) -> dict[str, object] | None:
    connection = ledger._connect(read_only=True)
    try:
        parent = _parent_row(connection, ledger.run_id)
        current_row = _latest_context_row(connection)
        if current_row is None:
            raise FreshnessError("integration acknowledgement lacks canonical context")
        context = json.loads(current_row["context_json"])
    finally:
        connection.close()
    coverage_current = {
        item["requirement_id"]: item["coverage_state"]
        for item in context["requirements"]
    }
    if context["integration"]["integration_head"] == outcome["candidate_head"] and all(
        coverage_current[item] == "covered" for item in outcome["coverage"]
    ):
        return None
    if all(coverage_current[item] == "covered" for item in outcome["coverage"]):
        current_head = context["integration"]["integration_head"]
        if _is_ancestor(ledger.repo_root, outcome["candidate_head"], current_head):
            return None
    if context["integration"]["integration_head"] != outcome["expected_old"]:
        raise RecoveryError("canonical context moved beyond this integration intent")
    updated = copy.deepcopy(context)
    updated["integration"]["integration_head"] = outcome["candidate_head"]
    updated["integration"]["integration_tree_id"] = outcome["candidate_tree_id"]
    for item in updated["requirements"]:
        if item["requirement_id"] in outcome["coverage"]:
            if item["coverage_state"] != "covered":
                item["revision"] += 1
            item["coverage_state"] = "covered"
            updated["dependency_state"][item["requirement_id"]] = "covered"
    return record_context_revision(
        ledger,
        lease,
        request_id=parent["start_gate_ref"],
        revision_id=f"integration-context-{outcome['integration_id']}",
        reason=f"integrated child {outcome['child_id']}",
        context=updated,
    )


def _recover_integration_operation(
    ledger: ParentLedger,
    lease: WriterLease,
    operation: Mapping[str, object],
    *,
    allow_ref_advance: bool,
) -> dict[str, object]:
    if operation["phase"] == "prepared":
        intent = operation["outcome"]
        actual = _git_text(
            ledger.repo_root, "rev-parse", "--verify", intent["integration_ref"]
        )
        if actual != intent["expected_old"]:
            raise RecoveryError("prepared candidate has unexplained ref movement")
        outcome = {
            **intent,
            "recovered": True,
            "status": "recovered_no_effect",
        }
        _recover_operation_authority(ledger, lease, operation, outcome)
        return {
            "operation_id": operation["operation_id"],
            "result": "no_effect",
        }
    source = operation["outcome"]
    if source["status"] == "candidate_failed":
        _ensure_problem_from_candidate(ledger, lease, source)
        _recover_operation_authority(ledger, lease, operation, source)
        return {
            "operation_id": operation["operation_id"],
            "result": "candidate_failed",
        }
    _assert_candidate_green(ledger, source, rerun_checks=True)
    actual = _git_text(
        ledger.repo_root, "rev-parse", "--verify", source["integration_ref"]
    )
    if actual == source["expected_old"]:
        if not allow_ref_advance:
            raise RecoveryError("green integration intent still awaits ref advance")
        _assert_ref_unattached(ledger.repo_root, source["integration_ref"])
        _git(
            ledger.repo_root,
            "update-ref",
            source["integration_ref"],
            source["candidate_head"],
            source["expected_old"],
        )
        actual = source["candidate_head"]
    if actual != source["candidate_head"]:
        raise RecoveryError("integration ref has an ambiguous crash outcome")
    acknowledged = _commit_integration_authority(
        ledger,
        lease,
        operation_id=operation["operation_id"],
        source=source,
        recovered=True,
        recovered_from_epoch=operation["epoch"],
    )
    _ensure_integration_context(ledger, lease, acknowledged)
    _ensure_problem_resolution(ledger, lease, acknowledged)
    return {
        "operation_id": operation["operation_id"],
        "result": "integrated",
    }


def _recover_child_commit(
    ledger: ParentLedger,
    lease: WriterLease,
    operation: Mapping[str, object],
) -> dict[str, object]:
    intent = operation["outcome"]
    required = {
        "base_head",
        "branch",
        "child_id",
        "tree_id",
        "validated_paths",
        "worktree",
    }
    if not required.issubset(intent):
        raise RecoveryError("child commit intent predates recoverable intent evidence")
    ref_name = f"refs/heads/{intent['branch']}"
    actual = _git_text(ledger.repo_root, "rev-parse", "--verify", ref_name)
    if actual == intent["base_head"]:
        outcome = {
            **intent,
            "recovered": True,
            "status": "recovered_no_effect",
        }
        _recover_operation_authority(ledger, lease, operation, outcome)
        return {"operation_id": operation["operation_id"], "result": "no_effect"}
    if (
        _git_text(ledger.repo_root, "rev-parse", f"{actual}^{{tree}}")
        != intent["tree_id"]
        or _git_text(ledger.repo_root, "rev-parse", f"{actual}^") != intent["base_head"]
    ):
        raise RecoveryError("child branch does not prove the intended commit effect")
    outcome = {
        **intent,
        "commit_id": actual,
        "git_operation_id": f"child-commit:{intent['child_id']}",
        "operation_id": operation["operation_id"],
        "phase": "committed",
        "recovered": True,
        "ref_name": ref_name,
        "staged_paths": sorted(intent["validated_paths"]),
    }
    with ledger._write_transaction(lease) as connection:
        current = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation["operation_id"],),
        ).fetchone()
        if current is None or current["phase"] not in {"prepared", "effect_observed"}:
            raise OperationConflict("child commit recovery lost its source operation")
        now = _now()
        connection.execute(
            """
            UPDATE operations
            SET phase = 'authority_committed', output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase IN ('prepared', 'effect_observed')
            """,
            (actual, _canonical_json(outcome), now, operation["operation_id"]),
        )
        connection.execute(
            """
            INSERT INTO git_operations (
                git_operation_id, run_id, operation_id, worktree, branch,
                ref_name, expected_old_ref, commit_id, tree_id, phase,
                outcome_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)
            """,
            (
                outcome["git_operation_id"],
                ledger.run_id,
                operation["operation_id"],
                intent["worktree"],
                intent["branch"],
                ref_name,
                intent["base_head"],
                actual,
                intent["tree_id"],
                _canonical_json(outcome),
                now,
            ),
        )
        child = connection.execute(
            "UPDATE child_operations SET state = 'committed', updated_at = ? "
            "WHERE child_id = ? AND state = 'reviewed'",
            (now, intent["child_id"]),
        )
        if child.rowcount != 1:
            raise FreshnessError("recovered child commit is not awaiting commit")
        ledger._insert_event(
            connection,
            operation_id=operation["operation_id"],
            event_type="child_commit_reconciled",
            phase="authority_committed",
            epoch=lease.epoch,
            payload={
                "recovered_from_epoch": operation["epoch"],
                "outcome": outcome,
            },
            created_at=now,
        )
    return {"operation_id": operation["operation_id"], "result": "committed"}


def _recover_operation_authority(
    ledger: ParentLedger,
    lease: WriterLease,
    operation: Mapping[str, object],
    outcome: Mapping[str, object],
) -> None:
    with ledger._write_transaction(lease) as connection:
        current = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation["operation_id"],),
        ).fetchone()
        if current is None or current["phase"] not in {"prepared", "effect_observed"}:
            raise OperationConflict("recovery source operation changed")
        now = _now()
        connection.execute(
            """
            UPDATE operations
            SET phase = 'authority_committed', output_fingerprint = ?,
                outcome_json = ?, updated_at = ?
            WHERE operation_id = ? AND phase IN ('prepared', 'effect_observed')
            """,
            (
                _digest_json(outcome),
                _canonical_json(outcome),
                now,
                operation["operation_id"],
            ),
        )
        ledger._insert_event(
            connection,
            operation_id=operation["operation_id"],
            event_type="operation_reconciled",
            phase="authority_committed",
            epoch=lease.epoch,
            payload={
                "recovered_from_epoch": operation["epoch"],
                "outcome": dict(outcome),
            },
            created_at=now,
        )


def _runtime_state(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    require_authorized: bool,
) -> dict[str, object]:
    parent = _parent_row(connection, run_id)
    if parent["start_gate_ref"] is None:
        raise FreshnessError("integration requires an approved start envelope")
    if require_authorized and parent["status"] != "authorized":
        raise FreshnessError(
            f"integration blocked by parent status: {parent['status']}"
        )
    envelope, _ = _approved_envelope(connection, parent["start_gate_ref"])
    context_row = _latest_context_row(connection)
    if context_row is None:
        raise FreshnessError("integration requires canonical context")
    return {
        "context": json.loads(context_row["context_json"]),
        "envelope": envelope,
        "parent_status": parent["status"],
        "start_gate_ref": parent["start_gate_ref"],
        "token": _freshness_token(connection, run_id),
    }


def _assert_parent_status(ledger: ParentLedger, expected: str, boundary: str) -> None:
    connection = ledger._connect(read_only=True)
    try:
        status = _parent_row(connection, ledger.run_id)["status"]
    finally:
        connection.close()
    if status != expected:
        raise ControlError(
            f"{boundary} requires parent status {expected}, found {status}"
        )


def _parent_row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise FreshnessError("parent ledger is missing")
    return row


def _child_row(connection: sqlite3.Connection, child_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM child_operations WHERE child_id = ?", (child_id,)
    ).fetchone()
    if row is None:
        raise FreshnessError(f"unknown child: {child_id}")
    return row


def _graph_node(
    graph: Sequence[Mapping[str, object]], child_id: str
) -> dict[str, object]:
    for node in graph:
        if node["child_id"] == child_id:
            return dict(node)
    raise FreshnessError("child is absent from the current integration graph")


def _authority_outcome(
    connection: sqlite3.Connection, operation_id: str, kind: str
) -> dict[str, object]:
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    if row is None or row["kind"] != kind or row["phase"] != "authority_committed":
        raise FreshnessError(f"required authority is missing: {operation_id}")
    return json.loads(row["outcome_json"])


def _operation(ledger: ParentLedger, operation_id: str, kind: str) -> dict[str, object]:
    operation = ledger.get_operation(operation_id)
    if operation is None or operation["kind"] != kind:
        raise IntegrationStateError(f"unknown {kind} operation: {operation_id}")
    return operation


def _operation_dict(row: sqlite3.Row) -> dict[str, object]:
    value = dict(row)
    value["outcome"] = json.loads(value.pop("outcome_json"))
    return value


def _integration_ref(value: str) -> str:
    ref = _required_text(value, "integration_ref")
    if not ref.startswith("refs/heads/"):
        raise IntegrationStateError("integration_ref must be a full local branch ref")
    return ref


def _registered_worktrees(repo: Path) -> list[Path]:
    return [
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git_text(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


def _assert_ref_unattached(repo: Path, ref_name: str) -> None:
    branches = {
        line.removeprefix("branch ")
        for line in _git_text(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("branch ")
    }
    if ref_name in branches:
        raise IntegrationStateError(
            "authoritative integration ref is attached to a worktree"
        )


def _merge_parents(repo: Path, commit_id: str) -> list[str]:
    tokens = _git_text(repo, "rev-list", "--parents", "-n", "1", commit_id).split()
    return tokens[1:]


def _assert_candidate_green(
    ledger: ParentLedger,
    outcome: Mapping[str, object],
    *,
    rerun_checks: bool,
) -> None:
    worktree = _repository_path(Path(outcome["candidate_worktree"]), "candidate")
    _assert_same_repository(ledger.repo_root, worktree)
    if _git_text(worktree, "rev-parse", "HEAD") != outcome["candidate_head"]:
        raise RecoveryError("candidate worktree HEAD changed")
    if (
        _git_text(worktree, "rev-parse", f"{outcome['candidate_head']}^{{tree}}")
        != outcome["candidate_tree_id"]
    ):
        raise RecoveryError("candidate tree changed")
    if _merge_parents(worktree, outcome["candidate_head"]) != [
        outcome["expected_old"],
        outcome["child_commit_id"],
    ]:
        raise RecoveryError("candidate no-ff ancestry changed")
    if not outcome.get("effect_boundary_clean", False):
        raise RecoveryError("candidate did not record a clean effect boundary")
    if rerun_checks:
        checks = [_run_parent_check(worktree, command) for command in outcome["checks"]]
        if any(item["status"] != "passed" for item in checks):
            raise RecoveryError("candidate check failed during reconciliation")
    if not _candidate_worktree_clean(worktree):
        raise RecoveryError("candidate worktree changed during verification")


def _assert_integration_authority(
    ledger: ParentLedger, outcome: Mapping[str, object]
) -> None:
    actual = _git_text(
        ledger.repo_root, "rev-parse", "--verify", outcome["integration_ref"]
    )
    if actual != outcome["candidate_head"] and not _is_ancestor(
        ledger.repo_root, outcome["candidate_head"], actual
    ):
        raise RecoveryError("durable integration authority differs from Git ref")
    if _git_text(
        ledger.repo_root,
        "rev-parse",
        f"{outcome['candidate_head']}^{{tree}}",
    ) != outcome["candidate_tree_id"] or _merge_parents(
        ledger.repo_root, outcome["candidate_head"]
    ) != [outcome["expected_old"], outcome["child_commit_id"]]:
        raise RecoveryError("durable integration candidate differs from Git objects")


def _candidate_worktree_clean(worktree: Path) -> bool:
    return not _git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )


def _identity_env(intent: Mapping[str, object]) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_EMAIL": str(intent["author_email"]),
        "GIT_AUTHOR_NAME": str(intent["author_name"]),
        "GIT_COMMITTER_EMAIL": str(intent["author_email"]),
        "GIT_COMMITTER_NAME": str(intent["author_name"]),
    }


def _git_process(
    repo: Path, *args: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _process_evidence(
    command: str, result: subprocess.CompletedProcess[bytes]
) -> dict[str, object]:
    payload = result.stdout + b"\0" + result.stderr
    return {
        "command": command,
        "exit_code": result.returncode,
        "output_digest": f"sha256:{sha256(payload).hexdigest()}",
        "status": "passed" if result.returncode == 0 else "failed",
    }


def _command_evidence(value: Mapping[str, object]) -> dict[str, object]:
    fields = {"command", "exit_code", "output_digest", "status"}
    if set(value) != fields:
        raise ProblemBudgetError("problem command evidence has an invalid schema")
    command = _required_text(value["command"], "command")
    output = _required_text(value["output_digest"], "output_digest")
    status = _required_text(value["status"], "status")
    exit_code = value["exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ProblemBudgetError("problem command exit_code must be an integer")
    if status not in {"passed", "failed"}:
        raise ProblemBudgetError("problem command status must be passed or failed")
    return {
        "command": command,
        "exit_code": exit_code,
        "output_digest": output,
        "status": status,
    }


def _problem_input(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    if set(value) != _PROBLEM_FIELDS:
        raise ProblemBudgetError("problem input has an invalid schema")
    round_number = value["round"]
    if (
        isinstance(round_number, bool)
        or not isinstance(round_number, int)
        or round_number < 1
        or round_number > MAX_REPAIR_ROUNDS
    ):
        raise ProblemBudgetError("repair candidate problem round must be 1..3")
    return {
        "action": _required_text(value["action"], "action"),
        "diagnosis": _required_text(value["diagnosis"], "diagnosis"),
        "problem_id": _required_text(value["problem_id"], "problem_id"),
        "root_condition": _required_text(value["root_condition"], "root_condition"),
        "round": round_number,
    }


def _problem_attempts(
    connection: sqlite3.Connection, problem_id: str | None
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT outcome_json FROM operations
        WHERE kind = ? AND phase = 'authority_committed'
        ORDER BY created_at, operation_id
        """,
        (_PROBLEM_KIND,),
    ).fetchall()
    outcomes = sorted(
        (json.loads(row["outcome_json"]) for row in rows),
        key=lambda item: (
            str(item["problem_id"]),
            int(item.get("recovery_generation", 1)),
            int(item["round"]),
        ),
    )
    if problem_id is None:
        return outcomes
    return [item for item in outcomes if item["problem_id"] == problem_id]


def _stable_problem_id(
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
) -> str:
    identity = {
        "operation_phase": operation_phase,
        "requirement_ids": sorted(requirement_ids),
        "root_condition": root_condition,
    }
    return f"problem-{_digest_json(identity)[:32]}"


def _replacement_opportunity(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    current = _latest_context_row(connection)
    if current is None:
        raise FreshnessError("resume requires canonical context")
    graph = json.loads(current["context_json"])["graph"]
    graph_ids = sorted(item["child_id"] for item in graph)
    placeholders = ",".join("?" for _ in graph_ids)
    rows = connection.execute(
        f"SELECT child_id, coverage_json FROM child_operations "
        f"WHERE state = 'stale' AND child_id IN ({placeholders}) "
        "ORDER BY child_id",
        graph_ids,
    ).fetchall()
    children = [row["child_id"] for row in rows]
    requirements: list[str] = []
    if children:
        requirements = sorted(
            {
                requirement
                for row in rows
                for requirement in json.loads(row["coverage_json"])
            }
        )
    return {
        "eligible_child_ids": children,
        "requirement_ids": requirements,
        "source_context_digest": current["digest"],
        "source_context_revision_id": current["revision_id"],
        "status": "available" if children else "not_needed",
    }


def _replay_committed(
    row: sqlite3.Row, kind: str, supplied: Mapping[str, object]
) -> dict[str, object]:
    outcome = json.loads(row["outcome_json"])
    expected = {key: outcome[key] for key in supplied}
    if (
        row["kind"] != kind
        or row["phase"] != "authority_committed"
        or expected != dict(supplied)
    ):
        raise OperationConflict("stable operation ID was reused with new input")
    return outcome


def _latest_pause(connection: sqlite3.Connection) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT outcome_json FROM operations
        WHERE kind = 'parent_pause' AND phase = 'authority_committed'
        ORDER BY created_at DESC, operation_id DESC LIMIT 1
        """
    ).fetchone()
    return json.loads(row["outcome_json"]) if row is not None else None


def _unresolved_operations(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        {
            "epoch": row["epoch"],
            "kind": row["kind"],
            "operation_id": row["operation_id"],
            "phase": row["phase"],
        }
        for row in connection.execute(
            """
            SELECT operation_id, kind, phase, epoch FROM operations
            WHERE phase IN ('prepared', 'effect_observed')
            ORDER BY created_at, operation_id
            """
        ).fetchall()
    ]


def _pause_for_conflict(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    integration_id: str,
    expected: str,
    actual: str,
) -> None:
    pause_parent(
        ledger,
        lease,
        operation_id=f"pause:integration-cas:{integration_id}:{actual[:12]}",
        reason=f"integration CAS mismatch expected {expected} actual {actual}",
        requested_by="runtime-safety",
        requires_human_resume=True,
    )


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = _git_process(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode not in {0, 1}:
        raise GitStateError("git merge-base failed during integration reconciliation")
    return result.returncode == 0


def _transitive_dependents(
    graph: Sequence[Mapping[str, object]], roots: set[str]
) -> set[str]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for node in graph:
            if node["child_id"] not in affected and affected.intersection(
                node["depends_on"]
            ):
                affected.add(node["child_id"])
                changed = True
    return affected
