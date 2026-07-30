"""Deterministic requirement and resource scheduler for Loop v1."""

from __future__ import annotations

import heapq
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .context import (
    ContextError,
    FreshnessError,
    _approved_envelope,
    _context_record,
    _digest_json,
    _freshness_token,
    _insert_committed_operation,
    _latest_context_row,
    _nonnegative_int,
    _now,
    _required_text,
    record_context_revision,
)
from .ledger import OperationConflict, ParentLedger, WriterLease


class SchedulerError(ContextError):
    """Raised when scheduler input or durable state is inconsistent."""


MAX_PARALLEL = 3
_WORK_ACTIVE_STATES = frozenset({"dispatched", "working", "reviewing"})
_INTEGRATION_ELIGIBLE_STATES = frozenset({"result_validated", "committed"})
_DEPENDENCY_SATISFIED_STATES = frozenset({"committed", "integrated"})


def schedule_ready(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    decision_id: str,
    live_capacity: int,
) -> dict[str, object]:
    """Record one deterministic ready-queue decision and reserve its resources."""
    decision_id = _required_text(decision_id, "decision_id")
    live_capacity = _nonnegative_int(live_capacity, "live_capacity")
    operation_id = f"schedule:{decision_id}"

    with ledger._write_transaction(lease) as connection:
        current = _current_state(connection, ledger.run_id)
        base_input = {
            "decision_id": decision_id,
            "freshness": current["token"],
            "live_capacity": live_capacity,
        }
        replay = _decision_replay(
            connection,
            operation_id=operation_id,
            kind="schedule_decision",
            base_input=base_input,
        )
        if replay is not None:
            return replay

        source_position = ledger._ledger_position(connection)
        graph = current["context"]["graph"]
        requirements = {
            item["requirement_id"]: item for item in current["context"]["requirements"]
        }
        cycles = _graph_cycles(graph, requirements)
        progress = _requirement_progress(connection, current["context"])
        active_rows = connection.execute(
            "SELECT child_id, state FROM child_operations"
        ).fetchall()
        child_states = {row["child_id"]: row["state"] for row in active_rows}
        acquired = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM resource_claims WHERE state = 'acquired' ORDER BY claim_id"
            ).fetchall()
        ]
        reserved_children = {row["child_id"] for row in acquired}
        active_children = {
            child_id
            for child_id, state in child_states.items()
            if state in _WORK_ACTIVE_STATES
        }
        occupied_children = active_children | reserved_children

        ready = _ready_nodes(graph, requirements, child_states, occupied_children)
        ready_order = [item["child_id"] for item in ready]
        claim_plans = {
            item["child_id"]: _claims_for_child(item, current["envelope"])
            for item in ready
        }
        declaration_issues = sorted(
            {issue for claims in claim_plans.values() for issue in claims["issues"]}
        )
        active_serial = any(
            row["resource_key"] == "scheduler:serial" for row in acquired
        )
        serial_reason = None
        if active_serial:
            serial_reason = "active_serial_reservation"
        elif "missing_declarations" in declaration_issues:
            serial_reason = "missing_declarations"
        elif declaration_issues:
            serial_reason = "ambiguous_declarations"

        hard_cap = min(
            MAX_PARALLEL,
            int(current["envelope"]["max_parallel"]),
            int(current["envelope"]["worker_capacity"]),
            live_capacity,
        )
        if serial_reason is not None:
            hard_cap = min(hard_cap, 1)
        available = max(0, hard_cap - len(occupied_children))

        selected: list[str] = []
        selected_claims: list[dict[str, object]] = []
        conflicts: dict[str, list[str]] = {}
        if (
            current["parent_status"] == "authorized"
            and not cycles
            and not progress["unassigned_required"]
        ):
            for child in ready:
                if len(selected) >= available:
                    break
                child_id = child["child_id"]
                plan = list(claim_plans[child_id]["claims"])
                if serial_reason is not None:
                    plan.append(_serial_claim(child_id))
                reasons = _claim_conflicts(plan, [*acquired, *selected_claims])
                if reasons:
                    conflicts[child_id] = reasons
                    continue
                selected.append(child_id)
                selected_claims.extend(plan)

        if current["parent_status"] != "authorized":
            status = "paused"
            reason = f"parent_status:{current['parent_status']}"
        elif cycles:
            status = "paused"
            reason = "dependency_cycle"
        elif progress["unassigned_required"]:
            status = "paused"
            reason = "required_coverage_gap"
        elif selected:
            status = "scheduled"
            reason = None
        elif progress["final_ready"]:
            status = "complete"
            reason = None
        elif occupied_children or available == 0:
            status = "waiting"
            reason = "capacity_or_active_work"
        elif ready_order and conflicts:
            status = "waiting"
            reason = "resource_conflict"
        else:
            status = "paused"
            reason = "dependency_deadlock"

        outcome = {
            "conflicts": conflicts,
            "cycles": cycles,
            "decision_id": decision_id,
            "declaration_issues": declaration_issues,
            "dispatch_count": len(selected),
            "effective_parallel": len(occupied_children) + len(selected),
            "hard_cap": hard_cap,
            "progress": progress,
            "ready_order": ready_order,
            "reason": reason,
            "selected": selected,
            "serial_reason": serial_reason,
            "source_position": source_position,
            "status": status,
        }
        now = _now()
        for index, claim in enumerate(selected_claims):
            connection.execute(
                """
                INSERT INTO resource_claims (
                    claim_id, run_id, child_id, resource_key, mode,
                    capacity, state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'acquired', ?)
                """,
                (
                    f"{decision_id}:{index:04d}",
                    ledger.run_id,
                    claim["child_id"],
                    claim["resource_key"],
                    claim["mode"],
                    claim["capacity"],
                    now,
                ),
            )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="schedule_decision",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(
                {**base_input, "source_position": source_position}
            ),
            outcome=outcome,
            event_type="schedule_decided",
            created_at=now,
        )
        return outcome


def release_child_resources(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    child_id: str,
    reason: str,
) -> dict[str, object]:
    """Release all acquired claims for one child using a stable operation ID."""
    operation_id = _required_text(operation_id, "operation_id")
    child_id = _required_text(child_id, "child_id")
    reason = _required_text(reason, "reason")
    supplied = {"child_id": child_id, "reason": reason}
    with ledger._write_transaction(lease) as connection:
        replay = _simple_replay(
            connection,
            operation_id=operation_id,
            kind="resource_release",
            supplied=supplied,
        )
        if replay is not None:
            return replay
        rows = connection.execute(
            """
            SELECT claim_id FROM resource_claims
            WHERE child_id = ? AND state = 'acquired'
            ORDER BY claim_id
            """,
            (child_id,),
        ).fetchall()
        claim_ids = [row["claim_id"] for row in rows]
        now = _now()
        connection.execute(
            """
            UPDATE resource_claims SET state = 'released', updated_at = ?
            WHERE child_id = ? AND state = 'acquired'
            """,
            (now, child_id),
        )
        outcome = {**supplied, "released_claims": claim_ids}
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="resource_release",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="resources_released",
            created_at=now,
        )
        return outcome


def record_optional_omission(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    requirement_id: str,
    rationale: str,
    impact: str,
) -> dict[str, object]:
    """Record rationale only for a dependency-free optional omission."""
    operation_id = _required_text(operation_id, "operation_id")
    requirement_id = _required_text(requirement_id, "requirement_id")
    rationale = _required_text(rationale, "rationale")
    impact = _required_text(impact, "impact")
    supplied = {
        "impact": impact,
        "rationale": rationale,
        "requirement_id": requirement_id,
    }
    with ledger._write_transaction(lease) as connection:
        replay = _simple_replay(
            connection,
            operation_id=operation_id,
            kind="optional_requirement_omitted",
            supplied=supplied,
        )
        if replay is not None:
            return replay
        current = _current_state(connection, ledger.run_id)
        requirements = {
            item["requirement_id"]: item for item in current["context"]["requirements"]
        }
        requirement = requirements.get(requirement_id)
        if requirement is None:
            raise SchedulerError("optional omission references an unknown requirement")
        if requirement["required"]:
            raise SchedulerError("required coverage cannot be omitted")
        if requirement["coverage_state"] != "omitted":
            raise FreshnessError(
                "canonical context must mark optional coverage omitted first"
            )
        dependents = sorted(
            item["requirement_id"]
            for item in requirements.values()
            if requirement_id in item["dependencies"]
        )
        if dependents:
            raise SchedulerError(
                f"optional requirement still has dependents: {', '.join(dependents)}"
            )
        outcome = {
            **supplied,
            "context_digest": current["token"]["context_digest"],
            "status": "omitted",
        }
        now = _now()
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="optional_requirement_omitted",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(
                {**supplied, "context_digest": outcome["context_digest"]}
            ),
            outcome=outcome,
            event_type="optional_requirement_omitted",
            created_at=now,
        )
        return outcome


def requirement_progress(ledger: ParentLedger) -> dict[str, object]:
    """Calculate product progress from requirement coverage, never child count."""
    connection = ledger._connect(read_only=True)
    try:
        current = _current_state(connection, ledger.run_id)
        return _requirement_progress(connection, current["context"])
    finally:
        connection.close()


def deterministic_integration_selection(ledger: ParentLedger) -> dict[str, object]:
    """Select eligible child results from committed graph/state, not finish time."""
    connection = ledger._connect(read_only=True)
    try:
        current = _current_state(connection, ledger.run_id)
        graph = current["context"]["graph"]
        requirements = {
            item["requirement_id"]: item for item in current["context"]["requirements"]
        }
        cycles = _graph_cycles(graph, requirements)
        if cycles:
            return {"cycles": cycles, "selected": [], "status": "paused"}
        order = _topological_order(graph, requirements)
        states = {
            row["child_id"]: row["state"]
            for row in connection.execute(
                "SELECT child_id, state FROM child_operations"
            ).fetchall()
        }
        nodes = {item["child_id"]: item for item in graph}
        selected = []
        for child_id in order:
            if states.get(child_id) not in _INTEGRATION_ELIGIBLE_STATES:
                continue
            dependencies_ready = all(
                dependency in selected
                or states.get(dependency) in _DEPENDENCY_SATISFIED_STATES
                or _node_coverage_satisfied(nodes[dependency], requirements)
                for dependency in nodes[child_id]["depends_on"]
            )
            if dependencies_ready:
                selected.append(child_id)
        return {"cycles": [], "selected": selected, "status": "ready"}
    finally:
        connection.close()


def repair_or_pause_graph(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    request_id: str,
    revision_id: str,
) -> dict[str, object]:
    """Remove only already-satisfied dependency edges; otherwise record pause."""
    request_id = _required_text(request_id, "request_id")
    revision_id = _required_text(revision_id, "revision_id")
    operation_id = f"scheduler-repair:{revision_id}"
    connection = ledger._connect(read_only=True)
    try:
        existing = connection.execute(
            "SELECT * FROM context_revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if existing is not None:
            return {
                "context_revision": _context_record(existing),
                "removed_edges": _removed_edges_from_reason(existing["reason"]),
                "status": "repaired",
            }
        paused = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if paused is not None:
            outcome = json.loads(paused["outcome_json"])
            if (
                paused["kind"] != "scheduler_graph_pause"
                or paused["phase"] != "authority_committed"
                or outcome.get("request_id") != request_id
                or outcome.get("revision_id") != revision_id
            ):
                raise OperationConflict(
                    "repair revision ID belongs to another scheduler operation"
                )
            return outcome
        current = _current_state(connection, ledger.run_id)
    finally:
        connection.close()

    context = json.loads(json.dumps(current["context"], sort_keys=True))
    requirements = {item["requirement_id"]: item for item in context["requirements"]}
    nodes = {item["child_id"]: item for item in context["graph"]}
    removable = sorted(
        (child["child_id"], dependency)
        for child in context["graph"]
        for dependency in child["depends_on"]
        if _node_coverage_satisfied(nodes[dependency], requirements)
    )
    if removable:
        removed = set(removable)
        for child in context["graph"]:
            child["depends_on"] = [
                dependency
                for dependency in child["depends_on"]
                if (child["child_id"], dependency) not in removed
            ]
        encoded = ",".join(f"{child}<-{dependency}" for child, dependency in removable)
        record = record_context_revision(
            ledger,
            lease,
            request_id=request_id,
            revision_id=revision_id,
            reason=f"scheduler safe dependency repair: {encoded}",
            context=context,
        )
        return {
            "context_revision": record,
            "removed_edges": [list(edge) for edge in removable],
            "status": "repaired",
        }

    supplied = {
        "context_digest": current["token"]["context_digest"],
        "request_id": request_id,
        "revision_id": revision_id,
    }
    with ledger._write_transaction(lease) as write_connection:
        replay = _simple_replay(
            write_connection,
            operation_id=operation_id,
            kind="scheduler_graph_pause",
            supplied=supplied,
        )
        if replay is not None:
            return replay
        cycles = _graph_cycles(context["graph"], requirements)
        outcome = {
            **supplied,
            "cycles": cycles,
            "reason": "dependency_cycle" if cycles else "dependency_deadlock",
            "removed_edges": [],
            "status": "paused",
        }
        now = _now()
        _insert_committed_operation(
            write_connection,
            ledger,
            operation_id=operation_id,
            kind="scheduler_graph_pause",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=outcome,
            event_type="scheduler_graph_paused",
            created_at=now,
        )
        return outcome


def _current_state(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    parent = connection.execute(
        "SELECT * FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if parent is None or parent["start_gate_ref"] is None:
        raise FreshnessError("scheduler requires an active approved envelope")
    envelope, _ = _approved_envelope(connection, parent["start_gate_ref"])
    context_row = _latest_context_row(connection)
    if context_row is None:
        raise FreshnessError("scheduler requires canonical context")
    return {
        "context": json.loads(context_row["context_json"]),
        "envelope": envelope,
        "parent_status": parent["status"],
        "token": _freshness_token(connection, run_id),
    }


def _ready_nodes(
    graph: Sequence[Mapping[str, object]],
    requirements: Mapping[str, Mapping[str, object]],
    child_states: Mapping[str, str],
    occupied_children: set[str],
) -> list[dict[str, object]]:
    nodes = {item["child_id"]: item for item in graph}
    ready = []
    for raw in graph:
        child = dict(raw)
        child_id = child["child_id"]
        if child_id in occupied_children:
            continue
        assigned = [requirements[item] for item in child["requirements"]]
        if not any(
            item["coverage_state"] in {"uncovered", "in_progress"} for item in assigned
        ):
            continue
        assigned_ids = set(child["requirements"])
        requirement_dependencies = {
            dependency
            for item in assigned
            for dependency in item["dependencies"]
            if dependency not in assigned_ids
        }
        if any(
            requirements[dependency]["coverage_state"] != "covered"
            for dependency in requirement_dependencies
        ):
            continue
        if any(
            child_states.get(dependency) not in _DEPENDENCY_SATISFIED_STATES
            and not _node_coverage_satisfied(nodes[dependency], requirements)
            for dependency in child["depends_on"]
        ):
            continue
        ready.append(child)
    return sorted(ready, key=lambda item: _node_key(item, requirements))


def _node_key(
    node: Mapping[str, object], requirements: Mapping[str, Mapping[str, object]]
) -> tuple[object, ...]:
    assigned = [requirements[item] for item in node["requirements"]]
    required_rank = 0 if any(item["required"] for item in assigned) else 1
    return (
        required_rank,
        len(node["depends_on"]),
        min(node["requirements"], default=""),
        node["child_id"],
    )


def _graph_cycles(
    graph: Sequence[Mapping[str, object]],
    requirements: Mapping[str, Mapping[str, object]],
) -> list[list[str]]:
    _, remaining = _topological(graph, requirements)
    return [remaining] if remaining else []


def _topological_order(
    graph: Sequence[Mapping[str, object]],
    requirements: Mapping[str, Mapping[str, object]],
) -> list[str]:
    order, remaining = _topological(graph, requirements)
    return [] if remaining else order


def _topological(
    graph: Sequence[Mapping[str, object]],
    requirements: Mapping[str, Mapping[str, object]],
) -> tuple[list[str], list[str]]:
    nodes = {item["child_id"]: item for item in graph}
    indegree = {child_id: 0 for child_id in nodes}
    outgoing = {child_id: [] for child_id in nodes}
    for child in graph:
        for dependency in child["depends_on"]:
            indegree[child["child_id"]] += 1
            outgoing[dependency].append(child["child_id"])
    heap = [
        (_node_key(nodes[child_id], requirements), child_id)
        for child_id, degree in indegree.items()
        if degree == 0
    ]
    heapq.heapify(heap)
    order = []
    while heap:
        _, child_id = heapq.heappop(heap)
        order.append(child_id)
        for follower in sorted(outgoing[child_id]):
            indegree[follower] -= 1
            if indegree[follower] == 0:
                heapq.heappush(
                    heap, (_node_key(nodes[follower], requirements), follower)
                )
    remaining = sorted(child_id for child_id in nodes if child_id not in order)
    return order, remaining


def _claims_for_child(
    child: Mapping[str, object], envelope: Mapping[str, object]
) -> dict[str, object]:
    child_id = child["child_id"]
    issues = []
    claims = []
    touches = child["touches"]
    resources = child["resources"]
    if not touches or not resources:
        issues.append("missing_declarations")
    for touch in touches:
        if _ambiguous_path(touch):
            issues.append("ambiguous_path_claim")
        claims.append(
            {
                "capacity": 1,
                "child_id": child_id,
                "mode": "exclusive",
                "resource_key": f"path:{touch}",
            }
        )

    lookup: dict[str, Mapping[str, object] | None] = {}
    for resource in envelope["resources"]:
        for name in (resource["resource_key"], *resource.get("aliases", [])):
            normalized = name.casefold()
            lookup[normalized] = resource if normalized not in lookup else None
    normalized_keys = []
    for requested in resources:
        resource = lookup.get(requested.casefold())
        if resource is None:
            issues.append("ambiguous_resource_claim")
            continue
        normalized_keys.append(resource["resource_key"])
        claims.append(
            {
                "capacity": resource["capacity"],
                "child_id": child_id,
                "mode": resource["mode"],
                "resource_key": f"resource:{resource['resource_key']}",
            }
        )
    if len(normalized_keys) != len(set(normalized_keys)):
        issues.append("ambiguous_resource_claim")
        deduplicated = {}
        for claim in claims:
            deduplicated[claim["resource_key"]] = claim
        claims = list(deduplicated.values())
    return {
        "claims": sorted(claims, key=lambda item: item["resource_key"]),
        "issues": sorted(set(issues)),
    }


def _claim_conflicts(
    claims: Sequence[Mapping[str, object]],
    existing: Sequence[Mapping[str, object]],
) -> list[str]:
    reasons = []
    for claim in claims:
        relevant = [item for item in existing if _same_resource(claim, item)]
        if not relevant:
            continue
        if claim["mode"] == "exclusive" or any(
            item["mode"] == "exclusive" for item in relevant
        ):
            reasons.append(f"exclusive:{claim['resource_key']}")
            continue
        capacity = min(
            int(claim["capacity"]), *(int(item["capacity"]) for item in relevant)
        )
        if len(relevant) >= capacity:
            reasons.append(f"capacity:{claim['resource_key']}")
    return sorted(set(reasons))


def _same_resource(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    left_key = str(left["resource_key"])
    right_key = str(right["resource_key"])
    if left_key.startswith("path:") and right_key.startswith("path:"):
        return _path_overlap(left_key[5:], right_key[5:])
    return left_key.casefold() == right_key.casefold()


def _path_overlap(left: str, right: str) -> bool:
    left_root = left[:-3].rstrip("/") if left.endswith("/**") else left
    right_root = right[:-3].rstrip("/") if right.endswith("/**") else right
    return (
        left_root == right_root
        or left_root.startswith(f"{right_root}/")
        or right_root.startswith(f"{left_root}/")
    )


def _ambiguous_path(path: str) -> bool:
    value = path[:-3] if path.endswith("/**") else path
    return any(character in value for character in "*?[")


def _serial_claim(child_id: str) -> dict[str, object]:
    return {
        "capacity": 1,
        "child_id": child_id,
        "mode": "exclusive",
        "resource_key": "scheduler:serial",
    }


def _node_coverage_satisfied(
    node: Mapping[str, object], requirements: Mapping[str, Mapping[str, object]]
) -> bool:
    return bool(node["requirements"]) and all(
        requirements[item]["coverage_state"] == "covered"
        for item in node["requirements"]
    )


def _requirement_progress(
    connection: sqlite3.Connection, context: Mapping[str, object]
) -> dict[str, object]:
    requirements = context["requirements"]
    graph = context["graph"]
    coverage_plan = {
        item["requirement_id"]: sorted(
            child["child_id"]
            for child in graph
            if item["requirement_id"] in child["requirements"]
        )
        for item in requirements
    }
    omissions = _omission_records(connection)
    required_covered = sorted(
        item["requirement_id"]
        for item in requirements
        if item["required"] and item["coverage_state"] == "covered"
    )
    required_unmet = sorted(
        item["requirement_id"]
        for item in requirements
        if item["required"] and item["coverage_state"] != "covered"
    )
    optional_covered = sorted(
        item["requirement_id"]
        for item in requirements
        if not item["required"] and item["coverage_state"] == "covered"
    )
    optional_omitted = sorted(
        item["requirement_id"]
        for item in requirements
        if not item["required"] and item["coverage_state"] == "omitted"
    )
    optional_pending = sorted(
        item["requirement_id"]
        for item in requirements
        if not item["required"] and item["coverage_state"] not in {"covered", "omitted"}
    )
    missing_rationale = sorted(set(optional_omitted) - set(omissions))
    unassigned_required = sorted(
        item["requirement_id"]
        for item in requirements
        if item["required"]
        and item["coverage_state"] != "covered"
        and not coverage_plan[item["requirement_id"]]
    )
    return {
        "coverage_plan": coverage_plan,
        "final_ready": not required_unmet
        and not optional_pending
        and not missing_rationale,
        "optional_covered": optional_covered,
        "optional_omissions": [
            omissions[item] for item in optional_omitted if item in omissions
        ],
        "optional_pending": optional_pending,
        "omission_missing_rationale": missing_rationale,
        "required_covered": required_covered,
        "required_total": sum(1 for item in requirements if item["required"]),
        "required_unmet": required_unmet,
        "unassigned_required": unassigned_required,
    }


def _omission_records(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
    result = {}
    rows = connection.execute(
        """
        SELECT outcome_json FROM operations
        WHERE kind = 'optional_requirement_omitted'
          AND phase = 'authority_committed'
        ORDER BY operation_id
        """
    ).fetchall()
    for row in rows:
        outcome = json.loads(row["outcome_json"])
        result[outcome["requirement_id"]] = {
            "impact": outcome["impact"],
            "rationale": outcome["rationale"],
            "requirement_id": outcome["requirement_id"],
        }
    return result


def _decision_replay(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    kind: str,
    base_input: Mapping[str, object],
) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    if row is None:
        return None
    outcome = json.loads(row["outcome_json"])
    expected = _digest_json(
        {**base_input, "source_position": outcome.get("source_position")}
    )
    if (
        row["kind"] != kind
        or row["phase"] != "authority_committed"
        or row["input_fingerprint"] != expected
    ):
        raise OperationConflict("schedule decision ID was reused with new input")
    return outcome


def _simple_replay(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    kind: str,
    supplied: Mapping[str, object],
) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    if row is None:
        return None
    outcome = json.loads(row["outcome_json"])
    if (
        row["kind"] != kind
        or row["phase"] != "authority_committed"
        or any(outcome.get(key) != value for key, value in supplied.items())
    ):
        raise OperationConflict("operation ID was reused with new input")
    return outcome


def _removed_edges_from_reason(reason: str) -> list[list[str]]:
    prefix = "scheduler safe dependency repair: "
    if not reason.startswith(prefix):
        raise OperationConflict("repair revision ID belongs to another context change")
    encoded = reason[len(prefix) :]
    return [item.split("<-", 1) for item in encoded.split(",") if item]
