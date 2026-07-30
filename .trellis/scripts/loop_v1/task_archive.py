"""Supported physical archive surfaces for terminal Loop task evidence."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from common.active_task import clear_task_from_sessions
from common.archive_transaction import (
    archive_paths_transaction,
    assert_archive_transactions_resolved,
)

from .ledger import ledger_path
from .pre_admission import pre_admission_cancellation_errors
from .task_projection import _digest_bytes, _task_matches, projection_status


class TaskArchiveError(RuntimeError):
    """Raised when Loop task evidence cannot be archived exactly."""


def _task_record(path: Path) -> tuple[dict[str, object], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskArchiveError("Loop task evidence is invalid") from exc
    if not isinstance(value, dict):
        raise TaskArchiveError("Loop task evidence is not an object")
    return value, _digest_bytes(payload)


def _single_task_path(repo_root: Path, run_id: str) -> Path:
    matches = _task_matches(repo_root, run_id)
    if len(matches) != 1:
        detail = "missing" if not matches else "ambiguous"
        raise TaskArchiveError(f"Loop task evidence location is {detail}")
    return matches[0]


def _is_archived(repo_root: Path, task_dir: Path) -> bool:
    return task_dir.parent.parent == (
        repo_root / ".trellis" / "tasks" / "archive"
    ).resolve()


def _destination(repo_root: Path, task_dir: Path) -> Path:
    return (
        repo_root
        / ".trellis"
        / "tasks"
        / "archive"
        / datetime.now().strftime("%Y-%m")
        / task_dir.name
    )


def _transaction_id(kind: str, values: Mapping[str, object]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{kind}-{sha256(payload).hexdigest()[:32]}"


def _result(
    *,
    run_id: str,
    task_digest: str,
    location: Path,
    repo_root: Path,
    transaction_id: str,
    replayed: bool,
    authority_digest: str | None = None,
) -> dict[str, object]:
    return {
        "authority_digest": authority_digest,
        "location": location.relative_to(repo_root).as_posix(),
        "phase": "committed",
        "replayed": replayed,
        "run_id": run_id,
        "task_digest": task_digest,
        "transaction_id": transaction_id,
    }


def archive_pre_admission_parent(
    repo_root: Path,
    run_id: str,
    *,
    expected_task_digest: str,
    commit_enabled: bool = True,
) -> dict[str, object]:
    """Physically archive one formally cancelled, uninitialized Loop parent."""
    root = Path(repo_root).resolve()
    assert_archive_transactions_resolved(root)
    task_path = _single_task_path(root, run_id)
    task_dir = task_path.parent.resolve()
    task, task_digest = _task_record(task_path)
    if task_digest != expected_task_digest:
        raise TaskArchiveError("pre-admission task digest changed")
    errors = pre_admission_cancellation_errors(task_dir, task, root)
    if errors:
        raise TaskArchiveError("; ".join(errors))
    transaction_id = _transaction_id(
        "loop-pre-admission",
        {"run_id": run_id, "task_digest": task_digest},
    )
    if _is_archived(root, task_dir):
        return _result(
            run_id=run_id,
            task_digest=task_digest,
            location=task_dir,
            repo_root=root,
            transaction_id=transaction_id,
            replayed=True,
        )
    if task_dir.parent != (root / ".trellis" / "tasks").resolve():
        raise TaskArchiveError("pre-admission task is not in the active task root")
    if ledger_path(root, run_id).is_file():
        raise TaskArchiveError("pre-admission archive rejects an initialized ledger")

    destination = _destination(root, task_dir)
    clear_task_from_sessions(str(task_dir), root)
    archive_paths_transaction(
        root,
        transaction_id=transaction_id,
        moves=[(task_dir, destination)],
        commit_message=f"chore(task): archive pre-admission Loop parent {run_id}",
        commit_enabled=commit_enabled,
        family_root=run_id,
    )
    return _result(
        run_id=run_id,
        task_digest=task_digest,
        location=destination,
        repo_root=root,
        transaction_id=transaction_id,
        replayed=False,
    )


def retire_task_evidence(
    repo_root: Path,
    run_id: str,
    *,
    expected_authority_digest: str,
    expected_task_digest: str,
    operator_state: Mapping[str, object],
    commit_enabled: bool = True,
) -> dict[str, object]:
    """Archive one current terminal projection while retaining Loop runtime facts."""
    root = Path(repo_root).resolve()
    assert_archive_transactions_resolved(root)
    if operator_state.get("pending_action") is not None:
        raise TaskArchiveError("Loop task evidence retirement requires no pending action")
    status = projection_status(root, run_id)
    if status.get("status") != "current":
        raise TaskArchiveError(
            f"Loop terminal task projection is not current: {status.get('status')}"
        )
    if status.get("authority_digest") != expected_authority_digest:
        raise TaskArchiveError("Loop task retirement authority digest changed")
    if status.get("task_digest") != expected_task_digest:
        raise TaskArchiveError("Loop task retirement task digest changed")

    task_path = root / str(status["location"])
    task_dir = task_path.parent.resolve()
    task, task_digest = _task_record(task_path)
    if task_digest != expected_task_digest:
        raise TaskArchiveError("Loop task retirement CAS changed before archive")
    month = (
        task_dir.parent.name
        if _is_archived(root, task_dir)
        else datetime.now().strftime("%Y-%m")
    )
    archive_root = root / ".trellis" / "tasks" / "archive" / month
    moves: list[tuple[Path, Path]] = []
    child_identities = []
    for child in status.get("child_projections") or []:
        if not isinstance(child, dict) or child.get("status") != "current":
            raise TaskArchiveError("Loop child terminal projection is not current")
        child_id = child.get("child_id")
        location = child.get("location")
        if not isinstance(child_id, str) or not isinstance(location, str):
            raise TaskArchiveError("Loop child terminal projection is invalid")
        child_path = root / location
        child_dir = child_path.parent.resolve()
        child_task, child_digest = _task_record(child_path)
        projection = (
            (child_task.get("meta") or {}).get("loop_v1_child_terminal")
            if isinstance(child_task.get("meta"), dict)
            else None
        )
        if (
            child_digest != child.get("task_digest")
            or child_task.get("id") != child_id
            or child_task.get("name") != child_id
            or child_task.get("tier") != "child"
            or child_task.get("parent") != task_dir.name
            or not isinstance(projection, dict)
            or projection.get("authority_digest") != expected_authority_digest
            or projection.get("child_id") != child_id
            or projection.get("parent_run_id") != run_id
            or projection.get("ledger_state") != child.get("ledger_state")
        ):
            raise TaskArchiveError(
                f"Loop child terminal projection conflicts: {child_id}"
            )
        if _is_archived(root, child_dir):
            if child_dir.parent.name != month:
                raise TaskArchiveError(
                    f"Loop task family spans archive months: {child_id}"
                )
        elif child_dir.parent == (root / ".trellis" / "tasks").resolve():
            destination = archive_root / child_dir.name
            if destination.exists():
                raise TaskArchiveError(
                    f"Loop child archive destination exists: {child_id}"
                )
            moves.append((child_dir, destination))
        else:
            raise TaskArchiveError(
                f"Loop child task is outside the active/archive roots: {child_id}"
            )
        child_identities.append(
            {"child_id": child_id, "task_digest": child_digest}
        )
    transaction_id = _transaction_id(
        "loop-retire-family",
        {
            "authority_digest": expected_authority_digest,
            "children": child_identities,
            "run_id": run_id,
            "task_digest": expected_task_digest,
        },
    )
    parent_location = task_dir
    if not _is_archived(root, task_dir):
        if task_dir.parent != (root / ".trellis" / "tasks").resolve():
            raise TaskArchiveError("Loop task evidence is not in the active task root")
        parent_location = archive_root / task_dir.name
        if parent_location.exists():
            raise TaskArchiveError("Loop parent archive destination exists")
        moves.append((task_dir, parent_location))
    if not moves:
        return _result(
            run_id=run_id,
            task_digest=task_digest,
            location=task_dir,
            repo_root=root,
            transaction_id=transaction_id,
            replayed=True,
            authority_digest=expected_authority_digest,
        )
    for source, _ in moves:
        clear_task_from_sessions(str(source), root)
    archive_paths_transaction(
        root,
        transaction_id=transaction_id,
        moves=moves,
        commit_message=f"chore(task): retire Loop evidence {run_id}",
        commit_enabled=commit_enabled,
        family_root=run_id,
    )
    return _result(
        run_id=run_id,
        task_digest=task_digest,
        location=parent_location,
        repo_root=root,
        transaction_id=transaction_id,
        replayed=False,
        authority_digest=expected_authority_digest,
    )


def cleanup_pre_admission_lock(repo_root: Path, run_id: str) -> None:
    """Remove the lock-only run root left by an uninitialized archive command."""
    root = ledger_path(repo_root, run_id).parent
    if (root / "ledger.sqlite3").exists() or (root / "operator-state.json").exists():
        return
    entries = list(root.iterdir()) if root.is_dir() else []
    if entries and {path.name for path in entries} != {"operator.lock"}:
        return
    for path in entries:
        path.unlink()
    try:
        root.rmdir()
        root.parent.rmdir()
        root.parent.parent.rmdir()
    except OSError:
        pass
