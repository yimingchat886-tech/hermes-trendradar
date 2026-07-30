#!/usr/bin/env python3
"""Opt-in Trellis v2 state engine."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.io import json_bytes, write_temp_bytes


HARNESS_MODE = "harness_state_machine"
DEFAULT_MODE = "default_trellis"
TASK_JSON = "task.json"
EVENT_LOG = "state-events.jsonl"

INIT_STATES = {
    "parent": "parent_prd_draft",
    "child": "child_plan_draft",
}

BLOCKED_STATES = {
    "parent": "parent_blocked",
    "child": "child_blocked",
}

ARCHIVED_STATES = {
    "parent": "parent_archived",
    "child": "child_archived",
}

CANCELLED_STATES = {
    "parent": "parent_cancelled",
    "child": "child_cancelled",
}

TRANSITIONS = {
    "parent": {
        ("parent_prd_draft", "prd_drafted"): "parent_waiting_completion_signal",
        ("parent_waiting_completion_signal", "parent_completion_signal_received"): "parent_commit_ready",
        ("parent_commit_ready", "parent_commit_created"): "parent_archive_ready",
        ("parent_archive_ready", "parent_archive_completed"): "parent_archived",
    },
    "child": {
        ("child_plan_draft", "plan_drafted"): "child_waiting_completion_signal",
        ("child_waiting_completion_signal", "completion_signal_received"): "child_commit_ready",
        ("child_commit_ready", "commit_created"): "child_archive_ready",
        ("child_archive_ready", "child_archive_completed"): "child_archived",
    },
}

ALL_STATES = {
    kind: {
        INIT_STATES[kind],
        BLOCKED_STATES[kind],
        ARCHIVED_STATES[kind],
        CANCELLED_STATES[kind],
        *mapping.values(),
    }
    for kind, mapping in TRANSITIONS.items()
}


class StateMachineError(Exception):
    """Raised when a state operation is rejected."""


def init_task(task_dir: Path | str, kind: str, *, by: str = "agent", note: str = "") -> dict[str, Any]:
    """Initialize an opt-in harness state machine."""

    if kind not in INIT_STATES:
        raise StateMachineError(f"invalid kind: {kind}")

    task_path, log_path, task = _load_harness_task(task_dir)
    existing = _state(task)
    if existing:
        existing_kind = existing.get("kind")
        if existing_kind == kind:
            return {"changed": False, "state_machine": existing}
        raise StateMachineError(f"state machine already initialized as {existing_kind}")

    now = _now()
    machine = {
        "kind": kind,
        "current_state": INIT_STATES[kind],
        "previous_state": None,
        "last_event": "init",
        "blocked_from_state": None,
        "updated_at": now,
    }
    new_task = _with_state(task, machine)
    entry = _event_entry("init", kind, None, machine["current_state"], by, note, now)
    _write_task_and_log(task_path, new_task, log_path, entry)
    return {"changed": True, "state_machine": machine}


def apply_event(task_dir: Path | str, event: str, *, by: str = "agent", note: str = "") -> dict[str, Any]:
    """Apply one event to an initialized harness state machine."""

    task_path, log_path, task = _load_harness_task(task_dir)
    machine = _state(task)
    if not machine:
        raise StateMachineError("state machine is not initialized")

    kind = machine.get("kind")
    if kind not in TRANSITIONS:
        raise StateMachineError(f"invalid state machine kind: {kind}")

    current = machine.get("current_state")
    if current not in ALL_STATES[kind]:
        raise StateMachineError(f"invalid current state: {current}")

    next_state, blocked_from = _next_state(kind, current, event, machine.get("blocked_from_state"))
    now = _now()
    new_machine = dict(machine)
    new_machine.update(
        {
            "current_state": next_state,
            "previous_state": current,
            "last_event": event,
            "blocked_from_state": blocked_from,
            "updated_at": now,
        }
    )
    new_task = _with_state(task, new_machine)
    entry = _event_entry(event, kind, current, next_state, by, note, now)
    _write_task_and_log(task_path, new_task, log_path, entry)
    return {"changed": True, "state_machine": new_machine}


def cancel_task(
    task_dir: Path | str,
    *,
    by: str,
    cancellation: dict[str, Any],
) -> dict[str, Any]:
    """Atomically record one terminal cancellation event and task snapshot."""

    task_path, log_path, task = _load_harness_task(task_dir)
    reason = str(cancellation.get("reason") or "").strip()
    authorized_at = str(cancellation.get("authorized_at") or "").strip()
    if not by.strip() or not reason or not authorized_at:
        raise StateMachineError("cancellation actor, reason, and authorized_at are required")

    tier = task.get("tier")
    machine = _state(task)
    previous_state: str | None = None
    current_state = "cancelled"
    new_task = dict(task)
    if task.get("status") == "completed":
        raise StateMachineError("cannot cancel a completed task")

    if tier in CANCELLED_STATES:
        if not machine or machine.get("kind") != tier:
            raise StateMachineError(f"{tier} state machine is not initialized")
        previous_state = machine.get("current_state")
        if previous_state not in ALL_STATES[tier]:
            raise StateMachineError(f"invalid current state: {previous_state}")
        if previous_state == ARCHIVED_STATES[tier]:
            raise StateMachineError("cannot cancel an archived task")
        current_state = CANCELLED_STATES[tier]
        if task.get("status") == "cancelled" and previous_state != current_state:
            raise StateMachineError("top-level cancelled status lacks terminal cancellation state")
        if previous_state == current_state:
            if task.get("status") == "cancelled" and task.get("cancellation") == cancellation:
                return {"changed": False, "state_machine": machine}
            raise StateMachineError("cancelled task metadata conflicts with its terminal state")

        new_machine = dict(machine)
        new_machine.update(
            {
                "current_state": current_state,
                "previous_state": previous_state,
                "last_event": "task_cancelled",
                "blocked_from_state": None,
                "updated_at": authorized_at,
            }
        )
        new_task = _with_state(new_task, new_machine)
    elif tier == "light":
        if task.get("status") == "cancelled":
            if task.get("cancellation") == cancellation:
                return {"changed": False, "state_machine": None}
            raise StateMachineError("cancelled task metadata conflicts with the request")
        new_machine = None
    else:
        raise StateMachineError(f"unsupported task tier: {tier}")

    new_task["status"] = "cancelled"
    new_task["completedAt"] = None
    new_task["cancelledAt"] = authorized_at[:10]
    new_task["cancellation"] = cancellation

    entry = _event_entry(
        "task_cancelled",
        str(tier),
        previous_state,
        current_state,
        by,
        reason,
        authorized_at,
    )
    entry["reason"] = reason
    for field in ("authorized_by", "superseded_by", "rtm_disposition", "rtm_ids"):
        value = cancellation.get(field)
        if value not in (None, "", []):
            entry[field] = value
    _write_task_and_log(task_path, new_task, log_path, entry)
    return {"changed": True, "state_machine": new_machine}


def status(task_dir: Path | str) -> dict[str, Any]:
    """Return current state metadata without mutating files."""

    _, log_path, task = _load_harness_task(task_dir)
    machine = _state(task)
    if not machine:
        raise StateMachineError("state machine is not initialized")
    event_count = 0
    if log_path.exists():
        event_count = len([line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()])
    return {"state_machine": machine, "event_count": event_count}


def archive_transition_events(kind: str, current: str) -> list[str]:
    """Return the canonical events needed to reach the archived state."""

    if kind not in TRANSITIONS:
        raise StateMachineError(f"invalid state machine kind: {kind}")
    archived = ARCHIVED_STATES[kind]
    if current == archived:
        return []
    if current not in ALL_STATES[kind] or current in {
        BLOCKED_STATES[kind],
        CANCELLED_STATES[kind],
    }:
        raise StateMachineError(f"cannot archive {kind} from state: {current}")

    events: list[str] = []
    seen: set[str] = set()
    while current != archived:
        if current in seen:
            raise StateMachineError(f"archive transition cycle from state: {current}")
        seen.add(current)
        candidates = [
            (event, next_state)
            for (state, event), next_state in TRANSITIONS[kind].items()
            if state == current
        ]
        if len(candidates) != 1:
            raise StateMachineError(f"cannot archive {kind} from state: {current}")
        event, current = candidates[0]
        events.append(event)
        if current not in ALL_STATES[kind]:
            raise StateMachineError(f"archive transition reaches invalid state: {current}")
    return events


def _load_harness_task(task_dir: Path | str) -> tuple[Path, Path, dict[str, Any]]:
    task_path = Path(task_dir) / TASK_JSON
    log_path = Path(task_dir) / EVENT_LOG
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateMachineError(f"missing {TASK_JSON}") from exc
    except json.JSONDecodeError as exc:
        raise StateMachineError(f"invalid {TASK_JSON}") from exc

    mode = (task.get("meta") or {}).get("workflow_mode", DEFAULT_MODE)
    if mode != HARNESS_MODE:
        raise StateMachineError(f"workflow_mode must be {HARNESS_MODE}, got {mode}")
    return task_path, log_path, task


def _state(task: dict[str, Any]) -> dict[str, Any] | None:
    state = (task.get("meta") or {}).get("state_machine")
    return state if isinstance(state, dict) else None


def _with_state(task: dict[str, Any], machine: dict[str, Any]) -> dict[str, Any]:
    new_task = dict(task)
    meta = dict(new_task.get("meta") or {})
    meta["state_machine"] = machine
    new_task["meta"] = meta
    return new_task


def _next_state(kind: str, current: str, event: str, blocked_from: Any) -> tuple[str, str | None]:
    blocked_state = BLOCKED_STATES[kind]
    cancelled_state = CANCELLED_STATES[kind]
    if event == "blocker_opened":
        if current == blocked_state:
            raise StateMachineError("blocker is already open")
        if current in {ARCHIVED_STATES[kind], cancelled_state}:
            raise StateMachineError("cannot open blocker from terminal state")
        return blocked_state, current

    if event == "blocker_resolved":
        if current != blocked_state:
            raise StateMachineError("no blocker is open")
        if not isinstance(blocked_from, str) or blocked_from not in ALL_STATES[kind] or blocked_from == blocked_state:
            raise StateMachineError("blocked_from_state is missing or invalid")
        return blocked_from, None

    if current == blocked_state:
        raise StateMachineError("only blocker_resolved is valid while blocked")
    if current == cancelled_state:
        raise StateMachineError("cancelled state is terminal")

    try:
        return TRANSITIONS[kind][(current, event)], None
    except KeyError as exc:
        raise StateMachineError(f"invalid transition: {current} + {event}") from exc


def _write_task_and_log(
    task_path: Path,
    task: dict[str, Any],
    log_path: Path,
    event: dict[str, Any],
) -> None:
    old_log_exists = log_path.exists()
    old_log = log_path.read_bytes() if old_log_exists else b""
    new_log = _appended_log(old_log, event)
    task_bytes = json_bytes(task)

    log_tmp = _write_temp(log_path, new_log)
    task_tmp = _write_temp(task_path, task_bytes)
    log_replaced = False
    try:
        os.replace(log_tmp, log_path)
        log_replaced = True
        os.replace(task_tmp, task_path)
    except OSError as exc:
        if log_replaced:
            _restore_log(log_path, old_log, old_log_exists)
        raise StateMachineError(f"write failed: {exc}") from exc
    finally:
        _unlink_if_exists(log_tmp)
        _unlink_if_exists(task_tmp)


def _restore_log(log_path: Path, old_log: bytes, old_log_exists: bool) -> None:
    if old_log_exists:
        restore_tmp = _write_temp(log_path, old_log)
        try:
            os.replace(restore_tmp, log_path)
        finally:
            _unlink_if_exists(restore_tmp)
    else:
        _unlink_if_exists(log_path)


def _write_temp(path: Path, data: bytes) -> Path:
    return write_temp_bytes(path, data)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _appended_log(old_log: bytes, event: dict[str, Any]) -> bytes:
    line = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    if old_log and not old_log.endswith(b"\n"):
        return old_log + b"\n" + line
    return old_log + line


def _event_entry(
    event: str,
    kind: str,
    previous_state: str | None,
    current_state: str,
    by: str,
    note: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "event": event,
        "kind": kind,
        "previous_state": previous_state,
        "current_state": current_state,
        "by": by,
        "note": note,
        "created_at": created_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
