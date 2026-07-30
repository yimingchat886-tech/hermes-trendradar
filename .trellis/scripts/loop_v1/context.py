"""Start-envelope, canonical-context, and child-result authority for Loop v1."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from .ledger import (
    LedgerError,
    OperationConflict,
    ParentLedger,
    WriterLease,
    _canonical_json,
    _digest_json,
    _now,
    _required_text,
)


class ContextError(LedgerError):
    """Raised when an envelope, context, packet, or result is invalid."""


class FreshnessError(ContextError):
    """Raised before authority mutation when a boundary identity is stale."""


class InterventionRequired(ContextError):
    """Raised when a proposed change leaves the approved envelope."""


START_ENVELOPE_FIELDS = frozenset(
    {
        "goal",
        "requirements",
        "acceptance",
        "allowed_scope",
        "allowed_touches",
        "local_effects",
        "read_only_external_inputs",
        "resources",
        "risks",
        "prohibited_actions",
        "initial_child_graph",
        "default_parallel",
        "max_parallel",
        "base_branch",
        "base_head",
        "verification_policy",
        "review_policy",
        "retry_policy",
        "approved_agent_surfaces",
        "worker_capacity",
        "reviewer_capacity",
        "token_budget",
        "cost_budget",
        "dirty_path_fingerprint",
        "conformance_receipt",
    }
)
CONTEXT_FIELDS = frozenset(
    {
        "requirements",
        "graph",
        "decisions",
        "facts",
        "integration",
        "risks",
        "prohibitions",
        "context_slices",
        "dependency_state",
    }
)
ASSIGNMENT_FIELDS = frozenset(
    {
        "packet_id",
        "child_id",
        "requirements",
        "forbidden_touches",
        "tests",
        "context_slice_ids",
        "parent_contact",
        "result_deadline",
        "result_lease",
        "attempt",
        "round",
    }
)
RESULT_FIELDS = frozenset(
    {
        "result_id",
        "packet_id",
        "child_id",
        "actual_touches",
        "diff_identity",
        "base_head",
        "base_tree_id",
        "result_tree_id",
        "commands",
        "coverage",
        "risks",
        "findings",
        "artifacts",
        "epoch",
        "context_revision_id",
        "context_digest",
        "requirement_digest",
        "envelope_digest",
        "graph_digest",
        "dependency_digest",
    }
)
FRESHNESS_FIELDS = frozenset(
    {
        "epoch",
        "context_revision_id",
        "context_digest",
        "requirement_digest",
        "envelope_digest",
        "graph_digest",
        "dependency_digest",
    }
)
FRESHNESS_BOUNDARIES = frozenset(
    {
        "dispatch",
        "result",
        "review_request",
        "review_verdict",
        "commit",
        "candidate",
        "integration",
        "resume",
        "reconciliation",
    }
)
EXPECTED_RESULT_FIELDS = tuple(sorted(RESULT_FIELDS))

_REQUIREMENT_FIELDS = frozenset(
    {"requirement_id", "required", "acceptance", "dependencies"}
)
_CONTEXT_REQUIREMENT_FIELDS = frozenset(
    {
        "requirement_id",
        "required",
        "acceptance",
        "dependencies",
        "revision",
        "coverage_state",
    }
)
_GRAPH_NODE_FIELDS = frozenset(
    {"child_id", "requirements", "touches", "resources", "depends_on"}
)
_RESOURCE_FIELDS = frozenset({"resource_key", "mode", "capacity"})
_RESOURCE_ALIAS_FIELDS = frozenset((*_RESOURCE_FIELDS, "aliases"))
_INTEGRATION_FIELDS = frozenset(
    {
        "base_branch",
        "base_head",
        "base_tree_id",
        "integration_head",
        "integration_tree_id",
    }
)
_SLICE_FIELDS = frozenset(
    {"slice_id", "kind", "source_ref", "digest", "visibility", "excerpt"}
)
_COMMAND_FIELDS = frozenset({"command", "status", "output_digest"})
_ARTIFACT_FIELDS = frozenset({"path", "digest"})
_SENSITIVE_FIELDS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "credential",
        "credentials",
        "api_key",
        "private_key",
        "access_token",
        "refresh_token",
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
_INTERVENTION_ORDER = (
    "scope_or_resource_expansion",
    "dependency_schema_security_or_secret_change",
    "unknown_effect_data_loss_or_user_dirt",
    "retry_budget_exhausted",
    "ambiguous_product_semantics",
)


def create_start_request(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    envelope: Mapping[str, object],
) -> dict[str, object]:
    """Persist one immutable generated start request or replay it exactly."""
    request_id = _required_text(request_id, "request_id")
    canonical_envelope = _validate_envelope(envelope)
    envelope_digest = _digest_json(canonical_envelope)
    request = {
        "envelope": canonical_envelope,
        "envelope_digest": envelope_digest,
        "request_id": request_id,
    }
    request_digest = _digest_json(request)
    request["request_digest"] = request_digest
    operation_id = f"start-request:{request_id}"
    outcome = {
        "envelope_digest": envelope_digest,
        "request_digest": request_digest,
        "request_id": request_id,
        "status": "pending_direct_response",
    }
    input_fingerprint = _digest_json(request)

    with ledger._write_transaction(lease) as connection:
        if _operation_replays(
            connection,
            operation_id=operation_id,
            kind="start_request_created",
            epoch=lease.epoch,
            input_fingerprint=input_fingerprint,
            outcome=outcome,
        ):
            _assert_start_request_rows(
                connection,
                request_id=request_id,
                envelope_digest=envelope_digest,
                request_digest=request_digest,
            )
            return request

        if connection.execute(
            "SELECT 1 FROM gate_requests WHERE gate_id = ?", (request_id,)
        ).fetchone():
            raise OperationConflict("start request identity is already in use")
        now = _now()
        connection.execute(
            """
            INSERT INTO envelope_revisions (
                revision_id, run_id, digest, contract_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                request_id,
                ledger.run_id,
                envelope_digest,
                _canonical_json(canonical_envelope),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO gate_requests (
                gate_id, run_id, kind, input_digest, response_identity,
                response_at, invalidation_reason, created_at
            ) VALUES (?, ?, 'start', ?, NULL, NULL, NULL, ?)
            """,
            (request_id, ledger.run_id, request_digest, now),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="start_request_created",
            epoch=lease.epoch,
            input_fingerprint=input_fingerprint,
            outcome=outcome,
            event_type="start_request_created",
            created_at=now,
        )
    return request


def approve_start_request(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    request_digest: str,
    response_identity: str,
    response_at: str,
    direct_user_action: bool,
) -> dict[str, object]:
    """Bind a direct user response to the exact immutable request digest."""
    request_id = _required_text(request_id, "request_id")
    request_digest = _required_text(request_digest, "request_digest")
    response_identity = _required_text(response_identity, "response_identity")
    response_at = _required_text(response_at, "response_at")
    if direct_user_action is not True:
        raise ContextError("start approval must come from a direct user action")

    operation_id = f"start-approval:{request_id}"
    input_value = {
        "direct_user_action": True,
        "request_digest": request_digest,
        "request_id": request_id,
        "response_at": response_at,
        "response_identity": response_identity,
    }
    input_fingerprint = _digest_json(input_value)

    with ledger._write_transaction(lease) as connection:
        gate = connection.execute(
            "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'start'",
            (request_id,),
        ).fetchone()
        if gate is None:
            raise ContextError("start request does not exist")
        envelope_row = connection.execute(
            "SELECT contract_json FROM envelope_revisions WHERE revision_id = ?",
            (request_id,),
        ).fetchone()
        if envelope_row is None:
            raise ContextError("start envelope does not exist")
        if gate["input_digest"] != request_digest:
            raise FreshnessError("start response does not match the presented request")
        if gate["invalidation_reason"] is not None:
            raise FreshnessError("start request is invalidated")
        existing = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if existing is not None:
            try:
                stored_outcome = json.loads(existing["outcome_json"])
            except json.JSONDecodeError as exc:
                raise OperationConflict("durable start approval is malformed") from exc
            if (
                existing["kind"] != "start_request_approved"
                or existing["phase"] != "authority_committed"
                or existing["epoch"] != lease.epoch
                or existing["input_fingerprint"] != input_fingerprint
                or existing["output_fingerprint"] != _digest_json(stored_outcome)
                or not isinstance(stored_outcome, dict)
                or any(
                    stored_outcome.get(field) != value
                    for field, value in {**input_value, "status": "approved"}.items()
                )
            ):
                raise OperationConflict("durable start approval differs")
            if (
                gate["response_identity"] != response_identity
                or gate["response_at"] != response_at
            ):
                raise OperationConflict("durable start response is inconsistent")
            return stored_outcome

        from .qualification import (
            QualificationError,
            capture_execution_binding,
            operation_qualification,
        )

        envelope = json.loads(envelope_row["contract_json"])
        qualification = operation_qualification(
            ledger.repo_root,
            envelope_receipt=envelope.get("conformance_receipt"),
        )
        if qualification.enforced and not qualification.valid:
            raise QualificationError("; ".join(qualification.issues))
        outcome = {
            **input_value,
            "execution_binding": capture_execution_binding(
                ledger.repo_root, qualification
            ),
            "status": "approved",
        }
        if gate["response_identity"] is not None:
            raise OperationConflict("start request already has a different response")

        now = _now()
        connection.execute(
            """
            UPDATE gate_requests
            SET response_identity = ?, response_at = ?
            WHERE gate_id = ? AND response_identity IS NULL
            """,
            (response_identity, response_at, request_id),
        )
        connection.execute(
            """
            UPDATE parent_runs
            SET start_gate_ref = ?, status = 'authorized', updated_at = ?
            WHERE run_id = ?
            """,
            (request_id, now, ledger.run_id),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="start_request_approved",
            epoch=lease.epoch,
            input_fingerprint=input_fingerprint,
            outcome=outcome,
            event_type="start_request_approved",
            created_at=now,
        )
    return outcome


def record_context_revision(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    revision_id: str,
    reason: str,
    context: Mapping[str, object],
    expected_previous_digest: str | None = None,
) -> dict[str, object]:
    """Record a canonical context revision and any semantic graph revision."""
    request_id = _required_text(request_id, "request_id")
    revision_id = _required_text(revision_id, "revision_id")
    reason = _required_text(reason, "reason")
    expected_previous = (
        _required_text(expected_previous_digest, "expected_previous_digest")
        if expected_previous_digest is not None
        else None
    )
    canonical_context = _validate_context(context)

    with ledger._write_transaction(lease) as connection:
        envelope, envelope_digest = _approved_envelope(connection, request_id)
        _validate_context_against_envelope(canonical_context, envelope)
        existing = connection.execute(
            "SELECT * FROM context_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["reason"] != reason
                or existing["envelope_digest"] != envelope_digest
                or existing["context_json"] != _canonical_json(canonical_context)
            ):
                raise OperationConflict("context revision ID was reused with new input")
            return _context_record(existing)

        previous = _latest_context_row(connection)
        if expected_previous is not None and (
            previous is None or previous["digest"] != expected_previous
        ):
            raise FreshnessError("context revision source changed before commit")
        if previous is not None and previous["context_json"] == _canonical_json(
            canonical_context
        ):
            raise ContextError("context revision must contain a semantic change")
        previous_digest = previous["digest"] if previous is not None else None
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        requirement_digest = _digest_json(canonical_context["requirements"])
        graph_digest = _digest_json(canonical_context["graph"])
        dependency_digest = _digest_json(canonical_context["dependency_state"])
        context_digest = _digest_json(
            {
                "context": canonical_context,
                "envelope_digest": envelope_digest,
                "previous_digest": previous_digest,
                "reason": reason,
            }
        )
        operation_id = f"context-revision:{revision_id}"
        input_value = {
            "context_digest": context_digest,
            "request_id": request_id,
            "revision_id": revision_id,
        }
        outcome = {
            "context_digest": context_digest,
            "dependency_digest": dependency_digest,
            "envelope_digest": envelope_digest,
            "graph_digest": graph_digest,
            "requirement_digest": requirement_digest,
            "revision_id": revision_id,
            "sequence": sequence,
        }
        now = _now()

        connection.execute(
            """
            INSERT INTO context_revisions (
                revision_id, run_id, sequence, previous_digest, digest,
                envelope_digest, requirement_digest, graph_digest,
                dependency_digest, reason, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                ledger.run_id,
                sequence,
                previous_digest,
                context_digest,
                envelope_digest,
                requirement_digest,
                graph_digest,
                dependency_digest,
                reason,
                _canonical_json(canonical_context),
                now,
            ),
        )
        previous_graph_digest = (
            previous["graph_digest"] if previous is not None else None
        )
        if graph_digest != previous_graph_digest:
            connection.execute(
                """
                INSERT INTO graph_revisions (
                    graph_revision_id, run_id, context_revision_id, digest,
                    reason, graph_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"graph:{revision_id}",
                    ledger.run_id,
                    revision_id,
                    graph_digest,
                    reason,
                    _canonical_json(canonical_context["graph"]),
                    now,
                ),
            )
        if previous is not None:
            previous_ids = {
                str(item["child_id"])
                for item in json.loads(previous["context_json"])["graph"]
            }
            current_ids = {
                str(item["child_id"]) for item in canonical_context["graph"]
            }
            removed_ids = sorted(previous_ids - current_ids)
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                connection.execute(
                    f"UPDATE child_operations SET state = 'invalidated', updated_at = ? "
                    f"WHERE child_id IN ({placeholders}) "
                    "AND state IN ('dispatched', 'result_validated', "
                    "'candidate_validated', 'reviewed', 'review_blocked', 'committed')",
                    (now, *removed_ids),
                )
                connection.execute(
                    f"UPDATE resource_claims SET state = 'released', updated_at = ? "
                    f"WHERE child_id IN ({placeholders}) AND state = 'acquired'",
                    (now, *removed_ids),
                )
        _synchronize_requirements(connection, ledger.run_id, canonical_context, now)
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="context_revision_recorded",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(input_value),
            outcome=outcome,
            event_type="context_revision_recorded",
            created_at=now,
        )
        row = connection.execute(
            "SELECT * FROM context_revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        return _context_record(row)


def freshness_token(ledger: ParentLedger) -> dict[str, object]:
    """Return the exact current identity checked at every authority boundary."""
    connection = ledger._connect(read_only=True)
    try:
        return _freshness_token(connection, ledger.run_id)
    finally:
        connection.close()


def assert_fresh(
    ledger: ParentLedger,
    boundary: str,
    token: Mapping[str, object],
) -> dict[str, object]:
    """Fail closed when a supplied boundary token differs from authority."""
    boundary = _required_text(boundary, "boundary")
    if boundary not in FRESHNESS_BOUNDARIES:
        raise ContextError(f"unsupported freshness boundary: {boundary}")
    supplied = _exact_object(token, FRESHNESS_FIELDS, "freshness token")
    current = freshness_token(ledger)
    _compare_freshness(boundary, supplied, current)
    return current


def issue_child_packet(
    ledger: ParentLedger,
    lease: WriterLease,
    assignment: Mapping[str, object],
) -> dict[str, object]:
    """Issue and persist one minimal digest-bound child packet."""
    value = _exact_object(assignment, ASSIGNMENT_FIELDS, "child assignment")
    packet_id = _required_text(value["packet_id"], "packet_id")
    child_id = _required_text(value["child_id"], "child_id")
    requirement_ids = _text_list(value["requirements"], "requirements")
    forbidden_touches = _path_list(value["forbidden_touches"], "forbidden_touches")
    tests = _text_list(value["tests"], "tests")
    slice_ids = _text_list(value["context_slice_ids"], "context_slice_ids")
    attempt = _positive_int(value["attempt"], "attempt")
    round_number = _positive_int(value["round"], "round")
    parent_contact = _required_text(value["parent_contact"], "parent_contact")
    result_deadline = _required_text(value["result_deadline"], "result_deadline")
    result_lease = _required_text(value["result_lease"], "result_lease")

    with ledger._write_transaction(lease) as connection:
        _assert_parent_authorized(connection, ledger.run_id, "child dispatch")
        current_token = _freshness_token(connection, ledger.run_id)
        context_row = _latest_context_row(connection)
        context = json.loads(context_row["context_json"])
        graph_node = _graph_node(context["graph"], child_id)
        if set(requirement_ids) != set(graph_node["requirements"]):
            raise ContextError("child coverage must match its current graph assignment")
        requirement_map = {
            item["requirement_id"]: item for item in context["requirements"]
        }
        try:
            packet_requirements = [requirement_map[item] for item in requirement_ids]
        except KeyError as exc:
            raise ContextError(
                "child assignment references an unknown requirement"
            ) from exc

        slice_map = {item["slice_id"]: item for item in context["context_slices"]}
        selected_slices = []
        for slice_id in slice_ids:
            if slice_id not in slice_map:
                raise ContextError(f"unknown context slice: {slice_id}")
            context_slice = slice_map[slice_id]
            if context_slice["visibility"] == "secret_ref":
                raise ContextError("secret context slices cannot enter a child packet")
            selected_slices.append(context_slice)

        packet = {
            "acceptance": [
                {
                    "requirement_id": item["requirement_id"],
                    "criteria": item["acceptance"],
                }
                for item in packet_requirements
            ],
            "attempt": attempt,
            "base": {
                "branch": context["integration"]["base_branch"],
                "head": context["integration"]["base_head"],
                "tree_id": context["integration"]["base_tree_id"],
            },
            "child_id": child_id,
            "context_slices": selected_slices,
            "dependencies": graph_node["depends_on"],
            "execution_base": {
                "head": context["integration"]["integration_head"],
                "tree_id": context["integration"]["integration_tree_id"],
            },
            "expected_result_fields": list(EXPECTED_RESULT_FIELDS),
            "identity": current_token,
            "packet_id": packet_id,
            "parent": {
                "contact": parent_contact,
                "result_deadline": result_deadline,
                "result_lease": result_lease,
            },
            "prohibitions": context["prohibitions"],
            "requirements": packet_requirements,
            "resources": graph_node["resources"],
            "round": round_number,
            "scope": {
                "allowed_touches": graph_node["touches"],
                "forbidden_touches": forbidden_touches,
            },
            "tests": tests,
        }
        _reject_sensitive_fields(packet)
        packet_digest = _digest_json(packet)
        packet["packet_digest"] = packet_digest

        existing = connection.execute(
            "SELECT * FROM child_packets WHERE packet_id = ?", (packet_id,)
        ).fetchone()
        if existing is not None:
            if existing["packet_digest"] != packet_digest:
                raise OperationConflict("packet ID was reused with new input")
            return json.loads(existing["packet_json"])
        if connection.execute(
            "SELECT 1 FROM child_operations WHERE child_id = ?", (child_id,)
        ).fetchone():
            raise OperationConflict("child identity already has an issued operation")

        now = _now()
        connection.execute(
            """
            INSERT INTO child_operations (
                child_id, run_id, coverage_json, context_digest, attempt,
                round, state, epoch, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'dispatched', ?, ?)
            """,
            (
                child_id,
                ledger.run_id,
                _canonical_json(requirement_ids),
                current_token["context_digest"],
                attempt,
                round_number,
                lease.epoch,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO child_packets (
                packet_id, run_id, child_id, epoch, context_digest,
                requirement_digest, envelope_digest, graph_digest,
                dependency_digest, base_head, base_tree_id, packet_digest,
                packet_json, issued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                packet_id,
                ledger.run_id,
                child_id,
                lease.epoch,
                current_token["context_digest"],
                current_token["requirement_digest"],
                current_token["envelope_digest"],
                current_token["graph_digest"],
                current_token["dependency_digest"],
                context["integration"]["base_head"],
                context["integration"]["base_tree_id"],
                packet_digest,
                _canonical_json(packet),
                now,
            ),
        )
        outcome = {
            "child_id": child_id,
            "packet_digest": packet_digest,
            "packet_id": packet_id,
        }
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=f"child-packet:{packet_id}",
            kind="child_packet_issued",
            epoch=lease.epoch,
            input_fingerprint=packet_digest,
            outcome=outcome,
            event_type="child_packet_issued",
            created_at=now,
        )
        return packet


def accept_child_result(
    ledger: ParentLedger,
    lease: WriterLease,
    result: Mapping[str, object],
) -> dict[str, object]:
    """Validate a structured result completely before recording acceptance."""
    value = _exact_object(result, RESULT_FIELDS, "child result")
    _reject_sensitive_fields(value)
    result_id = _required_text(value["result_id"], "result_id")
    child_id = _required_text(value["child_id"], "child_id")
    packet_id = _required_text(value["packet_id"], "packet_id")
    payload_digest = _digest_json(value)

    with ledger._write_transaction(lease) as connection:
        receipt = connection.execute(
            "SELECT * FROM message_receipts WHERE receipt_id = ?", (result_id,)
        ).fetchone()
        if receipt is not None:
            if (
                receipt["sender"] == child_id
                and receipt["payload_digest"] == payload_digest
                and receipt["disposition"] in {"accepted", "rejected"}
            ):
                accepted = receipt["disposition"] == "accepted"
                failed_tests: list[str] = []
                if not accepted:
                    operation = connection.execute(
                        "SELECT outcome_json FROM operations WHERE operation_id = ?",
                        (f"child-result:{result_id}",),
                    ).fetchone()
                    if operation is None:
                        raise ContextError("rejected result lacks durable outcome evidence")
                    failed_tests = list(
                        json.loads(operation["outcome_json"])["failed_tests"]
                    )
                return {
                    "accepted": accepted,
                    "failed_tests": failed_tests,
                    "payload_digest": payload_digest,
                    "replayed": True,
                    "result": value,
                }
            raise OperationConflict("result identity was reused with new input")

        _assert_parent_authorized(connection, ledger.run_id, "child result")
        packet_row = connection.execute(
            "SELECT * FROM child_packets WHERE packet_id = ?", (packet_id,)
        ).fetchone()
        if packet_row is None:
            raise ContextError("result references an unknown child packet")
        packet = json.loads(packet_row["packet_json"])
        if packet["child_id"] != child_id:
            raise ContextError("result child does not own the referenced packet")

        supplied_token = {key: value[key] for key in FRESHNESS_FIELDS}
        current_token = _freshness_token(connection, ledger.run_id)
        _compare_freshness("result", supplied_token, current_token)
        _compare_freshness("result packet", supplied_token, packet["identity"])
        execution_base = _packet_execution_base(packet)
        if value["base_head"] != execution_base["head"]:
            raise FreshnessError("result base HEAD differs from its packet")
        if value["base_tree_id"] != execution_base["tree_id"]:
            raise FreshnessError("result base tree differs from its packet")

        actual_touches = _path_list(value["actual_touches"], "actual_touches")
        allowed = packet["scope"]["allowed_touches"]
        forbidden = packet["scope"]["forbidden_touches"]
        for path in actual_touches:
            if not any(_path_matches(path, pattern) for pattern in allowed):
                raise ContextError(f"result touch is outside assigned scope: {path}")
            if any(_path_matches(path, pattern) for pattern in forbidden):
                raise ContextError(f"result touch is explicitly forbidden: {path}")

        expected_coverage = {item["requirement_id"] for item in packet["requirements"]}
        coverage = set(_text_list(value["coverage"], "coverage"))
        if coverage != expected_coverage:
            raise ContextError("result coverage does not match the packet assignment")
        command_results = _validate_commands(value["commands"])
        failed_tests = [
            command
            for command in packet["tests"]
            if command_results.get(command) != "passed"
        ]
        _required_text(value["diff_identity"], "diff_identity")
        _required_text(value["result_tree_id"], "result_tree_id")
        _validate_artifacts(value["artifacts"], allowed, forbidden)
        _list_value(value["risks"], "risks")
        _list_value(value["findings"], "findings")
        now = _now()
        disposition = "rejected" if failed_tests else "accepted"
        connection.execute(
            """
            INSERT INTO message_receipts (
                receipt_id, run_id, sender, role, expected_epoch,
                context_digest, payload_digest, disposition, received_at
            ) VALUES (?, ?, ?, 'worker', ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                ledger.run_id,
                child_id,
                lease.epoch,
                value["context_digest"],
                payload_digest,
                disposition,
                now,
            ),
        )
        if failed_tests:
            outcome = {
                "child_id": child_id,
                "failed_tests": failed_tests,
                "payload_digest": payload_digest,
                "result_id": result_id,
            }
            _insert_committed_operation(
                connection,
                ledger,
                operation_id=f"child-result:{result_id}",
                kind="child_result_rejected",
                epoch=lease.epoch,
                input_fingerprint=payload_digest,
                outcome=outcome,
                event_type="child_result_rejected",
                created_at=now,
            )
            return {
                "accepted": False,
                "failed_tests": failed_tests,
                "payload_digest": payload_digest,
                "replayed": False,
                "result": value,
            }
        updated = connection.execute(
            """
            UPDATE child_operations
            SET state = 'result_validated', updated_at = ?
            WHERE child_id = ? AND state = 'dispatched' AND epoch = ?
              AND context_digest = ?
            """,
            (now, child_id, lease.epoch, value["context_digest"]),
        )
        if updated.rowcount != 1:
            raise FreshnessError("child operation changed before result acceptance")
        outcome = {
            "child_id": child_id,
            "payload_digest": payload_digest,
            "result_id": result_id,
        }
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=f"child-result:{result_id}",
            kind="child_result_accepted",
            epoch=lease.epoch,
            input_fingerprint=payload_digest,
            outcome=outcome,
            event_type="child_result_accepted",
            created_at=now,
        )
        return {
            "accepted": True,
            "failed_tests": [],
            "payload_digest": payload_digest,
            "replayed": False,
            "result": value,
        }


def intervention_reason(signals: Mapping[str, object]) -> str | None:
    """Return only an exhaustive human-intervention condition, if present."""
    allowed = frozenset((*_INTERVENTION_ORDER, "explicit_user_control"))
    value = _exact_object(signals, allowed, "intervention signals")
    control = value["explicit_user_control"]
    if control is not None:
        control = _required_text(control, "explicit_user_control")
        if control not in {"pause", "resume", "cancel"}:
            raise ContextError("explicit user control must be pause, resume, or cancel")
        return f"explicit_user_{control}"
    for condition in _INTERVENTION_ORDER:
        if value[condition] is True:
            return condition
        if value[condition] is not False:
            raise ContextError(f"{condition} must be boolean")
    return None


def _validate_envelope(envelope: Mapping[str, object]) -> dict[str, object]:
    value = _exact_object(envelope, START_ENVELOPE_FIELDS, "start envelope")
    _reject_sensitive_fields(value)
    value["goal"] = _required_text(value["goal"], "goal")
    value["requirements"] = _validate_requirements(value["requirements"], False)
    for field in (
        "acceptance",
        "allowed_scope",
        "local_effects",
        "read_only_external_inputs",
        "risks",
        "prohibited_actions",
    ):
        value[field] = _list_value(value[field], field)
    value["allowed_touches"] = _path_list(value["allowed_touches"], "allowed_touches")
    value["resources"] = _validate_resources(value["resources"])
    value["initial_child_graph"] = _validate_graph(value["initial_child_graph"])
    value["base_branch"] = _required_text(value["base_branch"], "base_branch")
    value["base_head"] = _required_text(value["base_head"], "base_head")
    for field in ("verification_policy", "review_policy", "retry_policy"):
        if not isinstance(value[field], dict):
            raise ContextError(f"{field} must be an object")
    value["approved_agent_surfaces"] = _text_list(
        value["approved_agent_surfaces"], "approved_agent_surfaces"
    )
    value["default_parallel"] = _positive_int(
        value["default_parallel"], "default_parallel"
    )
    value["max_parallel"] = _positive_int(value["max_parallel"], "max_parallel")
    if value["default_parallel"] > value["max_parallel"] or value["max_parallel"] > 3:
        raise ContextError("parallel limits must satisfy default <= max <= 3")
    value["worker_capacity"] = _nonnegative_int(
        value["worker_capacity"], "worker_capacity"
    )
    value["reviewer_capacity"] = _nonnegative_int(
        value["reviewer_capacity"], "reviewer_capacity"
    )
    for field in ("token_budget", "cost_budget"):
        budget = value[field]
        if budget is not None and (
            isinstance(budget, bool)
            or not isinstance(budget, (int, float))
            or budget < 0
        ):
            raise ContextError(f"{field} must be null or non-negative")
    value["dirty_path_fingerprint"] = _required_text(
        value["dirty_path_fingerprint"], "dirty_path_fingerprint"
    )
    value["conformance_receipt"] = _required_text(
        value["conformance_receipt"], "conformance_receipt"
    )
    _validate_graph_against_envelope(value["initial_child_graph"], value)
    return value


def _validate_context(context: Mapping[str, object]) -> dict[str, object]:
    value = _exact_object(context, CONTEXT_FIELDS, "canonical context")
    _reject_sensitive_fields(value)
    value["requirements"] = _validate_requirements(value["requirements"], True)
    value["graph"] = _validate_graph(value["graph"])
    for field in ("decisions", "facts", "risks", "prohibitions"):
        value[field] = _list_value(value[field], field)
    value["integration"] = _exact_object(
        value["integration"], _INTEGRATION_FIELDS, "integration identity"
    )
    for field in _INTEGRATION_FIELDS:
        value["integration"][field] = _required_text(value["integration"][field], field)
    if not isinstance(value["dependency_state"], dict):
        raise ContextError("dependency_state must be an object")
    value["context_slices"] = _validate_context_slices(value["context_slices"])
    return value


def _validate_context_against_envelope(
    context: dict[str, object], envelope: dict[str, object]
) -> None:
    approved = {item["requirement_id"]: item for item in envelope["requirements"]}
    current = {item["requirement_id"]: item for item in context["requirements"]}
    if set(approved) != set(current):
        raise InterventionRequired("requirement set expansion requires intervention")
    for requirement_id, item in current.items():
        source = approved[requirement_id]
        for field in ("required", "acceptance", "dependencies"):
            if item[field] != source[field]:
                raise InterventionRequired(
                    f"requirement contract changed outside envelope: {requirement_id}"
                )
    integration = context["integration"]
    if (
        integration["base_branch"] != envelope["base_branch"]
        or integration["base_head"] != envelope["base_head"]
    ):
        raise FreshnessError(
            "canonical context base differs from approved start request"
        )
    _validate_graph_against_envelope(context["graph"], envelope)


def _validate_graph_against_envelope(
    graph: list[dict[str, object]], envelope: dict[str, object]
) -> None:
    requirement_ids = {item["requirement_id"] for item in envelope["requirements"]}
    resource_keys = {
        name.casefold()
        for item in envelope["resources"]
        for name in (item["resource_key"], *item.get("aliases", []))
    }
    child_ids = {item["child_id"] for item in graph}
    for child in graph:
        if not set(child["requirements"]).issubset(requirement_ids):
            raise InterventionRequired(
                "graph adds requirement coverage outside envelope"
            )
        for touch in child["touches"]:
            if not any(
                _pattern_within(touch, allowed)
                for allowed in envelope["allowed_touches"]
            ):
                raise InterventionRequired(f"graph touch expands scope: {touch}")
        if not {resource.casefold() for resource in child["resources"]}.issubset(
            resource_keys
        ):
            raise InterventionRequired("graph adds a resource outside envelope")
        if not set(child["depends_on"]).issubset(child_ids):
            raise ContextError("graph dependency references an unknown child")


def _validate_requirements(value: object, context: bool) -> list[dict[str, object]]:
    fields = _CONTEXT_REQUIREMENT_FIELDS if context else _REQUIREMENT_FIELDS
    items = _mapping_list(value, "requirements")
    result = []
    seen = set()
    for raw in items:
        item = _exact_object(raw, fields, "requirement")
        requirement_id = _required_text(item["requirement_id"], "requirement_id")
        if requirement_id in seen:
            raise ContextError(f"duplicate requirement: {requirement_id}")
        seen.add(requirement_id)
        if not isinstance(item["required"], bool):
            raise ContextError("requirement required flag must be boolean")
        item["acceptance"] = _list_value(item["acceptance"], "acceptance")
        item["dependencies"] = _text_list(item["dependencies"], "dependencies")
        if context:
            item["revision"] = _positive_int(item["revision"], "revision")
            item["coverage_state"] = _required_text(
                item["coverage_state"], "coverage_state"
            )
            if item["coverage_state"] not in {
                "uncovered",
                "in_progress",
                "blocked",
                "covered",
                "omitted",
            }:
                raise ContextError("coverage_state is invalid")
            if item["required"] and item["coverage_state"] == "omitted":
                raise InterventionRequired("required coverage cannot be omitted")
        result.append(item)
    if not result:
        raise ContextError("requirements must not be empty")
    return result


def _validate_graph(value: object) -> list[dict[str, object]]:
    items = _mapping_list(value, "graph")
    result = []
    seen = set()
    for raw in items:
        item = _exact_object(raw, _GRAPH_NODE_FIELDS, "graph node")
        child_id = _required_text(item["child_id"], "child_id")
        if child_id in seen:
            raise ContextError(f"duplicate graph child: {child_id}")
        seen.add(child_id)
        item["requirements"] = _text_list(item["requirements"], "requirements")
        item["touches"] = _path_list(item["touches"], "touches")
        item["resources"] = _text_list(item["resources"], "resources")
        item["depends_on"] = _text_list(item["depends_on"], "depends_on")
        result.append(item)
    if not result:
        raise ContextError("graph must not be empty")
    return result


def _validate_resources(value: object) -> list[dict[str, object]]:
    items = _mapping_list(value, "resources")
    result = []
    seen = set()
    for raw in items:
        fields = frozenset(raw)
        if fields == _RESOURCE_FIELDS:
            item = _exact_object(raw, _RESOURCE_FIELDS, "resource")
            item["aliases"] = []
        elif fields == _RESOURCE_ALIAS_FIELDS:
            item = _exact_object(raw, _RESOURCE_ALIAS_FIELDS, "resource")
            item["aliases"] = _text_list(item["aliases"], "resource aliases")
        else:
            item = _exact_object(raw, _RESOURCE_ALIAS_FIELDS, "resource")
        key = _required_text(item["resource_key"], "resource_key")
        normalized_names = {
            key.casefold(),
            *(alias.casefold() for alias in item["aliases"]),
        }
        if len(normalized_names) != len(item["aliases"]) + 1:
            raise ContextError(f"resource aliases repeat their canonical key: {key}")
        if seen.intersection(normalized_names):
            raise ContextError(f"duplicate resource: {key}")
        seen.update(normalized_names)
        item["mode"] = _required_text(item["mode"], "mode")
        if item["mode"] not in {"exclusive", "shared"}:
            raise ContextError("resource mode must be exclusive or shared")
        item["capacity"] = _positive_int(item["capacity"], "capacity")
        if item["mode"] == "exclusive" and item["capacity"] != 1:
            raise ContextError("exclusive resource capacity must be one")
        result.append(item)
    return result


def _validate_context_slices(value: object) -> list[dict[str, object]]:
    items = _mapping_list(value, "context_slices")
    result = []
    seen = set()
    for raw in items:
        item = _exact_object(raw, _SLICE_FIELDS, "context slice")
        slice_id = _required_text(item["slice_id"], "slice_id")
        if slice_id in seen:
            raise ContextError(f"duplicate context slice: {slice_id}")
        seen.add(slice_id)
        for field in ("kind", "source_ref", "digest", "visibility"):
            item[field] = _required_text(item[field], field)
        if item["visibility"] not in {"public", "internal", "secret_ref"}:
            raise ContextError("context slice visibility is invalid")
        if item["visibility"] == "secret_ref":
            if item["excerpt"] is not None:
                raise ContextError("secret references cannot store excerpt contents")
        else:
            item["excerpt"] = _required_text(item["excerpt"], "excerpt")
        result.append(item)
    return result


def _synchronize_requirements(
    connection: sqlite3.Connection,
    run_id: str,
    context: dict[str, object],
    now: str,
) -> None:
    for item in context["requirements"]:
        connection.execute(
            """
            INSERT INTO requirements (
                requirement_id, run_id, requirement_class, acceptance_json,
                dependencies_json, coverage_state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(requirement_id) DO UPDATE SET
                requirement_class = excluded.requirement_class,
                acceptance_json = excluded.acceptance_json,
                dependencies_json = excluded.dependencies_json,
                coverage_state = excluded.coverage_state,
                updated_at = excluded.updated_at
            """,
            (
                item["requirement_id"],
                run_id,
                "required" if item["required"] else "optional",
                _canonical_json(item["acceptance"]),
                _canonical_json(item["dependencies"]),
                item["coverage_state"],
                now,
            ),
        )


def _approved_envelope(
    connection: sqlite3.Connection, request_id: str
) -> tuple[dict[str, object], str]:
    gate = connection.execute(
        "SELECT * FROM gate_requests WHERE gate_id = ? AND kind = 'start'",
        (request_id,),
    ).fetchone()
    if gate is None or gate["response_identity"] is None:
        raise FreshnessError("canonical context requires an approved start request")
    if gate["invalidation_reason"] is not None:
        raise FreshnessError("approved start request has been invalidated")
    parent = connection.execute(
        "SELECT start_gate_ref FROM parent_runs WHERE run_id = ?", (gate["run_id"],)
    ).fetchone()
    if parent is None or parent["start_gate_ref"] != request_id:
        raise FreshnessError("approved start request is no longer the active envelope")
    row = connection.execute(
        "SELECT * FROM envelope_revisions WHERE revision_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        raise ContextError("approved envelope record is missing")
    return json.loads(row["contract_json"]), row["digest"]


def _freshness_token(connection: sqlite3.Connection, run_id: str) -> dict[str, object]:
    context = _latest_context_row(connection)
    if context is None:
        raise FreshnessError("canonical context has not been recorded")
    parent = connection.execute(
        "SELECT epoch, start_gate_ref FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    envelope = connection.execute(
        "SELECT digest FROM envelope_revisions WHERE revision_id = ?",
        (parent["start_gate_ref"],),
    ).fetchone()
    if envelope is None or envelope["digest"] != context["envelope_digest"]:
        raise FreshnessError("canonical context does not match the active envelope")
    return {
        "context_digest": context["digest"],
        "context_revision_id": context["revision_id"],
        "dependency_digest": context["dependency_digest"],
        "envelope_digest": context["envelope_digest"],
        "epoch": parent["epoch"],
        "graph_digest": context["graph_digest"],
        "requirement_digest": context["requirement_digest"],
    }


def _packet_source_base(packet: Mapping[str, object]) -> dict[str, object]:
    """Read the immutable source base from current or legacy packet bytes."""
    return _exact_object(
        packet.get("base"),
        frozenset({"branch", "head", "tree_id"}),
        "packet source base",
    )


def _packet_execution_base(packet: Mapping[str, object]) -> dict[str, object]:
    """Read the child execution base without rewriting legacy packet bytes."""
    value = packet.get("execution_base")
    if value is None:
        source = _packet_source_base(packet)
        return {"head": source["head"], "tree_id": source["tree_id"]}
    return _exact_object(
        value,
        frozenset({"head", "tree_id"}),
        "packet execution base",
    )


def _assert_parent_authorized(
    connection: sqlite3.Connection, run_id: str, boundary: str
) -> None:
    row = connection.execute(
        "SELECT status FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or row["status"] != "authorized":
        status = row["status"] if row is not None else "missing"
        raise FreshnessError(f"{boundary} blocked by parent status: {status}")


def _compare_freshness(
    boundary: str, supplied: Mapping[str, object], current: Mapping[str, object]
) -> None:
    stale = sorted(
        key for key in FRESHNESS_FIELDS if supplied.get(key) != current.get(key)
    )
    if stale:
        raise FreshnessError(f"{boundary} freshness mismatch: {', '.join(stale)}")


def _graph_node(
    graph: Sequence[Mapping[str, object]], child_id: str
) -> dict[str, object]:
    for item in graph:
        if item["child_id"] == child_id:
            return dict(item)
    raise FreshnessError("child is not present in the current graph")


def _validate_commands(value: object) -> dict[str, str]:
    items = _mapping_list(value, "commands")
    result = {}
    for raw in items:
        item = _exact_object(raw, _COMMAND_FIELDS, "command result")
        command = _required_text(item["command"], "command")
        status = _required_text(item["status"], "status")
        if status not in {"passed", "failed", "skipped"}:
            raise ContextError("command status must be passed, failed, or skipped")
        _required_text(item["output_digest"], "output_digest")
        if command in result:
            raise ContextError(f"duplicate command result: {command}")
        result[command] = status
    return result


def _validate_artifacts(
    value: object, allowed: list[str], forbidden: list[str]
) -> None:
    for raw in _mapping_list(value, "artifacts"):
        item = _exact_object(raw, _ARTIFACT_FIELDS, "artifact")
        path = _repo_path(item["path"], "artifact path", allow_pattern=False)
        _required_text(item["digest"], "artifact digest")
        if not any(_path_matches(path, pattern) for pattern in allowed):
            raise ContextError(f"artifact is outside assigned scope: {path}")
        if any(_path_matches(path, pattern) for pattern in forbidden):
            raise ContextError(f"artifact path is explicitly forbidden: {path}")


def _operation_replays(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    kind: str,
    epoch: int,
    input_fingerprint: str,
    outcome: Mapping[str, object],
) -> bool:
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    if row is None:
        return False
    if (
        row["kind"] != kind
        or row["phase"] != "authority_committed"
        or row["epoch"] != epoch
        or row["input_fingerprint"] != input_fingerprint
        or row["output_fingerprint"] != _digest_json(outcome)
        or row["outcome_json"] != _canonical_json(outcome)
    ):
        raise OperationConflict("operation ID was reused with new input or outcome")
    return True


def _insert_committed_operation(
    connection: sqlite3.Connection,
    ledger: ParentLedger,
    *,
    operation_id: str,
    kind: str,
    epoch: int,
    input_fingerprint: str,
    outcome: Mapping[str, object],
    event_type: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO operations (
            operation_id, run_id, kind, phase, epoch, input_fingerprint,
            output_fingerprint, outcome_json, created_at, updated_at
        ) VALUES (?, ?, ?, 'authority_committed', ?, ?, ?, ?, ?, ?)
        """,
        (
            operation_id,
            ledger.run_id,
            kind,
            epoch,
            input_fingerprint,
            _digest_json(outcome),
            _canonical_json(outcome),
            created_at,
            created_at,
        ),
    )
    ledger._insert_event(
        connection,
        operation_id=operation_id,
        event_type=event_type,
        phase="authority_committed",
        epoch=epoch,
        payload=dict(outcome),
        created_at=created_at,
    )


def _assert_start_request_rows(
    connection: sqlite3.Connection,
    *,
    request_id: str,
    envelope_digest: str,
    request_digest: str,
) -> None:
    envelope = connection.execute(
        "SELECT * FROM envelope_revisions WHERE revision_id = ?", (request_id,)
    ).fetchone()
    gate = connection.execute(
        "SELECT * FROM gate_requests WHERE gate_id = ?", (request_id,)
    ).fetchone()
    if (
        envelope is None
        or gate is None
        or envelope["digest"] != envelope_digest
        or gate["input_digest"] != request_digest
    ):
        raise OperationConflict("durable start request rows are inconsistent")


def _latest_context_row(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM context_revisions ORDER BY sequence DESC LIMIT 1"
    ).fetchone()


def _context_record(row: sqlite3.Row) -> dict[str, object]:
    value = dict(row)
    value["context"] = json.loads(value.pop("context_json"))
    return value


def _exact_object(
    value: object, fields: frozenset[str], label: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContextError(f"{label} must be an object")
    normalized = _canonical_value(value)
    actual = set(normalized)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise ContextError(f"{label} fields are invalid: {'; '.join(details)}")
    return normalized


def _canonical_value(value: object) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ContextError(f"value is not canonical JSON: {exc}") from exc


def _list_value(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ContextError(f"{field} must be a list")
    return _canonical_value(value)


def _mapping_list(value: object, field: str) -> list[dict[str, object]]:
    items = _list_value(value, field)
    if not all(isinstance(item, dict) for item in items):
        raise ContextError(f"{field} must contain objects")
    return items


def _text_list(value: object, field: str) -> list[str]:
    items = _list_value(value, field)
    result = [_required_text(item, field) for item in items]
    if len(result) != len(set(result)):
        raise ContextError(f"{field} must not contain duplicates")
    return result


def _path_list(value: object, field: str) -> list[str]:
    items = _text_list(value, field)
    return [_repo_path(item, field, allow_pattern=True) for item in items]


def _repo_path(value: object, field: str, *, allow_pattern: bool) -> str:
    path = _required_text(value, field)
    if "\\" in path:
        raise ContextError(f"{field} must use repository-relative POSIX separators")
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ContextError(f"{field} must be a repository-relative path")
    if not allow_pattern and any(character in path for character in "*?["):
        raise ContextError(f"{field} must not be a path pattern")
    return path


def _path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return PurePosixPath(path).match(pattern)


def _pattern_within(pattern: str, allowed: str) -> bool:
    if pattern == allowed:
        return True
    if allowed.endswith("/**"):
        prefix = allowed[:-3].rstrip("/")
        return pattern.startswith(f"{prefix}/")
    return False


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContextError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextError(f"{field} must be a non-negative integer")
    return value


def _reject_sensitive_fields(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_FIELDS:
                raise ContextError(f"sensitive field is forbidden at {path}.{key}")
            _reject_sensitive_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_fields(item, f"{path}[{index}]")
    elif isinstance(value, str) and (
        _SENSITIVE_ASSIGNMENT.search(value)
        or _SENSITIVE_TOKEN.search(value)
        or "-----BEGIN PRIVATE KEY-----" in value
    ):
        raise ContextError(f"secret-like content is forbidden at {path}")
