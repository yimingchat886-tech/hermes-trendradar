"""Formal cancellation for a Loop parent before runtime initialization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from common.io import read_json


AUTHORITY_KIND = "direct_user_uninitialized"
EVENT_LOG = "state-events.jsonl"
TASK_JSON = "task.json"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_CANCELLATION_FIELDS = {
    "authority_kind",
    "authorized_at",
    "authorized_by",
    "pre_admission",
    "preserved",
    "reason",
    "retained_files",
    "rtm_disposition",
    "rtm_ids",
    "run_id",
    "superseded_by",
}
_EVENT_FIELDS = {
    "authority_kind",
    "authorized_by",
    "by",
    "created_at",
    "current_state",
    "event",
    "kind",
    "note",
    "pre_admission",
    "previous_state",
    "reason",
    "rtm_disposition",
    "rtm_ids",
    "run_id",
    "superseded_by",
}


class PreAdmissionCancellationError(Exception):
    """Raised when a task is not an exact uninitialized Loop parent."""


def cancel_pre_admission_parent(
    task_dir: Path,
    repo_root: Path,
    *,
    actor: str,
    reason: str,
    superseded_by: str | None,
) -> bool:
    """Atomically write one formal cancellation, or accept its exact replay."""
    actor = _input_text(actor, "actor")
    reason = _input_text(reason, "reason")
    if superseded_by is not None:
        superseded_by = _input_text(superseded_by, "superseded_by")

    initial = read_json(task_dir / TASK_JSON)
    run_id = _run_id(initial)
    initial_errors = (
        _replay_errors(
            task_dir,
            initial,
            repo_root,
            actor=actor,
            reason=reason,
            superseded_by=superseded_by,
        )
        if initial.get("status") == "cancelled"
        else _eligibility_errors(task_dir, initial, repo_root)
    )
    if initial_errors:
        raise PreAdmissionCancellationError("; ".join(initial_errors))

    from loop_v1.orchestrator import _operator_lock

    with _operator_lock(Path(repo_root).resolve(), run_id, create=True):
        task_path = task_dir / TASK_JSON
        task = read_json(task_path)
        if not isinstance(task, dict) or task.get("id") != run_id:
            raise PreAdmissionCancellationError(
                "Loop parent task identity changed before cancellation lock"
            )
        if task.get("status") == "cancelled":
            errors = _replay_errors(
                task_dir,
                task,
                repo_root,
                actor=actor,
                reason=reason,
                superseded_by=superseded_by,
            )
            if errors:
                raise PreAdmissionCancellationError("; ".join(errors))
            return False

        errors = _eligibility_errors(task_dir, task, repo_root)
        if errors:
            raise PreAdmissionCancellationError("; ".join(errors))

        authorized_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        cancellation = {
            "authority_kind": AUTHORITY_KIND,
            "authorized_at": authorized_at,
            "authorized_by": actor,
            "pre_admission": True,
            "preserved": {
                field: task.get(field)
                for field in ("branch", "worktree_path", "commit", "children", "parent")
            },
            "reason": reason,
            "retained_files": _retained_files(task_dir),
            "rtm_disposition": None,
            "rtm_ids": [],
            "run_id": run_id,
            "superseded_by": superseded_by,
        }
        cancelled = dict(task)
        cancelled.update(
            {
                "status": "cancelled",
                "completedAt": None,
                "cancelledAt": authorized_at[:10],
                "cancellation": cancellation,
            }
        )
        event = {
            "authority_kind": AUTHORITY_KIND,
            "authorized_by": actor,
            "by": actor,
            "created_at": authorized_at,
            "current_state": "parent_cancelled",
            "event": "task_cancelled",
            "kind": "parent",
            "note": reason,
            "pre_admission": True,
            "previous_state": None,
            "reason": reason,
            "rtm_disposition": None,
            "rtm_ids": [],
            "run_id": run_id,
            "superseded_by": superseded_by,
        }

        from state_machine import _write_task_and_log

        _write_task_and_log(task_path, cancelled, task_dir / EVENT_LOG, event)
        return True


def _replay_errors(
    task_dir: Path,
    task: dict[str, Any],
    repo_root: Path,
    *,
    actor: str,
    reason: str,
    superseded_by: str | None,
) -> list[str]:
    errors = pre_admission_cancellation_errors(task_dir, task, repo_root)
    cancellation = task.get("cancellation")
    if isinstance(cancellation, dict) and any(
        cancellation.get(field) != value
        for field, value in {
            "authorized_by": actor,
            "reason": reason,
            "superseded_by": superseded_by,
        }.items()
    ):
        errors.append("cancelled Loop parent metadata conflicts with the request")
    return errors


def pre_admission_cancellation_errors(
    task_dir: Path,
    task: dict[str, Any],
    repo_root: Path,
) -> list[str]:
    """Validate the complete formal pre-admission cancellation projection."""
    errors = _identity_errors(task_dir, task, repo_root, allow_archived=True)
    if task.get("tier") != "parent":
        errors.append("pre-admission cancellation requires a parent task")
    if (task.get("meta") or {}).get("workflow_mode") != "loop_v1":
        errors.append("pre-admission cancellation requires loop_v1 workflow_mode")
    if isinstance(task.get("meta"), dict) and "state_machine" in task["meta"]:
        errors.append("pre-admission cancellation must not declare HSM metadata")
    if task.get("status") != "cancelled":
        errors.append("pre-admission cancellation status is not cancelled")
    errors.extend(_relationship_errors(task))
    errors.extend(_runtime_residue_errors(repo_root, task.get("id")))

    cancellation = task.get("cancellation")
    if not isinstance(cancellation, dict):
        errors.append("pre-admission cancellation metadata is missing")
        return errors
    if set(cancellation) != _CANCELLATION_FIELDS:
        errors.append("pre-admission cancellation metadata fields do not match")

    run_id = task.get("id")
    for field in ("authorized_at", "authorized_by", "reason"):
        if not _is_nonempty_text(cancellation.get(field)):
            errors.append(f"pre-admission cancellation {field} must be a non-empty string")
    superseded_by = cancellation.get("superseded_by")
    if superseded_by is not None and not _is_nonempty_text(superseded_by):
        errors.append(
            "pre-admission cancellation superseded_by must be null or a non-empty string"
        )
    if cancellation.get("authority_kind") != AUTHORITY_KIND:
        errors.append("pre-admission cancellation authority_kind is invalid")
    if cancellation.get("pre_admission") is not True:
        errors.append("pre-admission cancellation marker is missing")
    if cancellation.get("run_id") != run_id:
        errors.append("pre-admission cancellation run identity conflicts")
    if cancellation.get("rtm_disposition") is not None or cancellation.get("rtm_ids") != []:
        errors.append("pre-admission cancellation must not contain RTM disposition")
    if cancellation.get("preserved") != {
        field: task.get(field)
        for field in ("branch", "worktree_path", "commit", "children", "parent")
    }:
        errors.append("pre-admission cancellation preserved relationships conflict")
    if cancellation.get("retained_files") != _retained_files(task_dir):
        errors.append("pre-admission cancellation retained_files conflict")

    authorized_at = cancellation.get("authorized_at")
    if isinstance(authorized_at, str) and _valid_utc_timestamp(authorized_at):
        if task.get("cancelledAt") != authorized_at[:10]:
            errors.append("pre-admission cancellation terminal date conflicts")
    elif _is_nonempty_text(authorized_at):
        errors.append("pre-admission cancellation authorized_at is invalid")
    if task.get("completedAt") is not None:
        errors.append("pre-admission cancellation completedAt must be null")

    events = _event_entries(task_dir / EVENT_LOG, errors)
    if len(events) != 1 or events[0].get("event") != "task_cancelled":
        errors.append("pre-admission cancellation requires exactly one task_cancelled event")
    elif set(events[0]) != _EVENT_FIELDS:
        errors.append("pre-admission cancellation event fields do not match")
    elif any(
        events[0].get(field) != value
        for field, value in {
            "authority_kind": AUTHORITY_KIND,
            "authorized_by": cancellation.get("authorized_by"),
            "by": cancellation.get("authorized_by"),
            "created_at": authorized_at,
            "current_state": "parent_cancelled",
            "kind": "parent",
            "note": cancellation.get("reason"),
            "pre_admission": True,
            "previous_state": None,
            "reason": cancellation.get("reason"),
            "rtm_disposition": None,
            "rtm_ids": [],
            "run_id": run_id,
            "superseded_by": superseded_by,
        }.items()
    ):
        errors.append("pre-admission cancellation event conflicts with task metadata")
    return errors


def _eligibility_errors(
    task_dir: Path,
    task: dict[str, Any],
    repo_root: Path,
) -> list[str]:
    errors = _identity_errors(task_dir, task, repo_root)
    if task.get("tier") != "parent":
        errors.append("pre-admission cancellation requires a parent task")
    if task.get("status") != "planning":
        errors.append("pre-admission cancellation requires planning status")
    if (task.get("meta") or {}).get("workflow_mode") != "loop_v1":
        errors.append("pre-admission cancellation requires loop_v1 workflow_mode")
    if isinstance(task.get("meta"), dict) and "state_machine" in task["meta"]:
        errors.append("pre-admission cancellation rejects HSM metadata")
    errors.extend(_relationship_errors(task))
    errors.extend(_runtime_residue_errors(repo_root, task.get("id")))
    if task.get("cancellation") is not None or task.get("cancelledAt") is not None:
        errors.append("pre-admission cancellation residue is present")
    event_path = task_dir / EVENT_LOG
    if event_path.exists() and event_path.read_bytes():
        errors.append("pre-admission event residue is present")
    return errors


def _identity_errors(
    task_dir: Path,
    task: dict[str, Any],
    repo_root: Path,
    *,
    allow_archived: bool = False,
) -> list[str]:
    errors: list[str] = []
    root = Path(repo_root).resolve()
    tasks_root = (root / ".trellis" / "tasks").resolve()
    target = Path(task_dir).resolve()
    active = target.parent == tasks_root
    archived = target.parent.parent == (tasks_root / "archive").resolve()
    if not active and not (allow_archived and archived):
        errors.append("pre-admission task location is not supported in this repository")

    run_id = task.get("id")
    if not _is_canonical_run_id(run_id):
        errors.append("pre-admission task run identity is invalid")
        return errors
    if task.get("name") != run_id:
        errors.append("pre-admission task id and name differ")

    matches = []
    if tasks_root.is_dir():
        for path in tasks_root.rglob(TASK_JSON):
            candidate = read_json(path)
            if isinstance(candidate, dict) and candidate.get("id") == run_id:
                matches.append(path.parent.resolve())
    if len(matches) != 1 or matches[0] != target:
        errors.append("pre-admission task/run identity is not unique")
    return errors


def _relationship_errors(task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if task.get("children") not in (None, []):
        errors.append("pre-admission parent has linked children")
    if task.get("subtasks") not in (None, []):
        errors.append("pre-admission parent has linked subtasks")
    if task.get("parent") is not None:
        errors.append("pre-admission parent has a parent link")
    for field in ("branch", "worktree_path", "commit", "pr_url"):
        if task.get(field) not in (None, ""):
            errors.append(f"pre-admission parent has Git relationship: {field}")
    return errors


def _runtime_residue_errors(repo_root: Path, run_id: object) -> list[str]:
    if not _is_canonical_run_id(run_id):
        return []
    run_root = (
        Path(repo_root).resolve()
        / ".trellis"
        / ".runtime"
        / "loop-v1"
        / "parents"
        / str(run_id)
    )
    conflicts = [path.name for path in run_root.glob("ledger.sqlite3*") if path.exists()]
    if (run_root / "operator-state.json").exists():
        conflicts.append("operator-state.json")
    return (
        [f"pre-admission runtime authority residue exists: {', '.join(sorted(conflicts))}"]
        if conflicts
        else []
    )


def _event_entries(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append("pre-admission cancellation event log is invalid")
            return []
        if not isinstance(value, dict):
            errors.append("pre-admission cancellation event is not an object")
            return []
        entries.append(value)
    return entries


def _retained_files(task_dir: Path) -> list[str]:
    return sorted(
        path.relative_to(task_dir).as_posix()
        for path in task_dir.rglob("*")
        if path.is_file()
        and path.relative_to(task_dir).as_posix() not in {TASK_JSON, EVENT_LOG}
    )


def _run_id(task: object) -> str:
    if not isinstance(task, dict) or not _is_canonical_run_id(task.get("id")):
        raise PreAdmissionCancellationError("Loop parent run identity is invalid")
    return str(task["id"])


def _is_canonical_run_id(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value not in {".", ".."}
        and all(component not in {"", ".", ".."} for component in value.split("/"))
        and path.as_posix() == value
        and len(path.parts) == 1
        and _SAFE_RUN_ID.fullmatch(value) is not None
    )


def _input_text(value: object, field: str) -> str:
    if not _is_nonempty_text(value):
        raise PreAdmissionCancellationError(f"{field} must be a non-empty string")
    return value.strip()


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_utc_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return value.endswith("Z") and parsed.tzinfo == timezone.utc
