"""Non-default TaskRun bootstrap, strategy, and structured-ingest helpers."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .authority import (
    InvalidTransition,
    OperationConflict,
    TaskRun,
    TaskRunError,
    build_final_request,
)


STRATEGIES = frozenset({"single", "loop"})
OPERATOR_LOOP_STRATEGY_REVISION = "taskrun-operator-loop-v1"
_OPERATOR_SINGLE_STRATEGY_REVISION = "taskrun-operator-single-v1"
DECISIONS = frozenset(
    {
        "continue",
        "review_required",
        "candidate_ready",
        "retry_exact_slice",
        "pause",
        "intervention",
    }
)
RESULT_FIELDS = frozenset(
    {
        "action_id",
        "actual_effects",
        "actual_touches",
        "artifact_digests",
        "attempt",
        "candidate_digest",
        "checks",
        "diff_digest",
        "effect_receipts",
        "findings",
        "freshness",
        "input_digest",
        "provider_id",
        "requirement_ids",
        "risks",
        "task_run_id",
        "transport_id",
        "tree_digest",
        "unknowns",
        "worker_id",
    }
)
VALIDATED_RESULT_FIELDS = RESULT_FIELDS | {"result_digest"}

_START_FIELDS = frozenset(
    {
        "actions",
        "allowed_effects",
        "approval_refs",
        "authorization_ref",
        "budgets",
        "context_digest",
        "prohibited_effects",
        "providers",
        "reviewers",
        "strategy_revision",
        "workers",
    }
)
_OPERATOR_START_FIELDS = _START_FIELDS | {
    "attempt_offset",
    "identities",
    "review_policy",
}
_OPERATOR_LOOP_START_FIELDS = _OPERATOR_START_FIELDS | {"candidate_commit"}
_OPERATOR_START_FIELDS_WITH_SLOTS = _OPERATOR_START_FIELDS | {"delivery_slots"}
_OPERATOR_LOOP_START_FIELDS_WITH_SLOTS = _OPERATOR_LOOP_START_FIELDS | {
    "delivery_slots"
}
_IDENTITY_FIELDS = frozenset({"base", "contract", "runtime"})
_REVIEW_POLICY_FIELDS = frozenset(
    {"action_risk", "independent_gates", "low_risk_mode"}
)
_INDEPENDENT_REVIEW_GATES = frozenset(
    {"external_effect", "final_candidate", "material_concurrency", "trust_boundary"}
)
_ACTION_FIELDS = frozenset(
    {
        "action_id",
        "checks",
        "dependencies",
        "problem_id",
        "requirement_ids",
        "result_schema",
        "touches",
    }
)
_ACTION_FIELDS_WITH_RESOURCES = _ACTION_FIELDS | {"resources"}
_BUDGET_FIELDS = frozenset(
    {"attempts", "concurrency", "cost", "providers", "reviewers", "workers"}
)
_INTENT_FIELDS = frozenset(
    {
        "action_id",
        "allowed_effects",
        "approval_refs",
        "attempt",
        "checks",
        "freshness",
        "input_digest",
        "problem_id",
        "remaining_budgets",
        "requirement_ids",
        "result_schema",
        "strategy_revision",
        "task_run_id",
        "touches",
    }
)
_REVIEW_FIELDS = frozenset(
    {
        "action_id",
        "attempt",
        "candidate_digest",
        "findings",
        "input_digest",
        "reviewer_id",
        "task_run_id",
        "verdict",
    }
)
_DECISION_FIELDS = frozenset(
    {
        "action_id",
        "attempt",
        "input_digest",
        "kind",
        "next_input_digest",
        "reason",
        "task_run_id",
    }
)
_FINAL_REVIEW_FIELDS = frozenset(
    {
        "candidate_digest",
        "component_candidates",
        "findings",
        "review_id",
        "reviewer_id",
        "task_run_id",
        "verdict",
    }
)
_DISPATCH_FIELDS = frozenset(
    {"claims_digest", "epoch", "fence_digest", "operation_id", "worker_id"}
)
_CANDIDATE_COMMIT_FIELDS = frozenset(
    {"authorization_ref", "expected_old_oid", "ref"}
)
_DELIVERY_SLOT_FIELDS = frozenset(
    {
        "initial_task_dir_name",
        "initial_task_run_id",
        "requirement_ids",
        "scope",
        "slot_digest",
        "slot_id",
        "touches",
    }
)
_GIT_COMMIT_RECEIPT_FIELDS = frozenset(
    {
        "authorization_ref",
        "commit_oid",
        "expected_old_oid",
        "fence_digest",
        "input_digest",
        "operation_id",
        "ref",
        "schema",
        "task_run_id",
        "tree_oid",
    }
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OID = re.compile(r"[0-9a-f]{40,64}\Z")
_SENSITIVE_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secrets",
    }
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?:^|[\s;&])(?:[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY|"
    r"CREDENTIAL)[A-Z0-9_]*)\s*=",
    re.IGNORECASE,
)
_SENSITIVE_TOKEN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{16,}|\bghp_[A-Za-z0-9]{16,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{16,})"
)


class ExecutionError(TaskRunError):
    """Raised when execution-bound data is malformed or unverifiable."""


class InterventionRequired(ExecutionError):
    """Raised when input attempts to widen an approved execution boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason.replace("_", " "))


@contextmanager
def _repository_lock(repo_root: Path) -> Iterator[None]:
    try:
        descriptor = os.open(repo_root, os.O_RDONLY)
    except OSError as exc:
        raise ExecutionError("TaskRun repository is unreadable") from exc
    locked = False
    try:
        # ponytail: repository-wide lock; shard only if bootstrap contention matters.
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def bootstrap_task_run(
    repo_root: Path,
    task_dir_name: str,
    task_json: Mapping[str, object],
    *,
    actor: str,
    strategy: str,
    start_envelope: Mapping[str, object],
) -> TaskRun:
    """Create or exactly resume the one deterministic run bound to a task."""
    root = Path(repo_root).resolve()
    with _repository_lock(root):
        return _bootstrap_task_run_locked(
            root,
            task_dir_name,
            task_json,
            actor=actor,
            strategy=strategy,
            start_envelope=start_envelope,
        )


def _bootstrap_task_run_locked(
    repo_root: Path,
    task_dir_name: str,
    task_json: Mapping[str, object],
    *,
    actor: str,
    strategy: str,
    start_envelope: Mapping[str, object],
) -> TaskRun:
    """Bootstrap while the repository execution lock is already held."""
    root = Path(repo_root).resolve()
    task = _canonical_object(task_json, "task_json")
    task_id = _required_text(task.get("id") or task.get("name"), "task id")
    task_dir_name = _required_text(task_dir_name, "task_dir_name")
    if Path(task_dir_name).name != task_dir_name or task_dir_name in {".", ".."}:
        raise ExecutionError("task_dir_name must be one safe directory name")
    strategy = _required_text(strategy, "strategy")
    if strategy not in STRATEGIES:
        raise ExecutionError("strategy must be single or loop")
    envelope = _validate_start_envelope(start_envelope, strategy)
    actor = _required_text(actor, "actor")
    run_id = f"task-{_digest_json({'task_dir_name': task_dir_name, 'task_id': task_id})}"
    reservation = _assert_unique_binding(root, run_id, task_dir_name, task)
    _validate_supersession_reservation(
        reservation,
        run_id=run_id,
        task_dir_name=task_dir_name,
        task=task,
        actor=actor,
        envelope=envelope,
    )

    meta = task.get("meta") or {}
    if not isinstance(meta, dict):
        raise ExecutionError("task_json.meta must be an object")
    task_run = meta.get("task_run") or {}
    if task_run and (
        not isinstance(task_run, dict)
        or task_run.get("authority") != "sqlite"
        or task_run.get("id") != run_id
    ):
        raise OperationConflict("task_json already names a different TaskRun authority")
    execution = {
        "context_digest": envelope["context_digest"],
        "revision": envelope["strategy_revision"],
        "start_envelope": envelope,
        "strategy": strategy,
    }
    existing_execution = meta.get("execution")
    if existing_execution is not None and existing_execution != execution:
        raise OperationConflict("task_json already contains a different execution binding")
    meta["execution"] = execution
    task["meta"] = meta

    run = TaskRun.initialize(
        root,
        run_id,
        actor=actor,
        task_dir_name=task_dir_name,
        task_json=task,
    )
    run.record_started(operation_id=f"start:{run_id}", actor=actor)
    return run


def plan(snapshot: Mapping[str, object]) -> tuple[dict[str, Any], ...]:
    """Calculate the next deterministic action intents without writing authority."""
    state = _canonical_object(snapshot, "snapshot")
    execution = _execution_state(state)
    if (
        state.get("status") != "running"
        or state.get("terminal") is not None
        or execution.get("paused") is not None
        or execution.get("candidate_ready") is not None
    ):
        return ()

    config = execution["config"]
    strategy = config["strategy"]
    envelope = config["start_envelope"]
    actions = envelope["actions"]
    accepted = _accepted_action_ids(execution)
    active = _active_action_ids(execution)
    capacity = 1 if strategy == "single" else envelope["budgets"]["concurrency"]
    available = max(0, capacity - len(active))
    operator_loop = (
        strategy == "loop"
        and envelope.get("strategy_revision") == OPERATOR_LOOP_STRATEGY_REVISION
    )
    available_workers: list[str] = []
    if operator_loop:
        active_workers = {
            item["intent"]["freshness"]["dispatch"]["worker_id"]
            for item in execution["actions"].values()
            if _terminal_decision(item) is None
        }
        available_workers = [
            worker for worker in envelope["workers"] if worker not in active_workers
        ]
        available = min(
            available,
            len(available_workers),
        )
    if available == 0:
        return ()

    ready = [
        action
        for action in actions
        if action["action_id"] not in accepted
        and action["action_id"] not in active
        and set(action["dependencies"]).issubset(accepted)
        and _action_can_plan(execution, action)
    ]
    ready.sort(key=lambda action: action["action_id"])
    action_specs = {action["action_id"]: action for action in actions}
    occupied = [action_specs[action_id] for action_id in sorted(active)]
    selected = []
    for action in ready:
        if any(_claims_conflict(action, claimed) for claimed in occupied):
            continue
        selected.append(action)
        occupied.append(action)
        if len(selected) == available:
            break
    if operator_loop:
        return tuple(
            _action_intent(
                state,
                action,
                worker_id=available_workers[index],
            )
            for index, action in enumerate(selected)
        )
    return tuple(
        _action_intent(state, action)
        for action in selected
    )


def issue_strategy_actions(run: TaskRun, *, actor: str) -> tuple[dict[str, Any], ...]:
    """Commit the pure strategy plan through the TaskRun operator boundary."""
    intents = plan(run.snapshot())
    for intent in intents:
        run.record_action_planned(
            operation_id=f"plan:{intent['action_id']}:{intent['attempt']}",
            actor=actor,
            intent=intent,
        )
    return intents


def validate_action_result(
    snapshot: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate one captured structured result before any authority mutation."""
    state = _canonical_object(snapshot, "snapshot")
    execution = _execution_state(state)
    if execution.get("paused") is not None:
        raise ExecutionError("execution is paused and requires exact recovery authority")
    value = _exact_object(payload, RESULT_FIELDS, "action result")
    action = _planned_action(execution, value, state["task_run_id"])
    intent = action["intent"]
    for key in ("input_digest", "freshness"):
        if value[key] != intent[key]:
            raise ExecutionError(f"action result {key} is stale")
    dispatch = intent["freshness"].get("dispatch")
    if dispatch is not None:
        bound = _exact_object(dispatch, _DISPATCH_FIELDS, "dispatch fence")
        if value["worker_id"] != bound["worker_id"]:
            raise ExecutionError("action result worker fence is stale")
    if value["requirement_ids"] != intent["requirement_ids"]:
        raise ExecutionError("action result requirement coverage is not exact")
    _reject_sensitive(value)

    value["worker_id"] = _approved_identity(
        value["worker_id"],
        execution["config"]["start_envelope"]["workers"],
        "worker_id",
    )
    value["provider_id"] = _approved_identity(
        value["provider_id"],
        execution["config"]["start_envelope"]["providers"],
        "provider_id",
    )
    value["transport_id"] = _required_text(value["transport_id"], "transport_id")
    value["candidate_digest"] = _required_digest(
        value["candidate_digest"], "candidate_digest"
    )
    value["tree_digest"] = _required_text(value["tree_digest"], "tree_digest")
    value["diff_digest"] = _required_text(value["diff_digest"], "diff_digest")
    value["actual_touches"] = _paths(value["actual_touches"], "actual_touches")
    for path in value["actual_touches"]:
        if not any(_path_matches(path, pattern) for pattern in intent["touches"]):
            raise InterventionRequired("scope_expansion")

    artifacts = _text_mapping(value["artifact_digests"], "artifact_digests")
    for path in artifacts:
        _repo_path(path, "artifact path", allow_pattern=False)
        if not any(_path_matches(path, pattern) for pattern in intent["touches"]):
            raise InterventionRequired("artifact_scope_expansion")
    value["artifact_digests"] = artifacts

    actual_effects = _texts(value["actual_effects"], "actual_effects")
    allowed = set(intent["allowed_effects"])
    prohibited = set(execution["config"]["start_envelope"]["prohibited_effects"])
    if set(actual_effects) & prohibited or not set(actual_effects).issubset(allowed):
        raise InterventionRequired("effect_expansion")
    receipts = _text_mapping(value["effect_receipts"], "effect_receipts")
    if not set(receipts).issubset(actual_effects):
        raise ExecutionError("effect receipt does not name an actual effect")
    value["actual_effects"] = actual_effects
    value["effect_receipts"] = receipts

    checks = _status_mapping(value["checks"], "checks")
    if set(checks) != set(intent["checks"]):
        raise ExecutionError("action result checks do not match the action intent")
    value["checks"] = checks
    value["findings"] = _texts(value["findings"], "findings")
    value["risks"] = _texts(value["risks"], "risks")
    value["unknowns"] = _texts(value["unknowns"], "unknowns")
    if "git_commit" in actual_effects:
        reason = _git_commit_receipt_unknown(
            repo_root,
            execution["config"]["start_envelope"].get("candidate_commit"),
            intent,
            value,
            receipts.get("git_commit"),
        )
        if reason is not None:
            value["unknowns"].append(reason)
    missing_receipts = sorted(set(actual_effects) - set(receipts))
    value["unknowns"].extend(
        f"missing_effect_receipt:{effect}" for effect in missing_receipts
    )
    value["result_digest"] = _digest_json(value)
    if action["result"] is not None:
        if action["result"] != value:
            raise OperationConflict("action result replay conflicts with authority")
        return value
    if _terminal_decision(action) is not None:
        raise ExecutionError("action attempt already has a terminal decision")
    return value


def ingest_action_result(
    run: TaskRun,
    *,
    actor: str,
    payload: Mapping[str, object],
) -> dict[str, Any]:
    """Validate, record, reduce, and record one action result deterministically."""
    try:
        result = validate_action_result(
            run.snapshot(), payload, repo_root=run.repo_root
        )
    except InterventionRequired as exc:
        _record_result_intervention(run, actor=actor, payload=payload, reason=exc.reason)
        raise
    suffix = f"{result['action_id']}:{result['attempt']}"
    run.record_action_result(
        operation_id=f"result:{suffix}", actor=actor, result=result
    )
    decision = reduce(run.snapshot(), result)
    run.record_strategy_decision(
        operation_id=f"decision:{suffix}:{decision['kind']}",
        actor=actor,
        decision=decision,
    )
    return decision


def validate_action_review(
    snapshot: Mapping[str, object], payload: Mapping[str, object]
) -> dict[str, Any]:
    """Validate a fresh independent review against the exact candidate."""
    state = _canonical_object(snapshot, "snapshot")
    execution = _execution_state(state)
    if execution.get("paused") is not None:
        raise ExecutionError("execution is paused and requires exact recovery authority")
    value = _exact_object(payload, _REVIEW_FIELDS, "action review")
    _reject_sensitive(value)
    action = _planned_action(execution, value, state["task_run_id"])
    result = action["result"]
    if result is None:
        raise ExecutionError("review requires one validated result")
    if value["input_digest"] != action["intent"]["input_digest"]:
        raise ExecutionError("review input digest is stale")
    if value["candidate_digest"] != result["candidate_digest"]:
        raise ExecutionError("review candidate digest is stale")
    value["reviewer_id"] = _approved_identity(
        value["reviewer_id"],
        execution["config"]["start_envelope"]["reviewers"],
        "reviewer_id",
    )
    if value["reviewer_id"] == result["worker_id"]:
        raise InterventionRequired("worker cannot review its own candidate")
    value["verdict"] = _required_text(value["verdict"], "verdict")
    if value["verdict"] not in {"accepted", "changes_required"}:
        raise ExecutionError("review verdict must be accepted or changes_required")
    value["findings"] = _texts(value["findings"], "review findings")
    if value["verdict"] == "accepted" and value["findings"]:
        raise ExecutionError("accepted review must not contain required findings")
    if value["verdict"] == "changes_required" and not value["findings"]:
        raise ExecutionError("changes_required review requires actionable findings")
    if action["review"] is not None:
        if action["review"] != value:
            raise OperationConflict("action review replay conflicts with authority")
        return value
    if _terminal_decision(action) is not None:
        raise ExecutionError("review cannot follow a terminal strategy decision")
    return value


def record_action_review_and_reduce(
    run: TaskRun,
    *,
    actor: str,
    payload: Mapping[str, object],
) -> dict[str, Any]:
    """Record an independent review and its deterministic strategy decision."""
    review = validate_action_review(run.snapshot(), payload)
    suffix = f"{review['action_id']}:{review['attempt']}"
    run.record_action_review(
        operation_id=f"review:{suffix}", actor=actor, review=review
    )
    state = run.snapshot()
    action = _planned_action(state["execution"], review, state["task_run_id"])
    decision = reduce(state, action["result"])
    run.record_strategy_decision(
        operation_id=f"decision:{suffix}:{decision['kind']}",
        actor=actor,
        decision=decision,
    )
    return decision


def reduce(
    snapshot: Mapping[str, object], validated_result: Mapping[str, object]
) -> dict[str, Any]:
    """Calculate the next decision from authority facts without writing them."""
    state = _canonical_object(snapshot, "snapshot")
    execution = _execution_state(state)
    result = _exact_object(
        validated_result, VALIDATED_RESULT_FIELDS, "validated action result"
    )
    action = _planned_action(execution, result, state["task_run_id"])
    if action["result"] != result:
        raise ExecutionError("validated result is not the current authority fact")
    if result["unknowns"]:
        return _decision(state, result, "pause", "unknown_outcome")
    if any(status != "passed" for status in result["checks"].values()):
        return _retry_or_pause(state, result, "required_check_failed")
    if result["findings"]:
        return _retry_or_pause(state, result, "worker_finding_requires_repair")
    review = action["review"]
    if review is None:
        return _decision(state, result, "review_required", "independent_review_required")
    if review["verdict"] != "accepted" or review["findings"]:
        return _retry_or_pause(state, result, "review_requires_repair")
    accepted = _accepted_action_ids(execution) | {result["action_id"]}
    required = {
        item["action_id"]
        for item in execution["config"]["start_envelope"]["actions"]
    }
    if execution["config"]["strategy"] == "single" and accepted == required:
        kind = "candidate_ready"
        reason = "all_required_actions_reviewed"
    else:
        kind = "continue"
        reason = "slice_accepted"
    return _decision(state, result, kind, reason)


def record_final_candidate_review(
    run: TaskRun,
    *,
    actor: str,
    payload: Mapping[str, object],
) -> dict[str, Any]:
    """Bind a loop aggregate candidate to one fresh accepted final review."""
    final = _exact_object(payload, _FINAL_REVIEW_FIELDS, "final candidate review")
    _reject_sensitive(final)
    candidate_digest = _required_digest(
        final["candidate_digest"], "candidate_digest"
    )
    review_id = _required_text(final["review_id"], "review_id")
    run.record_final_candidate_review(
        operation_id=f"final:{candidate_digest}:{review_id}",
        actor=actor,
        final_candidate=final,
    )
    ready = run.snapshot()["execution"]["candidate_ready"]
    if not isinstance(ready, dict):
        raise ExecutionError("final candidate review did not create readiness authority")
    return copy.deepcopy(ready)


def issue_final_request(run: TaskRun, *, actor: str) -> dict[str, Any]:
    """Record and return one exact immutable final request."""
    final_request = build_final_request(run.snapshot())
    run.record_final_request(
        operation_id=f"final-request:{final_request['request_digest']}",
        actor=actor,
        final_request=final_request,
    )
    stored = run.snapshot()["execution"]["final_request"]
    if not isinstance(stored, dict):
        raise ExecutionError("final request was not recorded")
    return copy.deepcopy(stored)


def record_final_acceptance(
    run: TaskRun,
    *,
    actor: str,
    receipt: Mapping[str, object],
) -> dict[str, Any]:
    """Consume one outer-operator receipt and return its terminal binding."""
    gate_receipt = _canonical_object(receipt, "final gate receipt")
    receipt_digest = _required_digest(
        gate_receipt.get("receipt_digest"), "final gate receipt.receipt_digest"
    )
    run.record_final_response(
        operation_id=f"final-response:{receipt_digest}",
        actor=actor,
        final_gate_receipt=gate_receipt,
    )
    binding = run.snapshot()["execution"]["terminal_authority"]
    if not isinstance(binding, dict):
        raise ExecutionError("final response did not create terminal authority")
    return copy.deepcopy(binding)


def resume_execution(
    run: TaskRun,
    *,
    actor: str,
    authorization_ref: str,
    intervention_digest: str,
) -> dict[str, Any]:
    """Clear one exact intervention through a separately authorized fact."""
    digest = _required_digest(intervention_digest, "intervention_digest")
    resume = {
        "authorization_ref": _required_text(authorization_ref, "authorization_ref"),
        "intervention_digest": digest,
        "task_run_id": run.task_run_id,
    }
    return run.record_execution_resumed(
        operation_id=f"resume:{digest}", actor=actor, resume=resume
    )


def _record_result_intervention(
    run: TaskRun,
    *,
    actor: str,
    payload: Mapping[str, object],
    reason: str,
) -> None:
    state = run.snapshot()
    execution = _execution_state(state)
    value = _canonical_object(payload, "action result")
    action = _planned_action(execution, value, state["task_run_id"])
    intent = action["intent"]
    generation = len(execution["resumes"]) + 1
    intervention = {
        "action_id": intent["action_id"],
        "attempt": intent["attempt"],
        "generation": generation,
        "input_digest": intent["input_digest"],
        "phase": "result",
        "reason": reason,
        "task_run_id": state["task_run_id"],
    }
    intervention["intervention_digest"] = _digest_json(intervention)
    run.record_execution_intervention(
        operation_id=(
            f"intervention:{intent['action_id']}:{intent['attempt']}:"
            f"{generation}:{reason}"
        ),
        actor=actor,
        intervention=intervention,
    )


def adapt_loop_result(
    intent: Mapping[str, object], accepted_result: Mapping[str, object]
) -> dict[str, Any]:
    """Convert an already accepted Loop v1 result without writing either authority."""
    from loop_v1.context import RESULT_FIELDS as LOOP_RESULT_FIELDS

    action = _exact_object(intent, _INTENT_FIELDS, "action intent")
    wrapper = _exact_object(
        accepted_result,
        frozenset({"accepted", "failed_tests", "payload_digest", "replayed", "result"}),
        "accepted Loop result",
    )
    if wrapper["accepted"] is not True or wrapper["failed_tests"] != []:
        raise ExecutionError("Loop adapter requires an accepted child result")
    legacy = _exact_object(wrapper["result"], LOOP_RESULT_FIELDS, "Loop child result")
    if wrapper["payload_digest"] != _digest_json(legacy):
        raise ExecutionError("Loop result payload digest is invalid")
    commands = {
        _required_text(item.get("command"), "command"): _required_text(
            item.get("status"), "command status"
        )
        for item in _mapping_sequence(legacy["commands"], "commands")
    }
    artifacts = {
        _required_text(item.get("path"), "artifact path"): _required_text(
            item.get("digest"), "artifact digest"
        )
        for item in _mapping_sequence(legacy["artifacts"], "artifacts")
    }
    candidate_digest = _digest_json(
        {
            "artifacts": artifacts,
            "diff_identity": legacy["diff_identity"],
            "result_tree_id": legacy["result_tree_id"],
        }
    )
    return {
        "action_id": action["action_id"],
        "actual_effects": [],
        "actual_touches": copy.deepcopy(legacy["actual_touches"]),
        "artifact_digests": artifacts,
        "attempt": action["attempt"],
        "candidate_digest": candidate_digest,
        "checks": commands,
        "diff_digest": _required_text(legacy["diff_identity"], "diff_identity"),
        "effect_receipts": {},
        "findings": copy.deepcopy(legacy["findings"]),
        "freshness": copy.deepcopy(action["freshness"]),
        "input_digest": action["input_digest"],
        "provider_id": "legacy-loop",
        "requirement_ids": copy.deepcopy(legacy["coverage"]),
        "risks": copy.deepcopy(legacy["risks"]),
        "task_run_id": action["task_run_id"],
        "transport_id": f"legacy-loop:{legacy['packet_id']}",
        "tree_digest": _required_text(legacy["result_tree_id"], "result_tree_id"),
        "unknowns": [],
        "worker_id": _required_text(legacy["child_id"], "child_id"),
    }


def _validate_start_envelope(
    value: Mapping[str, object], strategy: str
) -> dict[str, Any]:
    envelope = _canonical_object(value, "start envelope")
    actual_fields = frozenset(envelope)
    operator_single_fields = {
        _OPERATOR_START_FIELDS,
        _OPERATOR_START_FIELDS_WITH_SLOTS,
    }
    operator_loop_fields = {
        _OPERATOR_LOOP_START_FIELDS,
        _OPERATOR_LOOP_START_FIELDS_WITH_SLOTS,
    }
    operator_bound = actual_fields in operator_single_fields | operator_loop_fields
    operator_loop = actual_fields in operator_loop_fields
    valid_fields = {_START_FIELDS} | operator_single_fields | operator_loop_fields
    if actual_fields not in valid_fields:
        raise ExecutionError(
            "start envelope fields do not match the legacy or operator schema"
        )
    _reject_sensitive(envelope)
    envelope["authorization_ref"] = _required_text(
        envelope["authorization_ref"], "authorization_ref"
    )
    envelope["strategy_revision"] = _required_text(
        envelope["strategy_revision"], "strategy_revision"
    )
    if operator_bound:
        if strategy == "loop" and not operator_loop:
            raise ExecutionError("operator start envelope requires the single strategy")
        if strategy == "single" and operator_loop:
            raise ExecutionError(
                "operator start envelope strategy revision does not match strategy"
            )
        expected_revision = {
            "loop": OPERATOR_LOOP_STRATEGY_REVISION,
            "single": _OPERATOR_SINGLE_STRATEGY_REVISION,
        }[strategy]
        if envelope["strategy_revision"] != expected_revision:
            if (
                strategy == "loop"
                and envelope["strategy_revision"]
                == _OPERATOR_SINGLE_STRATEGY_REVISION
            ):
                raise ExecutionError(
                    "operator start envelope requires the single strategy"
                )
            raise ExecutionError(
                "operator start envelope strategy revision does not match strategy"
            )
    envelope["context_digest"] = _required_digest(
        envelope["context_digest"], "context_digest"
    )
    for field in (
        "allowed_effects",
        "approval_refs",
        "prohibited_effects",
        "providers",
        "reviewers",
        "workers",
    ):
        envelope[field] = _texts(envelope[field], field)
    if not envelope["approval_refs"] or not envelope["workers"] or not envelope["reviewers"]:
        raise ExecutionError("approval_refs, workers, and reviewers must not be empty")
    if set(envelope["allowed_effects"]) & set(envelope["prohibited_effects"]):
        raise ExecutionError("allowed and prohibited effects overlap")
    if operator_loop:
        binding = envelope["candidate_commit"]
        if binding is None:
            if (
                "git_commit" in envelope["allowed_effects"]
                or "git_commit" not in envelope["prohibited_effects"]
            ):
                raise ExecutionError(
                    "git_commit requires an immutable candidate binding"
                )
        else:
            binding = _exact_object(
                binding, _CANDIDATE_COMMIT_FIELDS, "candidate_commit"
            )
            binding["authorization_ref"] = _required_text(
                binding["authorization_ref"], "candidate_commit.authorization_ref"
            )
            binding["expected_old_oid"] = _required_git_oid(
                binding["expected_old_oid"], "candidate_commit.expected_old_oid"
            )
            binding["ref"] = _required_local_branch_ref(
                binding["ref"], "candidate_commit.ref"
            )
            if binding["authorization_ref"] == envelope["authorization_ref"]:
                raise ExecutionError(
                    "candidate_commit requires separate authorization"
                )
            if (
                binding["authorization_ref"] not in envelope["approval_refs"]
                or "git_commit" not in envelope["allowed_effects"]
                or "git_commit" in envelope["prohibited_effects"]
            ):
                raise ExecutionError(
                    "candidate_commit is not bound to the approved effect surface"
                )
            envelope["candidate_commit"] = binding

    budgets = _exact_object(envelope["budgets"], _BUDGET_FIELDS, "budgets")
    for field in _BUDGET_FIELDS:
        budgets[field] = _nonnegative_int(budgets[field], f"budgets.{field}")
    if not 1 <= budgets["attempts"] <= 4:
        raise ExecutionError("attempt budget must be one initial plus at most three repairs")
    if budgets["concurrency"] < 1 or budgets["workers"] < 1 or budgets["reviewers"] < 1:
        raise ExecutionError("execution capacity budgets must be positive")
    if budgets["workers"] > len(envelope["workers"]):
        raise ExecutionError("worker budget exceeds the approved worker surface")
    if budgets["reviewers"] > len(envelope["reviewers"]):
        raise ExecutionError("reviewer budget exceeds the approved reviewer surface")
    if budgets["providers"] > len(envelope["providers"]):
        raise ExecutionError("provider budget exceeds the approved provider surface")
    if strategy == "single" and budgets["concurrency"] != 1:
        raise ExecutionError("single strategy concurrency must be one")
    envelope["budgets"] = budgets

    if operator_bound:
        offset = _nonnegative_int(envelope["attempt_offset"], "attempt_offset")
        if offset + budgets["attempts"] > 4:
            raise ExecutionError("operator repair budget exceeds four total attempts")
        envelope["attempt_offset"] = offset

        identities = _exact_object(
            envelope["identities"], _IDENTITY_FIELDS, "identities"
        )
        base = _required_text(identities["base"], "identities.base")
        if not _GIT_OID.fullmatch(base):
            raise ExecutionError(
                "identities.base must be one full lowercase Git object ID"
            )
        identities["base"] = base
        for field in ("contract", "runtime"):
            identities[field] = _required_digest(
                identities[field], f"identities.{field}"
            )
        envelope["identities"] = identities
        candidate_commit = envelope.get("candidate_commit")
        if (
            isinstance(candidate_commit, Mapping)
            and candidate_commit["expected_old_oid"] != identities["base"]
        ):
            raise ExecutionError(
                "candidate_commit expected old object must equal identities.base"
            )

        review_policy = _exact_object(
            envelope["review_policy"], _REVIEW_POLICY_FIELDS, "review_policy"
        )
        review_policy["action_risk"] = _required_text(
            review_policy["action_risk"], "review_policy.action_risk"
        )
        if review_policy["action_risk"] not in {"low", "high"}:
            raise ExecutionError("review_policy.action_risk must be low or high")
        review_policy["low_risk_mode"] = _required_text(
            review_policy["low_risk_mode"], "review_policy.low_risk_mode"
        )
        if review_policy["low_risk_mode"] not in {"aggregate", "independent"}:
            raise ExecutionError(
                "review_policy.low_risk_mode must be aggregate or independent"
            )
        review_policy["independent_gates"] = _texts(
            review_policy["independent_gates"], "review_policy.independent_gates"
        )
        if set(review_policy["independent_gates"]) != _INDEPENDENT_REVIEW_GATES:
            raise ExecutionError("review_policy cannot weaken independent gates")
        envelope["review_policy"] = review_policy

        if "delivery_slots" in envelope:
            slots = _mapping_sequence(envelope["delivery_slots"], "delivery_slots")
            if not slots:
                raise ExecutionError("delivery_slots must not be empty")
            seen_tasks: set[str] = set()
            seen_runs: set[str] = set()
            seen_requirements: set[str] = set()
            normalized_slots = []
            for index, raw_slot in enumerate(slots, start=1):
                slot = _exact_object(raw_slot, _DELIVERY_SLOT_FIELDS, "delivery slot")
                slot["slot_id"] = _required_text(slot["slot_id"], "delivery slot id")
                if slot["slot_id"] != f"slot-{index:03d}":
                    raise ExecutionError("delivery slots are not in canonical order")
                slot["initial_task_dir_name"] = _required_text(
                    slot["initial_task_dir_name"], "delivery slot initial task"
                )
                slot["initial_task_run_id"] = _required_text(
                    slot["initial_task_run_id"], "delivery slot initial TaskRun"
                )
                if not re.fullmatch(r"task-[0-9a-f]{64}", slot["initial_task_run_id"]):
                    raise ExecutionError("delivery slot initial TaskRun ID is invalid")
                slot["requirement_ids"] = _texts(
                    slot["requirement_ids"], "delivery slot requirement_ids"
                )
                slot["touches"] = _paths(slot["touches"], "delivery slot touches")
                if slot["scope"] is not None:
                    slot["scope"] = _required_text(slot["scope"], "delivery slot scope")
                if (
                    slot["initial_task_dir_name"] in seen_tasks
                    or slot["initial_task_run_id"] in seen_runs
                    or seen_requirements.intersection(slot["requirement_ids"])
                ):
                    raise ExecutionError("delivery slot identities overlap")
                seen_tasks.add(slot["initial_task_dir_name"])
                seen_runs.add(slot["initial_task_run_id"])
                seen_requirements.update(slot["requirement_ids"])
                expected_digest = _digest_json(
                    {
                        key: slot[key]
                        for key in ("requirement_ids", "scope", "slot_id", "touches")
                    }
                )
                if slot["slot_digest"] != expected_digest:
                    raise ExecutionError("delivery slot digest is invalid")
                normalized_slots.append(slot)
            envelope["delivery_slots"] = normalized_slots

    actions = _mapping_sequence(envelope["actions"], "actions")
    if not actions or (strategy == "single" and len(actions) != 1):
        raise ExecutionError("single requires exactly one action; loop requires at least one")
    normalized = []
    action_ids = set()
    problem_ids = set()
    for raw in actions:
        fields = frozenset(raw)
        if fields not in {_ACTION_FIELDS, _ACTION_FIELDS_WITH_RESOURCES}:
            raise ExecutionError("action fields do not match the schema")
        action = _exact_object(raw, fields, "action")
        action["action_id"] = _required_text(action["action_id"], "action_id")
        action["problem_id"] = _required_text(action["problem_id"], "problem_id")
        if action["action_id"] in action_ids or action["problem_id"] in problem_ids:
            raise ExecutionError("action and problem identities must be unique")
        action_ids.add(action["action_id"])
        problem_ids.add(action["problem_id"])
        action["requirement_ids"] = _texts(action["requirement_ids"], "requirement_ids")
        action["dependencies"] = _texts(action["dependencies"], "dependencies")
        action["touches"] = _paths(action["touches"], "touches")
        if "resources" in action:
            action["resources"] = _texts(action["resources"], "resources")
        action["checks"] = _texts(action["checks"], "checks")
        action["result_schema"] = _texts(action["result_schema"], "result_schema")
        if not action["requirement_ids"] or not action["checks"]:
            raise ExecutionError("each action requires coverage and checks")
        if set(action["result_schema"]) != set(RESULT_FIELDS):
            raise ExecutionError("action result_schema does not match the ingest schema")
        normalized.append(action)
    for action in normalized:
        if not set(action["dependencies"]).issubset(action_ids - {action["action_id"]}):
            raise ExecutionError("action dependency is unknown or self-referential")
    _assert_acyclic(normalized)
    if "delivery_slots" in envelope and {
        requirement
        for action in normalized
        for requirement in action["requirement_ids"]
    } != {
        requirement
        for slot in envelope["delivery_slots"]
        for requirement in slot["requirement_ids"]
    }:
        raise ExecutionError("delivery slots do not cover the execution requirements")
    if operator_bound and strategy == "loop":
        if budgets["concurrency"] > budgets["workers"]:
            raise ExecutionError(
                "operator loop concurrency cannot exceed its worker budget"
            )
        candidate_commit = envelope.get("candidate_commit")
        if isinstance(candidate_commit, Mapping):
            git_claim = f"git-ref:{candidate_commit['ref']}"
            if any(
                git_claim not in action.get("resources", [])
                for action in normalized
            ):
                raise ExecutionError(
                    "candidate commit ref must be one shared resource claim"
                )
    envelope["actions"] = normalized
    return envelope


def _task_identity_values(task: Mapping[str, object]) -> set[str]:
    return {
        value.strip()
        for field in ("id", "name")
        if isinstance((value := task.get(field)), str) and value.strip()
    }


def _assert_unique_binding(
    root: Path,
    run_id: str,
    task_dir_name: str,
    task: Mapping[str, object],
) -> dict[str, Any] | None:
    runs_root = root / ".trellis/.runtime/taskrun/runs"
    if not runs_root.is_dir():
        return None
    task_identities = _task_identity_values(task)
    reservations = []
    # ponytail: linear scan avoids a second registry; index only at measured scale.
    for database in sorted(runs_root.glob("*/authority.sqlite3")):
        existing_id = database.parent.name
        run = TaskRun.open(root, existing_id)
        state = run.snapshot()
        existing_task = state["task_json_seed"]
        if existing_id != run_id and (
            state["task_dir_name"] == task_dir_name
            or _task_identity_values(existing_task) & task_identities
        ):
            raise OperationConflict("task identity is already bound to another TaskRun")
        terminal = state.get("terminal")
        evidence = terminal.get("evidence") if isinstance(terminal, Mapping) else None
        if isinstance(evidence, Mapping):
            successor = evidence.get("successor")
            successor_task = (
                successor.get("task_json") if isinstance(successor, Mapping) else None
            )
            successor_task_identities = (
                _task_identity_values(successor_task)
                if isinstance(successor_task, Mapping)
                else set()
            )
            if (
                evidence.get("superseded_by") == run_id
                or (
                    isinstance(successor, Mapping)
                    and successor.get("task_dir_name") == task_dir_name
                )
                or successor_task_identities & task_identities
            ):
                reservations.append(
                    _canonical_object(evidence, "supersession evidence")
                )
    if len(reservations) > 1:
        raise OperationConflict("successor has multiple supersession reservations")
    return reservations[0] if reservations else None


def _validate_supersession_reservation(
    reservation: Mapping[str, object] | None,
    *,
    run_id: str,
    task_dir_name: str,
    task: dict[str, Any],
    actor: str,
    envelope: dict[str, Any],
) -> None:
    if reservation is None:
        return
    budget = reservation.get("repair_budget")
    successor = reservation.get("successor")
    if (
        reservation.get("kind") != "material_drift"
        or reservation.get("superseded_by") != run_id
        or not isinstance(budget, Mapping)
        or not isinstance(successor, Mapping)
    ):
        raise OperationConflict("supersession reservation is invalid")
    if set(budget) != {"consumed", "remaining", "total"}:
        raise OperationConflict("supersession repair budget is invalid")
    consumed, remaining, total = (
        budget.get("consumed"),
        budget.get("remaining"),
        budget.get("total"),
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (consumed, remaining, total)
    ) or consumed + remaining != total or not 1 <= total <= 4:
        raise OperationConflict("supersession repair budget is invalid")
    successor_fields = {"actor", "task_dir_name", "task_json"}
    if remaining:
        successor_fields.add("start_envelope")
    if set(successor) != successor_fields:
        raise OperationConflict("supersession successor seed is invalid")
    if (
        successor.get("actor") != actor
        or successor.get("task_dir_name") != task_dir_name
        or _canonical_object(successor.get("task_json"), "successor task") != task
    ):
        raise OperationConflict("bootstrap does not match supersession reservation")
    if remaining == 0:
        raise OperationConflict("successor repair budget is exhausted")
    if (
        successor.get("start_envelope") != envelope
        or envelope["attempt_offset"] != consumed
        or envelope["budgets"]["attempts"] != remaining
    ):
        raise OperationConflict("bootstrap does not match supersession reservation")


def _execution_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    execution = snapshot.get("execution")
    if not isinstance(execution, dict):
        raise ExecutionError("snapshot is not a bootstrapped TaskRun")
    return execution


def _action_intent(
    state: dict[str, Any],
    action: dict[str, Any],
    *,
    worker_id: str | None = None,
) -> dict[str, Any]:
    execution = state["execution"]
    config = execution["config"]
    envelope = config["start_envelope"]
    problem_attempts = sum(
        1
        for item in execution["actions"].values()
        if item["intent"]["problem_id"] == action["problem_id"]
    )
    attempt = problem_attempts + 1
    budgets = envelope["budgets"]
    reconciliation = execution.get("commit_reconciliation")
    reconciled = (
        reconciliation.get("request")
        if isinstance(reconciliation, dict)
        and isinstance(reconciliation.get("response"), dict)
        else None
    )
    freshness = {
        "authority_position": state["position"],
        "context_digest": (
            reconciled["observed_identities"]["context"]
            if reconciled is not None
            else config["context_digest"]
        ),
        "strategy_revision": config["revision"],
    }
    if "identities" in envelope:
        freshness["identities"] = copy.deepcopy(
            {
                key: value
                for key, value in reconciled["observed_identities"].items()
                if key != "context"
            }
            if reconciled is not None
            else envelope["identities"]
        )
    if worker_id is not None:
        dispatch = {
            "claims_digest": _digest_json(
                {
                    "resources": action.get("resources", []),
                    "touches": action["touches"],
                }
            ),
            "epoch": state["position"],
            "operation_id": (
                f"taskrun:{state['task_run_id']}:{action['action_id']}:{attempt}"
            ),
            "worker_id": worker_id,
        }
        dispatch["fence_digest"] = _digest_json(dispatch)
        freshness["dispatch"] = dispatch
    remaining_attempts = budgets["attempts"] - problem_attempts
    if worker_id is not None:
        remaining_attempts = (
            budgets["attempts"]
            - 1
            - _loop_repair_reservations(execution)
        )
    intent = {
        "action_id": action["action_id"],
        "allowed_effects": copy.deepcopy(envelope["allowed_effects"]),
        "approval_refs": copy.deepcopy(envelope["approval_refs"]),
        "attempt": attempt,
        "checks": copy.deepcopy(action["checks"]),
        "freshness": freshness,
        "problem_id": action["problem_id"],
        "remaining_budgets": {
            "attempts": remaining_attempts,
            "concurrency": budgets["concurrency"],
            "cost": budgets["cost"],
            "providers": budgets["providers"],
            "reviewers": budgets["reviewers"],
            "workers": budgets["workers"],
        },
        "requirement_ids": copy.deepcopy(action["requirement_ids"]),
        "result_schema": copy.deepcopy(action["result_schema"]),
        "strategy_revision": config["revision"],
        "task_run_id": state["task_run_id"],
        "touches": copy.deepcopy(action["touches"]),
    }
    intent["input_digest"] = _digest_json(intent)
    return intent


def _action_can_plan(execution: dict[str, Any], spec: dict[str, Any]) -> bool:
    history = sorted(
        (
            item
            for item in execution["actions"].values()
            if item["intent"]["action_id"] == spec["action_id"]
        ),
        key=lambda item: item["intent"]["attempt"],
    )
    if not history:
        return True
    decision = _terminal_decision(history[-1])
    return decision is not None and decision["kind"] == "retry_exact_slice"


def _accepted_action_ids(execution: dict[str, Any]) -> set[str]:
    accepted = set()
    for action in execution["actions"].values():
        decision = _terminal_decision(action)
        if decision and decision["kind"] in {"continue", "candidate_ready"}:
            accepted.add(action["intent"]["action_id"])
    return accepted


def _loop_repair_reservations(execution: dict[str, Any]) -> int:
    reservations = 0
    for action in execution["actions"].values():
        decision = _terminal_decision(action)
        if decision is not None and decision["kind"] == "retry_exact_slice":
            reservations += 1
    return reservations


def _active_action_ids(execution: dict[str, Any]) -> set[str]:
    active = set()
    for action in execution["actions"].values():
        if _terminal_decision(action) is None:
            active.add(action["intent"]["action_id"])
    return active


def _terminal_decision(action: dict[str, Any]) -> dict[str, Any] | None:
    terminal = [
        decision
        for decision in action["decisions"]
        if decision["kind"] != "review_required"
    ]
    return terminal[-1] if terminal else None


def _planned_action(
    execution: dict[str, Any], value: dict[str, Any], task_run_id: str
) -> dict[str, Any]:
    if value.get("task_run_id") != task_run_id:
        raise ExecutionError("action task_run_id does not match authority")
    action_id = _required_text(value.get("action_id"), "action_id")
    attempt = _positive_int(value.get("attempt"), "attempt")
    action = execution["actions"].get(f"{action_id}:{attempt}")
    if action is None:
        raise ExecutionError("action attempt is not planned")
    return action


def _retry_or_pause(
    state: dict[str, Any], result: dict[str, Any], reason: str
) -> dict[str, Any]:
    execution = state["execution"]
    envelope = execution["config"]["start_envelope"]
    attempts = envelope["budgets"]["attempts"]
    if envelope.get("strategy_revision") == OPERATOR_LOOP_STRATEGY_REVISION:
        retry_available = _loop_repair_reservations(execution) < attempts - 1
    else:
        retry_available = result["attempt"] < attempts
    kind = "retry_exact_slice" if retry_available else "pause"
    if kind == "pause":
        reason = "retry_budget_exhausted"
    return _decision(state, result, kind, reason)


def _decision(
    state: dict[str, Any], result: dict[str, Any], kind: str, reason: str
) -> dict[str, Any]:
    if kind not in DECISIONS:
        raise ExecutionError("strategy decision is unknown")
    decision = {
        "action_id": result["action_id"],
        "attempt": result["attempt"],
        "input_digest": result["input_digest"],
        "kind": kind,
        "reason": reason,
        "task_run_id": state["task_run_id"],
    }
    decision["next_input_digest"] = _digest_json(decision)
    if set(decision) != _DECISION_FIELDS:
        raise ExecutionError("strategy decision does not match the schema")
    return decision


def _assert_acyclic(actions: Sequence[dict[str, Any]]) -> None:
    remaining = {item["action_id"]: set(item["dependencies"]) for item in actions}
    while remaining:
        ready = {action_id for action_id, deps in remaining.items() if not deps}
        if not ready:
            raise ExecutionError("action dependency graph contains a cycle")
        remaining = {
            action_id: deps - ready
            for action_id, deps in remaining.items()
            if action_id not in ready
        }


def _approved_identity(value: object, approved: list[str], field: str) -> str:
    identity = _required_text(value, field)
    if identity not in approved:
        reason = {
            "provider_id": "provider_surface_expansion",
            "reviewer_id": "reviewer_surface_expansion",
            "worker_id": "worker_surface_expansion",
        }.get(field, "identity_surface_expansion")
        raise InterventionRequired(reason)
    return identity


def _exact_object(
    value: object, fields: frozenset[str], label: str
) -> dict[str, Any]:
    normalized = _canonical_object(value, label)
    actual = set(normalized)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise ExecutionError(f"{label} fields are invalid: {'; '.join(details)}")
    return normalized


def _canonical_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionError(f"{label} must be an object")
    try:
        normalized = json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ExecutionError(f"{label} is not canonical JSON: {exc}") from exc
    if not isinstance(normalized, dict):
        raise ExecutionError(f"{label} must be an object")
    return normalized


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _digest_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError(f"{field} must be a non-empty string")
    return value.strip()


def _required_digest(value: object, field: str) -> str:
    digest = _required_text(value, field)
    if not _DIGEST.fullmatch(digest):
        raise ExecutionError(f"{field} must be one lowercase SHA-256 digest")
    return digest


def _required_git_oid(value: object, field: str) -> str:
    oid = _required_text(value, field)
    if not _GIT_OID.fullmatch(oid):
        raise ExecutionError(f"{field} must be one full lowercase Git object ID")
    return oid


def _required_local_branch_ref(value: object, field: str) -> str:
    ref = _required_text(value, field)
    if (
        not ref.startswith("refs/heads/")
        or ref.endswith("/")
        or ref.endswith(".")
        or ".." in ref
        or "@{" in ref
        or any(character in ref for character in " ~^:?*[\\")
    ):
        raise ExecutionError(f"{field} must be one full local branch ref")
    return ref


def _git_commit_receipt_unknown(
    repo_root: Path | None,
    binding: object,
    intent: dict[str, Any],
    result: dict[str, Any],
    receipt: str | None,
) -> str | None:
    reason = "git_commit_receipt_unverified"
    if repo_root is None or not isinstance(binding, Mapping) or receipt is None:
        return reason
    try:
        raw = json.loads(receipt)
        value = _exact_object(
            raw, _GIT_COMMIT_RECEIPT_FIELDS, "git commit receipt"
        )
        if receipt != _canonical_json(value):
            return reason
        value["schema"] = _required_text(value["schema"], "git receipt.schema")
        value["task_run_id"] = _required_text(
            value["task_run_id"], "git receipt.task_run_id"
        )
        value["operation_id"] = _required_text(
            value["operation_id"], "git receipt.operation_id"
        )
        value["input_digest"] = _required_digest(
            value["input_digest"], "git receipt.input_digest"
        )
        value["fence_digest"] = _required_digest(
            value["fence_digest"], "git receipt.fence_digest"
        )
        value["authorization_ref"] = _required_text(
            value["authorization_ref"], "git receipt.authorization_ref"
        )
        value["ref"] = _required_local_branch_ref(
            value["ref"], "git receipt.ref"
        )
        for field in ("commit_oid", "expected_old_oid", "tree_oid"):
            value[field] = _required_git_oid(value[field], f"git receipt.{field}")
    except (ExecutionError, json.JSONDecodeError, TypeError, ValueError):
        return reason

    dispatch = intent["freshness"].get("dispatch") or {}
    expected = {
        "authorization_ref": binding.get("authorization_ref"),
        "expected_old_oid": binding.get("expected_old_oid"),
        "fence_digest": dispatch.get("fence_digest"),
        "input_digest": intent["input_digest"],
        "operation_id": dispatch.get("operation_id"),
        "ref": binding.get("ref"),
        "schema": "taskrun-git-commit-receipt-v1",
        "task_run_id": intent["task_run_id"],
    }
    if any(
        value[field] != expected_value
        for field, expected_value in expected.items()
    ):
        return reason
    expected_candidate = _digest_json(
        {"commit_oid": value["commit_oid"], "tree_oid": value["tree_oid"]}
    )
    if (
        result["tree_digest"] != value["tree_oid"]
        or result["candidate_digest"] != expected_candidate
    ):
        return reason

    root = Path(repo_root).resolve()
    try:
        if not _candidate_ref_is_independent(root, value["ref"]):
            return reason
        current = _git_text(root, "rev-parse", "--verify", f"{value['ref']}^{{commit}}")
        tree = _git_text(
            root, "rev-parse", "--verify", f"{value['commit_oid']}^{{tree}}"
        )
        parents = _git_text(
            root, "rev-list", "--parents", "-n", "1", value["commit_oid"]
        ).split()
        message = _git_text(root, "show", "-s", "--format=%B", value["commit_oid"])
        changed_paths = _git_text(
            root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            value["commit_oid"],
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return reason
    if (
        current != value["commit_oid"]
        or tree != value["tree_oid"]
        or parents != [value["commit_oid"], value["expected_old_oid"]]
        or sorted(changed_paths) != sorted(result["actual_touches"])
    ):
        return reason
    trailers = (
        f"TaskRun-Operation: {value['operation_id']}",
        f"TaskRun-Input: {value['input_digest']}",
        f"TaskRun-Fence: {value['fence_digest']}",
        f"TaskRun-Authorization: {value['authorization_ref']}",
    )
    if not _taskrun_commit_trailers_match(message, trailers):
        return reason
    return None


def _taskrun_commit_trailers_match(
    message: str, trailers: Sequence[str]
) -> bool:
    expected = {trailer.partition(":")[0].casefold(): trailer for trailer in trailers}
    observed = {key: [] for key in expected}
    for line in message.splitlines():
        key, separator, _ = line.partition(":")
        normalized = key.strip().casefold()
        if not separator or not normalized.startswith("taskrun-"):
            continue
        if normalized not in observed:
            return False
        observed[normalized].append(line)
    return all(observed[key] == [line] for key, line in expected.items())


def _candidate_ref_is_independent(repo_root: Path, ref: str) -> bool:
    head = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "-q", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    candidate = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "-q", ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode not in {0, 1} or candidate.returncode not in {0, 1}:
        return False
    return candidate.returncode == 1 and (
        head.returncode == 1 or head.stdout.strip() != ref
    )


def _git_text(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExecutionError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExecutionError(f"{field} must be a non-negative integer")
    return value


def _texts(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ExecutionError(f"{field} must be a list")
    result = [_required_text(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ExecutionError(f"{field} must not contain duplicates")
    return result


def _paths(value: object, field: str) -> list[str]:
    return [_repo_path(item, field, allow_pattern=True) for item in _texts(value, field)]


def _repo_path(value: object, field: str, *, allow_pattern: bool) -> str:
    path = _required_text(value, field)
    if "\\" in path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ExecutionError(f"{field} must be a repository-relative POSIX path")
    if not allow_pattern and any(character in path for character in "*?["):
        raise ExecutionError(f"{field} must not be a path pattern")
    return path


def _path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return PurePosixPath(path).match(pattern)


def _touches_conflict(left: list[str], right: list[str]) -> bool:
    for left_pattern in left:
        for right_pattern in right:
            if left_pattern == right_pattern:
                return True
            if left_pattern.endswith("/**") and _path_matches(
                right_pattern.removesuffix("/**"), left_pattern
            ):
                return True
            if right_pattern.endswith("/**") and _path_matches(
                left_pattern.removesuffix("/**"), right_pattern
            ):
                return True
            if any(character in left_pattern + right_pattern for character in "*?["):
                left_prefix = re.split(r"[*?\[]", left_pattern, maxsplit=1)[0].rstrip("/")
                right_prefix = re.split(r"[*?\[]", right_pattern, maxsplit=1)[0].rstrip("/")
                if not left_prefix or not right_prefix:
                    return True
                if left_prefix.startswith(right_prefix) or right_prefix.startswith(
                    left_prefix
                ):
                    return True
    return False


def _claims_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _touches_conflict(left["touches"], right["touches"]):
        return True
    left_resources = {item.casefold() for item in left.get("resources", [])}
    right_resources = {item.casefold() for item in right.get("resources", [])}
    return bool(left_resources & right_resources)


def _text_mapping(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ExecutionError(f"{field} must be an object")
    normalized = _canonical_object(value, field)
    return {
        _required_text(key, f"{field} key"): _required_text(item, field)
        for key, item in normalized.items()
    }


def _status_mapping(value: object, field: str) -> dict[str, str]:
    result = _text_mapping(value, field)
    if any(status not in {"passed", "failed", "skipped", "unknown"} for status in result.values()):
        raise ExecutionError(f"{field} status is invalid")
    return result


def _mapping_sequence(value: object, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ExecutionError(f"{field} must be a list of objects")
    return [_canonical_object(item, field) for item in value]


def _reject_sensitive(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_FIELDS:
                raise InterventionRequired("secret_like_content")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{path}[{index}]")
    elif isinstance(value, str) and (
        _SENSITIVE_ASSIGNMENT.search(value)
        or _SENSITIVE_TOKEN.search(value)
        or "-----BEGIN PRIVATE KEY-----" in value
    ):
        raise InterventionRequired("secret_like_content")
