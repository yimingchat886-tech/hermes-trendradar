"""Idempotent status-only close for one terminal TaskRun."""

from __future__ import annotations

import fcntl
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from .authority import InvalidTransition, OperationConflict, TaskRun, TaskRunError


FAULT_BOUNDARIES = (
    "close_prepared",
    "authority_committed",
    "task_projection_effect",
    "projection_checkpointed",
)


class CloseError(TaskRunError):
    """Raised when a close cannot make a proven forward step."""


class CloseUnknownOutcome(CloseError):
    """Retained for callers that read legacy physical-close outcomes."""


class InjectedFailure(CloseError):
    """Test-only durable-boundary failure."""


def close_task_run(
    run: TaskRun,
    *,
    operation_id: str,
    task_dir: Path,
    actor: str,
    fault_after: str | None = None,
) -> dict[str, Any]:
    """Close authority and update only its in-place task projection."""
    if fault_after is not None and fault_after not in FAULT_BOUNDARIES:
        raise CloseError(f"unsupported fault boundary: {fault_after}")
    actor = _required_text(actor, "actor")
    task_path = _validate_task_path(run, task_dir)
    try:
        descriptor = os.open(run.path.parent, os.O_RDONLY)
    except OSError as exc:
        raise CloseError("TaskRun authority is unreadable") from exc
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        return _close_task_run_locked(
            run,
            operation_id=operation_id,
            task_path=task_path,
            actor=actor,
            fault_after=fault_after,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _close_task_run_locked(
    run: TaskRun,
    *,
    operation_id: str,
    task_path: Path,
    actor: str,
    fault_after: str | None,
) -> dict[str, Any]:
    state = run.snapshot()
    terminal = state.get("terminal")
    if terminal is None:
        raise InvalidTransition("close requires a terminal TaskRun")
    request = {
        "actor": actor,
        "task_path": _relative(task_path, run.repo_root),
        "terminal_disposition": terminal["disposition"],
    }

    existing = run.get_operation(operation_id)
    if existing is not None:
        if existing["kind"] != "close":
            raise OperationConflict("operation ID is not a close operation")
        intent = existing["intent"]
        if any(intent.get(key) != value for key, value in request.items()):
            raise OperationConflict("close operation replay input changed")
    else:
        preimage = run.task_projection_bytes()
        if _read_task_projection(task_path, run.task_run_id) != preimage:
            raise CloseError("task projection preimage does not match authority")
        intent = {
            **request,
            "task_projection_preimage_digest": sha256(preimage).hexdigest(),
        }

    operation = run.prepare_close(operation_id=operation_id, intent=intent)
    if operation["phase"] == "projected":
        projection_digest = operation["outcome"]["projection_digest"]
        final_projection = run.task_projection_bytes()
        _verify_final_projection(
            run,
            task_path,
            final_projection,
            projection_digest,
        )
        return _close_result(operation_id, intent, projection_digest)
    if operation["phase"] not in {"prepared", "authority_committed"}:
        raise InvalidTransition("status-only close has an invalid durable phase")

    if operation["phase"] == "prepared":
        _fault(fault_after, "close_prepared")
        try:
            current = _read_task_projection(task_path, run.task_run_id)
        except CloseError:
            run.record_projection_failure("task_json", "ProjectionConflict")
            raise
        if sha256(current).hexdigest() != intent["task_projection_preimage_digest"]:
            run.record_projection_failure("task_json", "ProjectionConflict")
            raise CloseError("task projection changed after close preparation")
        run.commit_close(operation_id=operation_id)

    _fault(fault_after, "authority_committed")
    final_projection = run.task_projection_bytes()
    projection_digest = sha256(final_projection).hexdigest()
    try:
        _publish_task_projection(
            task_path / "task.json",
            preimage_digest=intent["task_projection_preimage_digest"],
            final_projection=final_projection,
            operation_id=operation_id,
        )
        if _read_task_projection(task_path, run.task_run_id) != final_projection:
            raise CloseError("task projection changed during close projection")
    except CloseError:
        run.record_projection_failure("task_json", "ProjectionConflict")
        raise
    except OSError as exc:
        run.record_projection_failure("task_json", type(exc).__name__)
        raise CloseError(f"task projection write failed: {type(exc).__name__}") from exc
    _fault(fault_after, "task_projection_effect")

    run.mark_projected(
        operation_id=operation_id,
        projection_digest=projection_digest,
    )
    _verify_final_projection(
        run,
        task_path,
        final_projection,
        projection_digest,
    )
    _fault(fault_after, "projection_checkpointed")
    return _close_result(operation_id, intent, projection_digest)


def _validate_task_path(run: TaskRun, task_dir: Path) -> Path:
    tasks_root = (run.repo_root / ".trellis" / "tasks").resolve()
    task_input = Path(task_dir)
    if task_input.is_symlink():
        raise CloseError("task close path must not be a symlink")
    task_path = task_input.resolve()
    if task_path.parent != tasks_root:
        raise CloseError("task close requires one direct active task directory")
    if task_path.name != run.snapshot()["task_dir_name"]:
        raise CloseError("task directory does not match TaskRun genesis")
    return task_path


def _publish_task_projection(
    path: Path,
    *,
    preimage_digest: str,
    final_projection: bytes,
    operation_id: str,
) -> None:
    claim = path.with_name(
        f".{path.name}.{sha256(operation_id.encode('utf-8')).hexdigest()}.close-claim"
    )
    candidate = claim.with_name(f"{claim.name}.candidate")
    if claim.is_symlink() or candidate.is_symlink():
        raise CloseError("task projection claim paths must not be symlinks")
    if candidate.exists() and candidate.read_bytes() != final_projection:
        candidate.unlink()
    if claim.exists():
        claimed = claim.read_bytes()
        if sha256(claimed).hexdigest() != preimage_digest:
            candidate.unlink(missing_ok=True)
            _restore_claim_without_overwrite(path, claim)
            raise CloseError("task projection changed while close held its claim")
        if path.exists():
            current = path.read_bytes()
            if current == final_projection:
                claim.unlink()
                candidate.unlink(missing_ok=True)
                return
            if sha256(current).hexdigest() == preimage_digest:
                claim.unlink()
            else:
                claim.unlink()
                candidate.unlink(missing_ok=True)
                raise CloseError("task projection changed during close projection")
        else:
            _install_claimed_projection(
                path,
                claim,
                candidate,
                final_projection,
                preimage_digest,
            )
            return

    if path.is_symlink():
        raise CloseError("task projection must not be a symlink")
    current = path.read_bytes()
    if current == final_projection:
        candidate.unlink(missing_ok=True)
        return
    if sha256(current).hexdigest() != preimage_digest:
        candidate.unlink(missing_ok=True)
        raise CloseError("task projection changed after close preparation")

    os.rename(path, claim)
    claimed = claim.read_bytes()
    if sha256(claimed).hexdigest() != preimage_digest:
        candidate.unlink(missing_ok=True)
        _restore_claim_without_overwrite(path, claim)
        raise CloseError("task projection changed while close acquired its claim")
    _install_claimed_projection(
        path,
        claim,
        candidate,
        final_projection,
        preimage_digest,
    )


def _install_claimed_projection(
    path: Path,
    claim: Path,
    candidate: Path,
    final_projection: bytes,
    preimage_digest: str,
) -> None:
    if not candidate.exists():
        with candidate.open("xb") as stream:
            stream.write(final_projection)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        os.link(candidate, path)
    except FileExistsError as exc:
        claim.unlink()
        candidate.unlink()
        raise CloseError("task projection changed during close projection") from exc

    if path.read_bytes() != final_projection:
        claim.unlink()
        candidate.unlink()
        raise CloseError("task projection changed during close projection")
    if sha256(claim.read_bytes()).hexdigest() != preimage_digest:
        raise CloseError("task projection claim changed during close projection")
    claim.unlink()
    candidate.unlink()


def _restore_claim_without_overwrite(path: Path, claim: Path) -> None:
    try:
        os.link(claim, path)
    except FileExistsError:
        return
    claim.unlink()


def _read_task_projection(task_dir: Path, task_run_id: str) -> bytes:
    path = task_dir / "task.json"
    if path.is_symlink():
        raise CloseError("task projection must not be a symlink")
    try:
        payload = path.read_bytes()
        task = json.loads(payload.decode("utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise CloseError("task projection is unreadable") from exc
    if not isinstance(task, dict):
        raise CloseError("task projection must be a JSON object")
    meta = task.get("meta")
    task_run = meta.get("task_run") if isinstance(meta, dict) else None
    projected_id = task_run.get("id") if isinstance(task_run, dict) else None
    if projected_id != task_run_id:
        raise CloseError("task projection TaskRun identity mismatch")
    return payload


def _verify_final_projection(
    run: TaskRun,
    task_dir: Path,
    final_projection: bytes,
    projection_digest: str,
) -> None:
    try:
        current = _read_task_projection(task_dir, run.task_run_id)
    except CloseError:
        run.record_projection_failure("task_json", "ProjectionConflict")
        raise
    if current != final_projection:
        run.record_projection_failure("task_json", "ProjectionConflict")
        raise CloseError("closed task projection does not match authority")
    checkpoint = run.projection_status("task_json")
    if checkpoint.get("status") != "current" or checkpoint.get("digest") != projection_digest:
        run.record_projection_current("task_json", projection_digest)


def _close_result(
    operation_id: str,
    intent: dict[str, Any],
    projection_digest: str,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "phase": "projected",
        "projection_digest": projection_digest,
        "status": "closed",
        "task_path": intent["task_path"],
    }


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise CloseError("close path escapes repository root") from exc


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CloseError(f"{field} must be a non-empty string")
    return value.strip()


def _fault(selected: str | None, boundary: str) -> None:
    if selected == boundary:
        raise InjectedFailure(f"injected failure after {boundary}")
