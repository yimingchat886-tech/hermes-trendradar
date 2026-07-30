"""Project terminal Loop ledger facts into the exact Trellis task record."""

from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from common.io import write_bytes_atomic

from .ledger import ParentLedger


class TaskProjectionError(RuntimeError):
    """Raised when terminal task projection cannot be proven or applied."""


def _digest_bytes(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def _task_matches(repo_root: Path, run_id: str) -> list[Path]:
    tasks = repo_root / ".trellis" / "tasks"
    candidates = list(tasks.glob("*/task.json"))
    candidates.extend(tasks.glob("archive/*/*/task.json"))
    matches = []
    for path in sorted(candidates):
        if path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("id") == run_id:
            matches.append(path)
    return matches


def _task_ref_matches(repo_root: Path, task_ref: str) -> list[Path]:
    if Path(task_ref).name != task_ref:
        return []
    tasks = repo_root / ".trellis" / "tasks"
    candidates = [tasks / task_ref / "task.json"]
    candidates.extend(tasks.glob(f"archive/*/{task_ref}/task.json"))
    return [path for path in candidates if path.is_file() and not path.is_symlink()]


def _terminal_fact(ledger: ParentLedger) -> dict[str, str] | None:
    connection = ledger._connect(read_only=True)
    try:
        parent = connection.execute(
            "SELECT status, updated_at FROM parent_runs WHERE run_id = ?",
            (ledger.run_id,),
        ).fetchone()
        if parent is None or parent["status"] not in {
            "archived",
            "cancelled",
            "revoked",
        }:
            return None
        status = str(parent["status"])
        timestamp = str(parent["updated_at"])
        kind = "parent_cancel" if status == "cancelled" else (
            "execution_binding_revocation" if status == "revoked" else "parent_archive"
        )
        row = connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = ? AND phase IN ('authority_committed', 'projected')
            ORDER BY created_at DESC, operation_id DESC LIMIT 1
            """,
            (kind,),
        ).fetchone()
        if row is not None:
            outcome = json.loads(row["outcome_json"])
            timestamp = str(
                outcome.get("requested_at")
                or outcome.get("revoked_at")
                or timestamp
            )
        return {
            "ledger_status": status,
            "terminal_at": timestamp,
            "terminal_kind": status,
        }
    finally:
        connection.close()


def _desired_task(
    task: dict[str, Any],
    *,
    run_id: str,
    authority_digest: str,
    terminal: dict[str, str],
) -> dict[str, Any]:
    desired = dict(task)
    meta = desired.get("meta")
    if not isinstance(meta, dict):
        raise TaskProjectionError("task meta must be an object")
    desired_meta = dict(meta)
    desired_meta["loop_v1_terminal"] = {
        "authority_digest": authority_digest,
        "ledger_status": terminal["ledger_status"],
        "run_id": run_id,
        "terminal_at": terminal["terminal_at"],
        "terminal_kind": terminal["terminal_kind"],
    }
    desired["meta"] = desired_meta
    date = terminal["terminal_at"][:10]
    if terminal["ledger_status"] == "archived":
        desired["status"] = "completed"
        desired["completedAt"] = date
        desired.pop("cancelledAt", None)
    else:
        desired["status"] = "cancelled"
        desired["completedAt"] = None
        desired["cancelledAt"] = date
    return desired


def _child_projection_plan(
    repo_root: Path,
    ledger: ParentLedger,
    parent_task: dict[str, Any],
    parent_task_ref: str,
    authority_digest: str,
) -> list[dict[str, object]]:
    children = parent_task.get("children") or []
    if (
        not isinstance(children, list)
        or any(not isinstance(child, str) or not child for child in children)
        or len(set(children)) != len(children)
    ):
        raise TaskProjectionError("Loop parent child identities are invalid")
    connection = ledger._connect(read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT child_id, state, updated_at
            FROM child_operations WHERE run_id = ?
            """,
            (ledger.run_id,),
        ).fetchall()
    finally:
        connection.close()
    by_id = {str(row["child_id"]): row for row in rows}
    plan = []
    seen_child_ids = set()
    for child_ref in children:
        matches = _task_ref_matches(repo_root, child_ref)
        if len(matches) != 1:
            detail = "missing" if not matches else "ambiguous"
            raise TaskProjectionError(
                f"Loop child task location is {detail}: {child_ref}"
            )
        path = matches[0]
        original = path.read_bytes()
        try:
            task = json.loads(original)
        except json.JSONDecodeError as exc:
            raise TaskProjectionError(
                f"Loop child task is invalid: {child_ref}"
            ) from exc
        meta = task.get("meta") if isinstance(task, dict) else None
        child_id = task.get("id") if isinstance(task, dict) else None
        if (
            not isinstance(task, dict)
            or not isinstance(child_id, str)
            or not child_id
            or child_id in seen_child_ids
            or task.get("name") != child_id
            or task.get("tier") != "child"
            or task.get("parent") != parent_task_ref
            or not isinstance(meta, dict)
            or meta.get("workflow_mode") != "loop_v1"
        ):
            raise TaskProjectionError(
                f"Loop child task identity conflicts: {child_ref}"
            )
        seen_child_ids.add(child_id)
        row = by_id.get(child_id)
        if row is None:
            raise TaskProjectionError(f"Loop child ledger fact is missing: {child_id}")
        state = str(row["state"])
        if state == "integrated":
            task_status = "completed"
        elif state in {"cancelled", "invalidated", "stale"}:
            task_status = "cancelled"
        else:
            raise TaskProjectionError(
                f"Loop child ledger fact is not terminal: {child_id}:{state}"
            )
        desired = dict(task)
        desired_meta = dict(meta)
        desired_meta["loop_v1_child_terminal"] = {
            "authority_digest": authority_digest,
            "child_id": child_id,
            "ledger_state": state,
            "parent_run_id": ledger.run_id,
            "terminal_at": str(row["updated_at"]),
            "terminal_kind": state,
        }
        desired["meta"] = desired_meta
        date = str(row["updated_at"])[:10]
        desired["status"] = task_status
        if task_status == "completed":
            desired["completedAt"] = date
            desired.pop("cancelledAt", None)
        else:
            desired["completedAt"] = None
            desired["cancelledAt"] = date
        desired_bytes = (
            json.dumps(desired, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        plan.append(
            {
                "child_id": child_id,
                "desired": desired_bytes,
                "desired_task_digest": _digest_bytes(desired_bytes),
                "ledger_state": state,
                "location": path.relative_to(repo_root).as_posix(),
                "original": original,
                "status": "current" if original == desired_bytes else "stale",
                "task_digest": _digest_bytes(original),
            }
        )
    return plan


def projection_status(repo_root: Path, run_id: str) -> dict[str, object]:
    """Return current|stale|missing|conflict without writing task or BOARD."""
    root = Path(repo_root).resolve()
    ledger = ParentLedger(root, run_id)
    if not ledger.path.is_file():
        return {"run_id": run_id, "status": "missing", "reason": "ledger_missing"}
    terminal = _terminal_fact(ledger)
    authority_digest = f"sha256:{ledger.authority_digest()}"
    if terminal is None:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": "ledger_not_terminal",
        }
    matches = _task_matches(root, run_id)
    if not matches:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "missing",
            "reason": "task_missing",
        }
    if len(matches) != 1:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": "task_location_ambiguous",
        }
    path = matches[0]
    if path.is_symlink():
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": "task_symlink",
        }
    try:
        original = path.read_bytes()
        task = json.loads(original)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": f"task_invalid:{type(exc).__name__}",
        }
    meta = task.get("meta") if isinstance(task, dict) else None
    if (
        not isinstance(task, dict)
        or task.get("id") != run_id
        or task.get("name") != run_id
        or task.get("tier") != "parent"
        or not isinstance(meta, dict)
        or meta.get("workflow_mode") != "loop_v1"
    ):
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": "task_identity_conflict",
        }
    try:
        desired = _desired_task(
            task,
            run_id=run_id,
            authority_digest=authority_digest,
            terminal=terminal,
        )
    except TaskProjectionError as exc:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": str(exc),
        }
    desired_bytes = (
        json.dumps(desired, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    try:
        child_plan = _child_projection_plan(
            root,
            ledger,
            task,
            path.parent.name,
            authority_digest,
        )
    except (OSError, TaskProjectionError) as exc:
        return {
            "authority_digest": authority_digest,
            "run_id": run_id,
            "status": "conflict",
            "reason": str(exc),
        }
    child_projections = [
        {key: value for key, value in item.items() if key not in {"desired", "original"}}
        for item in child_plan
    ]
    current = original == desired_bytes and all(
        item["status"] == "current" for item in child_plan
    )
    return {
        "authority_digest": authority_digest,
        "child_projections": child_projections,
        "desired_task_digest": _digest_bytes(desired_bytes),
        "ledger_status": terminal["ledger_status"],
        "location": path.relative_to(root).as_posix(),
        "run_id": run_id,
        "status": "current" if current else "stale",
        "task_digest": _digest_bytes(original),
        "terminal_kind": terminal["terminal_kind"],
    }


def project_terminal(
    repo_root: Path,
    run_id: str,
    *,
    expected_authority_digest: str,
    expected_task_digest: str,
) -> dict[str, object]:
    """CAS one terminal task projection and refresh BOARD through its generator."""
    root = Path(repo_root).resolve()
    before = projection_status(root, run_id)
    if before["status"] not in {"current", "stale"}:
        raise TaskProjectionError(
            f"terminal task projection is {before['status']}: {before.get('reason')}"
        )
    if before["authority_digest"] != expected_authority_digest:
        raise TaskProjectionError("terminal projection authority digest changed")
    if before["task_digest"] != expected_task_digest:
        raise TaskProjectionError("terminal projection task digest changed")
    if before["status"] == "current":
        return before

    path = root / str(before["location"])
    original = path.read_bytes()
    ledger = ParentLedger(root, run_id)
    terminal = _terminal_fact(ledger)
    if (
        terminal is None
        or f"sha256:{ledger.authority_digest()}" != expected_authority_digest
        or _digest_bytes(path.read_bytes()) != expected_task_digest
    ):
        raise TaskProjectionError("terminal projection CAS changed before write")
    task = json.loads(original)
    desired = _desired_task(
        task,
        run_id=run_id,
        authority_digest=expected_authority_digest,
        terminal=terminal,
    )
    child_plan = _child_projection_plan(
        root,
        ledger,
        task,
        path.parent.name,
        expected_authority_digest,
    )
    parent_desired = (
        json.dumps(desired, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    originals = [(path, original)] + [
        (root / str(item["location"]), bytes(item["original"]))
        for item in child_plan
    ]
    board_path = root / "BOARD.md"
    board_original = board_path.read_bytes() if board_path.is_file() else None
    if any(target.read_bytes() != payload for target, payload in originals):
        raise TaskProjectionError("terminal child projection changed before write")
    try:
        write_bytes_atomic(path, parent_desired)
        for item in child_plan:
            write_bytes_atomic(
                root / str(item["location"]),
                bytes(item["desired"]),
            )
        board = root / ".trellis" / "scripts" / "board.py"
        if board.is_file():
            result = subprocess.run(
                [sys.executable, str(board)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise TaskProjectionError(
                    result.stderr.strip() or "BOARD generator failed"
                )
    except Exception:
        for target, payload in originals:
            write_bytes_atomic(target, payload)
        if board_original is not None:
            write_bytes_atomic(board_path, board_original)
        elif board_path.exists():
            board_path.unlink()
        raise
    return projection_status(root, run_id)
