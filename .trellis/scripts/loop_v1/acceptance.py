"""Deterministic acceptance evidence and the Loop v1 local final gate."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from common.io import write_bytes_atomic
from common.trellis_config import parse_simple_yaml

from .context import (
    ContextError,
    FreshnessError,
    _approved_envelope,
    _digest_json,
    _insert_committed_operation,
    _latest_context_row,
    _path_matches,
    _required_text,
    _text_list,
)
from .integration import (
    _EMPTY_DIFF_IDENTITY,
    _ZERO_DIFF_INTEGRATION_KIND,
    _is_ancestor,
    _merge_parents,
    _replacement_maps_to_child,
)
from .ledger import OperationConflict, ParentLedger, WriterLease, _canonical_json, _now
from .worker_commit import (
    GitStateError,
    _dispositions,
    _findings,
    _git,
    _git_text,
    _run_parent_check,
    scan_repository_dirt,
)


class AcceptanceError(ContextError):
    """Base error for acceptance evidence and the final local gate."""


class FinalReviewError(AcceptanceError):
    """Raised when the exact-integration review is incomplete or stale."""


class ReadinessError(AcceptanceError):
    """Raised when an acceptance pack or readiness claim is invalid."""


class FinalGateError(AcceptanceError):
    """Raised when the exact final request is missing, stale, or unauthorized."""


class FinalMergeError(AcceptanceError):
    """Raised when the approved local merge cannot be proved or verified."""


_FINAL_REVIEW_KIND = "final_integration_review"
_FINAL_REQUEST_KIND = "final_request_created"
_FINAL_APPROVAL_KIND = "final_request_approved"
_LOCAL_COMMAND_AUTHORITY_KIND = "local_command_authority"
_FINAL_MERGE_KIND = "final_local_merge"
_PARENT_ARCHIVE_KIND = "parent_archive"
_QUALIFICATION_ROTATION_KIND = "source_release_qualification_rotation"
_QUALIFICATION_CONFIG_PATH = ".trellis/config.yaml"
_QUALIFICATION_RECEIPT_DIR = ".trellis/spec/project/receipts/loop-v1"
_QUALIFICATION_CONFIG_KEYS = (
    "qualification_receipt",
    "qualification_receipt_digest",
)
_FINAL_ACTIONS = ["local_merge", "parent_archive", "verification"]
_PROHIBITED_EXTERNAL_ACTIONS = [
    "deploy",
    "downstream_sync",
    "push",
    "release",
    "tag",
]
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
_EFFECT_FIELDS = frozenset({"classification", "effect_id", "evidence_digest", "status"})
_SKIPPED_FIELDS = frozenset({"check_id", "reason", "status"})
_SPECIALIST_FIELDS = frozenset({"evidence_digest", "specialist", "status"})
_MODEL_IDENTITY = re.compile(r"model:([A-Za-z0-9._-]+):([A-Za-z0-9._-]+)\Z")
_SPECIALIST_TRIGGERS = {
    "api_config": ("api", "cli", "config", "public interface"),
    "concurrency_recovery": (
        "concurrency",
        "git ref",
        "lock",
        "recovery",
        "resource",
        "scheduler",
        "state machine",
    ),
    "dependency_supply": (
        "dependency",
        "generated lock",
        "license",
        "lockfile",
        "package",
        "supply",
    ),
    "external_input": ("external input", "parser", "parsing", "webhook"),
    "frontend_accessibility": (
        "accessibility",
        "critical workflow",
        "frontend",
        "user-visible",
    ),
    "performance": (
        "latency",
        "memory",
        "performance",
        "resource limit",
        "throughput",
    ),
    "schema_migration": (
        "data conversion",
        "database",
        "deletion",
        "durable data",
        "migration",
        "schema",
    ),
    "security_privacy": (
        "authentication",
        "credential",
        "permission",
        "privacy",
        "secret",
        "security",
        "token",
        "user data",
    ),
}


def record_final_review(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    review_id: str,
    integration_ref: str,
    reviewer_identity: str,
    fresh_context_receipt: str,
    verdict: str,
    required_findings: Sequence[Mapping[str, object]],
    advisory_findings: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
    specialist_results: Sequence[Mapping[str, object]],
    affected_requirement_ids: Sequence[str] = (),
) -> dict[str, object]:
    """Record one fresh non-implementer review of the exact integration HEAD."""
    review_id = _required_text(review_id, "review_id")
    integration_ref = _integration_ref(integration_ref)
    reviewer_identity = _required_text(reviewer_identity, "reviewer_identity")
    fresh_context_receipt = _required_text(
        fresh_context_receipt, "fresh_context_receipt"
    )
    if verdict not in {"passed", "failed"}:
        raise FinalReviewError("final review verdict must be passed or failed")
    required = _findings(required_findings, "required_findings")
    advisory = _findings(advisory_findings, "advisory_findings")
    finding_ids = [item["finding_id"] for item in (*required, *advisory)]
    if len(finding_ids) != len(set(finding_ids)):
        raise FinalReviewError("final review finding IDs must be unique")
    disposition_rows = _dispositions(dispositions)
    if {item["finding_id"] for item in disposition_rows} != {
        item["finding_id"] for item in advisory
    }:
        raise FinalReviewError(
            "every advisory final finding requires exactly one disposition"
        )
    if verdict == "passed" and required:
        raise FinalReviewError("a final review with required findings cannot pass")

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime(connection, ledger.run_id, require_authorized=True)
        implementers = {
            row["actor"]
            for row in connection.execute(
                "SELECT actor FROM verifications WHERE actor LIKE 'model:%'"
            ).fetchall()
        }
        actual_touches = _integrated_touches(connection)
        requirements_ready, _ = _requirements_ready(connection, runtime["context"])
        children_ready, _ = _children_ready(connection)
    finally:
        connection.close()
    if not requirements_ready or not children_ready:
        raise FinalReviewError(
            "final review requires complete integrated requirement evidence"
        )
    identity = _MODEL_IDENTITY.fullmatch(reviewer_identity)
    if identity is None:
        raise FinalReviewError(
            "final reviewer identity must be model:<surface>:<fresh-context>"
        )
    if identity.group(1) not in runtime["envelope"]["approved_agent_surfaces"]:
        raise FinalReviewError("final reviewer surface is outside the start envelope")
    if reviewer_identity in implementers:
        raise FinalReviewError("final reviewer must be a fresh non-implementer")

    git_state = _integration_state(ledger, runtime, integration_ref)
    triggers = _specialist_triggers(runtime, actual_touches)
    specialists = _specialist_matrix(specialist_results, triggers)
    if verdict == "passed" and any(
        item["applicable"] and item["status"] != "passed" for item in specialists
    ):
        raise FinalReviewError("a triggered specialist review has not passed")
    requirements = _applicable_requirements(runtime["context"])
    affected_requirements = sorted(
        _text_list(list(affected_requirement_ids), "affected_requirement_ids")
    )
    known_requirements = {str(item["requirement_id"]) for item in requirements}
    if not set(affected_requirements).issubset(known_requirements):
        raise FinalReviewError("final review names an unknown affected requirement")
    if verdict == "failed" and not affected_requirements:
        raise FinalReviewError("a failed final review must name affected requirements")
    if verdict == "passed" and affected_requirements:
        raise FinalReviewError("a passed final review cannot name affected requirements")
    input_value = {
        "advisory_findings": advisory,
        "affected_requirement_ids": affected_requirements,
        "dispositions": disposition_rows,
        "fresh_context_receipt": fresh_context_receipt,
        "integration_head": git_state["integration_head"],
        "integration_ref": integration_ref,
        "integration_tree_id": git_state["integration_tree_id"],
        "required_findings": required,
        "requirements": requirements,
        "review_id": review_id,
        "reviewer_identity": reviewer_identity,
        "specialists": specialists,
        "verdict": verdict,
    }
    outcome = {
        **input_value,
        "artifact_digest": _digest_json(
            {
                "integration_head": git_state["integration_head"],
                "integration_tree_id": git_state["integration_tree_id"],
                "requirements": requirements,
            }
        ),
        "status": "reviewed" if verdict == "passed" else "review_blocked",
    }
    operation_id = f"final-review:{review_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return _replay(existing, _FINAL_REVIEW_KIND, input_value, outcome)

    with ledger._write_transaction(lease) as connection:
        current = _runtime(connection, ledger.run_id, require_authorized=True)
        current_git = _integration_state(ledger, current, integration_ref)
        if (
            current_git != git_state
            or _applicable_requirements(current["context"]) != requirements
        ):
            raise FreshnessError("final review artifact changed during review")
        if connection.execute(
            "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone():
            raise OperationConflict("final review ID was concurrently reused")
        now = _now()
        connection.execute(
            """
            INSERT INTO verifications (
                verification_id, run_id, artifact_digest, actor, verdict,
                findings_json, applicability_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"final-review:{review_id}",
                ledger.run_id,
                outcome["artifact_digest"],
                reviewer_identity,
                verdict,
                _canonical_json(
                    {
                        "advisory": advisory,
                        "dispositions": disposition_rows,
                        "required": required,
                    }
                ),
                _canonical_json(
                    {
                        "fresh_context_receipt": fresh_context_receipt,
                        "requirements": requirements,
                        "specialists": specialists,
                    }
                ),
                now,
            ),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_FINAL_REVIEW_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(input_value),
            outcome=outcome,
            event_type="final_integration_review_recorded",
            created_at=now,
        )
    ledger.rebuild_projection(lease)
    return outcome


def generate_acceptance_pack(
    ledger: ParentLedger,
    *,
    pack_id: str,
    integration_ref: str,
    tool_receipt: str,
    environment: Mapping[str, object],
    effect_proofs: Sequence[Mapping[str, object]],
    skipped_checks: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Project the current ledger and Git state into stable JSON and Markdown."""
    pack_id = _required_text(pack_id, "pack_id")
    integration_ref = _integration_ref(integration_ref)
    tool_receipt = _required_text(tool_receipt, "tool_receipt")
    environment_value = _exact_text_mapping(
        environment, _ENVIRONMENT_FIELDS, "environment"
    )
    effect_rows = _effect_proofs(effect_proofs)
    skipped_rows = _skipped_checks(skipped_checks)
    snapshot = ledger.authority_snapshot()
    source_position = _snapshot_position(snapshot)
    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime(connection, ledger.run_id, require_authorized=True)
        final_review = _latest_final_review(connection)
        child_evidence = _child_evidence(connection)
        problem_evidence = _problem_evidence(connection)
        omission_evidence = _omission_evidence(connection)
    finally:
        connection.close()
    git_state = _integration_state(ledger, runtime, integration_ref)
    projection = ledger.projection_status()
    dirt = scan_repository_dirt(ledger.repo_root)
    request_id = _predicted_request_id(
        ledger.run_id,
        pack_id,
        runtime["envelope"]["base_head"],
        git_state["integration_head"],
        source_position,
    )
    pack = {
        "authority": {
            "digest": _digest_json(snapshot),
            "snapshot": snapshot,
            "source_position": source_position,
        },
        "children": child_evidence,
        "context": {
            "context_digest": runtime["context_row"]["digest"],
            "envelope_digest": runtime["context_row"]["envelope_digest"],
            "graph_digest": runtime["context_row"]["graph_digest"],
            "requirement_digest": runtime["context_row"]["requirement_digest"],
        },
        "effects": effect_rows,
        "environment": environment_value,
        "final_request_id": request_id,
        "git": git_state,
        "goal": runtime["envelope"]["goal"],
        "omissions": omission_evidence,
        "pack_id": pack_id,
        "problems": problem_evidence,
        "requirements": _applicable_requirements(runtime["context"]),
        "review": final_review,
        "risks": sorted(runtime["context"]["risks"]),
        "run_id": ledger.run_id,
        "runtime_health": {
            "dirt": dirt,
            "projection": projection,
        },
        "schema_version": 1,
        "skipped_checks": skipped_rows,
        "tool_receipt": tool_receipt,
    }
    pack_digest = _digest_json(pack)
    root = ledger.path.parent / "acceptance"
    json_path = root / f"{pack_id}.json"
    markdown_path = root / f"{pack_id}.md"
    json_payload = (
        json.dumps(
            {"pack": pack, "pack_digest": pack_digest},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    markdown_payload = _pack_markdown(pack, pack_digest).encode("utf-8")
    root.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(json_path, json_payload)
    write_bytes_atomic(markdown_path, markdown_payload)
    return {
        "json_digest": f"sha256:{sha256(json_payload).hexdigest()}",
        "json_path": str(json_path),
        "markdown_digest": f"sha256:{sha256(markdown_payload).hexdigest()}",
        "markdown_path": str(markdown_path),
        "pack": pack,
        "pack_digest": pack_digest,
    }


def compute_final_readiness(
    ledger: ParentLedger,
    *,
    pack_result: Mapping[str, object],
    integration_ref: str,
    tool_receipt: str,
    request_id: str | None = None,
) -> dict[str, object]:
    """Recompute every final predicate directly; no verdict is persisted."""
    integration_ref = _integration_ref(integration_ref)
    tool_receipt = _required_text(tool_receipt, "tool_receipt")
    pack, supplied_digest = _pack_value(pack_result)
    expected_request = _required_text(pack["final_request_id"], "final_request_id")
    if request_id is not None and request_id != expected_request:
        raise ReadinessError("request identity differs from the acceptance pack")
    predicates: list[dict[str, object]] = []

    def add(predicate_id: str, passed: bool, evidence: object) -> None:
        predicates.append(
            {"evidence": evidence, "id": predicate_id, "passed": bool(passed)}
        )

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime(connection, ledger.run_id, require_authorized=False)
        parent = runtime["parent"]
        current_review = _latest_final_review(connection)
        requirements_ok, requirements_evidence = _requirements_ready(
            connection, runtime["context"]
        )
        children_ok, children_evidence = _children_ready(connection)
        problems_ok, problems_evidence = _problems_ready(connection)
        resources = [
            dict(row)
            for row in connection.execute(
                "SELECT child_id, resource_key FROM resource_claims "
                "WHERE state = 'acquired' ORDER BY child_id, resource_key"
            ).fetchall()
        ]
        unresolved = [
            dict(row)
            for row in connection.execute(
                "SELECT operation_id, kind, phase FROM operations "
                "WHERE phase IN ('prepared', 'effect_observed') "
                "ORDER BY operation_id"
            ).fetchall()
        ]
        fence = connection.execute(
            "SELECT epoch, released_at FROM writer_fences ORDER BY epoch DESC LIMIT 1"
        ).fetchone()
        ledger_effects = [
            dict(row)
            for row in connection.execute(
                "SELECT effect_id, classification, authorization_ref "
                "FROM effects ORDER BY effect_id"
            ).fetchall()
        ]
        source_ok, source_evidence = _source_position_current(
            connection,
            int(pack["authority"]["source_position"]),
            expected_request,
            pack["pack_digest"] if "pack_digest" in pack else supplied_digest,
        )
    finally:
        connection.close()

    start_gate = _start_gate_ready(pack["authority"]["snapshot"])
    add("start_gate_current", start_gate[0], start_gate[1])
    add("parent_authorized", parent["status"] == "authorized", parent["status"])
    add("requirements_complete", requirements_ok, requirements_evidence)
    add("integrated_children_traceable", children_ok, children_evidence)
    review_ok = bool(
        current_review
        and current_review["verdict"] == "passed"
        and not current_review["required_findings"]
    )
    add("final_review_passed", review_ok, current_review)
    specialist_ok = bool(
        current_review
        and all(
            not item["applicable"] or item["status"] == "passed"
            for item in current_review["specialists"]
        )
    )
    add(
        "specialist_reviews_passed",
        specialist_ok,
        current_review["specialists"] if current_review else [],
    )
    add("problem_budget_clear", problems_ok, problems_evidence)
    add("no_unresolved_operations", not unresolved, unresolved)
    add(
        "writer_fence_current",
        bool(
            fence and fence["epoch"] == parent["epoch"] and fence["released_at"] is None
        ),
        dict(fence) if fence else None,
    )
    projection = ledger.projection_status()
    projection_file_ok = _projection_file_current(ledger, projection)
    add(
        "summary_projection_current",
        projection["status"] == "current" and projection_file_ok[0],
        {"file": projection_file_ok[1], "status": projection},
    )
    add("resources_released", not resources, resources)

    git_error = None
    try:
        git_state = _integration_state(ledger, runtime, integration_ref)
    except (AcceptanceError, FreshnessError, GitStateError) as exc:
        git_state = {}
        git_error = str(exc)
    add(
        "integration_identity_current",
        git_error is None and git_state == pack["git"],
        git_error or git_state,
    )
    actual_branch = _current_branch(ledger.repo_root)
    actual_head = _git_text(ledger.repo_root, "rev-parse", "HEAD")
    actual_tree = _git_text(ledger.repo_root, "rev-parse", "HEAD^{tree}")
    add(
        "main_unchanged",
        actual_branch == pack["git"]["base_branch"]
        and actual_head == pack["git"]["base_head"]
        and actual_tree == pack["git"]["base_tree_id"],
        {"branch": actual_branch, "head": actual_head, "tree_id": actual_tree},
    )
    dirt = scan_repository_dirt(ledger.repo_root)
    add("main_clean", not dirt["paths"], dirt)
    add("tool_receipt_current", tool_receipt == pack["tool_receipt"], tool_receipt)
    add(
        "envelope_receipt_current",
        pack["tool_receipt"] == runtime["envelope"]["conformance_receipt"],
        runtime["envelope"]["conformance_receipt"],
    )
    effect_ok = _effects_ready(pack["effects"], ledger_effects)
    add("effects_within_boundary", effect_ok[0], effect_ok[1])
    actual_digest = _digest_json(pack)
    add(
        "pack_digest_current",
        actual_digest == supplied_digest,
        {"actual": actual_digest, "supplied": supplied_digest},
    )
    pack_review_ok = current_review == pack["review"]
    add("pack_review_current", pack_review_ok, current_review)
    file_ok, file_evidence = _pack_file_current(pack_result, pack, supplied_digest)
    add("pack_file_current", file_ok, file_evidence)
    add("pack_source_current", source_ok, source_evidence)
    ready = all(item["passed"] for item in predicates)
    bindings = {
        "base_head": pack["git"]["base_head"],
        "integration_head": pack["git"]["integration_head"],
        "pack_digest": supplied_digest,
        "request_id": expected_request,
        "review_id": pack["review"]["review_id"] if pack["review"] else None,
        "tool_receipt": tool_receipt,
    }
    return {
        "bindings": bindings,
        "predicates": predicates,
        "readiness_digest": _digest_json(
            {"bindings": bindings, "predicates": predicates, "ready": ready}
        ),
        "ready": ready,
    }


def create_final_request(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    pack_result: Mapping[str, object],
    readiness: Mapping[str, object],
    integration_ref: str,
    post_merge_checks: Sequence[str],
    author_name: str,
    author_email: str,
) -> dict[str, object]:
    """Create the immutable local-only final request for direct user approval."""
    request_id = _required_text(request_id, "request_id")
    integration_ref = _integration_ref(integration_ref)
    checks = _text_list(list(post_merge_checks), "post_merge_checks")
    if not checks:
        raise FinalGateError("final request requires at least one smoke check")
    author_name = _required_text(author_name, "author_name")
    author_email = _required_text(author_email, "author_email")
    if "@" not in author_email:
        raise FinalGateError("author_email must contain '@'")
    pack, pack_digest = _pack_value(pack_result)
    if request_id != pack["final_request_id"]:
        raise FinalGateError("final request ID does not match the acceptance pack")
    existing = ledger.get_operation(f"final-request:{request_id}")
    if existing is not None:
        request = _request_value(
            pack,
            pack_digest,
            request_id,
            integration_ref,
            checks,
            author_name,
            author_email,
            _required_text(readiness.get("readiness_digest"), "readiness_digest"),
        )
        outcome = {
            "request": request,
            "request_digest": _digest_json(request),
            "status": "pending_direct_response",
        }
        return _replay(existing, _FINAL_REQUEST_KIND, request, outcome)
    current = compute_final_readiness(
        ledger,
        pack_result=pack_result,
        integration_ref=integration_ref,
        tool_receipt=pack["tool_receipt"],
    )
    if not current["ready"] or dict(readiness) != current:
        raise FinalGateError("final request requires the exact current readiness")
    request = _request_value(
        pack,
        pack_digest,
        request_id,
        integration_ref,
        checks,
        author_name,
        author_email,
        current["readiness_digest"],
    )
    request_digest = _digest_json(request)
    outcome = {
        "request": request,
        "request_digest": request_digest,
        "status": "pending_direct_response",
    }
    operation_id = f"final-request:{request_id}"
    with ledger._write_transaction(lease) as connection:
        runtime = _runtime(connection, ledger.run_id, require_authorized=True)
        if runtime["parent"]["final_gate_ref"] is not None:
            raise FinalGateError("parent already has a final gate")
        if connection.execute(
            "SELECT 1 FROM gate_requests WHERE gate_id = ?", (request_id,)
        ).fetchone():
            raise OperationConflict("final request identity is already in use")
        now = _now()
        connection.execute(
            """
            INSERT INTO gate_requests (
                gate_id, run_id, kind, input_digest, response_identity,
                response_at, invalidation_reason, created_at
            ) VALUES (?, ?, 'final', ?, NULL, NULL, NULL, ?)
            """,
            (request_id, ledger.run_id, request_digest, now),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_FINAL_REQUEST_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(request),
            outcome=outcome,
            event_type="final_request_created",
            created_at=now,
        )
    ledger.rebuild_projection(lease)
    return outcome


def approve_final_request(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    request_digest: str,
    pack_result: Mapping[str, object],
    integration_ref: str,
    response_identity: str | None,
    response_at: str | None,
    direct_user_action: bool,
    authorization_ref: str | None = None,
) -> dict[str, object]:
    """Approve exact local finalization by a direct or retained start authority."""
    request_id = _required_text(request_id, "request_id")
    request_digest = _required_text(request_digest, "request_digest")
    if not isinstance(direct_user_action, bool):
        raise FinalGateError("direct_user_action must be boolean")
    if direct_user_action:
        response_identity = _required_text(response_identity, "response_identity")
        response_at = _required_text(response_at, "response_at")
        if authorization_ref is not None:
            raise FinalGateError("direct final approval cannot reuse start authority")
    else:
        if response_identity is not None or response_at is not None:
            raise FinalGateError(
                "start-authorized finalization cannot supply a direct response"
            )
        authorization_ref = _required_text(authorization_ref, "authorization_ref")
    request_operation = _final_request_operation(ledger, request_id)
    if request_operation["request_digest"] != request_digest:
        raise FinalGateError("final response digest does not match the request")
    pack, pack_digest = _pack_value(pack_result)
    if pack_digest != request_operation["request"]["pack_digest"]:
        raise FinalGateError("final response pack digest does not match the request")
    readiness = compute_final_readiness(
        ledger,
        pack_result=pack_result,
        integration_ref=integration_ref,
        tool_receipt=pack["tool_receipt"],
        request_id=request_id,
    )
    if not readiness["ready"]:
        _invalidate_final_gate(
            ledger, lease, request_id, "readiness drift before approval"
        )
        raise FinalGateError(
            "final request drifted before "
            + ("direct approval" if direct_user_action else "local finalization")
        )
    supplied: dict[str, object] = {
        "direct_user_action": direct_user_action,
        "pack_digest": pack_digest,
        "request_digest": request_digest,
        "request_id": request_id,
    }
    if direct_user_action:
        supplied.update(
            {
                "response_at": response_at,
                "response_identity": response_identity,
            }
        )
    else:
        supplied.update(
            {
                "authorization_kind": "local_start",
                "authorization_ref": authorization_ref,
            }
        )
    outcome = {**supplied, "status": "approved"}
    operation_id = f"final-approval:{request_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return _replay(existing, _FINAL_APPROVAL_KIND, supplied, outcome)

    with ledger._write_transaction(lease) as connection:
        runtime = _runtime(connection, ledger.run_id, require_authorized=True)
        gate = connection.execute(
            "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'final'",
            (request_id,),
        ).fetchone()
        if gate is None or gate["input_digest"] != request_digest:
            raise FinalGateError("exact final gate is missing")
        if gate["invalidation_reason"] is not None:
            raise FinalGateError("final gate was invalidated")
        if gate["response_identity"] is not None:
            raise OperationConflict("final gate already has another response")
        if runtime["parent"]["final_gate_ref"] not in {None, request_id}:
            raise FinalGateError("parent references another final gate")
        if not direct_user_action:
            start_gate = connection.execute(
                "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'start'",
                (authorization_ref,),
            ).fetchone()
            authority = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (f"local-command-authority:{authorization_ref}",),
            ).fetchone()
            authority_outcome = (
                json.loads(authority["outcome_json"]) if authority is not None else {}
            )
            if (
                runtime["parent"]["start_gate_ref"] != authorization_ref
                or start_gate is None
                or start_gate["response_identity"] is None
                or start_gate["invalidation_reason"] is not None
                or authority is None
                or authority["kind"] != _LOCAL_COMMAND_AUTHORITY_KIND
                or authority["phase"] != "authority_committed"
                or authority_outcome.get("profile") != "single_user"
                or "local_final_merge"
                not in authority_outcome.get("authorized_local_actions", [])
            ):
                raise FinalGateError(
                    "local finalization lacks current single-user start authority"
                )
        now = _now()
        if direct_user_action:
            connection.execute(
                """
                UPDATE gate_requests SET response_identity = ?, response_at = ?
                WHERE gate_id = ? AND response_identity IS NULL
                """,
                (response_identity, response_at, request_id),
            )
        connection.execute(
            "UPDATE parent_runs SET final_gate_ref = ?, updated_at = ? WHERE run_id = ?",
            (request_id, now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_FINAL_APPROVAL_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="final_request_approved",
            created_at=now,
        )
    ledger.rebuild_projection(lease)
    return outcome


def execute_final_merge(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    pack_result: Mapping[str, object],
) -> dict[str, object]:
    """Create or recover the one approved no-ff local-main merge and verify it."""
    request_id = _required_text(request_id, "request_id")
    request_outcome = _approved_final_request(ledger, request_id)
    request = request_outcome["request"]
    pack, pack_digest = _pack_value(pack_result)
    if pack_digest != request["pack_digest"]:
        raise FinalGateError("final merge pack differs from approved request")
    operation_id = f"final-merge:{request_id}"
    operation = ledger.get_operation(operation_id)
    if operation is None:
        readiness = compute_final_readiness(
            ledger,
            pack_result=pack_result,
            integration_ref=request["integration_ref"],
            tool_receipt=request["tool_receipt"],
            request_id=request_id,
        )
        if not readiness["ready"]:
            _invalidate_final_gate(
                ledger, lease, request_id, "readiness drift before merge"
            )
            raise FinalGateError("approved final request drifted before merge")
        intent = {**request, "operation_id": operation_id, "status": "prepared"}
        operation = ledger.prepare_operation(
            lease,
            operation_id=operation_id,
            kind=_FINAL_MERGE_KIND,
            input_fingerprint=_digest_json(intent),
            intent=intent,
        )
    elif operation["kind"] != _FINAL_MERGE_KIND:
        raise OperationConflict("final merge operation identity has another kind")
    if operation["phase"] == "authority_committed":
        _assert_final_merge_git(
            ledger.repo_root,
            operation["outcome"],
            execution_binding=_execution_binding_for_recovery(ledger),
        )
        return operation["outcome"]

    intent = operation["outcome"]
    if operation["epoch"] != lease.epoch:
        raise FinalMergeError("final merge intent belongs to a stale writer epoch")
    try:
        merge_head = _observe_or_create_final_merge(ledger.repo_root, intent)
    except (FinalMergeError, GitStateError) as exc:
        _invalidate_final_gate(
            ledger, lease, request_id, "Git drift during final merge"
        )
        if isinstance(exc, FinalMergeError):
            raise
        raise FinalMergeError("final merge Git observation failed") from exc
    merge_tree = _git_text(ledger.repo_root, "rev-parse", f"{merge_head}^{{tree}}")
    observed = {
        **intent,
        "merge_head": merge_head,
        "merge_tree_id": merge_tree,
        "parents": _merge_parents(ledger.repo_root, merge_head),
        "status": "merge_observed",
    }
    if operation["phase"] == "prepared":
        operation = ledger.advance_operation(
            lease,
            operation_id=operation_id,
            expected_phase="prepared",
            phase="effect_observed",
            output_fingerprint=merge_head,
            outcome=observed,
        )
    elif operation["phase"] != "effect_observed":
        raise FinalMergeError("final merge operation has an invalid durable phase")
    else:
        if operation["outcome"] != observed:
            raise FinalMergeError("observed final merge differs from durable evidence")
    checks = [
        _run_parent_check(ledger.repo_root, item)
        for item in intent["post_merge_checks"]
    ]
    head_after_checks = _git_text(ledger.repo_root, "rev-parse", "HEAD")
    tree_after_checks = _git_text(ledger.repo_root, "rev-parse", "HEAD^{tree}")
    dirt_after_checks = scan_repository_dirt(ledger.repo_root)
    passed = (
        all(item["status"] == "passed" for item in checks)
        and head_after_checks == merge_head
        and tree_after_checks == merge_tree
        and not dirt_after_checks["paths"]
    )
    verification = _record_post_merge_verification(
        ledger,
        lease,
        request_id=request_id,
        merge_head=merge_head,
        checks=checks,
        passed=passed,
        dirt=dirt_after_checks,
    )
    if not passed:
        ledger.rebuild_projection(lease)
        raise FinalMergeError(
            "final merge is preserved but post-merge verification did not pass"
        )
    outcome = {
        **observed,
        "checks": checks,
        "effect_boundary_clean": True,
        "status": "final_merged",
        "verification_id": verification["verification_id"],
    }
    _commit_final_merge_authority(ledger, lease, operation_id, outcome)
    ledger.rebuild_projection(lease)
    return outcome


def recover_final_merge(
    ledger: ParentLedger,
    lease: WriterLease,
    operation: Mapping[str, object],
    *,
    expected_qualification_rotation_proof: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Acknowledge one retained exact final merge under a rotated writer fence."""
    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime(connection, ledger.run_id, require_authorized=False)
    finally:
        connection.close()
    retained = _final_merge_recovery_state(
        ledger.repo_root,
        operation,
        runtime["envelope"]["allowed_touches"],
        execution_binding=_execution_binding_for_recovery(ledger),
    )
    proof = retained["qualification_rotation_proof"]
    if proof != (
        dict(expected_qualification_rotation_proof)
        if expected_qualification_rotation_proof is not None
        else None
    ):
        raise FinalMergeError(
            "qualification rotation proof changed after resume safe-point"
        )
    source = operation["outcome"]
    merge_head = retained["merge_head"]
    publication_head = retained["publication_head"]
    retained_main_branch = retained["retained_main_branch"]
    retained_main_head = retained["retained_main_head"]
    retained_main_tree = retained["retained_main_tree"]
    retained_dirt_digest = retained["retained_dirt_digest"]

    checks = [
        _run_parent_check(ledger.repo_root, item)
        for item in source["post_merge_checks"]
    ]
    head_after_checks = _git_text(ledger.repo_root, "rev-parse", "HEAD")
    tree_after_checks = _git_text(ledger.repo_root, "rev-parse", "HEAD^{tree}")
    branch_after_checks = _current_branch(ledger.repo_root)
    dirt_after_checks = scan_repository_dirt(ledger.repo_root)
    try:
        retained_after_checks = _final_merge_recovery_state(
            ledger.repo_root,
            operation,
            runtime["envelope"]["allowed_touches"],
            execution_binding=_execution_binding_for_recovery(ledger),
        )
    except (FinalMergeError, GitStateError):
        retained_after_checks = None
    passed = (
        all(item["status"] == "passed" for item in checks)
        and head_after_checks == retained_main_head
        and tree_after_checks == retained_main_tree
        and branch_after_checks == retained_main_branch
        and dirt_after_checks["digest"] == retained_dirt_digest
        and retained_after_checks == retained
    )
    verification = _record_post_merge_verification(
        ledger,
        lease,
        request_id=str(source["request_id"]),
        merge_head=retained_main_head,
        checks=checks,
        passed=passed,
        dirt=dirt_after_checks,
    )
    if not passed:
        raise FinalMergeError(
            "retained final merge post-merge verification did not pass"
        )
    outcome = {
        **source,
        "checks": checks,
        "descendant_drift_digest": retained["descendant_drift_digest"],
        "descendant_drift_paths": retained["descendant_drift_paths"],
        "effect_boundary_clean": True,
        "merge_head": merge_head,
        "merge_tree_id": str(source["integration_tree_id"]),
        "parents": _merge_parents(ledger.repo_root, merge_head),
        "publication_head": publication_head,
        "qualification_rotation_proof": proof,
        "qualification_rotation_proof_digest": (
            proof["proof_digest"] if isinstance(proof, Mapping) else None
        ),
        "recovered": True,
        "recovered_from_epoch": operation["epoch"],
        "retained_dirt_digest": retained_dirt_digest,
        "retained_main_branch": retained_main_branch,
        "retained_main_head": retained_main_head,
        "status": "final_merged",
        "verification_id": verification["verification_id"],
    }
    _commit_final_merge_authority(
        ledger,
        lease,
        str(operation["operation_id"]),
        outcome,
        recovered_from_epoch=int(operation["epoch"]),
    )
    return {
        "operation_id": operation["operation_id"],
        "result": "final_merged",
    }


def final_merge_recovery_eligible(
    repo: Path,
    operation: Mapping[str, object],
    allowed_touches: Sequence[str],
    *,
    execution_binding: Mapping[str, object] | None = None,
) -> bool:
    """Return whether current Git facts satisfy final-merge recovery preflight."""
    try:
        _final_merge_recovery_state(
            repo,
            operation,
            allowed_touches,
            execution_binding=execution_binding,
        )
    except (FinalMergeError, GitStateError):
        return False
    return True


def _final_merge_recovery_state(
    repo: Path,
    operation: Mapping[str, object],
    allowed_touches: Sequence[str],
    *,
    execution_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if operation["kind"] != _FINAL_MERGE_KIND or operation["phase"] not in {
        "prepared",
        "effect_observed",
    }:
        raise FinalMergeError("final merge operation is not recoverable")
    source = operation["outcome"]
    required = {
        "base_branch",
        "base_head",
        "integration_head",
        "integration_tree_id",
        "operation_id",
        "post_merge_checks",
        "request_id",
    }
    if not required.issubset(source) or source["operation_id"] != operation[
        "operation_id"
    ]:
        raise FinalMergeError("final merge recovery intent is incomplete")
    retained_main_branch = _current_branch(repo)
    if retained_main_branch == "DETACHED":
        raise FinalMergeError("canonical worktree is detached")
    dirt = scan_repository_dirt(repo)
    if any(
        _path_matches(path, pattern)
        for path in dirt["paths"]
        for pattern in allowed_touches
    ):
        raise FinalMergeError("canonical dirt overlaps final merge scope")

    if operation["phase"] == "effect_observed":
        merge_head = _required_text(source.get("merge_head"), "merge_head")
        _assert_final_merge_shape(repo, merge_head, source)
    else:
        candidates = []
        history = _git_text(repo, "rev-list", "--merges", "HEAD")
        for commit in history.splitlines():
            if (
                _merge_parents(repo, commit)
                == [source["base_head"], source["integration_head"]]
                and _git_text(repo, "rev-parse", f"{commit}^{{tree}}")
                == source["integration_tree_id"]
            ):
                candidates.append(commit)
        if len(candidates) != 1:
            raise FinalMergeError(
                "final merge recovery requires one retained exact merge"
            )
        merge_head = candidates[0]

    retained_main_head = _git_text(repo, "rev-parse", "HEAD")
    retained_main_tree = _git_text(repo, "rev-parse", "HEAD^{tree}")
    publication_head = merge_head
    if retained_main_branch != source["base_branch"]:
        publications = []
        history = _git_text(repo, "rev-list", "--merges", "HEAD")
        for commit in history.splitlines():
            if (
                merge_head in _merge_parents(repo, commit)
                and _git_text(repo, "rev-parse", f"{commit}^{{tree}}")
                == source["integration_tree_id"]
            ):
                publications.append(commit)
        if len(publications) != 1:
            raise FinalMergeError(
                "canonical branch change lacks one exact publication wrapper"
            )
        publication_head = publications[0]
    if (
        not _is_ancestor(repo, merge_head, publication_head)
        or not _is_ancestor(repo, publication_head, retained_main_head)
    ):
        raise FinalMergeError("current main does not retain final merge publication")
    drift_paths = sorted(
        path
        for path in _git_text(
            repo,
            "diff",
            "--name-only",
            f"{publication_head}..{retained_main_head}",
            "--",
        ).splitlines()
        if path
    )
    overlapping_paths = sorted(
        path
        for path in drift_paths
        if any(_path_matches(path, pattern) for pattern in allowed_touches)
    )
    qualification_rotation_proof = None
    if overlapping_paths:
        try:
            qualification_rotation_proof = _qualification_rotation_proof(
                repo,
                publication_head=publication_head,
                retained_main_head=retained_main_head,
                execution_binding=execution_binding,
                allowed_touches=allowed_touches,
            )
        except FinalMergeError as exc:
            raise FinalMergeError(
                f"later canonical drift overlaps final merge scope: {exc}"
            ) from None
        if overlapping_paths != qualification_rotation_proof[
            "qualification_rotation_paths"
        ]:
            raise FinalMergeError(
                "later canonical drift overlaps final merge scope"
            )
    return {
        "descendant_drift_digest": _digest_json(drift_paths),
        "descendant_drift_paths": drift_paths,
        "merge_head": merge_head,
        "non_overlapping_descendant_paths": sorted(
            set(drift_paths) - set(overlapping_paths)
        ),
        "publication_head": publication_head,
        "qualification_rotation_paths": (
            qualification_rotation_proof["qualification_rotation_paths"]
            if qualification_rotation_proof is not None
            else []
        ),
        "qualification_rotation_proof": qualification_rotation_proof,
        "qualification_rotation_proof_digest": (
            qualification_rotation_proof["proof_digest"]
            if qualification_rotation_proof is not None
            else None
        ),
        "rejected_overlapping_paths": [],
        "retained_dirt_digest": dirt["digest"],
        "retained_main_branch": retained_main_branch,
        "retained_main_head": retained_main_head,
        "retained_main_tree": retained_main_tree,
    }


def _qualification_rotation_proof(
    repo: Path,
    *,
    publication_head: str,
    retained_main_head: str,
    execution_binding: Mapping[str, object] | None,
    allowed_touches: Sequence[str],
) -> dict[str, object]:
    from .qualification import (
        SOURCE_DEVELOPMENT_PURPOSE,
        SOURCE_RELEASE_PURPOSE,
        configured_qualification,
        execution_qualification,
        read_qualification_receipt,
    )

    if not isinstance(execution_binding, Mapping):
        raise FinalMergeError("qualification rotation lacks execution binding")
    execution = execution_qualification(repo, execution_binding)
    execution_receipt_id = execution_binding.get("receipt_id")
    if (
        not execution.enforced
        or not execution.valid
        or not isinstance(execution_receipt_id, str)
        or execution.receipt_id != execution_receipt_id
    ):
        raise FinalMergeError("historical execution qualification is invalid")

    current_config = _git(repo, "show", f"{retained_main_head}:{_QUALIFICATION_CONFIG_PATH}")
    publication_config = _git(
        repo,
        "show",
        f"{publication_head}:{_QUALIFICATION_CONFIG_PATH}",
    )
    if (
        _worktree_file_bytes(
            repo,
            _QUALIFICATION_CONFIG_PATH,
            "qualification config",
        )
        != current_config
    ):
        raise FinalMergeError("qualification config differs from retained Git bytes")
    publication_path, publication_digest = _qualification_config_values(
        publication_config
    )
    current_path, current_digest = _qualification_config_values(current_config)
    if (
        publication_path == current_path
        or publication_digest == current_digest
        or current_path
        != f"{_QUALIFICATION_RECEIPT_DIR}/{current_digest}.json"
    ):
        raise FinalMergeError("qualification rotation identity is not exact")

    commits = [
        item
        for item in _git_text(
            repo,
            "rev-list",
            "--reverse",
            f"{publication_head}..{retained_main_head}",
            "--",
            _QUALIFICATION_CONFIG_PATH,
            _QUALIFICATION_RECEIPT_DIR,
        ).splitlines()
        if item
    ]
    if not commits:
        raise FinalMergeError("qualification rotation requires a binding chain")
    previous_config = publication_config
    previous_path = publication_path
    previous_digest = publication_digest
    chain = []
    for binding_commit in commits:
        ancestry = _git_text(
            repo,
            "rev-list",
            "--parents",
            "-n",
            "1",
            binding_commit,
        ).split()
        if (
            len(ancestry) != 2
            or not _is_ancestor(repo, publication_head, binding_commit)
            or not _is_ancestor(repo, binding_commit, retained_main_head)
        ):
            raise FinalMergeError("qualification binding ancestry is invalid")
        qualified_runtime_commit = ancestry[1]
        parent_config = _git(
            repo,
            "show",
            f"{qualified_runtime_commit}:{_QUALIFICATION_CONFIG_PATH}",
        )
        binding_config = _git(
            repo,
            "show",
            f"{binding_commit}:{_QUALIFICATION_CONFIG_PATH}",
        )
        if (
            parent_config != previous_config
            or _normalized_qualification_config(parent_config)
            != _normalized_qualification_config(binding_config)
        ):
            raise FinalMergeError(
                "qualification config changed outside an exact binding link"
            )
        from_path, from_digest = _qualification_config_values(parent_config)
        to_path, to_digest = _qualification_config_values(binding_config)
        if (
            (from_path, from_digest) != (previous_path, previous_digest)
            or from_path == to_path
            or from_digest == to_digest
        ):
            raise FinalMergeError("qualification rotation chain is discontinuous")
        changes = [
            tuple(line.split("\t", 1))
            for line in _git_text(
                repo,
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                binding_commit,
            ).splitlines()
            if line
        ]
        expected_changes = [
            ("M", _QUALIFICATION_CONFIG_PATH),
            ("A", to_path),
        ]
        if any(expected not in changes for expected in expected_changes):
            raise FinalMergeError(
                "qualification binding commit lacks its exact config/receipt link"
            )
        attached_changes = sorted(
            (status, path)
            for status, path in changes
            if (status, path) not in expected_changes
        )
        if any(
            status not in {"A", "D", "M"}
            or "\t" in path
            or path == _QUALIFICATION_CONFIG_PATH
            or path.startswith(f"{_QUALIFICATION_RECEIPT_DIR}/")
            or any(_path_matches(path, pattern) for pattern in allowed_touches)
            for status, path in attached_changes
        ):
            raise FinalMergeError(
                "qualification binding commit has unproved attached paths"
            )
        receipt_bytes = _git(repo, "show", f"{binding_commit}:{to_path}")
        if (
            _git(repo, "show", f"{retained_main_head}:{to_path}")
            != receipt_bytes
            or _worktree_file_bytes(
                repo,
                to_path,
                "qualification chain receipt",
            )
            != receipt_bytes
        ):
            raise FinalMergeError("qualification chain receipt changed")
        try:
            receipt = json.loads(receipt_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinalMergeError(
                "qualification chain receipt is malformed"
            ) from exc
        runtime = receipt.get("runtime") if isinstance(receipt, dict) else None
        if (
            not isinstance(receipt, dict)
            or receipt.get("receipt_digest") != to_digest
            or receipt.get("receipt_id") != f"sha256:{to_digest}"
            or not isinstance(runtime, dict)
            or runtime.get("git_commit") != qualified_runtime_commit
        ):
            raise FinalMergeError("qualification chain receipt identity is invalid")
        _verify_qualification_receipt_at_commit(
            repo,
            binding_commit=binding_commit,
            receipt_path=to_path,
            receipt_digest=to_digest,
        )
        chain.append(
            {
                "binding_commit": binding_commit,
                "attached_non_overlapping_changes": [
                    {"path": path, "status": status}
                    for status, path in attached_changes
                ],
                "config_blob_oid": _git_text(
                    repo,
                    "rev-parse",
                    f"{binding_commit}:{_QUALIFICATION_CONFIG_PATH}",
                ),
                "from_receipt_path": from_path,
                "from_receipt_id": f"sha256:{from_digest}",
                "qualified_runtime_commit": qualified_runtime_commit,
                "receipt_path": to_path,
                "receipt_blob_oid": _git_text(
                    repo,
                    "rev-parse",
                    f"{binding_commit}:{to_path}",
                ),
                "to_receipt_id": f"sha256:{to_digest}",
            }
        )
        previous_config = binding_config
        previous_path = to_path
        previous_digest = to_digest
    if (
        previous_config != current_config
        or (previous_path, previous_digest) != (current_path, current_digest)
    ):
        raise FinalMergeError(
            "qualification rotation chain does not reach current config"
        )

    publication_receipt = _git(
        repo,
        "show",
        f"{publication_head}:{publication_path}",
    )
    if (
        _git(repo, "show", f"{retained_main_head}:{publication_path}")
        != publication_receipt
        or _worktree_file_bytes(
            repo,
            publication_path,
            "publication qualification receipt",
        )
        != publication_receipt
    ):
        raise FinalMergeError("historical qualification receipt changed")
    publication = read_qualification_receipt(repo / publication_path)
    if (
        publication.get("readable") is not True
        or publication.get("receipt_id") != f"sha256:{publication_digest}"
    ):
        raise FinalMergeError("publication qualification receipt is invalid")

    purposes = [
        configured_qualification(repo, purpose=purpose)
        for purpose in (SOURCE_DEVELOPMENT_PURPOSE, SOURCE_RELEASE_PURPOSE)
    ]
    if any(
        not status.valid or status.receipt_id != f"sha256:{current_digest}"
        for status in purposes
    ):
        raise FinalMergeError("configured source qualifications disagree")

    paths = [
        _QUALIFICATION_CONFIG_PATH,
        *(str(item["receipt_path"]) for item in chain),
    ]
    terminal = chain[-1]
    proof = {
        "allowed_touches": sorted(set(allowed_touches)),
        "binding_commit": terminal["binding_commit"],
        "binding_commits": [item["binding_commit"] for item in chain],
        "config_blob_oid": terminal["config_blob_oid"],
        "contract_version": "qualification_rotation_chain_v2",
        "current_config_receipt_id": f"sha256:{current_digest}",
        "execution_receipt_id": execution_receipt_id,
        "kind": _QUALIFICATION_ROTATION_KIND,
        "mixed_commit_non_overlapping_paths": sorted(
            {
                str(change["path"])
                for item in chain
                for change in item["attached_non_overlapping_changes"]
            }
        ),
        "non_overlapping_descendant_paths": sorted(
            set(
                _git_text(
                    repo,
                    "diff",
                    "--name-only",
                    f"{publication_head}..{retained_main_head}",
                    "--",
                ).splitlines()
            )
            - set(paths)
        ),
        "publication_config_receipt_id": f"sha256:{publication_digest}",
        "publication_head": publication_head,
        "qualification_rotation_chain": chain,
        "qualification_rotation_paths": sorted(paths),
        "qualified_runtime_commit": terminal["qualified_runtime_commit"],
        "receipt_blob_oid": terminal["receipt_blob_oid"],
        "rejected_overlapping_paths": [],
        "retained_main_head": retained_main_head,
    }
    return {**proof, "proof_digest": f"sha256:{_digest_json(proof)}"}


def _verify_qualification_receipt_at_commit(
    repo: Path,
    *,
    binding_commit: str,
    receipt_path: str,
    receipt_digest: str,
) -> None:
    from .qualification import QualificationError, verify_conformance_receipt

    try:
        with TemporaryDirectory(prefix="loop-v1-qualification-chain-") as tmp:
            checkout = Path(tmp) / "runtime"
            _git(
                repo,
                "clone",
                "--no-local",
                "--quiet",
                "--no-checkout",
                str(repo),
                str(checkout),
            )
            _git(
                checkout,
                "checkout",
                "--quiet",
                "--detach",
                binding_commit,
            )
            verification = verify_conformance_receipt(
                checkout,
                Path(receipt_path),
                expected_digest=receipt_digest,
                installed_root=repo,
            )
    except (OSError, GitStateError, QualificationError) as exc:
        raise FinalMergeError(
            "qualification chain strict verification could not run"
        ) from exc
    if (
        not verification["valid"]
        or verification["receipt_id"] != f"sha256:{receipt_digest}"
    ):
        raise FinalMergeError("qualification chain strict receipt is invalid")


def _worktree_file_bytes(repo: Path, relative: str, label: str) -> bytes:
    try:
        return (repo / relative).read_bytes()
    except OSError as exc:
        raise FinalMergeError(f"{label} is unreadable") from exc


def _qualification_config_values(config_bytes: bytes) -> tuple[str, str]:
    try:
        config = parse_simple_yaml(config_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FinalMergeError("qualification config is not UTF-8") from exc
    loop = config.get("loop_v1")
    if not isinstance(loop, dict):
        raise FinalMergeError("qualification config lacks loop_v1")
    path = str(loop.get("qualification_receipt", "")).strip()
    digest = str(loop.get("qualification_receipt_digest", "")).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise FinalMergeError("qualification config digest is invalid")
    if path != f"{_QUALIFICATION_RECEIPT_DIR}/{digest}.json":
        raise FinalMergeError("qualification config receipt path is invalid")
    return path, digest


def _normalized_qualification_config(config_bytes: bytes) -> bytes:
    try:
        lines = config_bytes.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise FinalMergeError("qualification config is not UTF-8") from exc
    found: dict[str, int] = {}
    normalized = []
    pattern = re.compile(
        r"^(?P<indent> +)(?P<key>qualification_receipt"
        r"|qualification_receipt_digest): (?P<value>[^ #\r\n]+)(?P<end>\r?\n)?$"
    )
    for line in lines:
        match = pattern.fullmatch(line)
        if match is None:
            normalized.append(line)
            continue
        key = match.group("key")
        if key in found:
            raise FinalMergeError("qualification config binding key is duplicated")
        found[key] = len(normalized)
        normalized.append(
            f"{match.group('indent')}{key}: <binding>{match.group('end') or ''}"
        )
    if set(found) != set(_QUALIFICATION_CONFIG_KEYS):
        raise FinalMergeError("qualification config binding lines are not canonical")
    _qualification_config_values(config_bytes)
    return "".join(normalized).encode("utf-8")


def _execution_binding_for_recovery(
    ledger: ParentLedger,
) -> Mapping[str, object] | None:
    connection = ledger._connect(read_only=True)
    try:
        parent = ledger._parent_row(connection)
        if parent["status"] == "revoked":
            raise FinalMergeError("historical execution binding was revoked")
        binding = ledger._execution_binding(connection, parent)
        envelope = connection.execute(
            "SELECT contract_json FROM envelope_revisions WHERE revision_id = ?",
            (parent["start_gate_ref"],),
        ).fetchone()
        try:
            historical_receipt = json.loads(envelope["contract_json"])[
                "conformance_receipt"
            ]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise FinalMergeError(
                "historical execution qualification envelope is invalid"
            ) from exc
        if (
            not isinstance(binding, Mapping)
            or (
                binding.get("mode") != "unenforced"
                and binding.get("receipt_id") != historical_receipt
            )
        ):
            raise FinalMergeError("historical execution binding changed")
        return binding
    finally:
        connection.close()


def _assert_qualification_rotation_proof(
    repo: Path,
    proof: Mapping[str, object],
    *,
    execution_binding: Mapping[str, object] | None,
) -> None:
    allowed_touches = proof.get("allowed_touches")
    if not isinstance(allowed_touches, list) or any(
        not isinstance(pattern, str) or not pattern for pattern in allowed_touches
    ):
        raise FinalMergeError("stored qualification rotation touches are invalid")
    current = _qualification_rotation_proof(
        repo,
        publication_head=_required_text(
            proof.get("publication_head"), "publication_head"
        ),
        retained_main_head=_required_text(
            proof.get("retained_main_head"), "retained_main_head"
        ),
        execution_binding=execution_binding,
        allowed_touches=allowed_touches,
    )
    if current != dict(proof):
        raise FinalMergeError("stored qualification rotation proof changed")


def archive_parent_run(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
) -> dict[str, object]:
    """Archive Loop parent authority only after exact verified final merge."""
    request_id = _required_text(request_id, "request_id")
    operation_id = f"parent-archive:{request_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        if existing["kind"] != _PARENT_ARCHIVE_KIND:
            raise OperationConflict("parent archive ID has another kind")
        merge = ledger.get_operation(f"final-merge:{request_id}")
        if merge is None or merge["kind"] != _FINAL_MERGE_KIND:
            raise FinalGateError("parent archive replay requires a final merge")
        _assert_final_merge_git(
            ledger.repo_root,
            merge["outcome"],
            execution_binding=_execution_binding_for_recovery(ledger),
        )
        return existing["outcome"]
    request = _approved_final_request(ledger, request_id)
    merge = ledger.get_operation(f"final-merge:{request_id}")
    if merge is None or merge["kind"] != _FINAL_MERGE_KIND:
        raise FinalGateError("parent archive requires a final merge")
    if (
        merge["phase"] != "authority_committed"
        or merge["outcome"].get("status") != "final_merged"
    ):
        raise FinalGateError("parent archive requires verified final merge authority")
    _assert_final_merge_git(
        ledger.repo_root,
        merge["outcome"],
        execution_binding=_execution_binding_for_recovery(ledger),
    )
    supplied = {
        "merge_head": merge["outcome"]["merge_head"],
        "request_digest": request["request_digest"],
        "request_id": request_id,
        "verification_id": merge["outcome"]["verification_id"],
    }
    outcome = {**supplied, "preserved": True, "status": "archived"}
    with ledger._write_transaction(lease) as connection:
        runtime = _runtime(connection, ledger.run_id, require_authorized=True)
        if runtime["parent"]["final_gate_ref"] != request_id:
            raise FinalGateError("parent does not reference the approved final gate")
        verification = connection.execute(
            "SELECT verdict FROM verifications WHERE verification_id = ?",
            (supplied["verification_id"],),
        ).fetchone()
        if verification is None or verification["verdict"] != "passed":
            raise FinalGateError("parent archive requires passing merge verification")
        unresolved = connection.execute(
            "SELECT operation_id FROM operations "
            "WHERE phase IN ('prepared', 'effect_observed') LIMIT 1"
        ).fetchone()
        if unresolved is not None:
            raise FinalGateError("parent archive is blocked by unresolved operations")
        now = _now()
        connection.execute(
            "UPDATE parent_runs SET status = 'archived', updated_at = ? WHERE run_id = ?",
            (now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind=_PARENT_ARCHIVE_KIND,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="parent_archived",
            created_at=now,
        )
    ledger.rebuild_projection(lease)
    return outcome


def _runtime(
    connection: sqlite3.Connection, run_id: str, *, require_authorized: bool
) -> dict[str, Any]:
    parent = connection.execute(
        "SELECT * FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if parent is None or parent["start_gate_ref"] is None:
        raise FreshnessError("acceptance requires an approved start gate")
    if require_authorized and parent["status"] != "authorized":
        raise FreshnessError(
            f"acceptance is blocked by parent status: {parent['status']}"
        )
    envelope, envelope_digest = _approved_envelope(connection, parent["start_gate_ref"])
    context_row = _latest_context_row(connection)
    if context_row is None:
        raise FreshnessError("acceptance requires canonical context")
    if context_row["envelope_digest"] != envelope_digest:
        raise FreshnessError("canonical context differs from the start envelope")
    return {
        "context": json.loads(context_row["context_json"]),
        "context_row": dict(context_row),
        "envelope": envelope,
        "parent": dict(parent),
    }


def _integration_ref(value: object) -> str:
    ref = _required_text(value, "integration_ref")
    if not ref.startswith("refs/heads/") or ".." in ref:
        raise AcceptanceError("integration_ref must be a full local branch ref")
    return ref


def _integration_state(
    ledger: ParentLedger, runtime: Mapping[str, object], integration_ref: str
) -> dict[str, object]:
    context = runtime["context"]["integration"]
    envelope = runtime["envelope"]
    base_head = _git_text(
        ledger.repo_root, "rev-parse", "--verify", envelope["base_head"]
    )
    base_tree = _git_text(ledger.repo_root, "rev-parse", f"{base_head}^{{tree}}")
    integration_head = _git_text(
        ledger.repo_root, "rev-parse", "--verify", integration_ref
    )
    integration_tree = _git_text(
        ledger.repo_root, "rev-parse", f"{integration_head}^{{tree}}"
    )
    if base_head != context["base_head"] or base_tree != context["base_tree_id"]:
        raise FreshnessError("base Git identity differs from canonical context")
    if (
        integration_head != context["integration_head"]
        or integration_tree != context["integration_tree_id"]
    ):
        raise FreshnessError("integration ref differs from canonical context")
    ancestry = _is_ancestor(ledger.repo_root, base_head, integration_head)
    if not ancestry:
        raise FreshnessError("integration HEAD does not descend from approved base")
    return {
        "ancestry": {
            "base_is_ancestor": ancestry,
            "digest": _digest_json([base_head, integration_head, integration_tree]),
        },
        "base_branch": context["base_branch"],
        "base_head": base_head,
        "base_tree_id": base_tree,
        "integration_head": integration_head,
        "integration_ref": integration_ref,
        "integration_tree_id": integration_tree,
    }


def _applicable_requirements(context: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        {
            "acceptance": item["acceptance"],
            "coverage_state": item["coverage_state"],
            "requirement_id": item["requirement_id"],
            "required": item["required"],
            "revision": item["revision"],
        }
        for item in sorted(
            context["requirements"], key=lambda row: row["requirement_id"]
        )
    ]


def _integrated_touches(connection: sqlite3.Connection) -> list[str]:
    touches: set[str] = set()
    rows = connection.execute(
        "SELECT outcome_json FROM operations "
        "WHERE kind = 'child_commit' AND phase = 'authority_committed'"
    ).fetchall()
    for row in rows:
        outcome = json.loads(row["outcome_json"])
        touches.update(
            outcome.get("validated_paths", outcome.get("actual_touches", []))
        )
    return sorted(touches)


def _specialist_triggers(
    runtime: Mapping[str, object], touches: Sequence[str]
) -> dict[str, list[str]]:
    source = [
        *runtime["envelope"]["risks"],
        *runtime["envelope"]["allowed_scope"],
        *touches,
    ]
    result: dict[str, list[str]] = {}
    for specialist, keywords in sorted(_SPECIALIST_TRIGGERS.items()):
        matches = sorted(
            {
                item
                for item in source
                if any(word in str(item).casefold() for word in keywords)
            }
        )
        if matches:
            result[specialist] = matches
    return result


def _specialist_matrix(
    supplied: Sequence[Mapping[str, object]], triggers: Mapping[str, Sequence[str]]
) -> list[dict[str, object]]:
    rows: dict[str, dict[str, str]] = {}
    for raw in supplied:
        item = _exact_text_mapping(raw, _SPECIALIST_FIELDS, "specialist result")
        specialist = item["specialist"]
        if specialist in rows:
            raise FinalReviewError(f"duplicate specialist result: {specialist}")
        if specialist not in _SPECIALIST_TRIGGERS:
            raise FinalReviewError(f"unknown specialist result: {specialist}")
        if item["status"] not in {"passed", "failed"}:
            raise FinalReviewError("specialist status must be passed or failed")
        rows[specialist] = item
    extra = sorted(set(rows) - set(triggers))
    if extra:
        raise FinalReviewError(
            f"specialist review ran without a declared trigger: {', '.join(extra)}"
        )
    missing = sorted(set(triggers) - set(rows))
    if missing:
        raise FinalReviewError(
            f"triggered specialist review is missing: {', '.join(missing)}"
        )
    return [
        {
            "applicable": specialist in triggers,
            "evidence_digest": (
                rows[specialist]["evidence_digest"]
                if specialist in rows
                else "not_applicable"
            ),
            "reasons": list(triggers.get(specialist, [])),
            "specialist": specialist,
            "status": rows[specialist]["status"]
            if specialist in rows
            else "not_applicable",
        }
        for specialist in sorted(_SPECIALIST_TRIGGERS)
    ]


def _exact_text_mapping(
    value: Mapping[str, object], fields: frozenset[str], label: str
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AcceptanceError(f"{label} fields are invalid")
    return {
        field: _required_text(value[field], f"{label}.{field}")
        for field in sorted(fields)
    }


def _effect_proofs(
    values: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    rows = [
        _exact_text_mapping(item, _EFFECT_FIELDS, "effect proof") for item in values
    ]
    if len({item["effect_id"] for item in rows}) != len(rows):
        raise ReadinessError("effect proof IDs must be unique")
    for item in rows:
        expected = {
            "local_declared": "passed",
            "prohibited_external": "not_performed",
        }.get(item["classification"])
        if expected is None or item["status"] != expected:
            raise ReadinessError("effect proof classification/status is invalid")
    if {item["classification"] for item in rows} != {
        "local_declared",
        "prohibited_external",
    }:
        raise ReadinessError(
            "effect proofs require local and prohibited-external evidence"
        )
    return sorted(rows, key=lambda item: item["effect_id"])


def _skipped_checks(
    values: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    rows = [
        _exact_text_mapping(item, _SKIPPED_FIELDS, "skipped check") for item in values
    ]
    if len({item["check_id"] for item in rows}) != len(rows):
        raise ReadinessError("skipped check IDs must be unique")
    if any(item["status"] != "not_applicable" for item in rows):
        raise ReadinessError("skipped checks must be explicitly not_applicable")
    return sorted(rows, key=lambda item: item["check_id"])


def _snapshot_position(snapshot: Mapping[str, object]) -> int:
    events = snapshot["tables"]["ledger_events"]
    return max((int(item["position"]) for item in events), default=0)


def _predicted_request_id(
    run_id: str, pack_id: str, base_head: str, integration_head: str, position: int
) -> str:
    digest = _digest_json(
        {
            "base_head": base_head,
            "integration_head": integration_head,
            "pack_id": pack_id,
            "run_id": run_id,
            "source_position": position,
        }
    )
    return f"final-{digest[:24]}"


def _latest_final_review(connection: sqlite3.Connection) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT o.outcome_json FROM operations o
        JOIN ledger_events e ON e.operation_id = o.operation_id
        WHERE o.kind = ? AND o.phase = 'authority_committed'
        ORDER BY e.position DESC LIMIT 1
        """,
        (_FINAL_REVIEW_KIND,),
    ).fetchone()
    return json.loads(row["outcome_json"]) if row is not None else None


def _zero_diff_integration_evidence(
    connection: sqlite3.Connection, child: sqlite3.Row
) -> dict[str, object] | None:
    rows = connection.execute(
        "SELECT * FROM operations WHERE kind = ? "
        "AND json_extract(outcome_json, '$.child_id') = ? "
        "ORDER BY created_at, operation_id",
        (_ZERO_DIFF_INTEGRATION_KIND, child["child_id"]),
    ).fetchall()
    if not rows:
        return None
    evidence: dict[str, object] = {
        "operation_ids": [row["operation_id"] for row in rows],
        "passed": False,
    }
    if len(rows) != 1:
        return evidence
    row = rows[0]
    outcome = json.loads(row["outcome_json"])
    review_row = connection.execute(
        "SELECT outcome_json FROM operations WHERE operation_id = ?",
        (f"precommit-review:{outcome.get('review_id')}",),
    ).fetchone()
    validation_row = connection.execute(
        "SELECT outcome_json FROM operations WHERE operation_id = ?",
        (f"candidate-validation:{outcome.get('validation_id')}",),
    ).fetchone()
    guidance_row = connection.execute(
        "SELECT outcome_json FROM operations WHERE operation_id = ? "
        "AND kind = 'final_check_recovery_guidance' "
        "AND phase = 'authority_committed'",
        (outcome.get("recovery_operation_id"),),
    ).fetchone()
    final_rows = connection.execute(
        "SELECT outcome_json FROM operations "
        "WHERE kind = 'final_integration_checks' "
        "AND phase = 'authority_committed' ORDER BY created_at, operation_id"
    ).fetchall()
    current = _latest_context_row(connection)
    if (
        review_row is None
        or validation_row is None
        or guidance_row is None
        or not final_rows
        or current is None
    ):
        return evidence
    review = json.loads(review_row["outcome_json"])
    validation = json.loads(validation_row["outcome_json"])
    guidance = json.loads(guidance_row["outcome_json"])
    final_checks = json.loads(final_rows[-1]["outcome_json"])
    context = json.loads(current["context_json"])
    coverage = sorted(json.loads(child["coverage_json"]))
    recovery_child_id = str(outcome.get("recovery_child_id", ""))
    problem_failures = [
        json.loads(item["outcome_json"])
        for item in connection.execute(
            "SELECT outcome_json FROM operations "
            "WHERE kind = 'problem_attempt' AND phase = 'authority_committed' "
            "ORDER BY created_at, operation_id"
        ).fetchall()
        if json.loads(item["outcome_json"]).get("problem_id")
        == outcome.get("problem_id")
        and json.loads(item["outcome_json"]).get("result") == "failed"
    ]
    failed_row = connection.execute(
        "SELECT outcome_json FROM operations WHERE operation_id = ? "
        "AND kind = 'final_integration_checks' "
        "AND phase = 'authority_committed'",
        (guidance.get("failed_operation_id"),),
    ).fetchone()
    failed = json.loads(failed_row["outcome_json"]) if failed_row else None
    commit = connection.execute(
        "SELECT 1 FROM git_operations WHERE git_operation_id = ?",
        (f"child-commit:{child['child_id']}",),
    ).fetchone()
    integration = connection.execute(
        "SELECT 1 FROM git_operations WHERE phase = 'integrated' "
        "AND json_extract(outcome_json, '$.child_id') = ?",
        (child["child_id"],),
    ).fetchone()
    evidence.update(
        {
            "operation_id": row["operation_id"],
            "passed": bool(
                row["phase"] == "authority_committed"
                and child["state"] == "integrated"
                and int(row["epoch"]) == int(child["epoch"])
                and outcome.get("status") == "integrated"
                and outcome.get("disposition") == "reviewed_zero_diff"
                and outcome.get("git_effect") == "none"
                and outcome.get("child_commit_id") is None
                and sorted(outcome.get("coverage", [])) == coverage
                and review.get("verdict") == "passed"
                and not review.get("required_findings")
                and review.get("actual_touches") == []
                and review.get("diff_identity") == _EMPTY_DIFF_IDENTITY
                and review.get("tree_id") == outcome.get("candidate_tree_id")
                and validation.get("actual_touches") == []
                and validation.get("untracked_files") == []
                and validation.get("diff_identity") == _EMPTY_DIFF_IDENTITY
                and validation.get("tree_id") == outcome.get("candidate_tree_id")
                and validation.get("parent_checks")
                and all(
                    item.get("status") == "passed"
                    for item in validation["parent_checks"]
                )
                and guidance.get("direct_user_action") is True
                and guidance.get("affected_child_ids") == [recovery_child_id]
                and sorted(guidance.get("requirement_ids", [])) == coverage
                and problem_failures
                and recovery_child_id in problem_failures[-1].get("artifact_ids", [])
                and guidance.get("failed_operation_id")
                in problem_failures[-1].get("artifact_ids", [])
                and failed is not None
                and failed.get("status") == "failed"
                and _replacement_maps_to_child(
                    connection,
                    problem_id=str(outcome.get("problem_id", "")),
                    source_child_id=recovery_child_id,
                    current_child_id=str(child["child_id"]),
                )
                and final_checks.get("status") == "passed"
                and final_checks.get("integration_head")
                == outcome.get("candidate_head")
                and final_checks.get("integration_tree_id")
                == outcome.get("candidate_tree_id")
                and context["integration"]["integration_head"]
                == outcome.get("candidate_head")
                and context["integration"]["integration_tree_id"]
                == outcome.get("candidate_tree_id")
                and commit is None
                and integration is None
            ),
            "status": outcome.get("status"),
        }
    )
    return evidence


def _child_evidence(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT * FROM child_operations ORDER BY child_id"
    ).fetchall()
    result = []
    for row in rows:
        child_id = row["child_id"]
        commit = connection.execute(
            "SELECT * FROM git_operations WHERE git_operation_id = ?",
            (f"child-commit:{child_id}",),
        ).fetchone()
        integration = connection.execute(
            "SELECT * FROM git_operations WHERE phase = 'integrated' "
            "AND json_extract(outcome_json, '$.child_id') = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (child_id,),
        ).fetchone()
        evidence = {
            "child_id": child_id,
            "commit": dict(commit) if commit else None,
            "coverage": json.loads(row["coverage_json"]),
            "integration": dict(integration) if integration else None,
            "state": row["state"],
        }
        zero_diff = _zero_diff_integration_evidence(connection, row)
        if zero_diff is not None:
            evidence["zero_diff_integration"] = zero_diff
        result.append(evidence)
    return result


def _problem_evidence(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        json.loads(row["outcome_json"])
        for row in connection.execute(
            "SELECT outcome_json FROM operations WHERE kind = 'problem_attempt' "
            "ORDER BY created_at, operation_id"
        ).fetchall()
    ]


def _omission_evidence(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        json.loads(row["outcome_json"])
        for row in connection.execute(
            "SELECT outcome_json FROM operations "
            "WHERE kind = 'optional_requirement_omitted' ORDER BY operation_id"
        ).fetchall()
    ]


def _pack_markdown(pack: Mapping[str, object], pack_digest: str) -> str:
    requirements = "\n".join(
        f"- `{item['requirement_id']}`: {item['coverage_state']}"
        for item in pack["requirements"]
    )
    children = (
        "\n".join(
            f"- `{item['child_id']}`: {item['state']}" for item in pack["children"]
        )
        or "- none"
    )
    return (
        f"# Loop v1 acceptance pack {pack['pack_id']}\n\n"
        f"- Pack digest: `{pack_digest}`\n"
        f"- Base HEAD: `{pack['git']['base_head']}`\n"
        f"- Integration HEAD: `{pack['git']['integration_head']}`\n"
        f"- Final request: `{pack['final_request_id']}`\n\n"
        f"## Requirements\n\n{requirements}\n\n"
        f"## Children\n\n{children}\n"
    )


def _pack_value(
    pack_result: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    if not isinstance(pack_result, Mapping):
        raise ReadinessError("pack_result must be an object")
    pack = pack_result.get("pack")
    digest = pack_result.get("pack_digest")
    if not isinstance(pack, Mapping):
        raise ReadinessError("pack_result.pack must be an object")
    return dict(pack), _required_text(digest, "pack_digest")


def _requirements_ready(
    connection: sqlite3.Connection, context: Mapping[str, object]
) -> tuple[bool, list[dict[str, object]]]:
    omissions = {
        item["requirement_id"]: item
        for item in _omission_evidence(connection)
        if item.get("status") == "omitted"
    }
    evidence = []
    for item in _applicable_requirements(context):
        passed = item["coverage_state"] == "covered" or (
            not item["required"]
            and item["coverage_state"] == "omitted"
            and item["requirement_id"] in omissions
        )
        evidence.append(
            {
                "coverage_state": item["coverage_state"],
                "omission": omissions.get(item["requirement_id"]),
                "passed": passed,
                "requirement_id": item["requirement_id"],
                "required": item["required"],
            }
        )
    return all(item["passed"] for item in evidence), evidence


def _children_ready(
    connection: sqlite3.Connection,
) -> tuple[bool, list[dict[str, object]]]:
    evidence = []
    for child in connection.execute(
        "SELECT child_id, state, coverage_json, epoch "
        "FROM child_operations ORDER BY child_id"
    ).fetchall():
        commit = connection.execute(
            "SELECT commit_id, operation_id, tree_id, phase FROM git_operations "
            "WHERE git_operation_id = ?",
            (f"child-commit:{child['child_id']}",),
        ).fetchone()
        integrated = connection.execute(
            "SELECT commit_id, tree_id, phase, outcome_json FROM git_operations "
            "WHERE phase = 'integrated' "
            "AND json_extract(outcome_json, '$.child_id') = ? LIMIT 1",
            (child["child_id"],),
        ).fetchone()
        review = None
        validation = None
        integration_checks: list[dict[str, object]] = []
        if commit is not None:
            commit_operation = connection.execute(
                "SELECT outcome_json FROM operations WHERE operation_id = ?",
                (commit["operation_id"],),
            ).fetchone()
            if commit_operation is not None:
                commit_outcome = json.loads(commit_operation["outcome_json"])
                review_row = connection.execute(
                    "SELECT outcome_json FROM operations WHERE operation_id = ?",
                    (f"precommit-review:{commit_outcome['review_id']}",),
                ).fetchone()
                if review_row is not None:
                    review = json.loads(review_row["outcome_json"])
                    validation_row = connection.execute(
                        "SELECT outcome_json FROM operations WHERE operation_id = ?",
                        (f"candidate-validation:{review['validation_id']}",),
                    ).fetchone()
                    if validation_row is not None:
                        validation = json.loads(validation_row["outcome_json"])
        if integrated is not None:
            integration_outcome = json.loads(integrated["outcome_json"])
            integration_checks = integration_outcome.get("checks_result", [])
        else:
            integration_outcome = None
        zero_diff = _zero_diff_integration_evidence(connection, child)
        if child["state"] == "integrated":
            passed = bool(
                (
                    commit is not None
                    and commit["phase"] == "committed"
                    and integrated is not None
                    and integration_outcome["child_commit_id"] == commit["commit_id"]
                    and integration_outcome["child_tree_id"] == commit["tree_id"]
                    and review
                    and review["verdict"] == "passed"
                    and not review["required_findings"]
                    and review["tree_id"] == commit["tree_id"]
                    and validation
                    and validation["tree_id"] == commit["tree_id"]
                    and all(
                        item["status"] == "passed"
                        for item in validation["parent_checks"]
                    )
                    and integration_checks
                    and all(
                        item["status"] == "passed" for item in integration_checks
                    )
                )
                or (zero_diff is not None and zero_diff["passed"])
            )
        else:
            passed = child["state"] in {"cancelled", "invalidated", "stale"}
        evidence.append(
            {
                "child_id": child["child_id"],
                "commit": dict(commit) if commit else None,
                "integration_checks": integration_checks,
                "passed": passed,
                "review": review,
                "state": child["state"],
                "validation": validation,
                "zero_diff_integration": zero_diff,
            }
        )
    return all(item["passed"] for item in evidence), evidence


def _problems_ready(
    connection: sqlite3.Connection,
) -> tuple[bool, list[dict[str, object]]]:
    latest: dict[str, dict[str, object]] = {}
    for item in _problem_evidence(connection):
        latest[item["problem_id"]] = item
    evidence = [latest[key] for key in sorted(latest)]
    return all(
        item["result"] == "passed" and not item["exhausted"] for item in evidence
    ), evidence


def _start_gate_ready(snapshot: Mapping[str, object]) -> tuple[bool, object]:
    parents = snapshot["tables"]["parent_runs"]
    gates = snapshot["tables"]["gate_requests"]
    if len(parents) != 1:
        return False, parents
    start_ref = parents[0]["start_gate_ref"]
    matches = [
        item
        for item in gates
        if item["gate_id"] == start_ref and item["kind"] == "start"
    ]
    passed = bool(
        len(matches) == 1
        and matches[0]["response_identity"]
        and matches[0]["invalidation_reason"] is None
    )
    return passed, matches


def _effects_ready(
    pack_effects: Sequence[Mapping[str, object]],
    ledger_effects: Sequence[Mapping[str, object]],
) -> tuple[bool, object]:
    pack_ok = all(
        (item["classification"] == "local_declared" and item["status"] == "passed")
        or (
            item["classification"] == "prohibited_external"
            and item["status"] == "not_performed"
        )
        for item in pack_effects
    )
    ledger_ok = all(
        item["classification"] == "local_declared" for item in ledger_effects
    )
    return pack_ok and ledger_ok, {
        "ledger": list(ledger_effects),
        "pack": list(pack_effects),
    }


def _source_position_current(
    connection: sqlite3.Connection,
    source_position: int,
    request_id: str,
    pack_digest: str,
) -> tuple[bool, object]:
    rows = connection.execute(
        """
        SELECT DISTINCT o.operation_id, o.kind, o.outcome_json
        FROM ledger_events e JOIN operations o ON o.operation_id = e.operation_id
        WHERE e.position > ? ORDER BY e.position
        """,
        (source_position,),
    ).fetchall()
    allowed = {
        f"final-request:{request_id}": _FINAL_REQUEST_KIND,
        f"final-approval:{request_id}": _FINAL_APPROVAL_KIND,
    }
    unexpected = []
    seen: set[str] = set()
    for row in rows:
        if row["operation_id"] in seen:
            continue
        seen.add(row["operation_id"])
        outcome = json.loads(row["outcome_json"])
        valid = (
            allowed.get(row["operation_id"]) == row["kind"]
            and outcome.get("request", {}).get(
                "pack_digest", outcome.get("pack_digest")
            )
            == pack_digest
        )
        if not valid:
            unexpected.append(
                {"kind": row["kind"], "operation_id": row["operation_id"]}
            )
    return not unexpected, {"unexpected_operations": unexpected}


def _pack_file_current(
    pack_result: Mapping[str, object], pack: Mapping[str, object], digest: str
) -> tuple[bool, object]:
    path = Path(_required_text(pack_result.get("json_path"), "json_path"))
    if not path.is_file():
        return False, {"path": str(path), "status": "missing"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, {"error": str(exc), "path": str(path)}
    passed = value == {"pack": pack, "pack_digest": digest}
    return passed, {"path": str(path), "status": "current" if passed else "drifted"}


def _projection_file_current(
    ledger: ParentLedger, projection: Mapping[str, object]
) -> tuple[bool, object]:
    path = ledger.path.parent / "projections" / "summary.json"
    if not path.is_file() or not projection.get("digest"):
        return False, {"path": str(path), "status": "missing"}
    try:
        actual = sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return False, {"error": str(exc), "path": str(path)}
    expected = projection["digest"]
    return actual == expected, {
        "actual_digest": actual,
        "expected_digest": expected,
        "path": str(path),
    }


def _current_branch(repo: Path) -> str:
    try:
        return _git_text(repo, "symbolic-ref", "--short", "HEAD")
    except GitStateError:
        return "DETACHED"


def _replay(
    operation: Mapping[str, object],
    kind: str,
    input_value: Mapping[str, object],
    outcome: Mapping[str, object],
) -> dict[str, object]:
    if (
        operation["kind"] != kind
        or operation["phase"] != "authority_committed"
        or operation["input_fingerprint"] != _digest_json(input_value)
        or operation["outcome"] != dict(outcome)
    ):
        raise OperationConflict("stable acceptance operation ID was reused")
    return dict(operation["outcome"])


def _request_value(
    pack: Mapping[str, object],
    pack_digest: str,
    request_id: str,
    integration_ref: str,
    checks: Sequence[str],
    author_name: str,
    author_email: str,
    readiness_digest: str,
) -> dict[str, object]:
    return {
        "allowed_actions": _FINAL_ACTIONS,
        "author_email": author_email,
        "author_name": author_name,
        "base_branch": pack["git"]["base_branch"],
        "base_head": pack["git"]["base_head"],
        "base_tree_id": pack["git"]["base_tree_id"],
        "final_review": {
            "artifact_digest": pack["review"]["artifact_digest"],
            "review_id": pack["review"]["review_id"],
        },
        "integration_head": pack["git"]["integration_head"],
        "integration_ref": integration_ref,
        "integration_tree_id": pack["git"]["integration_tree_id"],
        "pack_digest": pack_digest,
        "pack_id": pack["pack_id"],
        "pack_source_position": pack["authority"]["source_position"],
        "post_merge_checks": list(checks),
        "prohibited_external_actions": _PROHIBITED_EXTERNAL_ACTIONS,
        "readiness_digest": readiness_digest,
        "request_id": request_id,
        "tool_receipt": pack["tool_receipt"],
    }


def _final_request_operation(
    ledger: ParentLedger, request_id: str
) -> dict[str, object]:
    operation = ledger.get_operation(f"final-request:{request_id}")
    if (
        operation is None
        or operation["kind"] != _FINAL_REQUEST_KIND
        or operation["phase"] != "authority_committed"
    ):
        raise FinalGateError("exact final request authority is missing")
    return operation["outcome"]


def _approved_final_request(ledger: ParentLedger, request_id: str) -> dict[str, object]:
    request = _final_request_operation(ledger, request_id)
    approval = ledger.get_operation(f"final-approval:{request_id}")
    connection = ledger._connect(read_only=True)
    try:
        gate = connection.execute(
            "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'final'",
            (request_id,),
        ).fetchone()
        parent = connection.execute(
            "SELECT start_gate_ref, final_gate_ref FROM parent_runs WHERE run_id = ?",
            (ledger.run_id,),
        ).fetchone()
        start_gate = (
            connection.execute(
                "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'start'",
                (parent["start_gate_ref"],),
            ).fetchone()
            if parent is not None
            else None
        )
        local_authority = (
            connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (f"local-command-authority:{parent['start_gate_ref']}",),
            ).fetchone()
            if parent is not None
            else None
        )
    finally:
        connection.close()
    approval_outcome = approval["outcome"] if approval is not None else {}
    direct = (
        approval_outcome.get("direct_user_action") is True
        and gate is not None
        and gate["response_identity"] is not None
    )
    local_authority_outcome = (
        json.loads(local_authority["outcome_json"])
        if local_authority is not None
        else {}
    )
    local = (
        approval_outcome.get("direct_user_action") is False
        and approval_outcome.get("authorization_kind") == "local_start"
        and parent is not None
        and approval_outcome.get("authorization_ref") == parent["start_gate_ref"]
        and gate is not None
        and gate["response_identity"] is None
        and start_gate is not None
        and start_gate["response_identity"] is not None
        and start_gate["invalidation_reason"] is None
        and local_authority is not None
        and local_authority["kind"] == _LOCAL_COMMAND_AUTHORITY_KIND
        and local_authority["phase"] == "authority_committed"
        and local_authority_outcome.get("profile") == "single_user"
        and "local_final_merge"
        in local_authority_outcome.get("authorized_local_actions", [])
    )
    if (
        approval is None
        or approval["kind"] != _FINAL_APPROVAL_KIND
        or approval["phase"] != "authority_committed"
        or gate is None
        or gate["invalidation_reason"] is not None
        or parent is None
        or parent["final_gate_ref"] != request_id
        or not (direct or local)
    ):
        raise FinalGateError("final request is not approved and current")
    return request


def _invalidate_final_gate(
    ledger: ParentLedger, lease: WriterLease, request_id: str, reason: str
) -> None:
    operation_id = f"final-invalidation:{request_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return
    supplied = {"reason": reason, "request_id": request_id}
    outcome = {**supplied, "status": "invalidated"}
    with ledger._write_transaction(lease) as connection:
        gate = connection.execute(
            "SELECT invalidation_reason FROM gate_requests WHERE gate_id = ?",
            (request_id,),
        ).fetchone()
        if gate is None:
            raise FinalGateError("cannot invalidate a missing final gate")
        now = _now()
        connection.execute(
            "UPDATE gate_requests SET invalidation_reason = ? WHERE gate_id = ?",
            (reason, request_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="final_request_invalidated",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="final_request_invalidated",
            created_at=now,
        )
    ledger.rebuild_projection(lease)


def _observe_or_create_final_merge(repo: Path, intent: Mapping[str, object]) -> str:
    branch = _current_branch(repo)
    head = _git_text(repo, "rev-parse", "HEAD")
    if branch != intent["base_branch"]:
        raise FinalMergeError("canonical worktree is not on the approved base branch")
    if scan_repository_dirt(repo)["paths"]:
        raise FinalMergeError("canonical worktree is dirty before final merge")
    integration_head = _git_text(
        repo, "rev-parse", "--verify", intent["integration_ref"]
    )
    integration_tree = _git_text(repo, "rev-parse", f"{integration_head}^{{tree}}")
    if (
        integration_head != intent["integration_head"]
        or integration_tree != intent["integration_tree_id"]
    ):
        raise FinalMergeError("approved integration ref changed before final merge")
    if head == intent["base_head"]:
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_EMAIL": intent["author_email"],
                "GIT_AUTHOR_NAME": intent["author_name"],
                "GIT_COMMITTER_EMAIL": intent["author_email"],
                "GIT_COMMITTER_NAME": intent["author_name"],
            }
        )
        _git(repo, "merge", "--no-ff", "--no-edit", integration_head, env=env)
        head = _git_text(repo, "rev-parse", "HEAD")
    _assert_final_merge_shape(repo, head, intent)
    return head


def _assert_final_merge_shape(
    repo: Path, head: str, intent: Mapping[str, object]
) -> None:
    if _merge_parents(repo, head) != [intent["base_head"], intent["integration_head"]]:
        raise FinalMergeError("final merge does not have the exact approved parents")
    tree = _git_text(repo, "rev-parse", f"{head}^{{tree}}")
    if tree != intent["integration_tree_id"]:
        raise FinalMergeError("final merge tree differs from approved integration tree")
    if not _is_ancestor(repo, intent["integration_head"], head):
        raise FinalMergeError("approved integration HEAD is absent from final merge")


def _assert_final_merge_git(
    repo: Path,
    outcome: Mapping[str, object],
    *,
    execution_binding: Mapping[str, object] | None = None,
) -> None:
    head = _git_text(repo, "rev-parse", "HEAD")
    dirt = scan_repository_dirt(repo)
    _assert_final_merge_shape(repo, outcome["merge_head"], outcome)
    if outcome.get("recovered") is True:
        proof = outcome.get("qualification_rotation_proof")
        if proof is not None:
            if (
                not isinstance(proof, Mapping)
                or outcome.get("qualification_rotation_proof_digest")
                != proof.get("proof_digest")
            ):
                raise FinalMergeError("stored qualification rotation proof is invalid")
            _assert_qualification_rotation_proof(
                repo,
                proof,
                execution_binding=execution_binding,
            )
        publication_head = _required_text(
            outcome.get("publication_head"), "publication_head"
        )
        if (
            head != outcome.get("retained_main_head")
            or _current_branch(repo) != outcome.get("retained_main_branch")
            or not _is_ancestor(repo, outcome["merge_head"], publication_head)
            or not _is_ancestor(repo, publication_head, head)
            or _git_text(repo, "rev-parse", f"{publication_head}^{{tree}}")
            != outcome["merge_tree_id"]
            or dirt["digest"] != outcome.get("retained_dirt_digest")
        ):
            raise FinalMergeError("canonical HEAD moved after recovered final merge")
    elif head != outcome["merge_head"] and (
        not _is_ancestor(repo, outcome["merge_head"], head)
        or _git_text(repo, "rev-parse", "HEAD^{tree}") != outcome["merge_tree_id"]
    ):
        raise FinalMergeError("canonical HEAD moved beyond verified final merge")
    if outcome.get("recovered") is not True and dirt["paths"]:
        raise FinalMergeError("canonical worktree is dirty after verified final merge")


def _record_post_merge_verification(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    merge_head: str,
    checks: Sequence[Mapping[str, object]],
    passed: bool,
    dirt: Mapping[str, object],
) -> dict[str, object]:
    evidence = {"checks": list(checks), "dirt": dict(dirt), "merge_head": merge_head}
    digest = _digest_json(evidence)
    verification_id = f"post-merge:{request_id}:{digest[:16]}"
    operation_id = f"post-merge-verification:{verification_id}"
    supplied = {
        "evidence": evidence,
        "passed": passed,
        "request_id": request_id,
        "verification_id": verification_id,
    }
    outcome = {
        "artifact_digest": merge_head,
        "verification_id": verification_id,
        "verdict": "passed" if passed else "failed",
    }
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return _replay(existing, "post_merge_verification", supplied, outcome)
    with ledger._write_transaction(lease) as connection:
        now = _now()
        connection.execute(
            """
            INSERT INTO verifications (
                verification_id, run_id, artifact_digest, actor, verdict,
                findings_json, applicability_json, created_at
            ) VALUES (?, ?, ?, 'parent-final-verifier', ?, ?, ?, ?)
            """,
            (
                verification_id,
                ledger.run_id,
                merge_head,
                "passed" if passed else "failed",
                _canonical_json(
                    []
                    if passed
                    else [
                        {
                            "finding_id": "post-merge",
                            "summary": "post-merge verification failed",
                        }
                    ]
                ),
                _canonical_json(evidence),
                now,
            ),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="post_merge_verification",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="post_merge_verification_recorded",
            created_at=now,
        )
    return outcome


def _commit_final_merge_authority(
    ledger: ParentLedger,
    lease: WriterLease,
    operation_id: str,
    outcome: Mapping[str, object],
    *,
    recovered_from_epoch: int | None = None,
) -> None:
    with ledger._write_transaction(lease) as connection:
        operation = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        allowed_phases = (
            {"effect_observed"}
            if recovered_from_epoch is None
            else {"prepared", "effect_observed"}
        )
        if (
            operation is None
            or operation["phase"] not in allowed_phases
            or (
                recovered_from_epoch is not None
                and operation["epoch"] != recovered_from_epoch
            )
        ):
            raise OperationConflict("final merge is not awaiting acknowledgement")
        now = _now()
        connection.execute(
            """
            UPDATE operations SET phase = 'authority_committed',
                output_fingerprint = ?, outcome_json = ?, updated_at = ?
            WHERE operation_id = ?
              AND phase IN ('prepared', 'effect_observed')
            """,
            (outcome["merge_head"], _canonical_json(outcome), now, operation_id),
        )
        connection.execute(
            """
            INSERT INTO git_operations (
                git_operation_id, run_id, operation_id, worktree, branch,
                ref_name, expected_old_ref, commit_id, tree_id, phase,
                outcome_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final_merged', ?, ?)
            """,
            (
                operation_id,
                ledger.run_id,
                operation_id,
                str(ledger.repo_root),
                outcome["base_branch"],
                f"refs/heads/{outcome['base_branch']}",
                outcome["base_head"],
                outcome["merge_head"],
                outcome["merge_tree_id"],
                _canonical_json(outcome),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO effects (
                effect_id, run_id, classification, authorization_ref,
                outcome_json, created_at
            ) VALUES (?, ?, 'local_declared', ?, ?, ?)
            """,
            (
                operation_id,
                ledger.run_id,
                outcome["request_id"],
                _canonical_json(
                    {
                        "action": "local_merge",
                        "commit_id": outcome["merge_head"],
                        "push": False,
                    }
                ),
                now,
            ),
        )
        ledger._insert_event(
            connection,
            operation_id=operation_id,
            event_type="final_local_merge_acknowledged",
            phase="authority_committed",
            epoch=lease.epoch,
            payload={
                "outcome": dict(outcome),
                "recovered_from_epoch": recovered_from_epoch,
            }
            if recovered_from_epoch is not None
            else dict(outcome),
            created_at=now,
        )
