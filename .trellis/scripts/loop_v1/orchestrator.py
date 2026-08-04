"""Durable Loop v1 operator.

The default single-user start covers bounded local execution and finalization;
the strict profile keeps separate start and final responses. Use
recover-final-checks after an attributed final-check pause and continue-recovery
after recovery_waiting. Guide a quiescent required worker-test failure with
guide-replacement before advance. Cancel, revoke, and external or destructive
effects always require separate authority.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from common.io import write_bytes_atomic

from .acceptance import (
    _invalidate_final_gate,
    _effect_proofs,
    _skipped_checks,
    approve_final_request,
    archive_parent_run,
    compute_final_readiness,
    create_final_request,
    execute_final_merge,
    generate_acceptance_pack,
    record_final_review,
)
from .context import (
    _insert_committed_operation,
    _validate_context,
    _validate_context_against_envelope,
    _validate_envelope,
    accept_child_result,
    approve_start_request,
    create_start_request,
    issue_child_packet,
    intervention_reason,
    record_context_revision,
)
from .integration import (
    _canonical_recovery_observations,
    acknowledge_integration,
    advance_integration_ref,
    build_integration_candidate,
    cancel_parent,
    cancel_safe_point_status,
    integrate_reviewed_zero_diff_candidate,
    open_recovery_generation,
    pause_parent,
    prepare_integration_candidate,
    prepare_replacement_revision,
    record_replacement_guidance,
    record_problem_attempt,
    reconcile_for_cancel,
    resume_parent,
    resume_safe_point_status,
    verify_final_integration_checks,
)
from .ledger import (
    LedgerError,
    OperationConflict,
    ParentLedger,
    WriterLease,
    _now,
    ledger_path,
)
from .qualification import QualificationError, operation_qualification
from .scheduler import (
    deterministic_integration_selection,
    release_child_resources,
    requirement_progress,
    schedule_ready,
)
from .task_projection import (
    TaskProjectionError,
    project_terminal,
    projection_status,
)
from .task_archive import (
    TaskArchiveError,
    archive_pre_admission_parent,
    cleanup_pre_admission_lock,
    retire_task_evidence,
)
from .worker_commit import (
    DirtOverlapError,
    GitStateError,
    ParentValidationError,
    commit_reviewed_candidate,
    create_child_worktree,
    record_precommit_review,
    release_owned_worktree,
    scan_repository_dirt,
    validate_child_candidate,
)


STATE_SCHEMA_VERSION = 1
ACTION_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ENVIRONMENT_FIELDS = frozenset(
    {
        "git_version",
        "input_digest",
        "platform_digest",
        "python_version",
        "runtime_version",
        "tool_config_digest",
    }
)
_EFFECT_FIELDS = frozenset(
    {"classification", "effect_id", "evidence_digest", "status"}
)
_SKIPPED_FIELDS = frozenset({"check_id", "reason", "status"})
_OPERATOR_POLICY_FIELDS = frozenset(
    {
        "children",
        "effect_proofs",
        "environment",
        "final_integration_checks",
        "integration_checks",
        "post_merge_checks",
        "skipped_checks",
    }
)
_CHILD_POLICY_FIELDS = frozenset({"result_deadline", "tests"})
_INIT_FIELDS = frozenset(
    {
        "context",
        "context_reason",
        "context_revision_id",
        "envelope",
        "request_id",
        "task_dir",
    }
)
_INGEST_FIELDS = frozenset(
    {"action_digest", "action_id", "message_type", "payload"}
)
_REVIEW_FIELDS = frozenset(
    {
        "advisory_findings",
        "dispositions",
        "required_findings",
        "reviewer_identity",
        "verdict",
    }
)
_FINAL_REVIEW_FIELDS = frozenset(
    (
        *_REVIEW_FIELDS,
        "affected_requirement_ids",
        "fresh_context_receipt",
        "specialist_results",
    )
)
_START_RESPONSE_FIELDS = frozenset(
    {
        "action_digest",
        "action_id",
        "direct_user_action",
        "request_digest",
        "request_id",
        "response_at",
        "response_identity",
    }
)
_FINAL_RESPONSE_FIELDS = _START_RESPONSE_FIELDS
_PAUSE_FIELDS = frozenset(
    {"operation_id", "reason", "requested_by", "requires_human_resume"}
)
_RESUME_FIELDS = frozenset(
    {
        "authority_identity",
        "available_resources",
        "direct_user_action",
        "new_writer_id",
        "operation_id",
        "tool_receipt",
    }
)
_CANCEL_FIELDS = frozenset(
    {
        "action_digest",
        "action_id",
        "actor",
        "direct_user_action",
        "expected_authority_digest",
        "operation_id",
        "reason",
        "requested_at",
        "superseded_by",
    }
)
_RECONCILE_FOR_CANCEL_FIELDS = frozenset({"expected_authority_digest"})
_PROJECT_TERMINAL_FIELDS = frozenset(
    {"expected_authority_digest", "expected_task_digest"}
)
_ARCHIVE_PRE_ADMISSION_FIELDS = frozenset(
    {"commit_enabled", "expected_task_digest"}
)
_RETIRE_TASK_EVIDENCE_FIELDS = frozenset(
    {"commit_enabled", "expected_authority_digest", "expected_task_digest"}
)
_REVOKE_FIELDS = frozenset(
    {
        "actor",
        "direct_user_action",
        "execution_receipt",
        "operation_id",
        "reason",
        "revoked_at",
    }
)
_CONTINUE_RECOVERY_FIELDS = frozenset(
    {"action", "diagnosis", "operation_id", "problem_id"}
)
_RECOVER_FINAL_CHECKS_FIELDS = frozenset(
    {
        "action",
        "affected_child_ids",
        "diagnosis",
        "direct_user_action",
        "failed_operation_id",
        "operation_id",
    }
)
_GUIDE_REPLACEMENT_FIELDS = frozenset(
    {
        "failed_child_id",
        "failed_result_id",
        "operation_id",
        "prerequisite_child_ids",
        "problem_attempt_evidence_digest",
        "problem_id",
        "problem_round",
        "source_context_digest",
        "source_dispatch_id",
        "source_graph_digest",
    }
)
_TERMINAL_CHILD_STATES = frozenset(
    {"candidate_validated", "reviewed", "committed", "integrated", "cancelled"}
)
_OPERATOR_AUTHOR_NAME = "Loop Parent"
_OPERATOR_AUTHOR_EMAIL = "loop-parent@localhost"
_AUTHORIZATION_PROFILES = frozenset({"single_user", "strict"})
_LOCAL_COMMAND_ACTIONS = (
    "local_child_commit",
    "local_final_merge",
    "local_integration",
    "local_repair",
    "local_review",
    "local_worker",
)
_REPAIR_CHILD = re.compile(r"(?P<base>.+)-repair-(?P<round>[1-9][0-9]*)\Z")
_RECOVERY_PHASES = frozenset(
    {
        "final_integration_checks",
        "final_review",
        "integration_candidate",
        "precommit_review",
        "resume_stale",
        "worker_result",
        "worker_validation",
    }
)


class OrchestratorError(LedgerError):
    """Raised when the operator contract or durable projection is invalid."""


def _authorization_profile(
    task: Mapping[str, object], *, default: str = "single_user"
) -> str:
    meta = task.get("meta")
    profile = (
        str(meta.get("authorization_profile", default)).strip()
        if isinstance(meta, Mapping)
        else default
    )
    if profile not in _AUTHORIZATION_PROFILES:
        raise OrchestratorError(
            "authorization_profile must be single_user or strict"
        )
    return profile


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorError(f"{field} must be non-empty text")
    return value.strip()


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise OrchestratorError(f"{field} must be boolean")
    return value


def _exact_object(
    value: object, fields: frozenset[str], label: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise OrchestratorError(f"{label} must be an object")
    result = dict(value)
    missing = sorted(fields - set(result))
    unknown = sorted(set(result) - fields)
    if missing or unknown:
        raise OrchestratorError(
            f"{label} fields differ; missing={missing}, unknown={unknown}"
        )
    return result


def _text_list(value: object, field: str, *, nonempty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise OrchestratorError(f"{field} must be a list")
    result = [_required_text(item, field) for item in value]
    if nonempty and not result:
        raise OrchestratorError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise OrchestratorError(f"{field} must not contain duplicates")
    return result


def _object_list(
    value: object, fields: frozenset[str], field: str
) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise OrchestratorError(f"{field} must be a list")
    return [_exact_object(item, fields, field) for item in value]


def _repo_relative_path(repo_root: Path, value: object, field: str) -> Path:
    raw = _required_text(value, field)
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise OrchestratorError(f"{field} must be repository-relative")
    target = (repo_root / Path(*path.parts)).resolve()
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise OrchestratorError(f"{field} escapes the repository") from exc
    return target


def _run_root(repo_root: Path, run_id: str) -> Path:
    return ledger_path(repo_root, run_id).parent


def _output_path(repo_root: Path, run_id: str, value: object) -> Path:
    target = _repo_relative_path(repo_root, value, "output")
    output_root = (_run_root(repo_root, run_id) / "outputs").resolve()
    try:
        target.relative_to(output_root)
    except ValueError as exc:
        raise OrchestratorError(
            "output must stay under the run's ignored outputs directory"
        ) from exc
    if target.suffix != ".json":
        raise OrchestratorError("output must use a .json suffix")
    return target


def _state_path(repo_root: Path, run_id: str) -> Path:
    return _run_root(repo_root, run_id) / "operator-state.json"


def _lock_path(repo_root: Path, run_id: str) -> Path:
    return _run_root(repo_root, run_id) / "operator.lock"


def _worktree_root(repo_root: Path, run_id: str) -> Path:
    repo_key = sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]
    return (
        repo_root.parent
        / ".trellis-loop-v1-worktrees"
        / f"{repo_root.name}-{repo_key}"
        / run_id
    ).resolve()


@contextmanager
def _operator_lock(
    repo_root: Path, run_id: str, *, create: bool = False
) -> Iterator[None]:
    root = _run_root(repo_root, run_id)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    elif not ledger_path(repo_root, run_id).is_file():
        raise OrchestratorError("Loop parent ledger is not initialized")
    lock = _lock_path(repo_root, run_id)
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestratorError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise OrchestratorError(f"{label} must contain a JSON object")
    return value


def _read_state(repo_root: Path, run_id: str) -> dict[str, Any] | None:
    path = _state_path(repo_root, run_id)
    if not path.is_file():
        return None
    if path.stat().st_mode & 0o077:
        raise OrchestratorError("operator state permissions must be 0600")
    state = _read_json_object(path, "operator state")
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OrchestratorError("operator state schema is unsupported")
    if state.get("run_id") != run_id:
        raise OrchestratorError("operator state belongs to another run")
    return state


def _write_state(repo_root: Path, run_id: str, state: Mapping[str, object]) -> None:
    path = _state_path(repo_root, run_id)
    payload = (json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _lease_value(lease: WriterLease) -> dict[str, object]:
    return {
        "epoch": lease.epoch,
        "fence_token": lease.fence_token,
        "run_id": lease.run_id,
        "writer_id": lease.writer_id,
    }


def _lease_from_state(state: Mapping[str, object]) -> WriterLease:
    value = _exact_object(
        state.get("lease"),
        frozenset({"epoch", "fence_token", "run_id", "writer_id"}),
        "operator lease",
    )
    epoch = value["epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise OrchestratorError("operator lease epoch must be a positive integer")
    return WriterLease(
        _required_text(value["run_id"], "lease.run_id"),
        _required_text(value["writer_id"], "lease.writer_id"),
        epoch,
        _required_text(value["fence_token"], "lease.fence_token"),
    )


def _git(repo_root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise OrchestratorError(f"git {' '.join(args)}: {detail}")
    return process.stdout.strip()


def _git_optional(repo_root: Path, *args: str) -> str | None:
    process = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if process.returncode == 0:
        return process.stdout.strip()
    return None


def _operator_policy(envelope: Mapping[str, object]) -> dict[str, object]:
    verification = envelope.get("verification_policy")
    if not isinstance(verification, Mapping):
        raise OrchestratorError("verification_policy must be an object")
    policy = _exact_object(
        verification.get("operator"), _OPERATOR_POLICY_FIELDS, "operator policy"
    )
    children = policy["children"]
    if not isinstance(children, Mapping):
        raise OrchestratorError("operator policy children must be an object")
    graph = envelope.get("initial_child_graph")
    if isinstance(graph, (str, bytes)) or not isinstance(graph, Sequence):
        raise OrchestratorError("initial_child_graph must be a list")
    graph_ids = []
    for raw in graph:
        if not isinstance(raw, Mapping):
            raise OrchestratorError("initial_child_graph entries must be objects")
        child_id = _required_text(raw.get("child_id"), "child_id")
        if not _SAFE_ID.fullmatch(child_id):
            raise OrchestratorError("operator child IDs must use safe ASCII identifiers")
        graph_ids.append(child_id)
    if set(children) != set(graph_ids):
        raise OrchestratorError("operator child policy must exactly cover the graph")
    child_policy: dict[str, object] = {}
    for child_id in sorted(children):
        item = _exact_object(children[child_id], _CHILD_POLICY_FIELDS, "child policy")
        child_policy[child_id] = {
            "result_deadline": _required_text(
                item["result_deadline"], "result_deadline"
            ),
            "tests": _text_list(item["tests"], "child tests", nonempty=True),
        }
    environment = _exact_object(
        policy["environment"], _ENVIRONMENT_FIELDS, "operator environment"
    )
    environment = {
        key: _required_text(value, f"environment.{key}")
        for key, value in environment.items()
    }
    effects = _effect_proofs(
        _object_list(policy["effect_proofs"], _EFFECT_FIELDS, "effect proof")
    )
    skipped = _skipped_checks(
        _object_list(policy["skipped_checks"], _SKIPPED_FIELDS, "skipped check")
    )
    return {
        "children": child_policy,
        "effect_proofs": effects,
        "environment": environment,
        "final_integration_checks": _text_list(
            policy["final_integration_checks"],
            "final integration checks",
            nonempty=True,
        ),
        "integration_checks": _text_list(
            policy["integration_checks"], "integration checks", nonempty=True
        ),
        "post_merge_checks": _text_list(
            policy["post_merge_checks"], "post-merge checks", nonempty=True
        ),
        "skipped_checks": skipped,
    }


def _validate_init(
    repo_root: Path,
    run_id: str,
    raw: Mapping[str, object],
    *,
    require_admission: bool,
) -> dict[str, object]:
    value = _exact_object(raw, _INIT_FIELDS, "init input")
    if not _SAFE_ID.fullmatch(run_id):
        raise OrchestratorError("run ID must use safe ASCII identifiers")
    task_dir = _repo_relative_path(repo_root, value["task_dir"], "task_dir")
    task_json = task_dir / "task.json"
    if not task_json.is_file():
        raise OrchestratorError("Loop parent task.json is missing")
    task = _read_json_object(task_json, "Loop parent task")
    meta = task.get("meta")
    workflow_mode = meta.get("workflow_mode") if isinstance(meta, Mapping) else None
    if task.get("tier") != "parent" or workflow_mode != "loop_v1":
        raise OrchestratorError("operator init requires a loop_v1 parent task")
    if task.get("id") != run_id:
        raise OrchestratorError("run ID must equal the Loop parent task ID")
    if task.get("status") not in {"planning", "in_progress"}:
        raise OrchestratorError("Loop parent task is not start-eligible")

    envelope = value["envelope"]
    context = value["context"]
    if not isinstance(envelope, Mapping) or not isinstance(context, Mapping):
        raise OrchestratorError("envelope and context must be objects")
    envelope_value = _validate_envelope(envelope)
    context_value = _validate_context(context)
    _validate_context_against_envelope(context_value, envelope_value)
    policy = _operator_policy(envelope_value)
    context_graph = context_value.get("graph")
    if context_graph != envelope_value.get("initial_child_graph"):
        raise OrchestratorError("initial context graph must equal the approved graph")

    branch = _git(repo_root, "symbolic-ref", "--short", "HEAD")
    head = _git(repo_root, "rev-parse", "HEAD")
    tree = _git(repo_root, "rev-parse", "HEAD^{tree}")
    if envelope_value.get("base_branch") != branch:
        raise OrchestratorError("start envelope base branch differs from current branch")
    if envelope_value.get("base_head") != head:
        raise OrchestratorError("start envelope base HEAD differs from current HEAD")
    if task.get("base_branch") != branch:
        raise OrchestratorError("task base branch differs from the start envelope")
    integration = context_value.get("integration")
    if not isinstance(integration, Mapping) or any(
        integration.get(field) != expected
        for field, expected in {
            "base_branch": branch,
            "base_head": head,
            "base_tree_id": tree,
            "integration_head": head,
            "integration_tree_id": tree,
        }.items()
    ):
        raise OrchestratorError("initial context integration identity is not the base")
    dirt = scan_repository_dirt(repo_root)
    if envelope_value.get("dirty_path_fingerprint") != dirt["digest"]:
        raise OrchestratorError("start envelope dirt fingerprint is stale")
    if require_admission:
        qualification = operation_qualification(repo_root)
        if not qualification.enforced or not qualification.valid:
            detail = "; ".join(qualification.issues) or "qualification is unenforced"
            raise QualificationError(detail)
        if envelope_value.get("conformance_receipt") != qualification.receipt_id:
            raise QualificationError("start envelope names a different active receipt")
    if not set(envelope_value.get("approved_agent_surfaces", ())) or any(
        not isinstance(item, str) for item in envelope_value.get("approved_agent_surfaces", ())
    ):
        raise OrchestratorError("approved_agent_surfaces must not be empty")
    return {
        "authorization_profile": _authorization_profile(task),
        "context": context_value,
        "context_reason": _required_text(value["context_reason"], "context_reason"),
        "context_revision_id": _required_text(
            value["context_revision_id"], "context_revision_id"
        ),
        "envelope": envelope_value,
        "operator_policy": policy,
        "request_id": _required_text(value["request_id"], "request_id"),
        "task_dir": task_dir.relative_to(repo_root).as_posix(),
    }


def _parent_row(ledger: ParentLedger) -> dict[str, object]:
    connection = ledger._connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT * FROM parent_runs WHERE run_id = ?", (ledger.run_id,)
        ).fetchone()
        if row is None:
            raise OrchestratorError("Loop parent ledger is missing")
        return dict(row)
    finally:
        connection.close()


def _recover_lease(ledger: ParentLedger, writer_id: str | None = None) -> WriterLease:
    row = _parent_row(ledger)
    if writer_id is not None and row["writer_id"] != writer_id:
        raise OrchestratorError("existing ledger writer differs from operator init")
    return WriterLease(
        ledger.run_id,
        str(row["writer_id"]),
        int(row["epoch"]),
        str(row["fence_token"]),
    )


def _find_task_dir(repo_root: Path, run_id: str) -> str:
    matches = []
    for task_json in sorted((repo_root / ".trellis" / "tasks").glob("*/task.json")):
        try:
            task = _read_json_object(task_json, "task")
        except OrchestratorError:
            continue
        if task.get("id") == run_id:
            matches.append(task_json.parent)
    if len(matches) != 1:
        raise OrchestratorError("cannot uniquely recover the Loop parent task directory")
    return matches[0].relative_to(repo_root).as_posix()


def _recover_state(repo_root: Path, run_id: str, ledger: ParentLedger) -> dict[str, Any]:
    lease = _recover_lease(ledger)
    task_dir = _find_task_dir(repo_root, run_id)
    task = _read_json_object(repo_root / task_dir / "task.json", "Loop parent task")
    start_gate_ref = _parent_row(ledger).get("start_gate_ref")
    local_authority = (
        ledger.get_operation(f"local-command-authority:{start_gate_ref}")
        if start_gate_ref
        else None
    )
    authorization_profile = _authorization_profile(task, default="strict")
    if local_authority is not None:
        if (
            local_authority["kind"] != "local_command_authority"
            or local_authority["phase"] != "authority_committed"
            or local_authority["outcome"].get("profile") != "single_user"
            or local_authority["outcome"].get("request_id") != start_gate_ref
        ):
            raise OrchestratorError("durable local command authority is invalid")
        authorization_profile = "single_user"
    state: dict[str, Any] = {
        "action_history": [],
        "authorization_profile": authorization_profile,
        "final": None,
        "init": None,
        "init_digest": None,
        "lease": _lease_value(lease),
        "pending_action": None,
        "pending_writer_rotation": None,
        "run_id": run_id,
        "schema_version": STATE_SCHEMA_VERSION,
        "task_dir": task_dir,
    }
    _write_state(repo_root, run_id, state)
    return state


def _load_runtime(
    repo_root: Path, run_id: str
) -> tuple[dict[str, Any], ParentLedger, WriterLease]:
    ledger = ParentLedger(repo_root, run_id)
    if not ledger.path.is_file():
        raise OrchestratorError("Loop parent ledger is not initialized")
    state = _read_state(repo_root, run_id)
    if state is None:
        state = _recover_state(repo_root, run_id, ledger)
    pending_rotation = state.get("pending_writer_rotation")
    if isinstance(pending_rotation, Mapping):
        operation_id = _required_text(
            pending_rotation.get("operation_id"), "pending rotation operation_id"
        )
        operation = ledger.get_operation(f"resume-fence:{operation_id}")
        row = _parent_row(ledger)
        if (
            operation
            and operation["phase"] == "authority_committed"
            and operation["outcome"].get("writer_id") == row["writer_id"]
            and operation["outcome"].get("epoch") == row["epoch"]
        ):
            state["lease"] = _lease_value(_recover_lease(ledger))
            state["pending_writer_rotation"] = None
            _write_state(repo_root, run_id, state)
    lease = _lease_from_state(state)
    parent = _parent_row(ledger)
    if (
        (lease.epoch != parent["epoch"] or lease.writer_id != parent["writer_id"])
        and _resume_attempt_owns_active_fence(ledger, parent)
    ):
        lease = _recover_lease(ledger)
        state["lease"] = _lease_value(lease)
        state["pending_writer_rotation"] = None
        _write_state(repo_root, run_id, state)
    _recover_cancel_projection(state, ledger)
    if lease.run_id != run_id:
        raise OrchestratorError("operator lease belongs to another run")
    return state, ledger, lease


def _resume_attempt_owns_active_fence(
    ledger: ParentLedger, parent: Mapping[str, object]
) -> bool:
    connection = ledger._connect(read_only=True)
    try:
        attempts = connection.execute(
            """
            SELECT operation_id FROM operations
            WHERE kind = 'resume_attempt'
              AND phase IN ('prepared', 'effect_observed', 'authority_committed')
            ORDER BY created_at, operation_id
            """
        ).fetchall()
        matches = 0
        for attempt in attempts:
            rotation = connection.execute(
                "SELECT outcome_json FROM operations "
                "WHERE operation_id = ? AND kind = 'writer_rotation' "
                "AND phase = 'authority_committed'",
                (f"resume-fence:{attempt['operation_id']}",),
            ).fetchone()
            if rotation is None:
                continue
            outcome = json.loads(rotation["outcome_json"])
            if (
                outcome.get("epoch") == parent["epoch"]
                and outcome.get("writer_id") == parent["writer_id"]
            ):
                matches += 1
        return matches == 1
    finally:
        connection.close()


def _recover_cancel_projection(
    state: dict[str, Any],
    ledger: ParentLedger,
) -> None:
    connection = ledger._connect(read_only=True)
    try:
        row = connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = 'parent_cancel' AND phase = 'authority_committed'
            ORDER BY created_at DESC, operation_id DESC LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return
    outcome = json.loads(row["outcome_json"])
    action_id = outcome.get("pending_action_id")
    action_digest = outcome.get("pending_action_digest")
    input_digest = outcome.get("operator_input_digest")
    if action_id is None:
        return
    history = state.setdefault("action_history", [])
    if not isinstance(history, list):
        raise OrchestratorError("operator action history is malformed")
    if any(
        isinstance(item, Mapping)
        and item.get("action_id") == action_id
        and item.get("action_digest") == action_digest
        and item.get("status") == "consumed"
        for item in history
    ):
        return
    pending = _pending_action(state)
    if pending is not None and (
        pending.get("action_id") != action_id
        or pending.get("action_digest") != action_digest
    ):
        raise OrchestratorError(
            "cancelled ledger authority conflicts with pending operator action"
        )
    history.append(
        {
            "action_digest": action_digest,
            "action_id": action_id,
            "input_digest": input_digest,
            "status": "consumed",
        }
    )
    state["pending_action"] = None
    _write_state(ledger.repo_root, ledger.run_id, state)


def _table(snapshot: Mapping[str, object], name: str) -> list[dict[str, Any]]:
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise OrchestratorError("ledger authority snapshot is malformed")
    rows = tables.get(name)
    if not isinstance(rows, list):
        raise OrchestratorError(f"ledger snapshot table is missing: {name}")
    return [dict(row) for row in rows]


def _json_field(row: Mapping[str, object], field: str) -> dict[str, Any]:
    raw = row.get(field)
    if not isinstance(raw, str):
        raise OrchestratorError(f"ledger {field} is malformed")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"ledger {field} is malformed") from exc
    if not isinstance(value, dict):
        raise OrchestratorError(f"ledger {field} must be an object")
    return value


def _current_envelope(snapshot: Mapping[str, object]) -> dict[str, Any]:
    parents = _table(snapshot, "parent_runs")
    if len(parents) != 1:
        raise OrchestratorError("ledger must contain one parent run")
    start_ref = parents[0].get("start_gate_ref")
    rows = [
        row
        for row in _table(snapshot, "envelope_revisions")
        if row.get("revision_id") == start_ref
    ]
    if len(rows) != 1:
        raise OrchestratorError("approved start envelope is missing")
    return _json_field(rows[0], "contract_json")


def _current_context(snapshot: Mapping[str, object]) -> dict[str, Any]:
    return _json_field(_current_context_row(snapshot), "context_json")


def _current_context_row(snapshot: Mapping[str, object]) -> dict[str, Any]:
    rows = sorted(_table(snapshot, "context_revisions"), key=lambda row: int(row["sequence"]))
    if not rows:
        raise OrchestratorError("canonical context is missing")
    return rows[-1]


def _current_context_receipt(snapshot: Mapping[str, object]) -> str:
    return f"sha256:{_current_context_row(snapshot)['digest']}"


def _current_graph_digest(snapshot: Mapping[str, object]) -> str:
    return str(_current_context_row(snapshot)["graph_digest"])


def _child_states(snapshot: Mapping[str, object]) -> dict[str, str]:
    return {
        str(row["child_id"]): str(row["state"])
        for row in _table(snapshot, "child_operations")
    }


def _active_child_states(snapshot: Mapping[str, object]) -> dict[str, str]:
    states = _child_states(snapshot)
    return {
        str(node["child_id"]): states[str(node["child_id"])]
        for node in _current_context(snapshot)["graph"]
        if str(node["child_id"]) in states
    }


def _problem_attempts(snapshot: Mapping[str, object]) -> list[dict[str, Any]]:
    return sorted(
        (
            _json_field(row, "outcome_json")
            for row in _table(snapshot, "operations")
            if row.get("kind") == "problem_attempt"
            and row.get("phase") == "authority_committed"
        ),
        key=lambda item: (
            str(item["problem_id"]),
            int(item.get("recovery_generation", 1)),
            int(item["round"]),
        ),
    )


def _latest_problem_attempts(
    snapshot: Mapping[str, object],
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in _problem_attempts(snapshot):
        latest[str(attempt["problem_id"])] = attempt
    return [latest[key] for key in sorted(latest)]


def _recovery_failure_observations(
    snapshot: Mapping[str, object],
) -> list[dict[str, Any]]:
    return [
        _json_field(row, "outcome_json")
        for row in _table(snapshot, "operations")
        if row.get("kind") == "recovery_failure_observed"
        and row.get("phase") == "authority_committed"
    ]


def _recovery_generations(
    snapshot: Mapping[str, object],
) -> list[dict[str, Any]]:
    return sorted(
        (
            _json_field(row, "outcome_json")
            for row in _table(snapshot, "operations")
            if row.get("kind") == "recovery_generation_opened"
            and row.get("phase") == "authority_committed"
        ),
        key=lambda item: (
            str(item["problem_id"]),
            int(item["recovery_generation"]),
        ),
    )


def _problem_generation(problem: Mapping[str, object]) -> int:
    return int(problem.get("recovery_generation", 1))


def _current_problem_generation(
    snapshot: Mapping[str, object], problem_id: str | None
) -> int:
    generations = [
        int(item["recovery_generation"])
        for item in _recovery_generations(snapshot)
        if problem_id is None or item.get("problem_id") == problem_id
    ]
    return max(generations, default=1)


def _active_problem(
    snapshot: Mapping[str, object],
    *,
    operation_phase: str,
    root_condition: str | None = None,
    requirement_ids: Sequence[str],
) -> dict[str, Any] | None:
    requirements = sorted(str(item) for item in requirement_ids)
    matches = [
        item
        for item in _latest_problem_attempts(snapshot)
        if item.get("operation_phase") == operation_phase
        and (
            root_condition is None or item.get("root_condition") == root_condition
        )
        and item.get("requirement_ids") == requirements
        and item.get("result") == "failed"
        and item.get("exhausted") is False
    ]
    if len(matches) > 1:
        raise OrchestratorError("recovery phase has multiple active problem lineages")
    return matches[0] if matches else None


def _latest_matching_problem(
    snapshot: Mapping[str, object],
    *,
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
) -> dict[str, Any] | None:
    requirements = sorted(str(item) for item in requirement_ids)
    matches = [
        item
        for item in _latest_problem_attempts(snapshot)
        if item.get("operation_phase") == operation_phase
        and item.get("root_condition") == root_condition
        and item.get("requirement_ids") == requirements
        and item.get("result") == "failed"
    ]
    if len(matches) > 1:
        raise OrchestratorError("recovery phase has multiple durable problem lineages")
    return matches[0] if matches else None


def _recovery_projection(snapshot: Mapping[str, object]) -> dict[str, object]:
    latest = _latest_problem_attempts(snapshot)
    active = [
        item
        for item in latest
        if item.get("result") == "failed" and item.get("exhausted") is False
    ]
    parent_status = str(_table(snapshot, "parent_runs")[0]["status"])
    exhausted = [
        item
        for item in latest
        if item.get("exhausted") is True
        and _problem_generation(item)
        >= _current_problem_generation(snapshot, str(item["problem_id"]))
    ]
    pending_generation = any(
        _current_problem_generation(snapshot, str(item["problem_id"]))
        > _problem_generation(item)
        for item in latest
    )
    current_ids = {
        str(item["child_id"]) for item in _current_context(snapshot)["graph"]
    }
    envelope_ids = {
        str(item["child_id"])
        for item in _current_envelope(snapshot)["initial_child_graph"]
    }
    replacements = sorted(current_ids - envelope_ids)
    return {
        "active_problems": active,
        "exhausted_problems": exhausted,
        "generations": _recovery_generations(snapshot),
        "replacement_child_ids": replacements,
        "status": (
            "recovery_waiting"
            if parent_status == "recovery_waiting"
            else "repairing"
            if active or pending_generation
            else "clear"
        ),
    }


def _set_action(
    state: dict[str, Any],
    ledger: ParentLedger,
    *,
    action_id: str,
    action_type: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    payload_value = dict(payload)
    snapshot = ledger.authority_snapshot()
    if _table(snapshot, "context_revisions"):
        payload_value.setdefault("recovery", _recovery_projection(snapshot))
    core = {
        "action_id": _required_text(action_id, "action_id"),
        "action_type": _required_text(action_type, "action_type"),
        "authority_digest": ledger.authority_digest(),
        "payload": payload_value,
        "schema_version": ACTION_SCHEMA_VERSION,
    }
    action = {**core, "action_digest": f"sha256:{_digest_json(core)}"}
    current = state.get("pending_action")
    if current is not None and current != action:
        raise OrchestratorError("another external action is already pending")
    state["pending_action"] = action
    _write_state(ledger.repo_root, ledger.run_id, state)
    return action


def _pending_action(state: Mapping[str, object]) -> dict[str, object] | None:
    value = state.get("pending_action")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise OrchestratorError("pending operator action is malformed")
    action = dict(value)
    digest = action.pop("action_digest", None)
    if digest != f"sha256:{_digest_json(action)}":
        raise OrchestratorError("pending operator action digest is invalid")
    return dict(value)


def _state_authorization_profile(state: Mapping[str, object]) -> str:
    profile = state.get("authorization_profile", "strict")
    if profile not in _AUTHORIZATION_PROFILES:
        raise OrchestratorError("operator authorization profile is invalid")
    return str(profile)


def _validate_action_input(
    state: Mapping[str, object], value: Mapping[str, object], action_type: str
) -> dict[str, object]:
    action = _pending_action(state)
    if action is None or action.get("action_type") != action_type:
        raise OrchestratorError(f"no {action_type} action is pending")
    if value.get("action_id") != action["action_id"]:
        raise OrchestratorError("response action ID differs from the pending action")
    if value.get("action_digest") != action["action_digest"]:
        raise OrchestratorError("response action digest differs from the pending action")
    return action


def _consume_action(
    state: dict[str, Any], ledger: ParentLedger, input_value: Mapping[str, object]
) -> None:
    action = _pending_action(state)
    if action is None:
        raise OrchestratorError("no action is pending")
    history = state.setdefault("action_history", [])
    if not isinstance(history, list):
        raise OrchestratorError("operator action history is malformed")
    history.append(
        {
            "action_digest": action["action_digest"],
            "action_id": action["action_id"],
            "input_digest": f"sha256:{_digest_json(input_value)}",
            "status": "consumed",
        }
    )
    state["pending_action"] = None
    _write_state(ledger.repo_root, ledger.run_id, state)


def _start_request_from_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    rows = [row for row in _table(snapshot, "gate_requests") if row.get("kind") == "start"]
    if len(rows) != 1:
        raise OrchestratorError("ledger must contain one start request")
    gate = rows[0]
    envelope_rows = [
        row
        for row in _table(snapshot, "envelope_revisions")
        if row.get("revision_id") == gate.get("gate_id")
    ]
    if len(envelope_rows) != 1:
        raise OrchestratorError("start envelope is missing")
    envelope = _json_field(envelope_rows[0], "contract_json")
    return {
        "envelope": envelope,
        "envelope_digest": envelope_rows[0]["digest"],
        "request_digest": gate["input_digest"],
        "request_id": gate["gate_id"],
    }


def _complete_operator_start(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    request_digest: str,
    response_identity: str,
    response_at: str,
) -> None:
    init = state.get("init")
    if not isinstance(init, Mapping):
        raise OrchestratorError("rerun init with the original input before responding")
    approve_start_request(
        ledger,
        lease,
        request_id=request_id,
        request_digest=request_digest,
        response_identity=response_identity,
        response_at=response_at,
        direct_user_action=True,
    )
    record_context_revision(
        ledger,
        lease,
        request_id=request_id,
        revision_id=str(init["context_revision_id"]),
        reason=str(init["context_reason"]),
        context=init["context"],
    )
    _ensure_integration_ref(ledger, lease, init["context"])


def _record_local_command_authority(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    command_digest: str,
) -> dict[str, object]:
    supplied = {
        "authorization_kind": "local_command",
        "authorized_local_actions": list(_LOCAL_COMMAND_ACTIONS),
        "command_digest": _required_text(command_digest, "command_digest"),
        "profile": "single_user",
        "request_id": _required_text(request_id, "request_id"),
    }
    outcome = {**supplied, "status": "authorized"}
    operation_id = f"local-command-authority:{request_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        if (
            existing["kind"] != "local_command_authority"
            or existing["phase"] != "authority_committed"
            or existing["input_fingerprint"] != _digest_json(supplied)
            or existing["outcome"] != outcome
        ):
            raise OperationConflict("local command authority replay differs")
        return outcome
    with ledger._write_transaction(lease) as connection:
        parent = connection.execute(
            "SELECT start_gate_ref FROM parent_runs WHERE run_id = ?",
            (ledger.run_id,),
        ).fetchone()
        gate = connection.execute(
            "SELECT response_identity, invalidation_reason FROM gate_requests "
            "WHERE gate_id = ? AND kind = 'start'",
            (request_id,),
        ).fetchone()
        if (
            parent is None
            or parent["start_gate_ref"] != request_id
            or gate is None
            or gate["response_identity"] is None
            or gate["invalidation_reason"] is not None
        ):
            raise OrchestratorError(
                "local command authority requires the current approved start"
            )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="local_command_authority",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="local_command_authorized",
            created_at=_now(),
        )
    return outcome


def initialize_operator(
    repo_root: Path, run_id: str, init_input: Mapping[str, object]
) -> dict[str, object]:
    """Initialize or exactly replay one Loop operator run."""
    root = Path(repo_root).resolve()
    preflight_ledger = ParentLedger(root, run_id)
    validated = _validate_init(
        root,
        run_id,
        init_input,
        require_admission=not preflight_ledger.path.is_file(),
    )
    init_digest = f"sha256:{_digest_json(validated)}"
    writer_id = f"loop-v1-operator:{run_id}"
    with _operator_lock(root, run_id, create=True):
        ledger = ParentLedger(root, run_id)
        validated = _validate_init(
            root,
            run_id,
            init_input,
            require_admission=not ledger.path.is_file(),
        )
        state = _read_state(root, run_id)
        if ledger.path.is_file():
            if state is not None and state.get("init_digest") not in {None, init_digest}:
                raise OperationConflict("operator init was replayed with different input")
            lease = _recover_lease(ledger, writer_id)
            ParentLedger.initialize(
                root,
                run_id,
                selector="loop_v1",
                writer_id=writer_id,
                existing_lease=lease,
            )
        else:
            ledger, lease = ParentLedger.initialize(
                root,
                run_id,
                selector="loop_v1",
                writer_id=writer_id,
            )
        request = create_start_request(
            ledger,
            lease,
            request_id=str(validated["request_id"]),
            envelope=validated["envelope"],
        )
        state = state or {
            "action_history": [],
            "final": None,
            "pending_action": None,
            "pending_writer_rotation": None,
            "run_id": run_id,
            "schema_version": STATE_SCHEMA_VERSION,
        }
        state.update(
            {
                "authorization_profile": validated["authorization_profile"],
                "init": validated,
                "init_digest": init_digest,
                "lease": _lease_value(lease),
                "task_dir": validated["task_dir"],
            }
        )
        _write_state(root, run_id, state)
        if validated["authorization_profile"] == "single_user":
            snapshot = ledger.authority_snapshot()
            gates = [
                row
                for row in _table(snapshot, "gate_requests")
                if row.get("gate_id") == request["request_id"]
                and row.get("kind") == "start"
            ]
            if len(gates) != 1:
                raise OrchestratorError("single-user start gate is missing")
            _complete_operator_start(
                state,
                ledger,
                lease,
                request_id=str(request["request_id"]),
                request_digest=str(request["request_digest"]),
                response_identity=f"local-command:{init_digest}",
                response_at=str(gates[0]["created_at"]),
            )
            _record_local_command_authority(
                ledger,
                lease,
                request_id=str(request["request_id"]),
                command_digest=init_digest,
            )
            return _status_value(state, ledger)
        action = _set_action(
            state,
            ledger,
            action_id=f"start-response:{request['request_id']}",
            action_type="start_response",
            payload={"request": request},
        )
        return _status_value(state, ledger, action=action)


def _ensure_start_action(
    state: dict[str, Any], ledger: ParentLedger
) -> dict[str, object]:
    request = _start_request_from_snapshot(ledger.authority_snapshot())
    return _set_action(
        state,
        ledger,
        action_id=f"start-response:{request['request_id']}",
        action_type="start_response",
        payload={"request": request},
    )


def _integration_ref_name(run_id: str) -> str:
    return f"refs/heads/trellis/loop-v1/{run_id}/integration"


def request_start(repo_root: Path, run_id: str) -> dict[str, object]:
    root = Path(repo_root).resolve()
    with _operator_lock(root, run_id):
        state, ledger, _ = _load_runtime(root, run_id)
        action = _pending_action(state) or _ensure_start_action(state, ledger)
        if action["action_type"] != "start_response":
            raise OrchestratorError("start request is no longer pending")
        return _status_value(state, ledger, action=action)


def _ensure_integration_ref(
    ledger: ParentLedger,
    lease: WriterLease,
    context: Mapping[str, object],
) -> dict[str, object]:
    integration = context.get("integration")
    if not isinstance(integration, Mapping):
        raise OrchestratorError("context integration identity is missing")
    base_head = _required_text(integration.get("base_head"), "base_head")
    integration_ref = _integration_ref_name(ledger.run_id)
    operation_id = "operator-integration-ref-init"
    intent = {
        "base_head": base_head,
        "integration_ref": integration_ref,
        "run_id": ledger.run_id,
    }
    input_fingerprint = _digest_json(intent)
    outcome = {**intent, "status": "initialized"}
    operation = ledger.get_operation(operation_id)
    if operation is None:
        operation = ledger.prepare_operation(
            lease,
            operation_id=operation_id,
            kind="operator_integration_ref_init",
            input_fingerprint=input_fingerprint,
            intent=intent,
        )
    elif (
        operation["kind"] != "operator_integration_ref_init"
        or operation["input_fingerprint"] != input_fingerprint
    ):
        raise OperationConflict("integration ref initialization identity conflicts")
    current = _git_optional(ledger.repo_root, "rev-parse", "--verify", integration_ref)
    if operation["phase"] == "authority_committed":
        if operation["outcome"] != outcome or current is None:
            raise OrchestratorError("committed integration ref initialization is invalid")
        return outcome
    if operation["epoch"] != lease.epoch:
        raise OperationConflict(
            "incomplete integration ref initialization belongs to a stale writer epoch"
        )
    if current is None:
        _git(ledger.repo_root, "update-ref", integration_ref, base_head, "")
        current = base_head
    if current != base_head:
        raise OrchestratorError("integration ref initialization observed an unexpected ref")
    if operation["phase"] == "prepared":
        operation = ledger.advance_operation(
            lease,
            operation_id=operation_id,
            expected_phase="prepared",
            phase="effect_observed",
            output_fingerprint=_digest_json(outcome),
            outcome=outcome,
        )
    if operation["phase"] == "effect_observed":
        operation = ledger.advance_operation(
            lease,
            operation_id=operation_id,
            expected_phase="effect_observed",
            phase="authority_committed",
            output_fingerprint=_digest_json(outcome),
            outcome=outcome,
        )
    if operation["phase"] != "authority_committed" or operation["outcome"] != outcome:
        raise OrchestratorError("integration ref initialization did not commit")
    return outcome


def respond_start(
    repo_root: Path, run_id: str, response: Mapping[str, object]
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(response, _START_RESPONSE_FIELDS, "start response")
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        action = _validate_action_input(state, value, "start_response")
        request = action["payload"]["request"]
        if value["request_id"] != request["request_id"]:
            raise OrchestratorError("start response request ID differs")
        if value["request_digest"] != request["request_digest"]:
            raise OrchestratorError("start response request digest differs")
        if _boolean(value["direct_user_action"], "direct_user_action") is not True:
            raise OrchestratorError("start response requires direct user action")
        _complete_operator_start(
            state,
            ledger,
            lease,
            request_id=str(value["request_id"]),
            request_digest=str(value["request_digest"]),
            response_identity=_required_text(
                value["response_identity"], "response_identity"
            ),
            response_at=_required_text(value["response_at"], "response_at"),
        )
        _consume_action(state, ledger, value)
        return _status_value(state, ledger)


def _operation_outcomes(
    snapshot: Mapping[str, object], kind: str
) -> list[dict[str, Any]]:
    values = []
    for row in _table(snapshot, "operations"):
        if row.get("kind") == kind and row.get("phase") == "authority_committed":
            values.append(_json_field(row, "outcome_json"))
    return values


def _reviewed_zero_diff_recovery(
    snapshot: Mapping[str, object],
    child_id: str,
    review: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]] | None:
    if review.get("actual_touches") != []:
        return None
    problem = _active_problem(
        snapshot,
        operation_phase="final_integration_checks",
        requirement_ids=review["coverage"],
    )
    if problem is None:
        return None
    artifacts = {str(item) for item in problem.get("artifact_ids", [])}
    direct = []
    replacements = []
    for item in _operation_outcomes(snapshot, "final_check_recovery_guidance"):
        affected = item.get("affected_child_ids")
        if (
            item.get("direct_user_action") is not True
            or not isinstance(affected, list)
            or len(affected) != 1
            or item.get("requirement_ids") != review["coverage"]
            or item.get("failed_operation_id") not in artifacts
            or affected[0] not in artifacts
        ):
            continue
        if affected == [child_id]:
            direct.append(item)
        elif _replacement_maps_to_current(
            snapshot,
            problem_id=str(problem["problem_id"]),
            source_child_id=str(affected[0]),
            current_child_id=child_id,
        ):
            replacements.append(item)
    guidance = direct or replacements
    if len(guidance) > 1:
        raise OrchestratorError(
            "reviewed zero-diff child has ambiguous final-check recovery authority"
        )
    return (problem, guidance[0]) if guidance else None


def _replacement_maps_to_current(
    snapshot: Mapping[str, object],
    *,
    problem_id: str,
    source_child_id: str,
    current_child_id: str,
) -> bool:
    rows = _table(snapshot, "context_revisions")
    reason = f"recovery replacement for {problem_id}: {source_child_id}"
    matches = []
    for row in rows:
        if row.get("reason") != reason:
            continue
        source = [
            item for item in rows if item.get("digest") == row.get("previous_digest")
        ]
        if len(source) != 1:
            continue
        source_ids = {
            str(node["child_id"])
            for node in _json_field(source[0], "context_json")["graph"]
        }
        current_ids = {
            str(node["child_id"])
            for node in _json_field(row, "context_json")["graph"]
        }
        if source_child_id in source_ids and current_ids - source_ids == {
            current_child_id
        }:
            matches.append(row)
    if len(matches) > 1:
        raise OrchestratorError("zero-diff recovery has ambiguous replacement lineage")
    return bool(matches)


def _consumed_zero_diff_reconciliation(
    snapshot: Mapping[str, object],
) -> dict[str, object] | None:
    rows = sorted(
        _table(snapshot, "context_revisions"), key=lambda row: int(row["sequence"])
    )
    if len(rows) < 2:
        return None
    current = rows[-1]
    source_rows = [
        row for row in rows if row.get("digest") == current.get("previous_digest")
    ]
    if len(source_rows) != 1:
        return None
    source = source_rows[0]
    source_context = _json_field(source, "context_json")
    current_context = _json_field(current, "context_json")
    source_ids = {str(node["child_id"]) for node in source_context["graph"]}
    current_ids = {str(node["child_id"]) for node in current_context["graph"]}
    removed = source_ids - current_ids
    added = current_ids - source_ids
    if len(removed) != 1 or len(added) != 1:
        return None
    source_child_id = next(iter(removed))
    replacement_child_id = next(iter(added))
    operations = _operation_outcomes(snapshot, "reviewed_zero_diff_integration")
    matches = [
        outcome
        for outcome in operations
        if outcome.get("child_id") == source_child_id
        and current.get("reason")
        == (
            f"recovery replacement for {outcome.get('problem_id')}: "
            f"{source_child_id}"
        )
    ]
    states = {
        str(row["child_id"]): str(row["state"])
        for row in _table(snapshot, "child_operations")
    }
    replacement_started = any(
        row.get("kind") == "child_worktree_create"
        and _json_field(row, "outcome_json").get("child_id")
        == replacement_child_id
        for row in _table(snapshot, "operations")
        if row.get("phase") == "authority_committed"
    )
    if (
        len(matches) != 1
        or states.get(source_child_id) != "integrated"
        or states.get(replacement_child_id) != "dispatched"
        or replacement_started
    ):
        return None
    return {
        "current_digest": current["digest"],
        "problem_id": matches[0]["problem_id"],
        "replacement_child_id": replacement_child_id,
        "source_child_id": source_child_id,
        "source_context": source_context,
    }


def _reconcile_consumed_zero_diff_replacement(
    ledger: ParentLedger,
    lease: WriterLease,
) -> bool:
    reconciliation = _consumed_zero_diff_reconciliation(
        ledger.authority_snapshot()
    )
    if reconciliation is None:
        return False
    parent = _parent_row(ledger)
    record_context_revision(
        ledger,
        lease,
        request_id=str(parent["start_gate_ref"]),
        revision_id=(
            "zero-diff-guidance-reconciliation-"
            f"{_digest_json(reconciliation)[:32]}"
        ),
        reason=(
            "reconciled consumed zero-diff guidance replacement for "
            f"{reconciliation['problem_id']}: "
            f"{reconciliation['source_child_id']} -> "
            f"{reconciliation['replacement_child_id']}"
        ),
        context=reconciliation["source_context"],
        expected_previous_digest=str(reconciliation["current_digest"]),
    )
    return True


def _packet_for_child(snapshot: Mapping[str, object], child_id: str) -> dict[str, Any]:
    rows = [row for row in _table(snapshot, "child_packets") if row.get("child_id") == child_id]
    if len(rows) != 1:
        raise OrchestratorError(f"child packet is missing: {child_id}")
    return _json_field(rows[0], "packet_json")


def _worktree_for_child(snapshot: Mapping[str, object], child_id: str) -> str:
    rows = [
        row
        for row in _table(snapshot, "git_operations")
        if row.get("git_operation_id") == f"child-worktree:{child_id}"
    ]
    if len(rows) != 1:
        raise OrchestratorError(f"child worktree evidence is missing: {child_id}")
    return _required_text(rows[0].get("worktree"), "worktree")


def _release_child_scratch(
    ledger: ParentLedger,
    snapshot: Mapping[str, object],
    child_id: str,
    *,
    require_quiescence: bool = False,
) -> None:
    commit_rows = [
        row
        for row in _table(snapshot, "git_operations")
        if row.get("git_operation_id") == f"child-commit:{child_id}"
    ]
    if commit_rows:
        if len(commit_rows) != 1:
            raise GitStateError("child commit release authority is ambiguous")
        release_owned_worktree(
            ledger,
            operation_id=str(commit_rows[0]["operation_id"]),
            runtime_root=_worktree_root(ledger.repo_root, ledger.run_id),
        )
        return

    result_rows = [
        row
        for row in _table(snapshot, "operations")
        if row.get("kind") == "worker_result_observed"
        and _json_field(row, "outcome_json").get("child_id") == child_id
    ]
    if len(result_rows) != 1:
        raise GitStateError("worktree retained without one durable worker dirt identity")
    if require_quiescence and not any(
        child_id in _json_field(row, "outcome_json").get("child_ids", [])
        for row in _table(snapshot, "operations")
        if row.get("kind") == "worker_dispatch_quiesced"
        and row.get("phase") == "authority_committed"
    ):
        raise GitStateError("worktree retained until its worker dispatch is quiescent")
    release_owned_worktree(
        ledger,
        operation_id=str(result_rows[0]["operation_id"]),
        runtime_root=_worktree_root(ledger.repo_root, ledger.run_id),
    )


def _release_candidate_scratch(
    ledger: ParentLedger,
    snapshot: Mapping[str, object],
    child_id: str,
) -> None:
    rows = [
        row
        for row in _table(snapshot, "operations")
        if row.get("kind") == "integration_candidate"
        and _json_field(row, "outcome_json").get("child_id") == child_id
        and _json_field(row, "outcome_json").get("status")
        in {"candidate_failed", "integrated"}
    ]
    if not rows:
        return
    if len(rows) != 1:
        raise GitStateError("candidate release authority is ambiguous")
    release_owned_worktree(
        ledger,
        operation_id=str(rows[0]["operation_id"]),
        runtime_root=_worktree_root(ledger.repo_root, ledger.run_id),
    )


def _node_for_child(
    snapshot: Mapping[str, object], child_id: str
) -> dict[str, Any]:
    for node in _current_context(snapshot)["graph"]:
        if node.get("child_id") == child_id:
            return dict(node)
    raise OrchestratorError(f"current graph child is missing: {child_id}")


def _base_child_id(child_id: str, envelope: Mapping[str, object]) -> str:
    initial = {
        str(item["child_id"]) for item in envelope["initial_child_graph"]
    }
    if child_id in initial:
        return child_id
    match = _REPAIR_CHILD.fullmatch(child_id)
    if match and match.group("base") in initial:
        return match.group("base")
    return child_id


def _packet_attempt(
    child_id: str,
    envelope: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> int:
    if child_id == _base_child_id(child_id, envelope):
        return 1
    base_child_id = _base_child_id(child_id, envelope)
    prior_attempts = {
        str(row["child_id"])
        for row in _table(snapshot, "child_operations")
        if _base_child_id(str(row["child_id"]), envelope) == base_child_id
    }
    return len(prior_attempts) + 1


def _worker_failure_children(snapshot: Mapping[str, object]) -> set[str]:
    current_ids = {
        str(node["child_id"]) for node in _current_context(snapshot)["graph"]
    }
    return {
        str(child_id)
        for item in _recovery_failure_observations(snapshot)
        if item.get("operation_phase") in {"worker_result", "worker_validation"}
        for child_id in item.get("affected_child_ids", [])
        if str(child_id) in current_ids
    }


def _recovery_context_for_child(
    snapshot: Mapping[str, object], child_id: str
) -> list[dict[str, object]]:
    node = _node_for_child(snapshot, child_id)
    requirements = set(str(item) for item in node["requirements"])
    reviews = [
        *_operation_outcomes(snapshot, "precommit_review_recorded"),
        *_operation_outcomes(snapshot, "final_integration_review"),
    ]
    guidance = []
    for problem in _latest_problem_attempts(snapshot):
        if (
            problem.get("result") != "failed"
            or problem.get("exhausted") is not False
            or not requirements.intersection(
                str(item) for item in problem["requirement_ids"]
            )
        ):
            continue
        artifact_ids = {str(item) for item in problem.get("artifact_ids", [])}
        required_findings = [
            dict(finding)
            for review in reviews
            if str(review.get("review_id")) in artifact_ids
            for finding in review.get("required_findings", [])
        ]
        guidance.append(
            {
                "action": str(problem["action"]),
                "diagnosis": str(problem["diagnosis"]),
                "failed_commands": [
                    dict(command)
                    for command in problem.get("commands", [])
                    if command.get("status") == "failed"
                ],
                "operation_phase": str(problem["operation_phase"]),
                "problem_id": str(problem["problem_id"]),
                "repair_round": int(problem["round"]) + 1,
                "required_findings": required_findings,
                "root_condition": str(problem["root_condition"]),
            }
        )
    return guidance


def _dispatch_action(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    states = _active_child_states(snapshot)
    failed = _worker_failure_children(snapshot)
    child_ids = sorted(
        child_id
        for child_id, child_state in states.items()
        if child_state in {"dispatched", "result_validated"} and child_id not in failed
    )
    if not child_ids:
        raise OrchestratorError("no children are awaiting worker transport")
    entries = []
    for child_id in child_ids:
        packet = _packet_for_child(snapshot, child_id)
        try:
            worktree = _worktree_for_child(snapshot, child_id)
        except OrchestratorError as exc:
            if str(exc) != f"child worktree evidence is missing: {child_id}":
                raise
            runtime_root = _worktree_root(ledger.repo_root, ledger.run_id)
            created = create_child_worktree(
                ledger,
                lease,
                operation_id=f"operator-worktree:{child_id}",
                child_id=child_id,
                packet_id=str(packet["packet_id"]),
                worktree=runtime_root / "children" / child_id,
                branch=f"loop-v1/{ledger.run_id}/{child_id}",
                integration_worktree=runtime_root / "integration",
            )
            worktree = str(created["worktree"])
        entry = {
            "child_id": child_id,
            "packet": packet,
            "state": states[child_id],
            "worktree": worktree,
        }
        recovery_context = _recovery_context_for_child(snapshot, child_id)
        if recovery_context:
            entry["recovery_context"] = recovery_context
        entries.append(entry)
    action_key = _digest_json([entry["packet"]["packet_id"] for entry in entries])[:20]
    envelope = _current_envelope(snapshot)
    return _set_action(
        state,
        ledger,
        action_id=f"dispatch-workers:{action_key}",
        action_type="dispatch_workers",
        payload={
            "approved_agent_surfaces": envelope["approved_agent_surfaces"],
            "children": entries,
        },
    )


def _issue_scheduled_children(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    selected: Sequence[str],
) -> dict[str, object]:
    snapshot = ledger.authority_snapshot()
    context = _current_context(snapshot)
    envelope = _current_envelope(snapshot)
    policy = _operator_policy(envelope)
    public_slices = [
        str(item["slice_id"])
        for item in context["context_slices"]
        if item.get("kind") != "secret_ref" and item.get("visibility") == "public"
    ]
    runtime_root = _worktree_root(ledger.repo_root, ledger.run_id)
    runtime_root.mkdir(parents=True, exist_ok=True)
    integration_placeholder = runtime_root / "integration"
    for child_id in selected:
        policy_child_id = _base_child_id(child_id, envelope)
        if policy_child_id not in policy["children"]:
            raise OrchestratorError(
                f"replacement child lacks an approved policy source: {child_id}"
            )
        item = policy["children"][policy_child_id]
        attempt = _packet_attempt(child_id, envelope, snapshot)
        packet_id = f"packet-{ledger.run_id}-{child_id}-{attempt}"
        packet = issue_child_packet(
            ledger,
            lease,
            {
                "attempt": attempt,
                "child_id": child_id,
                "context_slice_ids": public_slices,
                "forbidden_touches": [],
                "packet_id": packet_id,
                "parent_contact": f"loop-v1:{ledger.run_id}",
                "requirements": next(
                    node["requirements"]
                    for node in context["graph"]
                    if node["child_id"] == child_id
                ),
                "result_deadline": item["result_deadline"],
                "result_lease": f"result-lease:{packet_id}",
                "round": attempt,
                "tests": item["tests"],
            },
        )
        create_child_worktree(
            ledger,
            lease,
            operation_id=f"operator-worktree:{child_id}",
            child_id=child_id,
            packet_id=packet["packet_id"],
            worktree=runtime_root / "children" / child_id,
            branch=f"loop-v1/{ledger.run_id}/{child_id}",
            integration_worktree=integration_placeholder,
        )
    return _dispatch_action(state, ledger, lease, ledger.authority_snapshot())


def _pending_schedule_selection(snapshot: Mapping[str, object]) -> list[str]:
    states = _child_states(snapshot)
    outcomes = _operation_outcomes(snapshot, "schedule_decision")
    for outcome in reversed(outcomes):
        selected = [str(item) for item in outcome.get("selected", [])]
        if selected and any(child_id not in states for child_id in selected):
            return selected
    return []


def _validation_for_child(snapshot: Mapping[str, object], child_id: str) -> dict[str, Any]:
    rows = [
        outcome
        for outcome in _operation_outcomes(snapshot, "candidate_validated")
        if outcome.get("child_id") == child_id
    ]
    if len(rows) != 1:
        raise OrchestratorError(f"candidate validation is missing: {child_id}")
    return rows[0]


def _precommit_action(
    state: dict[str, Any], ledger: ParentLedger, child_id: str
) -> dict[str, object]:
    snapshot = ledger.authority_snapshot()
    validation = _validation_for_child(snapshot, child_id)
    tree_id = _required_text(validation.get("tree_id"), "validation tree_id")
    review_id = f"operator-review-{child_id}-{tree_id[:12]}"
    return _set_action(
        state,
        ledger,
        action_id=f"precommit-review:{child_id}:{tree_id[:12]}",
        action_type="precommit_review",
        payload={
            "approved_agent_surfaces": _current_envelope(snapshot)["approved_agent_surfaces"],
            "child_id": child_id,
            "review_id": review_id,
            "validation": validation,
        },
    )


def _final_review_action(
    state: dict[str, Any], ledger: ParentLedger
) -> dict[str, object]:
    snapshot = ledger.authority_snapshot()
    context = _current_context(snapshot)
    integration = context["integration"]
    head = _required_text(integration["integration_head"], "integration_head")
    context_receipt = _current_context_receipt(snapshot)
    review_key = _digest_json(
        [head, integration["integration_tree_id"], context_receipt]
    )[:20]
    return _set_action(
        state,
        ledger,
        action_id=f"final-review:{review_key}",
        action_type="final_review",
        payload={
            "approved_agent_surfaces": _current_envelope(snapshot)["approved_agent_surfaces"],
            "fresh_context_receipt": context_receipt,
            "integration": integration,
            "requirements": context["requirements"],
            "review_id": f"operator-final-review-{review_key}",
        },
    )


def _final_integration_worktree(snapshot: Mapping[str, object]) -> str:
    integration = _current_context(snapshot)["integration"]
    matches = [
        outcome
        for outcome in _operation_outcomes(snapshot, "integration_candidate")
        if outcome.get("status") == "integrated"
        and outcome.get("candidate_head") == integration["integration_head"]
        and outcome.get("candidate_tree_id") == integration["integration_tree_id"]
        and outcome.get("integration_ref") == _integration_ref_name(
            str(_table(snapshot, "parent_runs")[0]["run_id"])
        )
    ]
    if len(matches) != 1:
        raise OrchestratorError(
            "cannot uniquely resolve the final integrated candidate worktree"
        )
    return _required_text(
        matches[0].get("candidate_worktree"), "candidate_worktree"
    )


def _verify_final_integration(
    ledger: ParentLedger,
    lease: WriterLease,
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    integration = _current_context(snapshot)["integration"]
    policy = _operator_policy(_current_envelope(snapshot))
    integration_ref = _integration_ref_name(ledger.run_id)
    verification_key = _digest_json(
        [
            integration_ref,
            integration["integration_head"],
            integration["integration_tree_id"],
            _current_context_receipt(snapshot),
            policy["final_integration_checks"],
            lease.epoch,
        ]
    )[:20]
    return verify_final_integration_checks(
        ledger,
        lease,
        verification_id=f"operator-final-integration-{verification_key}",
        integration_ref=integration_ref,
        worktree=Path(_final_integration_worktree(snapshot)),
        checks=policy["final_integration_checks"],
    )


def _latest_final_review(snapshot: Mapping[str, object]) -> dict[str, Any] | None:
    outcomes = _operation_outcomes(snapshot, "final_integration_review")
    integration = _current_context(snapshot)["integration"]
    context_receipt = _current_context_receipt(snapshot)
    matching = [
        item
        for item in outcomes
        if item.get("integration_head") == integration["integration_head"]
        and item.get("integration_tree_id") == integration["integration_tree_id"]
        and item.get("fresh_context_receipt") == context_receipt
    ]
    return matching[-1] if matching else None


def _final_review_action_is_current(
    action: Mapping[str, object], snapshot: Mapping[str, object]
) -> bool:
    payload = action.get("payload")
    if not isinstance(payload, Mapping):
        return False
    context = _current_context(snapshot)
    return (
        payload.get("fresh_context_receipt") == _current_context_receipt(snapshot)
        and payload.get("integration") == context["integration"]
        and payload.get("requirements") == context["requirements"]
    )


def _active_final_requests(snapshot: Mapping[str, object]) -> list[dict[str, Any]]:
    gate_ids = {
        str(row["gate_id"])
        for row in _table(snapshot, "gate_requests")
        if row.get("kind") == "final" and row.get("invalidation_reason") is None
    }
    return [
        outcome
        for outcome in _operation_outcomes(snapshot, "final_request_created")
        if str(outcome.get("request", {}).get("request_id")) in gate_ids
    ]


def _pack_result_from_path(path: Path) -> dict[str, object]:
    value = _read_json_object(path, "acceptance pack")
    if set(value) != {"pack", "pack_digest"}:
        raise OrchestratorError("acceptance pack JSON fields are invalid")
    return {
        **value,
        "json_path": str(path),
        "markdown_path": str(path.with_suffix(".md")),
    }


def _find_pack_for_request(ledger: ParentLedger, request_id: str) -> dict[str, object]:
    root = ledger.path.parent / "acceptance"
    matches = []
    for path in sorted(root.glob("*.json")):
        value = _pack_result_from_path(path)
        if value["pack"].get("final_request_id") == request_id:
            matches.append(value)
    if len(matches) != 1:
        raise OrchestratorError("cannot uniquely resolve the final acceptance pack")
    return matches[0]


def _ensure_final_request(
    state: dict[str, Any], ledger: ParentLedger, lease: WriterLease
) -> dict[str, object] | None:
    snapshot = ledger.authority_snapshot()
    envelope = _current_envelope(snapshot)
    context = _current_context(snapshot)
    policy = _operator_policy(envelope)
    integration_ref = _integration_ref_name(ledger.run_id)
    integration_head = context["integration"]["integration_head"]
    pack_key = _digest_json(
        [integration_head, _current_context_receipt(snapshot)]
    )[:20]
    pack_id = f"operator-{pack_key}"
    pack = generate_acceptance_pack(
        ledger,
        pack_id=pack_id,
        integration_ref=integration_ref,
        tool_receipt=str(envelope["conformance_receipt"]),
        environment=policy["environment"],
        effect_proofs=policy["effect_proofs"],
        skipped_checks=policy["skipped_checks"],
    )
    readiness = compute_final_readiness(
        ledger,
        pack_result=pack,
        integration_ref=integration_ref,
        tool_receipt=str(envelope["conformance_receipt"]),
    )
    if not readiness["ready"]:
        raise OrchestratorError("final readiness is not green")
    request = create_final_request(
        ledger,
        lease,
        request_id=str(pack["pack"]["final_request_id"]),
        pack_result=pack,
        readiness=readiness,
        integration_ref=integration_ref,
        post_merge_checks=policy["post_merge_checks"],
        author_name=_OPERATOR_AUTHOR_NAME,
        author_email=_OPERATOR_AUTHOR_EMAIL,
    )
    state["final"] = {
        "integration_ref": integration_ref,
        "pack_json_path": pack["json_path"],
        "request_id": pack["pack"]["final_request_id"],
    }
    _write_state(ledger.repo_root, ledger.run_id, state)
    if _state_authorization_profile(state) == "single_user":
        return None
    return _set_action(
        state,
        ledger,
        action_id=f"final-response:{request['request']['request_id']}",
        action_type="final_response",
        payload={
            "pack_digest": pack["pack_digest"],
            "pack_json_path": pack["json_path"],
            "pack_markdown_path": pack["markdown_path"],
            "readiness_digest": readiness["readiness_digest"],
            "request": request,
        },
    )


def _problem_evidence(
    label: str, details: str, *, passed: bool = False
) -> dict[str, object]:
    return {
        "command": label,
        "exit_code": 0 if passed else 1,
        "output_digest": f"sha256:{sha256(details.encode('utf-8')).hexdigest()}",
        "status": "passed" if passed else "failed",
    }


def _record_recovery_observation(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
    affected_child_ids: Sequence[str],
    artifact_ids: Sequence[str],
    graph_digest: str,
    problem_round: int,
    recovery_generation: int,
) -> dict[str, object]:
    outcome = {
        "affected_child_ids": sorted(set(str(item) for item in affected_child_ids)),
        "artifact_ids": sorted(str(item) for item in artifact_ids),
        "graph_digest": graph_digest,
        "operation_phase": operation_phase,
        "problem_round": problem_round,
        "requirement_ids": sorted(str(item) for item in requirement_ids),
        "root_condition": root_condition,
    }
    if recovery_generation > 1:
        outcome["recovery_generation"] = recovery_generation
    if not outcome["affected_child_ids"]:
        raise OrchestratorError("recovery observation requires an affected child")
    input_fingerprint = _digest_json(outcome)
    operation_id = f"recovery-observation:{input_fingerprint[:32]}"
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["kind"] != "recovery_failure_observed"
                or existing["phase"] != "authority_committed"
                or existing["input_fingerprint"] != input_fingerprint
                or json.loads(existing["outcome_json"]) != outcome
            ):
                raise OperationConflict(
                    "recovery observation identity was reused with new input"
                )
            return outcome
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="recovery_failure_observed",
            epoch=lease.epoch,
            input_fingerprint=input_fingerprint,
            outcome=outcome,
            event_type="recovery_failure_observed",
            created_at=_now(),
        )
    return outcome


def _record_worker_dispatch_quiescence(
    ledger: ParentLedger,
    lease: WriterLease,
    action: Mapping[str, object],
) -> dict[str, object]:
    outcome = {
        "action_digest": str(action["action_digest"]),
        "action_id": str(action["action_id"]),
        "child_ids": sorted(
            str(item["child_id"]) for item in action["payload"]["children"]
        ),
    }
    operation_id = f"worker-dispatch-quiesced:{action['action_id']}"
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["kind"] != "worker_dispatch_quiesced"
                or existing["phase"] != "authority_committed"
                or json.loads(existing["outcome_json"]) != outcome
            ):
                raise OperationConflict(
                    "worker dispatch quiescence identity was reused with new input"
                )
            return outcome
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="worker_dispatch_quiesced",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(outcome),
            outcome=outcome,
            event_type="worker_dispatch_quiesced",
            created_at=_now(),
        )
    return outcome


def _record_worker_result_observation(
    ledger: ParentLedger,
    lease: WriterLease,
    snapshot: Mapping[str, object],
    result: Mapping[str, object],
    payload_digest: str,
) -> dict[str, object]:
    child_id = _required_text(result.get("child_id"), "child_id")
    result_id = _required_text(result.get("result_id"), "result_id")
    worktrees = [
        row
        for row in _table(snapshot, "git_operations")
        if row.get("git_operation_id") == f"child-worktree:{child_id}"
    ]
    if len(worktrees) != 1:
        raise OrchestratorError("worker result lacks one child worktree authority")
    worktree = worktrees[0]
    if (
        worktree.get("expected_old_ref") != result.get("base_head")
        or worktree.get("tree_id") != result.get("base_tree_id")
    ):
        raise OrchestratorError("worker result base differs from worktree authority")
    outcome = {
        "actual_touches": sorted(str(item) for item in result["actual_touches"]),
        "allowed_touches": sorted(
            str(item) for item in _node_for_child(snapshot, child_id)["touches"]
        ),
        "base_head": str(result["base_head"]),
        "base_tree_id": str(result["base_tree_id"]),
        "branch": _required_text(worktree.get("branch"), "branch"),
        "child_id": child_id,
        "diff_identity": str(result["diff_identity"]),
        "payload_digest": payload_digest,
        "result_id": result_id,
        "result_tree_id": str(result["result_tree_id"]),
        "worktree": _required_text(worktree.get("worktree"), "worktree"),
    }
    operation_id = f"worker-result-observed:{result_id}"
    with ledger._write_transaction(lease) as connection:
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["kind"] != "worker_result_observed"
                or existing["phase"] != "authority_committed"
                or existing["input_fingerprint"] != payload_digest
                or json.loads(existing["outcome_json"]) != outcome
            ):
                raise OperationConflict(
                    "worker result observation identity was reused with new input"
                )
            return outcome
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="worker_result_observed",
            epoch=lease.epoch,
            input_fingerprint=payload_digest,
            outcome=outcome,
            event_type="worker_result_observed",
            created_at=_now(),
        )
    return outcome


def _replacement_guidance_binding(
    snapshot: Mapping[str, object],
    problem: Mapping[str, object],
    *,
    failed_child_id: str,
    failed_result_id: str,
    source_dispatch_id: str,
) -> dict[str, object]:
    observations = _canonical_recovery_observations(
        [
            item
            for item in _recovery_failure_observations(snapshot)
            if item.get("operation_phase") == problem["operation_phase"]
            and item.get("root_condition") == problem["root_condition"]
            and item.get("requirement_ids") == problem["requirement_ids"]
            and item.get("graph_digest") == _current_graph_digest(snapshot)
            and int(item.get("problem_round", -1)) == int(problem["round"])
            and int(item.get("recovery_generation", 1))
            == int(problem.get("recovery_generation", 1))
        ]
    )
    return {
        "failed_child_id": failed_child_id,
        "failed_result_id": failed_result_id,
        "problem_attempt_evidence_digest": _digest_json(observations),
        "problem_id": str(problem["problem_id"]),
        "problem_round": int(problem["round"]),
        "source_context_digest": str(_current_context_row(snapshot)["digest"]),
        "source_dispatch_id": source_dispatch_id,
        "source_graph_digest": _current_graph_digest(snapshot),
    }


def _quiescent_replacement_guidance(
    snapshot: Mapping[str, object],
    action: Mapping[str, object],
) -> dict[str, object] | None:
    source_dispatch_id = str(action["action_id"])
    allowed = {
        str(item["child_id"]) for item in action["payload"]["children"]
    }
    failed = sorted(_worker_failure_children(snapshot).intersection(allowed))
    if len(failed) != 1:
        return None
    failed_child_id = failed[0]
    observations = [
        item
        for item in _recovery_failure_observations(snapshot)
        if item.get("operation_phase") == "worker_result"
        and item.get("root_condition") == "worker_required_test_failed"
        and failed_child_id in item.get("affected_child_ids", [])
        and source_dispatch_id in item.get("artifact_ids", [])
    ]
    identities = {
        (
            tuple(str(value) for value in item["requirement_ids"]),
            int(item.get("problem_round", -1)),
            int(item.get("recovery_generation", 1)),
        )
        for item in observations
    }
    if len(identities) != 1:
        return None
    requirements, problem_round, recovery_generation = next(iter(identities))
    problem = _active_problem(
        snapshot,
        operation_phase="worker_result",
        root_condition="worker_required_test_failed",
        requirement_ids=requirements,
    )
    if (
        problem is None
        or int(problem["round"]) != problem_round
        or int(problem.get("recovery_generation", 1)) != recovery_generation
    ):
        return None
    artifact_ids = {
        str(value)
        for item in observations
        for value in item.get("artifact_ids", [])
    }
    rejected = [
        item
        for item in _operation_outcomes(snapshot, "child_result_rejected")
        if item.get("child_id") == failed_child_id
        and str(item.get("result_id")) in artifact_ids
    ]
    if not rejected:
        return None
    failed_result_id = str(
        _canonical_recovery_observations(rejected)[-1]["result_id"]
    )
    return _replacement_guidance_binding(
        snapshot,
        problem,
        failed_child_id=failed_child_id,
        failed_result_id=failed_result_id,
        source_dispatch_id=source_dispatch_id,
    )


def _guided_problem_requirements(
    snapshot: Mapping[str, object],
    *,
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
    affected_child_ids: Sequence[str],
) -> list[str] | None:
    if (
        operation_phase != "worker_result"
        or root_condition != "worker_required_test_failed"
    ):
        return None
    envelope = _current_envelope(snapshot)
    incoming_requirements = set(str(item) for item in requirement_ids)
    affected_bases = {
        _base_child_id(str(child_id), envelope) for child_id in affected_child_ids
    }
    problems = {
        str(problem["problem_id"]): problem
        for problem in _latest_problem_attempts(snapshot)
        if problem.get("result") == "failed"
        and problem.get("exhausted") is False
    }
    matches = []
    for guidance in _operation_outcomes(snapshot, "replacement_guidance"):
        problem = problems.get(str(guidance["problem_id"]))
        if (
            problem is not None
            and incoming_requirements.issubset(guidance["expanded_requirement_ids"])
            and affected_bases.issubset(guidance["expanded_child_ids"])
        ):
            matches.append(list(problem["requirement_ids"]))
    if len(matches) > 1:
        raise OrchestratorError("worker failure belongs to ambiguous guided repairs")
    return matches[0] if matches else None


def _record_recovery_failure(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_phase: str,
    root_condition: str,
    requirement_ids: Sequence[str],
    diagnosis: str,
    action: str,
    affected_child_ids: Sequence[str],
    artifact_ids: Sequence[str],
    commands: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    if operation_phase not in _RECOVERY_PHASES:
        raise OrchestratorError(f"unsupported recovery phase: {operation_phase}")
    snapshot = ledger.authority_snapshot()
    requirements = sorted(str(item) for item in requirement_ids)
    guided_requirements = _guided_problem_requirements(
        snapshot,
        operation_phase=operation_phase,
        root_condition=root_condition,
        requirement_ids=requirements,
        affected_child_ids=affected_child_ids,
    )
    if guided_requirements is not None:
        requirements = guided_requirements
    artifacts = sorted(str(item) for item in artifact_ids)
    graph_digest = _current_graph_digest(snapshot)
    active = _active_problem(
        snapshot,
        operation_phase=operation_phase,
        root_condition=root_condition,
        requirement_ids=requirements,
    )
    problem_id = str(active["problem_id"]) if active else None
    generation = _current_problem_generation(snapshot, problem_id)
    matching = _latest_matching_problem(
        snapshot,
        operation_phase=operation_phase,
        root_condition=root_condition,
        requirement_ids=requirements,
    )
    if matching is not None:
        problem_id = str(matching["problem_id"])
        generation = _current_problem_generation(snapshot, problem_id)
    current_observations = [
        item
        for item in _recovery_failure_observations(snapshot)
        if item.get("operation_phase") == operation_phase
        and item.get("root_condition") == root_condition
        and item.get("requirement_ids") == requirements
        and item.get("graph_digest") == graph_digest
        and int(item.get("recovery_generation", 1)) == generation
    ]
    if active is None:
        round_number = 0
    elif current_observations:
        round_number = max(int(item["problem_round"]) for item in current_observations)
    else:
        round_number = int(active["round"]) + 1
    _record_recovery_observation(
        ledger,
        lease,
        operation_phase=operation_phase,
        root_condition=root_condition,
        requirement_ids=requirements,
        affected_child_ids=affected_child_ids,
        artifact_ids=artifacts,
        graph_digest=graph_digest,
        problem_round=round_number,
        recovery_generation=generation,
    )
    if active is not None and round_number == int(active["round"]):
        return active
    if active is not None:
        root_condition = str(active["root_condition"])
        diagnosis = str(active["diagnosis"])
        action = str(active["action"])
    evidence = (
        [dict(item) for item in commands]
        if commands is not None
        else [_problem_evidence(operation_phase, diagnosis)]
    )
    return record_problem_attempt(
        ledger,
        lease,
        problem_id=problem_id or "operator-derived",
        round_number=round_number,
        operation_phase=operation_phase,
        root_condition=root_condition,
        requirement_ids=requirements,
        diagnosis=diagnosis,
        action=action,
        commands=evidence,
        artifact_ids=artifacts,
        result="failed",
    )


def _replacement_graph(
    context: Mapping[str, object],
    envelope: Mapping[str, object],
    snapshot: Mapping[str, object],
    replaced_child_ids: Sequence[str],
    repair_round: int,
) -> list[dict[str, Any]]:
    replaced = set(replaced_child_ids)
    replacements = {}
    occupied = {
        str(node["child_id"]) for node in context["graph"]
    } | {
        str(row["child_id"]) for row in _table(snapshot, "child_operations")
    }
    for child_id in replaced:
        base_child_id = _base_child_id(child_id, envelope)
        match = _REPAIR_CHILD.fullmatch(child_id)
        prior_round = (
            int(match.group("round"))
            if match and base_child_id != child_id
            else 0
        )
        replacement_round = max(repair_round, prior_round + 1)
        replacement_id = f"{base_child_id}-repair-{replacement_round}"
        while replacement_id in occupied or replacement_id in replacements.values():
            replacement_round += 1
            replacement_id = f"{base_child_id}-repair-{replacement_round}"
        replacements[child_id] = replacement_id
    graph = copy.deepcopy(list(context["graph"]))
    for node in graph:
        child_id = str(node["child_id"])
        if child_id in replacements:
            node["child_id"] = replacements[child_id]
        node["depends_on"] = [
            replacements.get(str(dependency), str(dependency))
            for dependency in node["depends_on"]
        ]
    return graph


def _invalidate_final_requests(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    reason: str = "required final review finding requires bounded repair",
) -> None:
    snapshot = ledger.authority_snapshot()
    for gate in _table(snapshot, "gate_requests"):
        if gate.get("kind") == "final" and gate.get("invalidation_reason") is None:
            _invalidate_final_gate(
                ledger,
                lease,
                str(gate["gate_id"]),
                reason,
            )
    if state.get("final") is not None:
        state["final"] = None
        _write_state(ledger.repo_root, ledger.run_id, state)


def _prepare_recovery_replacement(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    problem: Mapping[str, object],
    child_ids: Sequence[str],
) -> dict[str, object]:
    snapshot = ledger.authority_snapshot()
    context = _current_context(snapshot)
    active_states = _active_child_states(snapshot)
    replaced = sorted(set(str(item) for item in child_ids))
    guidance = next(
        (
            outcome
            for outcome in reversed(
                _operation_outcomes(snapshot, "replacement_guidance")
            )
            if outcome.get("problem_id") == problem["problem_id"]
            and int(outcome.get("problem_round", -1)) == int(problem["round"])
            and outcome.get("source_context_digest")
            == _current_context_row(snapshot)["digest"]
            and outcome.get("source_graph_digest") == _current_graph_digest(snapshot)
        ),
        None,
    )
    expected_coverage = None
    if guidance is not None:
        if not set(replaced).issubset(guidance["expanded_child_ids"]):
            raise OrchestratorError(
                "replacement guidance does not include the failed child slice"
            )
        replaced = list(guidance["expanded_child_ids"])
        expected_coverage = list(guidance["expanded_requirement_ids"])
    if not replaced or any(child_id not in active_states for child_id in replaced):
        raise OrchestratorError("recovery replacement must target current graph children")
    repair_round = int(problem["round"]) + 1
    if repair_round < 1 or repair_round > 3:
        raise OrchestratorError("repair replacement is outside the bounded 0+3 budget")
    for child_id in replaced:
        release_child_resources(
            ledger,
            lease,
            operation_id=f"operator-recovery-release:{problem['problem_id']}:{child_id}",
            child_id=child_id,
            reason=f"bounded replacement for {problem['problem_id']}",
        )
    if problem.get("operation_phase") != "resume_stale":
        try:
            release_snapshot = ledger.authority_snapshot()
            for child_id in replaced:
                _release_child_scratch(
                    ledger,
                    release_snapshot,
                    child_id,
                    require_quiescence=problem.get("operation_phase")
                    in {"worker_result", "worker_validation"},
                )
                _release_candidate_scratch(ledger, release_snapshot, child_id)
        except GitStateError as exc:
            return _pause_for_intervention(
                state,
                ledger,
                lease,
                details=f"superseded worktree retained: {exc}",
                signals=_intervention_signals(
                    "unknown_effect_data_loss_or_user_dirt"
                ),
            )
    if problem.get("operation_phase") in {
        "final_integration_checks",
        "final_review",
    }:
        _invalidate_final_requests(state, ledger, lease)
    snapshot = ledger.authority_snapshot()
    context_rows = sorted(
        _table(snapshot, "context_revisions"), key=lambda row: int(row["sequence"])
    )
    current_row = context_rows[-1]
    parent = _table(snapshot, "parent_runs")[0]
    return prepare_replacement_revision(
        ledger,
        lease,
        request_id=str(parent["start_gate_ref"]),
        problem_id=str(problem["problem_id"]),
        source_context_digest=str(current_row["digest"]),
        replaced_child_ids=replaced,
        replacement_graph=_replacement_graph(
            context, _current_envelope(snapshot), snapshot, replaced, repair_round
        ),
        expected_replacement_coverage=expected_coverage,
    )


def _resolve_recovered_problems(
    ledger: ParentLedger, lease: WriterLease
) -> bool:
    snapshot = ledger.authority_snapshot()
    context = _current_context(snapshot)
    states = _active_child_states(snapshot)
    requirements = {
        str(item["requirement_id"]): item for item in context["requirements"]
    }
    changed = False
    for problem in _latest_problem_attempts(snapshot):
        if (
            problem.get("result") != "failed"
            or problem.get("exhausted") is not False
            or problem.get("operation_phase") == "integration_candidate"
        ):
            continue
        if problem.get("operation_phase") == "final_review":
            final_review = _latest_final_review(snapshot)
            if final_review is None or final_review.get("verdict") != "passed":
                continue
            details = "fresh exact integration review passed after repair"
            record_problem_attempt(
                ledger,
                lease,
                problem_id=str(problem["problem_id"]),
                round_number=int(problem["round"]) + 1,
                operation_phase="final_review",
                root_condition=str(problem["root_condition"]),
                requirement_ids=sorted(str(item) for item in problem["requirement_ids"]),
                diagnosis=str(problem["diagnosis"]),
                action=str(problem["action"]),
                commands=[_problem_evidence("final_review", details, passed=True)],
                artifact_ids=[str(final_review["review_id"])],
                result="passed",
            )
            changed = True
            continue
        covered = set(str(item) for item in problem["requirement_ids"])
        nodes = [
            node
            for node in context["graph"]
            if covered.intersection(str(item) for item in node["requirements"])
        ]
        if not nodes or not all(
            requirements[requirement_id]["coverage_state"] == "covered"
            for requirement_id in covered
        ):
            continue
        if not all(states.get(str(node["child_id"])) == "integrated" for node in nodes):
            continue
        details = f"repaired integration covers {','.join(sorted(covered))}"
        record_problem_attempt(
            ledger,
            lease,
            problem_id=str(problem["problem_id"]),
            round_number=int(problem["round"]) + 1,
            operation_phase=str(problem["operation_phase"]),
            root_condition=str(problem["root_condition"]),
            requirement_ids=sorted(covered),
            diagnosis=str(problem["diagnosis"]),
            action=str(problem["action"]),
            commands=[_problem_evidence(str(problem["operation_phase"]), details, passed=True)],
            artifact_ids=[str(node["child_id"]) for node in nodes],
            result="passed",
        )
        changed = True
    if changed:
        ledger.rebuild_projection(lease)
    return changed


def _intervention_signals(
    condition: str | None = None, *, explicit_user_control: str | None = None
) -> dict[str, object]:
    names = (
        "scope_or_resource_expansion",
        "dependency_schema_security_or_secret_change",
        "unknown_effect_data_loss_or_user_dirt",
        "retry_budget_exhausted",
        "ambiguous_product_semantics",
    )
    if condition is not None and condition not in names:
        raise OrchestratorError(f"unknown intervention condition: {condition}")
    return {
        **{name: name == condition for name in names},
        "explicit_user_control": explicit_user_control,
    }


def _paused_intervention(snapshot: Mapping[str, object]) -> tuple[dict[str, object], str]:
    parent_epoch = int(_table(snapshot, "parent_runs")[0]["epoch"])
    current_operations = sorted(
        (
            row
            for row in _table(snapshot, "operations")
            if row.get("phase") == "authority_committed"
            and int(row.get("epoch", -1)) == parent_epoch
        ),
        key=lambda row: (str(row.get("created_at", "")), str(row["operation_id"])),
    )
    exhausted = [
        _json_field(row, "outcome_json")
        for row in current_operations
        if row.get("kind") == "problem_attempt"
        and _json_field(row, "outcome_json").get("exhausted") is True
    ]
    if exhausted:
        return (
            _intervention_signals("retry_budget_exhausted"),
            "the stable problem lineage exhausted repair round 3",
        )
    qualification_pauses = [
        row
        for row in current_operations
        if row.get("kind") == "qualification_pause"
    ]
    if qualification_pauses:
        latest_row = max(
            qualification_pauses,
            key=lambda row: (str(row.get("created_at", "")), str(row["operation_id"])),
        )
        latest = _json_field(latest_row, "outcome_json")
        return (
            _intervention_signals("dependency_schema_security_or_secret_change"),
            str(latest.get("reason") or "runtime qualification failed"),
        )
    pauses = [
        _json_field(row, "outcome_json")
        for row in current_operations
        if row.get("kind") == "parent_pause"
    ]
    if pauses:
        latest = pauses[-1]
        reason = str(latest.get("reason", ""))
        accepted = {
            "scope_or_resource_expansion",
            "dependency_schema_security_or_secret_change",
            "unknown_effect_data_loss_or_user_dirt",
            "retry_budget_exhausted",
            "ambiguous_product_semantics",
        }
        if reason in accepted:
            return _intervention_signals(reason), reason
        if latest.get("requested_by") == "runtime-safety":
            return (
                _intervention_signals("unknown_effect_data_loss_or_user_dirt"),
                reason or "runtime safety pause",
            )
    return _intervention_signals(explicit_user_control="pause"), "explicit parent pause"


def _pause_for_intervention(
    state: dict[str, Any],
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    details: str,
    signals: Mapping[str, object],
) -> dict[str, object]:
    reason = intervention_reason(signals)
    if reason is None:
        raise OrchestratorError("routine recovery cannot escalate to a human")
    pending = _pending_action(state)
    if pending is not None and pending.get("action_type") != "human_intervention":
        _consume_action(
            state,
            ledger,
            {"control": "classified_intervention", "details": details, "reason": reason},
        )
    if _parent_row(ledger)["status"] == "authorized":
        pause_parent(
            ledger,
            lease,
            operation_id=f"operator-pause:{_digest_json([reason, details])[:20]}",
            reason=reason,
            requested_by="loop-v1-operator",
            requires_human_resume=True,
        )
    return _set_action(
        state,
        ledger,
        action_id=f"human-intervention:{_digest_json([reason, details])[:20]}",
        action_type="human_intervention",
        payload={"details": details, "reason": reason},
    )


def _advance_once(
    state: dict[str, Any], ledger: ParentLedger, lease: WriterLease
) -> dict[str, object] | None:
    pending = _pending_action(state)
    snapshot = ledger.authority_snapshot()
    parent = _table(snapshot, "parent_runs")[0]
    status = parent["status"]
    if status == "initialized":
        return pending or _ensure_start_action(state, ledger)
    if status == "paused":
        signals, details = _paused_intervention(snapshot)
        return _pause_for_intervention(
            state,
            ledger,
            lease,
            details=details,
            signals=signals,
        )
    if status == "recovery_waiting":
        return None
    if status in {"cancelled", "archived", "revoked"}:
        return None
    if status != "authorized":
        raise OrchestratorError(f"unsupported parent status: {status}")
    if pending is not None:
        if pending.get("action_type") == "final_review" and not _final_review_action_is_current(
            pending, snapshot
        ):
            _consume_action(
                state,
                ledger,
                {"reconciliation": "stale final review action"},
            )
            return None
        if (
            pending.get("action_type") == "final_response"
            and pending.get("authority_digest") != ledger.authority_digest()
        ):
            _consume_action(
                state,
                ledger,
                {"reconciliation": "stale final response action"},
            )
            _invalidate_final_requests(
                state,
                ledger,
                lease,
                reason="pending final response authority changed",
            )
            return None
        return pending
    ledger.assert_runtime_qualification(lease)
    if _reconcile_consumed_zero_diff_replacement(ledger, lease):
        return None
    states = _active_child_states(snapshot)
    for child_id in sorted(
        child_id for child_id, value in states.items() if value == "reviewed"
    ):
        reviews = [
            outcome
            for outcome in _operation_outcomes(snapshot, "precommit_review_recorded")
            if outcome.get("child_id") == child_id and outcome.get("verdict") == "passed"
        ]
        if len(reviews) == 1:
            recovery = _reviewed_zero_diff_recovery(snapshot, child_id, reviews[0])
            if recovery is not None:
                problem, guidance = recovery
                integrate_reviewed_zero_diff_candidate(
                    ledger,
                    lease,
                    operation_id=f"zero-diff-integration:{child_id}",
                    review_id=str(reviews[0]["review_id"]),
                    recovery_operation_id=str(guidance["operation_id"]),
                    problem_id=str(problem["problem_id"]),
                )
                return None
    consumed_recovery_ids = {
        str(outcome["recovery_operation_id"])
        for outcome in _operation_outcomes(snapshot, "reviewed_zero_diff_integration")
    }
    guidance = next(
        (
            outcome
            for outcome in reversed(
                _operation_outcomes(snapshot, "final_check_recovery_guidance")
            )
            if outcome["operation_id"] not in consumed_recovery_ids
            if set(str(item) for item in outcome["affected_child_ids"]).issubset(
                _active_child_states(snapshot)
            )
        ),
        None,
    )
    if guidance is not None:
        failed = ledger.get_operation(str(guidance["failed_operation_id"]))
        if failed is None:
            raise OrchestratorError("final-check recovery evidence is missing")
        problem = _record_recovery_failure(
            ledger,
            lease,
            operation_phase="final_integration_checks",
            root_condition=str(failed["outcome"]["root_condition"]),
            requirement_ids=guidance["requirement_ids"],
            diagnosis=str(guidance["diagnosis"]),
            action=str(guidance["action"]),
            affected_child_ids=guidance["affected_child_ids"],
            artifact_ids=[
                str(guidance["failed_operation_id"]),
                *[str(item) for item in guidance["affected_child_ids"]],
            ],
            commands=failed["outcome"]["checks_result"],
        )
        if problem.get("exhausted") is not True:
            _prepare_recovery_replacement(
                state,
                ledger,
                lease,
                problem=problem,
                child_ids=guidance["affected_child_ids"],
            )
        return None
    context = _current_context(snapshot)
    _ensure_integration_ref(ledger, lease, context)
    if _resolve_recovered_problems(ledger, lease):
        return None
    snapshot = ledger.authority_snapshot()
    states = _active_child_states(snapshot)
    reviewed = sorted(child_id for child_id, value in states.items() if value == "reviewed")
    if reviewed:
        child_id = reviewed[0]
        reviews = [
            outcome
            for outcome in _operation_outcomes(snapshot, "precommit_review_recorded")
            if outcome.get("child_id") == child_id and outcome.get("verdict") == "passed"
        ]
        if len(reviews) != 1:
            raise OrchestratorError("reviewed child lacks one passing review")
        commit_reviewed_candidate(
            ledger,
            lease,
            operation_id=f"operator-commit:{child_id}",
            review_id=str(reviews[0]["review_id"]),
            message=f"loop({child_id}): commit reviewed candidate",
            author_name=_OPERATOR_AUTHOR_NAME,
            author_email=_OPERATOR_AUTHOR_EMAIL,
        )
        try:
            _release_child_scratch(ledger, ledger.authority_snapshot(), child_id)
        except GitStateError as exc:
            return _pause_for_intervention(
                state,
                ledger,
                lease,
                details=f"committed child worktree retained: {exc}",
                signals=_intervention_signals(
                    "unknown_effect_data_loss_or_user_dirt"
                ),
            )
        return None

    awaiting_review = sorted(
        child_id for child_id, value in states.items() if value == "candidate_validated"
    )
    if awaiting_review:
        return _precommit_action(state, ledger, awaiting_review[0])

    selection = deterministic_integration_selection(ledger)
    if selection["status"] == "paused":
        return _pause_for_intervention(
            state,
            ledger,
            lease,
            details="deterministic integration selection found a dependency cycle",
            signals=_intervention_signals("ambiguous_product_semantics"),
        )
    committed_selection = [
        str(child_id)
        for child_id in selection["selected"]
        if states.get(str(child_id)) == "committed"
    ]
    if committed_selection:
        child_id = committed_selection[0]
        try:
            _release_child_scratch(ledger, snapshot, child_id)
            for integrated_child_id, child_state in states.items():
                if child_state == "integrated":
                    _release_candidate_scratch(
                        ledger, snapshot, integrated_child_id
                    )
        except GitStateError as exc:
            return _pause_for_intervention(
                state,
                ledger,
                lease,
                details=f"integration scratch retained: {exc}",
                signals=_intervention_signals(
                    "unknown_effect_data_loss_or_user_dirt"
                ),
            )
        integration_id = f"operator-{child_id}"
        runtime_root = _worktree_root(ledger.repo_root, ledger.run_id)
        policy = _operator_policy(_current_envelope(snapshot))
        node = _node_for_child(snapshot, child_id)
        active_integration_problem = _active_problem(
            snapshot,
            operation_phase="integration_candidate",
            requirement_ids=node["requirements"],
        )
        problem_input = None
        if active_integration_problem is not None:
            problem_input = {
                "action": active_integration_problem["action"],
                "diagnosis": active_integration_problem["diagnosis"],
                "problem_id": active_integration_problem["problem_id"],
                "root_condition": active_integration_problem["root_condition"],
                "round": int(active_integration_problem["round"]) + 1,
            }
        prepare_integration_candidate(
            ledger,
            lease,
            integration_id=integration_id,
            child_id=child_id,
            integration_ref=_integration_ref_name(ledger.run_id),
            candidate_worktree=runtime_root / "candidates" / child_id,
            candidate_branch=f"loop-v1/{ledger.run_id}/candidate/{child_id}",
            checks=policy["integration_checks"],
            author_name=_OPERATOR_AUTHOR_NAME,
            author_email=_OPERATOR_AUTHOR_EMAIL,
            problem=problem_input,
        )
        candidate = build_integration_candidate(ledger, lease, integration_id=integration_id)
        if candidate["status"] != "candidate_green":
            problem = _latest_matching_problem(
                ledger.authority_snapshot(),
                operation_phase="integration_candidate",
                root_condition=str(candidate["root_condition"]),
                requirement_ids=node["requirements"],
            )
            if problem is None:
                raise OrchestratorError("failed integration candidate lacks a problem")
            if problem.get("exhausted") is not True:
                _prepare_recovery_replacement(
                    state, ledger, lease, problem=problem, child_ids=[child_id]
                )
            return None
        advance_integration_ref(ledger, lease, integration_id=integration_id)
        acknowledge_integration(ledger, lease, integration_id=integration_id)
        return None

    snapshot = ledger.authority_snapshot()
    states = _active_child_states(snapshot)
    failed_worker = sorted(
        child_id
        for child_id in _worker_failure_children(snapshot)
        if states.get(child_id) in {"dispatched", "result_validated"}
    )
    if failed_worker:
        child_id = failed_worker[0]
        observations = [
            item
            for item in _recovery_failure_observations(snapshot)
            if item.get("operation_phase") in {"worker_result", "worker_validation"}
            and child_id in {str(value) for value in item.get("affected_child_ids", [])}
        ]
        identities = {
            (
                str(item["operation_phase"]),
                str(item["root_condition"]),
                tuple(str(value) for value in item["requirement_ids"]),
            )
            for item in observations
        }
        if len(identities) != 1:
            raise OrchestratorError("failed worker boundary lacks one observed problem")
        operation_phase, root_condition, requirements = next(iter(identities))
        problem = _active_problem(
            snapshot,
            operation_phase=operation_phase,
            root_condition=root_condition,
            requirement_ids=requirements,
        )
        if problem is None:
            raise OrchestratorError("failed worker observation lacks an active problem")
        matching_observations = [
            item
            for item in _recovery_failure_observations(snapshot)
            if item.get("operation_phase") == operation_phase
            and item.get("root_condition") == root_condition
            and tuple(str(value) for value in item.get("requirement_ids", []))
            == requirements
        ]
        problem_children = sorted(
            {
                str(value)
                for item in matching_observations
                for value in item.get("affected_child_ids", [])
                if str(value) in failed_worker
            }
        )
        _prepare_recovery_replacement(
            state, ledger, lease, problem=problem, child_ids=problem_children
        )
        return None

    stale = sorted(child_id for child_id, value in states.items() if value == "stale")
    if stale:
        requirements = sorted(
            {
                requirement
                for child_id in stale
                for requirement in _node_for_child(snapshot, child_id)["requirements"]
            }
        )
        problem = _record_recovery_failure(
            ledger,
            lease,
            operation_phase="resume_stale",
            root_condition="resume_stale_work",
            requirement_ids=requirements,
            diagnosis="resumed work carries an obsolete writer epoch or context identity",
            action="replace stale work inside the approved graph",
            affected_child_ids=stale,
            artifact_ids=stale,
        )
        if problem.get("exhausted") is not True:
            _prepare_recovery_replacement(
                state, ledger, lease, problem=problem, child_ids=stale
            )
        return None

    blocked = sorted(
        child_id for child_id, value in states.items() if value == "review_blocked"
    )
    if blocked:
        child_id = blocked[0]
        reviews = [
            item
            for item in _operation_outcomes(snapshot, "precommit_review_recorded")
            if item.get("child_id") == child_id and item.get("verdict") == "failed"
        ]
        if not reviews:
            raise OrchestratorError("review-blocked child lacks failed review evidence")
        problem = _record_recovery_failure(
            ledger,
            lease,
            operation_phase="precommit_review",
            root_condition="required_precommit_review_finding",
            requirement_ids=_node_for_child(snapshot, child_id)["requirements"],
            diagnosis="required pre-commit findings block the exact candidate tree",
            action="replace the blocked candidate inside the approved graph",
            affected_child_ids=[child_id],
            artifact_ids=[str(reviews[-1]["review_id"]), child_id],
        )
        if problem.get("exhausted") is not True:
            _prepare_recovery_replacement(
                state, ledger, lease, problem=problem, child_ids=[child_id]
            )
        return None

    awaiting_worker = [
        child_id
        for child_id, value in states.items()
        if value in {"dispatched", "result_validated"}
        and child_id not in _worker_failure_children(snapshot)
    ]
    if awaiting_worker:
        return _dispatch_action(state, ledger, lease, snapshot)

    pending_selection = _pending_schedule_selection(snapshot)
    if pending_selection:
        return _issue_scheduled_children(state, ledger, lease, pending_selection)

    progress = requirement_progress(ledger)
    if progress["final_ready"]:
        final_checks = _verify_final_integration(ledger, lease, snapshot)
        if final_checks["status"] != "passed":
            return _pause_for_intervention(
                state,
                ledger,
                lease,
                details=(
                    "final integration checks failed: "
                    f"operation={final_checks['operation_id']}, "
                    f"root_condition={final_checks['root_condition']}"
                ),
                signals=_intervention_signals("ambiguous_product_semantics"),
            )
        try:
            release_snapshot = ledger.authority_snapshot()
            for child_id, child_state in _active_child_states(
                release_snapshot
            ).items():
                if child_state == "integrated":
                    _release_candidate_scratch(
                        ledger, release_snapshot, child_id
                    )
        except GitStateError as exc:
            return _pause_for_intervention(
                state,
                ledger,
                lease,
                details=f"verified candidate worktree retained: {exc}",
                signals=_intervention_signals(
                    "unknown_effect_data_loss_or_user_dirt"
                ),
            )
        final_review = _latest_final_review(snapshot)
        if final_review is None:
            return _final_review_action(state, ledger)
        if final_review.get("verdict") != "passed":
            required_findings = list(final_review.get("required_findings", []))
            graph = context["graph"]
            affected_requirements = sorted(
                str(item) for item in final_review["affected_requirement_ids"]
            )
            affected_children = [
                str(node["child_id"])
                for node in graph
                if set(str(item) for item in node["requirements"]).intersection(
                    affected_requirements
                )
            ]
            if not affected_children:
                raise OrchestratorError(
                    "failed final review has no affected current graph slice"
                )
            problem = _record_recovery_failure(
                ledger,
                lease,
                operation_phase="final_review",
                root_condition=(
                    "required_final_review_finding"
                    if required_findings
                    else "failed_final_review"
                ),
                requirement_ids=affected_requirements,
                diagnosis=(
                    "required final-review findings invalidate readiness"
                    if required_findings
                    else "the exact final integration review did not pass"
                ),
                action="repair one bounded graph slice and review the new integration identity",
                affected_child_ids=affected_children,
                artifact_ids=[str(final_review["review_id"]), *affected_children],
            )
            if problem.get("exhausted") is not True:
                _prepare_recovery_replacement(
                    state,
                    ledger,
                    lease,
                    problem=problem,
                    child_ids=affected_children,
                )
            return None
        final_requests = _active_final_requests(snapshot)
        if not final_requests:
            return _ensure_final_request(state, ledger, lease)
        current_request_id = str(final_requests[-1]["request"]["request_id"])
        approvals = [
            outcome
            for outcome in _operation_outcomes(snapshot, "final_request_approved")
            if outcome.get("request_id") == current_request_id
        ]
        if not approvals:
            request_id = current_request_id
            pack = _find_pack_for_request(ledger, request_id)
            if _state_authorization_profile(state) == "single_user":
                approve_final_request(
                    ledger,
                    lease,
                    request_id=request_id,
                    request_digest=str(final_requests[-1]["request_digest"]),
                    pack_result=pack,
                    integration_ref=_integration_ref_name(ledger.run_id),
                    response_identity=None,
                    response_at=None,
                    direct_user_action=False,
                    authorization_ref=str(parent["start_gate_ref"]),
                )
                return None
            state["final"] = {
                "integration_ref": _integration_ref_name(ledger.run_id),
                "pack_json_path": pack["json_path"],
                "request_id": request_id,
            }
            return _set_action(
                state,
                ledger,
                action_id=f"final-response:{request_id}",
                action_type="final_response",
                payload={
                    "pack_digest": pack["pack_digest"],
                    "pack_json_path": pack["json_path"],
                    "pack_markdown_path": pack["markdown_path"],
                    "readiness_digest": final_requests[-1]["request"][
                        "readiness_digest"
                    ],
                    "request": final_requests[-1],
                },
            )
        final = state.get("final")
        if not isinstance(final, Mapping):
            request_id = str(final_requests[-1]["request"]["request_id"])
            pack = _find_pack_for_request(ledger, request_id)
            final = {
                "integration_ref": _integration_ref_name(ledger.run_id),
                "pack_json_path": pack["json_path"],
                "request_id": request_id,
            }
            state["final"] = final
            _write_state(ledger.repo_root, ledger.run_id, state)
        pack = _pack_result_from_path(Path(str(final["pack_json_path"])))
        execute_final_merge(
            ledger,
            lease,
            request_id=str(final["request_id"]),
            pack_result=pack,
        )
        archive_parent_run(ledger, lease, request_id=str(final["request_id"]))
        return None

    envelope = _current_envelope(snapshot)
    schedule_count = len(_operation_outcomes(snapshot, "schedule_decision")) + 1
    decision = schedule_ready(
        ledger,
        lease,
        decision_id=f"operator-schedule-{schedule_count:04d}",
        live_capacity=int(envelope["worker_capacity"]),
    )
    if decision["status"] == "scheduled":
        return _issue_scheduled_children(state, ledger, lease, decision["selected"])
    if decision["status"] == "complete":
        return None
    if decision["status"] == "waiting":
        raise OrchestratorError("scheduler is waiting without an external action")
    return _pause_for_intervention(
        state,
        ledger,
        lease,
        details=f"scheduler paused: {decision.get('reason')}",
        signals=_intervention_signals("ambiguous_product_semantics"),
    )


def advance_operator(repo_root: Path, run_id: str) -> dict[str, object]:
    """Advance deterministic local transitions to one action or terminal state."""
    root = Path(repo_root).resolve()
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        while True:
            before = ledger.authority_digest()
            state_before = _digest_json(state)
            action = _advance_once(state, ledger, lease)
            if action is not None:
                return _status_value(state, ledger, action=action)
            if _parent_row(ledger)["status"] in {
                "cancelled",
                "archived",
                "paused",
                "recovery_waiting",
                "revoked",
            }:
                return _status_value(state, ledger)
            if ledger.authority_digest() == before and _digest_json(state) == state_before:
                raise OrchestratorError("advance made no durable authority progress")


def continue_recovery(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    """Open one new in-scope repair generation in the same parent run."""
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _CONTINUE_RECOVERY_FIELDS,
        "continue recovery input",
    )
    operation_id = _required_text(value["operation_id"], "operation_id")
    problem_id = _required_text(value["problem_id"], "problem_id")
    diagnosis = _required_text(value["diagnosis"], "diagnosis")
    action = _required_text(value["action"], "action")
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        snapshot = ledger.authority_snapshot()
        parent = _table(snapshot, "parent_runs")[0]
        existing = ledger.get_operation(operation_id)
        if parent["status"] != "recovery_waiting" and existing is None:
            raise OrchestratorError(
                "continue recovery requires a recovery_waiting parent"
            )
        if existing is not None and (
            existing["kind"] != "recovery_generation_opened"
            or existing["phase"] != "authority_committed"
            or not {
                "affected_child_ids",
                "from_generation",
                "integration_head",
                "integration_ref",
                "integration_tree_id",
                "source_context_digest",
            }.issubset(existing["outcome"])
        ):
            raise OperationConflict(
                "continue recovery operation ID belongs to another operation"
            )
        attempts = [
            item
            for item in _problem_attempts(snapshot)
            if item.get("problem_id") == problem_id
        ]
        if existing is not None:
            existing_outcome = existing["outcome"]
            from_generation = int(existing_outcome.get("from_generation", 0))
            matches = [
                item
                for item in attempts
                if _problem_generation(item) == from_generation
                and item.get("exhausted") is True
            ]
        else:
            matches = attempts[-1:] if attempts else []
        if len(matches) != 1 or matches[0].get("exhausted") is not True:
            raise OrchestratorError(
                "continue recovery requires the current exhausted problem"
            )
        exhausted = matches[0]
        context_rows = _table(snapshot, "context_revisions")
        current_row = _current_context_row(snapshot)
        context = _json_field(current_row, "context_json")
        requirement_ids = set(str(item) for item in exhausted["requirement_ids"])
        current_requirements = {
            str(item["requirement_id"]) for item in context["requirements"]
        }
        if existing is None:
            affected_children = [
                str(node["child_id"])
                for node in context["graph"]
                if requirement_ids.intersection(
                    str(item) for item in node["requirements"]
                )
            ]
            source_context_digest = str(current_row["digest"])
        else:
            affected_children = [
                str(item) for item in existing["outcome"]["affected_child_ids"]
            ]
            source_context_digest = str(
                existing["outcome"]["source_context_digest"]
            )
        if (
            not requirement_ids
            or not requirement_ids.issubset(current_requirements)
            or not affected_children
        ):
            raise OrchestratorError(
                "exhausted recovery scope is outside the current envelope"
            )
        if existing is None:
            integration = context["integration"]
            integration_ref = _integration_ref_name(run_id)
            integration_head = _git(root, "rev-parse", "--verify", integration_ref)
            integration_tree = _git(root, "rev-parse", f"{integration_head}^{{tree}}")
            if (
                integration_head != integration["integration_head"]
                or integration_tree != integration["integration_tree_id"]
            ):
                raise OrchestratorError(
                    "continue recovery integration identity is not current"
                )
        else:
            integration_ref = str(existing["outcome"]["integration_ref"])
            integration_head = str(existing["outcome"]["integration_head"])
            integration_tree = str(existing["outcome"]["integration_tree_id"])
        opened = open_recovery_generation(
            ledger,
            lease,
            operation_id=operation_id,
            problem_id=problem_id,
            diagnosis=diagnosis,
            action=action,
            integration_ref=integration_ref,
            integration_head=integration_head,
            integration_tree_id=integration_tree,
            source_context_digest=source_context_digest,
            affected_child_ids=affected_children,
        )
        replacement_reason = (
            f"recovery replacement for {problem_id}: "
            f"{','.join(sorted(affected_children))}"
        )
        replacement_rows = [
            row
            for row in context_rows
            if row.get("previous_digest") == source_context_digest
            and row.get("reason") == replacement_reason
        ]
        if len(replacement_rows) > 1:
            raise OrchestratorError("recovery continuation has ambiguous replacement evidence")
        if replacement_rows:
            source_rows = [
                row for row in context_rows if row.get("digest") == source_context_digest
            ]
            if len(source_rows) != 1:
                raise OrchestratorError("recovery continuation source context is missing")
            source_ids = {
                str(node["child_id"])
                for node in _json_field(source_rows[0], "context_json")["graph"]
            }
            replacement_ids = sorted(
                {
                    str(node["child_id"])
                    for node in _json_field(
                        replacement_rows[0], "context_json"
                    )["graph"]
                }
                - source_ids
            )
            result = _status_value(state, ledger)
            result["recovery_continuation"] = {
                **opened,
                "replacement_child_ids": replacement_ids,
            }
            return result
        problem = _record_recovery_failure(
            ledger,
            lease,
            operation_phase=str(exhausted["operation_phase"]),
            root_condition=str(exhausted["root_condition"]),
            requirement_ids=sorted(requirement_ids),
            diagnosis=diagnosis,
            action=action,
            affected_child_ids=affected_children,
            artifact_ids=[integration_head, integration_tree],
        )
        replacement = _prepare_recovery_replacement(
            state,
            ledger,
            lease,
            problem=problem,
            child_ids=affected_children,
        )
        result = _status_value(state, ledger)
        result["recovery_continuation"] = {
            **opened,
            "replacement_child_ids": replacement["replacement_child_ids"],
        }
        return result


def guide_replacement(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    """Accept one exact prerequisite slice for a quiescent worker failure."""
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _GUIDE_REPLACEMENT_FIELDS,
        "guide replacement input",
    )
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        existing = ledger.get_operation(str(value["operation_id"]))
        if existing is None and _pending_action(state) is not None:
            raise OrchestratorError(
                "replacement guidance requires a quiescent source dispatch"
            )
        guidance = record_replacement_guidance(
            ledger,
            lease,
            operation_id=value["operation_id"],
            problem_id=value["problem_id"],
            problem_round=value["problem_round"],
            failed_result_id=value["failed_result_id"],
            source_dispatch_id=value["source_dispatch_id"],
            problem_attempt_evidence_digest=value[
                "problem_attempt_evidence_digest"
            ],
            source_context_digest=value["source_context_digest"],
            source_graph_digest=value["source_graph_digest"],
            failed_child_id=value["failed_child_id"],
            prerequisite_child_ids=value["prerequisite_child_ids"],
        )
        ledger.rebuild_projection(lease)
        result = _status_value(state, ledger)
        result["replacement_guidance"] = guidance
        return result


def recover_final_checks(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    """Record the exact current graph slice selected to repair failed final checks."""
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _RECOVER_FINAL_CHECKS_FIELDS,
        "recover final checks input",
    )
    operation_id = _required_text(value["operation_id"], "operation_id")
    failed_operation_id = _required_text(
        value["failed_operation_id"], "failed_operation_id"
    )
    diagnosis = _required_text(value["diagnosis"], "diagnosis")
    action = _required_text(value["action"], "action")
    direct_user_action = _boolean(
        value["direct_user_action"], "direct_user_action"
    )
    affected_child_ids = sorted(
        set(_text_list(value["affected_child_ids"], "affected_child_ids"))
    )
    if not direct_user_action:
        raise OrchestratorError("final-check recovery requires direct user action")
    if not affected_child_ids:
        raise OrchestratorError("final-check recovery requires an affected child")
    supplied = {
        "action": action,
        "affected_child_ids": affected_child_ids,
        "diagnosis": diagnosis,
        "direct_user_action": True,
        "failed_operation_id": failed_operation_id,
        "operation_id": operation_id,
    }
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        existing = ledger.get_operation(operation_id)
        if existing is not None:
            if (
                existing["kind"] != "final_check_recovery_guidance"
                or existing["phase"] != "authority_committed"
                or any(
                    existing["outcome"].get(field) != supplied[field]
                    for field in supplied
                )
            ):
                raise OperationConflict(
                    "final-check recovery operation ID belongs to another operation"
                )
            result = _status_value(state, ledger)
            result["final_check_recovery"] = existing["outcome"]
            return result
        snapshot = ledger.authority_snapshot()
        if _table(snapshot, "parent_runs")[0]["status"] != "authorized":
            raise OrchestratorError(
                "final-check recovery requires an authorized resumed parent"
            )
        failed = ledger.get_operation(failed_operation_id)
        if (
            failed is None
            or failed["kind"] != "final_integration_checks"
            or failed["phase"] != "authority_committed"
            or failed["outcome"].get("status") != "failed"
        ):
            raise OrchestratorError(
                "failed_operation_id is not a committed failed final check"
            )
        latest_failed = [
            row
            for row in _table(snapshot, "operations")
            if row["kind"] == "final_integration_checks"
            and row["phase"] == "authority_committed"
            and _json_field(row, "outcome_json").get("status") == "failed"
        ]
        latest_failed.sort(
            key=lambda row: (
                int(row["epoch"]),
                str(row["created_at"]),
                str(row["operation_id"]),
            )
        )
        if not latest_failed or latest_failed[-1]["operation_id"] != failed_operation_id:
            raise OrchestratorError(
                "failed_operation_id is not the latest failed final check"
            )
        nodes = {
            str(node["child_id"]): node
            for node in _current_context(snapshot)["graph"]
        }
        if any(child_id not in nodes for child_id in affected_child_ids):
            raise OrchestratorError(
                "final-check recovery must target current graph children"
            )
        requirements = sorted(
            {
                str(requirement_id)
                for child_id in affected_child_ids
                for requirement_id in nodes[child_id]["requirements"]
            }
        )
        outcome = {
            **supplied,
            "requirement_ids": requirements,
            "status": "recorded",
        }
        with ledger._write_transaction(lease) as connection:
            _insert_committed_operation(
                connection,
                ledger,
                operation_id=operation_id,
                kind="final_check_recovery_guidance",
                epoch=lease.epoch,
                input_fingerprint=_digest_json(supplied),
                outcome=outcome,
                event_type="final_check_recovery_guidance_recorded",
                created_at=_now(),
            )
        ledger.rebuild_projection(lease)
        result = _status_value(state, ledger)
        result["final_check_recovery"] = outcome
        return result


def ingest_operator(
    repo_root: Path, run_id: str, message: Mapping[str, object]
) -> dict[str, object]:
    """Ingest one structured worker or reviewer message for the pending action."""
    root = Path(repo_root).resolve()
    value = _exact_object(message, _INGEST_FIELDS, "ingest message")
    message_type = _required_text(value["message_type"], "message_type")
    payload = value["payload"]
    if not isinstance(payload, Mapping):
        raise OrchestratorError("ingest payload must be an object")
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        if message_type == "worker_result":
            action = _validate_action_input(state, value, "dispatch_workers")
            allowed = {
                item["child_id"] for item in action["payload"]["children"]
            }
            child_id = payload.get("child_id")
            if child_id not in allowed:
                raise OrchestratorError("worker result is outside the pending dispatch")
            validation_error = None
            accepted = accept_child_result(ledger, lease, payload)
            _record_worker_result_observation(
                ledger,
                lease,
                ledger.authority_snapshot(),
                accepted["result"],
                str(accepted["payload_digest"]),
            )
            if not accepted["accepted"]:
                validation_error = (
                    "required test did not pass: "
                    + ", ".join(str(item) for item in accepted["failed_tests"])
                )
                snapshot = ledger.authority_snapshot()
                problem = _record_recovery_failure(
                    ledger,
                    lease,
                    operation_phase="worker_result",
                    root_condition="worker_required_test_failed",
                    requirement_ids=_node_for_child(snapshot, str(child_id))[
                        "requirements"
                    ],
                    diagnosis="worker evidence did not pass every required packet test",
                    action="dispatch a fresh bounded replacement worker",
                    affected_child_ids=[str(child_id)],
                    artifact_ids=[
                        str(action["action_id"]),
                        str(child_id),
                        str(accepted["result"]["result_id"]),
                        str(accepted["payload_digest"]),
                    ],
                    commands=[
                        {
                            "command": item["command"],
                            "exit_code": 0 if item["status"] == "passed" else 1,
                            "output_digest": item["output_digest"],
                            "status": (
                                "passed" if item["status"] == "passed" else "failed"
                            ),
                        }
                        for item in accepted["result"]["commands"]
                    ],
                )
            else:
                try:
                    validate_child_candidate(
                        ledger,
                        lease,
                        validation_id=f"operator-validation-{child_id}",
                        result=accepted["result"],
                        worktree=Path(
                            _worktree_for_child(
                                ledger.authority_snapshot(), str(child_id)
                            )
                        ),
                    )
                except DirtOverlapError as exc:
                    _consume_action(state, ledger, value)
                    action = _pause_for_intervention(
                        state,
                        ledger,
                        lease,
                        details=str(exc),
                        signals=_intervention_signals(
                            "unknown_effect_data_loss_or_user_dirt"
                        ),
                    )
                    return _status_value(state, ledger, action=action)
                except ParentValidationError as exc:
                    validation_error = str(exc)
                    snapshot = ledger.authority_snapshot()
                    problem = _record_recovery_failure(
                        ledger,
                        lease,
                        operation_phase="worker_validation",
                        root_condition="worker_candidate_validation_failed",
                        requirement_ids=_node_for_child(snapshot, str(child_id))[
                            "requirements"
                        ],
                        diagnosis="parent-owned candidate validation rejected the worker tree",
                        action="dispatch a fresh bounded replacement worker",
                        affected_child_ids=[str(child_id)],
                        artifact_ids=[
                            str(child_id), str(accepted["result"]["result_id"])
                        ],
                    )
            snapshot = ledger.authority_snapshot()
            states = _active_child_states(snapshot)
            failed = _worker_failure_children(snapshot)
            remaining = [
                item
                for item in sorted(allowed)
                if states.get(str(item)) not in _TERMINAL_CHILD_STATES
                and str(item) not in failed
            ]
            if not remaining:
                if failed:
                    _record_worker_dispatch_quiescence(ledger, lease, action)
                _consume_action(state, ledger, value)
            snapshot = ledger.authority_snapshot()
            guidance = (
                _quiescent_replacement_guidance(snapshot, action)
                if not remaining and failed
                else None
            )
            result = _status_value(state, ledger)
            result["remaining_child_ids"] = remaining
            if validation_error is not None:
                result["recovery_failure"] = {
                    "details": validation_error,
                    "problem_id": problem["problem_id"],
                    "round": problem["round"],
                }
            if guidance is not None:
                result["replacement_guidance"] = guidance
            return result
        if message_type == "precommit_review":
            action = _validate_action_input(state, value, "precommit_review")
            review = _exact_object(payload, _REVIEW_FIELDS, "pre-commit review")
            action_payload = action["payload"]
            record_precommit_review(
                ledger,
                lease,
                review_id=str(action_payload["review_id"]),
                validation_id=str(action_payload["validation"]["validation_id"]),
                reviewer_identity=_required_text(
                    review["reviewer_identity"], "reviewer_identity"
                ),
                verdict=_required_text(review["verdict"], "verdict"),
                required_findings=review["required_findings"],
                advisory_findings=review["advisory_findings"],
                dispositions=review["dispositions"],
            )
            _consume_action(state, ledger, value)
            return _status_value(state, ledger)
        if message_type == "final_review":
            action = _validate_action_input(state, value, "final_review")
            review = _exact_object(payload, _FINAL_REVIEW_FIELDS, "final review")
            action_payload = action["payload"]
            if review["fresh_context_receipt"] != action_payload["fresh_context_receipt"]:
                raise OrchestratorError("final review context receipt differs")
            current_receipt = _current_context_receipt(ledger.authority_snapshot())
            if action_payload["fresh_context_receipt"] != current_receipt:
                raise OrchestratorError("final review action context is stale")
            record_final_review(
                ledger,
                lease,
                review_id=str(action_payload["review_id"]),
                integration_ref=_integration_ref_name(ledger.run_id),
                reviewer_identity=_required_text(
                    review["reviewer_identity"], "reviewer_identity"
                ),
                affected_requirement_ids=_text_list(
                    review["affected_requirement_ids"],
                    "affected_requirement_ids",
                ),
                fresh_context_receipt=str(review["fresh_context_receipt"]),
                verdict=_required_text(review["verdict"], "verdict"),
                required_findings=review["required_findings"],
                advisory_findings=review["advisory_findings"],
                dispositions=review["dispositions"],
                specialist_results=review["specialist_results"],
            )
            _consume_action(state, ledger, value)
            return _status_value(state, ledger)
        raise OrchestratorError(f"unsupported ingest message_type: {message_type}")


def request_final(repo_root: Path, run_id: str) -> dict[str, object]:
    result = advance_operator(repo_root, run_id)
    action = result.get("next_action")
    if not isinstance(action, Mapping) or action.get("action_type") != "final_response":
        raise OrchestratorError("final response is not the current next action")
    return result


def respond_final(
    repo_root: Path, run_id: str, response: Mapping[str, object]
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(response, _FINAL_RESPONSE_FIELDS, "final response")
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        action = _validate_action_input(state, value, "final_response")
        request = action["payload"]["request"]
        request_value = request["request"]
        if value["request_id"] != request_value["request_id"]:
            raise OrchestratorError("final response request ID differs")
        if value["request_digest"] != request["request_digest"]:
            raise OrchestratorError("final response request digest differs")
        final = state.get("final")
        if not isinstance(final, Mapping):
            raise OrchestratorError("final acceptance state is missing")
        pack = _pack_result_from_path(Path(str(final["pack_json_path"])))
        approve_final_request(
            ledger,
            lease,
            request_id=str(value["request_id"]),
            request_digest=str(value["request_digest"]),
            pack_result=pack,
            integration_ref=str(final["integration_ref"]),
            response_identity=_required_text(value["response_identity"], "response_identity"),
            response_at=_required_text(value["response_at"], "response_at"),
            direct_user_action=_boolean(
                value["direct_user_action"], "direct_user_action"
            ),
        )
        _consume_action(state, ledger, value)
        return _status_value(state, ledger)


def pause_operator(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(request, _PAUSE_FIELDS, "pause input")
    operation_id = _required_text(value["operation_id"], "operation_id")
    reason = _required_text(value["reason"], "reason")
    requested_by = _required_text(value["requested_by"], "requested_by")
    requires_human_resume = _boolean(
        value["requires_human_resume"], "requires_human_resume"
    )
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        if _pending_action(state) is not None:
            _consume_action(state, ledger, {**value, "control": "pause"})
        pause_parent(
            ledger,
            lease,
            operation_id=operation_id,
            reason=reason,
            requested_by=requested_by,
            requires_human_resume=requires_human_resume,
        )
        return _status_value(state, ledger)


def resume_operator(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(request, _RESUME_FIELDS, "resume input")
    operation_id = _required_text(value["operation_id"], "operation_id")
    new_writer_id = _required_text(value["new_writer_id"], "new_writer_id")
    authority_identity = _required_text(
        value["authority_identity"], "authority_identity"
    )
    direct_user_action = _boolean(value["direct_user_action"], "direct_user_action")
    tool_receipt = _required_text(value["tool_receipt"], "tool_receipt")
    available_resources = _text_list(
        value["available_resources"], "available_resources"
    )
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        _reconcile_consumed_zero_diff_replacement(ledger, lease)
        outcome, next_lease = resume_parent(
            ledger,
            lease,
            operation_id=operation_id,
            new_writer_id=new_writer_id,
            authority_identity=authority_identity,
            direct_user_action=direct_user_action,
            tool_receipt=tool_receipt,
            available_resources=available_resources,
        )
        state["lease"] = _lease_value(next_lease)
        state["pending_writer_rotation"] = None
        state["pending_action"] = None
        _write_state(root, run_id, state)
        result = _status_value(state, ledger)
        result["resume"] = outcome
        return result


def cancel_operator(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(request, _CANCEL_FIELDS, "cancel input")
    operation_id = _required_text(value["operation_id"], "operation_id")
    reason = _required_text(value["reason"], "reason")
    actor = _required_text(value["actor"], "actor")
    requested_at = _required_text(value["requested_at"], "requested_at")
    direct_user_action = _boolean(value["direct_user_action"], "direct_user_action")
    expected_authority_digest = _required_text(
        value["expected_authority_digest"],
        "expected_authority_digest",
    )
    action_id = value["action_id"]
    action_digest = value["action_digest"]
    if action_id is not None:
        action_id = _required_text(action_id, "action_id")
    if action_digest is not None:
        action_digest = _required_text(action_digest, "action_digest")
    if (action_id is None) != (action_digest is None):
        raise OrchestratorError("cancel action ID and digest must be supplied together")
    superseded_by = (
        _required_text(value["superseded_by"], "superseded_by")
        if value["superseded_by"] is not None
        else None
    )
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        pending = _pending_action(state)
        if pending is None:
            if action_id is not None:
                raise OrchestratorError("cancel input binds a non-pending action")
        elif (
            pending.get("action_id") != action_id
            or pending.get("action_digest") != action_digest
        ):
            raise OrchestratorError(
                "cancel input differs from the pending operator action"
            )
        cancel_parent(
            ledger,
            lease,
            operation_id=operation_id,
            reason=reason,
            actor=actor,
            requested_at=requested_at,
            direct_user_action=direct_user_action,
            expected_authority_digest=expected_authority_digest,
            operator_input_digest=f"sha256:{_digest_json(value)}",
            pending_action_digest=action_digest,
            pending_action_id=action_id,
            superseded_by=superseded_by,
        )
        if pending is not None:
            _consume_action(state, ledger, value)
        return _status_value(state, ledger)


def cancel_safe_point_operator(repo_root: Path, run_id: str) -> dict[str, object]:
    """Read cancellation classification without loading mutable operator state."""
    root = Path(repo_root).resolve()
    with _operator_lock(root, run_id):
        ledger = ParentLedger(root, run_id)
        if not ledger.path.is_file():
            raise OrchestratorError("Loop parent ledger is not initialized")
        return cancel_safe_point_status(ledger)


def reconcile_for_cancel_operator(
    repo_root: Path,
    run_id: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _RECONCILE_FOR_CANCEL_FIELDS,
        "cancel reconciliation input",
    )
    expected_authority_digest = _required_text(
        value["expected_authority_digest"],
        "expected_authority_digest",
    )
    with _operator_lock(root, run_id):
        _, ledger, lease = _load_runtime(root, run_id)
        return reconcile_for_cancel(
            ledger,
            lease,
            expected_authority_digest=expected_authority_digest,
        )


def projection_status_operator(
    repo_root: Path,
    run_id: str,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    with _operator_lock(root, run_id):
        return projection_status(root, run_id)


def project_terminal_operator(
    repo_root: Path,
    run_id: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _PROJECT_TERMINAL_FIELDS,
        "terminal projection input",
    )
    with _operator_lock(root, run_id):
        return project_terminal(
            root,
            run_id,
            expected_authority_digest=_required_text(
                value["expected_authority_digest"],
                "expected_authority_digest",
            ),
            expected_task_digest=_required_text(
                value["expected_task_digest"],
                "expected_task_digest",
            ),
        )


def archive_pre_admission_operator(
    repo_root: Path,
    run_id: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _ARCHIVE_PRE_ADMISSION_FIELDS,
        "pre-admission archive input",
    )
    try:
        with _operator_lock(root, run_id, create=True):
            return archive_pre_admission_parent(
                root,
                run_id,
                expected_task_digest=_required_text(
                    value["expected_task_digest"],
                    "expected_task_digest",
                ),
                commit_enabled=_boolean(value["commit_enabled"], "commit_enabled"),
            )
    finally:
        cleanup_pre_admission_lock(root, run_id)


def retire_task_evidence_operator(
    repo_root: Path,
    run_id: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    value = _exact_object(
        request,
        _RETIRE_TASK_EVIDENCE_FIELDS,
        "task evidence retirement input",
    )
    with _operator_lock(root, run_id):
        state, _, _ = _load_runtime(root, run_id)
        return retire_task_evidence(
            root,
            run_id,
            expected_authority_digest=_required_text(
                value["expected_authority_digest"],
                "expected_authority_digest",
            ),
            expected_task_digest=_required_text(
                value["expected_task_digest"],
                "expected_task_digest",
            ),
            operator_state=state,
            commit_enabled=_boolean(value["commit_enabled"], "commit_enabled"),
        )


def revoke_operator(
    repo_root: Path, run_id: str, request: Mapping[str, object]
) -> dict[str, object]:
    """Terminally revoke one run's persisted execution binding by direct action."""
    root = Path(repo_root).resolve()
    value = _exact_object(request, _REVOKE_FIELDS, "revoke input")
    operation_id = _required_text(value["operation_id"], "operation_id")
    actor = _required_text(value["actor"], "actor")
    reason = _required_text(value["reason"], "reason")
    revoked_at = _required_text(value["revoked_at"], "revoked_at")
    direct_user_action = _boolean(value["direct_user_action"], "direct_user_action")
    receipt_value = value["execution_receipt"]
    if receipt_value is not None and not isinstance(receipt_value, str):
        raise OrchestratorError("execution_receipt must be a string or null")
    execution_receipt = (
        _required_text(receipt_value, "execution_receipt")
        if receipt_value is not None
        else None
    )
    with _operator_lock(root, run_id):
        state, ledger, lease = _load_runtime(root, run_id)
        revocation = ledger.revoke_execution_binding(
            lease,
            operation_id=operation_id,
            actor=actor,
            reason=reason,
            revoked_at=revoked_at,
            direct_user_action=direct_user_action,
            execution_receipt=execution_receipt,
        )
        result = _status_value(state, ledger)
        result["revocation"] = revocation
        return result


def _status_value(
    state: Mapping[str, object],
    ledger: ParentLedger,
    *,
    action: Mapping[str, object] | None = None,
) -> dict[str, object]:
    snapshot = ledger.authority_snapshot()
    parent = _table(snapshot, "parent_runs")[0]
    pending = dict(action) if action is not None else _pending_action(state)
    if parent["status"] in {"cancelled", "archived", "revoked"}:
        pending = None
    elif (
        parent["status"] == "paused"
        and (pending is None or pending.get("action_type") != "human_intervention")
    ):
        pending = None
    recovery = (
        _recovery_projection(snapshot)
        if _table(snapshot, "context_revisions")
        else {
            "active_problems": [],
            "exhausted_problems": [],
            "generations": [],
            "replacement_child_ids": [],
            "status": "clear",
        }
    )
    result = {
        "active_child_states": _active_child_states(snapshot)
        if _table(snapshot, "context_revisions")
        else {},
        "authority_digest": f"sha256:{ledger.authority_digest()}",
        "child_states": _child_states(snapshot),
        "epoch": parent["epoch"],
        "next_action": pending,
        "parent_status": parent["status"],
        "projection": ledger.projection_status(),
        "recovery": recovery,
        "run_id": ledger.run_id,
        "status": "awaiting_external_input" if pending else str(parent["status"]),
    }
    if parent["status"] == "recovery_waiting":
        exhausted = recovery["exhausted_problems"]
        context = _current_context(snapshot)
        result.update(
            {
                "integration": dict(context["integration"]),
                "problem": exhausted[-1] if exhausted else None,
                "required_input": "new diagnosis or repair guidance",
            }
        )
    elif parent["status"] == "paused":
        result["resume_safe_point"] = resume_safe_point_status(ledger)
    return result


def operator_status(repo_root: Path, run_id: str) -> dict[str, object]:
    root = Path(repo_root).resolve()
    with _operator_lock(root, run_id):
        state, ledger, _ = _load_runtime(root, run_id)
        return _status_value(state, ledger)


def _read_input(repo_root: Path, path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    if path == "-":
        try:
            value = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise OrchestratorError(f"stdin does not contain valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise OrchestratorError("stdin JSON must be an object")
        return value
    return _read_json_object(
        _repo_relative_path(repo_root, path, "input"), "command input"
    )


def _write_output(
    value: object, output: Path | None, *, create_parent: bool = False
) -> None:
    payload = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if output is not None:
        if create_parent:
            output.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(output, payload)
    else:
        sys.stdout.buffer.write(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "init",
        "respond-start",
        "ingest",
        "respond-final",
        "pause",
        "resume",
        "cancel",
        "revoke",
        "continue-recovery",
        "guide-replacement",
        "recover-final-checks",
        "reconcile-for-cancel",
        "project-terminal",
        "archive-pre-admission",
        "retire-task-evidence",
    ):
        child = subparsers.add_parser(command)
        child.add_argument("--input", required=True)
    for command in (
        "status",
        "request-start",
        "advance",
        "request-final",
        "cancel-safe-point",
        "projection-status",
    ):
        subparsers.add_parser(command)
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    output_path = None
    try:
        if args.output:
            output_path = _output_path(root, args.run_id, args.output)
        if args.command == "init":
            result = initialize_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "status":
            result = operator_status(root, args.run_id)
        elif args.command == "request-start":
            result = request_start(root, args.run_id)
        elif args.command == "respond-start":
            result = respond_start(root, args.run_id, _read_input(root, args.input))
        elif args.command == "advance":
            result = advance_operator(root, args.run_id)
        elif args.command == "ingest":
            result = ingest_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "request-final":
            result = request_final(root, args.run_id)
        elif args.command == "respond-final":
            result = respond_final(root, args.run_id, _read_input(root, args.input))
        elif args.command == "pause":
            result = pause_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "resume":
            result = resume_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "revoke":
            result = revoke_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "continue-recovery":
            result = continue_recovery(root, args.run_id, _read_input(root, args.input))
        elif args.command == "guide-replacement":
            result = guide_replacement(root, args.run_id, _read_input(root, args.input))
        elif args.command == "recover-final-checks":
            result = recover_final_checks(
                root, args.run_id, _read_input(root, args.input)
            )
        elif args.command == "cancel":
            result = cancel_operator(root, args.run_id, _read_input(root, args.input))
        elif args.command == "cancel-safe-point":
            result = cancel_safe_point_operator(root, args.run_id)
        elif args.command == "reconcile-for-cancel":
            result = reconcile_for_cancel_operator(
                root,
                args.run_id,
                _read_input(root, args.input),
            )
        elif args.command == "projection-status":
            result = projection_status_operator(root, args.run_id)
        elif args.command == "project-terminal":
            result = project_terminal_operator(
                root,
                args.run_id,
                _read_input(root, args.input),
            )
        elif args.command == "archive-pre-admission":
            result = archive_pre_admission_operator(
                root,
                args.run_id,
                _read_input(root, args.input),
            )
        elif args.command == "retire-task-evidence":
            result = retire_task_evidence_operator(
                root,
                args.run_id,
                _read_input(root, args.input),
            )
        else:
            raise OrchestratorError(f"unsupported command: {args.command}")
        _write_output(result, output_path, create_parent=True)
        return 0
    except (
        LedgerError,
        QualificationError,
        TaskArchiveError,
        TaskProjectionError,
        OrchestratorError,
        OSError,
        sqlite3.Error,
    ) as exc:
        error = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "status": "failed",
        }
        try:
            _write_output(
                error,
                output_path,
                create_parent=_run_root(root, args.run_id).is_dir(),
            )
        except OSError:
            _write_output(error, None)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
