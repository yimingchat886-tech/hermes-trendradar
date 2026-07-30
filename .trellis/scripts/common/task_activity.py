"""Canonical read-only activity classification across Trellis task modes."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .active_task import resolve_task_ref


HARNESS_MODE = "harness_state_machine"
LOOP_V1_MODE = "loop_v1"
_TERMINAL_STATUSES = {"cancelled", "completed", "done"}
_ACTIVE_STATUSES = {"blocked", "in_progress", "planning", "review"}


class TaskStateInvalid(ValueError):
    """Raised when lifecycle facts conflict or cannot be verified."""


@dataclass(frozen=True)
class TaskActivity:
    active: bool
    reason: str
    state: str


def _invalid(detail: str) -> TaskStateInvalid:
    return TaskStateInvalid(f"TASK_STATE_INVALID: {detail}")


def _session_points_to(task_dir: Path, repo_root: Path) -> bool:
    sessions = repo_root / ".trellis" / ".runtime" / "sessions"
    if not sessions.is_dir():
        return False
    target = task_dir.resolve()
    for path in sessions.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _invalid(f"session state is malformed: {path.name}") from exc
        current = value.get("current_task") if isinstance(value, dict) else None
        resolved = (
            resolve_task_ref(current, repo_root)
            if isinstance(current, str) and current.strip()
            else None
        )
        if resolved is not None and resolved.resolve() == target:
            return True
    return False


def _reachable_commit(repo_root: Path, commit: object) -> bool:
    if not isinstance(commit, str) or not commit.strip():
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _event_names(task_dir: Path) -> list[str]:
    path = task_dir / "state-events.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _invalid(f"{task_dir.name} state event is malformed") from exc
        event = value.get("event") if isinstance(value, dict) else None
        if not isinstance(event, str) or not event:
            raise _invalid(f"{task_dir.name} state event lacks an event name")
        events.append(event)
    return events


def _classify_harness(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
) -> TaskActivity:
    tier = task.get("tier")
    status = task.get("status")
    if tier == "light":
        if status in _TERMINAL_STATUSES:
            return TaskActivity(False, "terminal_light_task", str(status))
        if status in _ACTIVE_STATUSES:
            return TaskActivity(True, "active_light_task", str(status))
        raise _invalid(f"{task_dir.name} has unknown light-task status")
    if tier not in {"parent", "child"}:
        raise _invalid(f"{task_dir.name} has unsupported task tier")
    meta = task.get("meta")
    machine = meta.get("state_machine") if isinstance(meta, dict) else None
    if machine is None:
        if status in _TERMINAL_STATUSES:
            return TaskActivity(False, "terminal_legacy_harness_task", str(status))
        if status in _ACTIVE_STATUSES:
            return TaskActivity(True, "active_legacy_harness_task", str(status))
        raise _invalid(f"{task_dir.name} legacy HSM status is unknown")
    if not isinstance(machine, dict) or machine.get("kind") != tier:
        raise _invalid(f"{task_dir.name} state machine identity conflicts")
    state = machine.get("current_state")
    if not isinstance(state, str):
        raise _invalid(f"{task_dir.name} state machine lacks current_state")
    from state_machine import ALL_STATES

    if state not in ALL_STATES[tier]:
        raise _invalid(f"{task_dir.name} state machine state is unknown")
    if state.endswith("_cancelled"):
        if status != "cancelled":
            raise _invalid(f"{task_dir.name} cancelled state conflicts with status")
        return TaskActivity(False, "terminal_hsm_cancellation", state)
    if state.endswith("_archived"):
        if status != "completed":
            raise _invalid(f"{task_dir.name} archived state conflicts with status")
        if _session_points_to(task_dir, repo_root):
            return TaskActivity(True, "terminal_task_has_active_session", state)
        if tier == "child":
            events = _event_names(task_dir)
            required = {
                "child_archive_completed",
                "commit_created",
                "completion_signal_received",
            }
            if not required.issubset(events):
                raise _invalid(f"{task_dir.name} soft archive events are incomplete")
            if not _reachable_commit(repo_root, task.get("commit")):
                raise _invalid(f"{task_dir.name} soft archive commit is unreachable")
            report = task_dir / "stage-report.md"
            text = report.read_text(encoding="utf-8") if report.is_file() else ""
            if "- [x]" not in text.lower() or "## Verification" not in text:
                raise _invalid(f"{task_dir.name} stage evidence is invalid")
        return TaskActivity(False, "verified_hsm_evidence_only", state)
    if status in _TERMINAL_STATUSES:
        raise _invalid(f"{task_dir.name} terminal status conflicts with active HSM state")
    return TaskActivity(True, "active_hsm_task", state)


def _classify_loop(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
) -> TaskActivity:
    run_id = task.get("id")
    if not isinstance(run_id, str) or task.get("name") != run_id:
        raise _invalid(f"{task_dir.name} Loop identity conflicts")
    meta = task.get("meta")
    parent_run_id = task.get("parent")
    if task.get("tier") == "child" and isinstance(parent_run_id, str):
        if Path(parent_run_id).name != parent_run_id:
            raise _invalid(f"{task_dir.name} Loop parent task reference is invalid")
        tasks = repo_root / ".trellis" / "tasks"
        parent_matches = [tasks / parent_run_id / "task.json"]
        parent_matches.extend(tasks.glob(f"archive/*/{parent_run_id}/task.json"))
        parent_matches = [
            path for path in parent_matches if path.is_file() and not path.is_symlink()
        ]
        if len(parent_matches) > 1:
            raise _invalid(f"{task_dir.name} Loop parent task is ambiguous")
        if parent_matches:
            try:
                parent_task = json.loads(
                    parent_matches[0].read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise _invalid(f"{task_dir.name} Loop parent task is malformed") from exc
            parent_meta = (
                parent_task.get("meta") if isinstance(parent_task, dict) else None
            )
            parent_id = (
                parent_task.get("id") if isinstance(parent_task, dict) else None
            )
            parent_children = (
                parent_task.get("children")
                if isinstance(parent_task, dict)
                else None
            )
            if (
                not isinstance(parent_task, dict)
                or parent_task.get("tier") != "parent"
                or not isinstance(parent_id, str)
                or parent_task.get("name") != parent_id
                or not isinstance(parent_meta, dict)
                or parent_meta.get("workflow_mode") != LOOP_V1_MODE
                or not isinstance(parent_children, list)
                or task_dir.name not in parent_children
            ):
                raise _invalid(f"{task_dir.name} Loop parent task identity conflicts")
            parent_run_id = parent_id
        parent_runtime = (
            repo_root
            / ".trellis"
            / ".runtime"
            / "loop-v1"
            / "parents"
            / parent_run_id
            / "ledger.sqlite3"
        )
        if parent_runtime.is_file():
            try:
                from loop_v1.ledger import ParentLedger

                parent_ledger = ParentLedger(repo_root, parent_run_id)
                snapshot = parent_ledger.authority_snapshot()
                rows = [
                    row
                    for row in snapshot["tables"]["child_operations"]
                    if row.get("child_id") == run_id
                ]
                if len(rows) != 1:
                    raise _invalid(
                        f"{task_dir.name} Loop child ledger identity conflicts"
                    )
                child_state = str(rows[0]["state"])
                authority_digest = f"sha256:{parent_ledger.authority_digest()}"
            except TaskStateInvalid:
                raise
            except Exception as exc:
                raise _invalid(f"{task_dir.name} Loop child ledger is malformed") from exc
            projection = (
                meta.get("loop_v1_child_terminal")
                if isinstance(meta, dict)
                else None
            )
            terminal_states = {"integrated", "cancelled", "invalidated", "stale"}
            if child_state in terminal_states:
                expected_status = (
                    "completed" if child_state == "integrated" else "cancelled"
                )
                if projection is not None and (
                    not isinstance(projection, dict)
                    or projection.get("authority_digest") != authority_digest
                    or projection.get("child_id") != run_id
                    or projection.get("ledger_state") != child_state
                    or projection.get("parent_run_id") != parent_run_id
                    or projection.get("terminal_kind") != child_state
                    or task.get("status") != expected_status
                ):
                    raise _invalid(
                        f"{task_dir.name} Loop child terminal projection conflicts"
                    )
                return TaskActivity(
                    False,
                    "terminal_loop_child_ledger",
                    child_state,
                )
            if projection is not None:
                raise _invalid(
                    f"{task_dir.name} Loop child projection is not terminal"
                )
    runtime = (
        repo_root
        / ".trellis"
        / ".runtime"
        / "loop-v1"
        / "parents"
        / run_id
        / "ledger.sqlite3"
    )
    ledger_status = None
    authority_digest = None
    if runtime.is_file():
        try:
            from loop_v1.ledger import ParentLedger

            ledger = ParentLedger(repo_root, run_id)
            snapshot = ledger.authority_snapshot()
            parents = snapshot["tables"]["parent_runs"]
            if len(parents) != 1:
                raise _invalid(f"{task_dir.name} Loop ledger parent identity conflicts")
            ledger_status = parents[0]["status"]
            authority_digest = f"sha256:{ledger.authority_digest()}"
        except TaskStateInvalid:
            raise
        except Exception as exc:
            raise _invalid(f"{task_dir.name} Loop ledger is malformed") from exc
    projection = meta.get("loop_v1_terminal") if isinstance(meta, dict) else None
    if ledger_status in {"archived", "cancelled", "revoked"}:
        if projection is not None and (
            not isinstance(projection, dict)
            or projection.get("run_id") != run_id
            or projection.get("ledger_status") != ledger_status
            or projection.get("authority_digest") != authority_digest
            or projection.get("terminal_kind") != ledger_status
        ):
            raise _invalid(f"{task_dir.name} Loop terminal projection conflicts")
        return TaskActivity(False, "terminal_loop_ledger", str(ledger_status))
    if projection is not None:
        if not isinstance(projection, dict) or projection.get("run_id") != run_id:
            raise _invalid(f"{task_dir.name} Loop terminal projection is malformed")
        terminal_kind = projection.get("terminal_kind")
        expected_status = "completed" if terminal_kind == "archived" else "cancelled"
        digest = projection.get("authority_digest")
        if (
            terminal_kind not in {"archived", "cancelled", "revoked"}
            or projection.get("ledger_status") != terminal_kind
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or not isinstance(projection.get("terminal_at"), str)
            or not projection["terminal_at"]
        ):
            raise _invalid(f"{task_dir.name} Loop terminal kind is invalid")
        if task.get("status") != expected_status:
            raise _invalid(f"{task_dir.name} Loop terminal status conflicts")
        return TaskActivity(False, "verified_loop_projection", str(terminal_kind))
    if task.get("status") == "cancelled":
        from loop_v1.pre_admission import pre_admission_cancellation_errors

        if pre_admission_cancellation_errors(task_dir, task, repo_root):
            raise _invalid(f"{task_dir.name} pre-admission cancellation is invalid")
        return TaskActivity(False, "verified_pre_admission_cancellation", "cancelled")
    if task.get("status") not in _ACTIVE_STATUSES:
        raise _invalid(f"{task_dir.name} Loop status is unknown")
    return TaskActivity(True, "active_loop_task", str(ledger_status or task["status"]))


def classify_task_activity(task_dir: Path, repo_root: Path) -> TaskActivity:
    """Classify one task as active or evidence-only, failing closed on conflict."""
    path = Path(task_dir) / "task.json"
    if path.is_symlink():
        raise _invalid(f"{path} is a symlink")
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid(f"{path} cannot be read") from exc
    if not isinstance(task, dict):
        raise _invalid(f"{path} is not an object")
    meta = task.get("meta")
    mode = meta.get("workflow_mode") if isinstance(meta, dict) else None
    if mode == HARNESS_MODE:
        return _classify_harness(Path(task_dir), Path(repo_root), task)
    if mode == LOOP_V1_MODE:
        return _classify_loop(Path(task_dir), Path(repo_root), task)
    status = task.get("status")
    if status in _TERMINAL_STATUSES:
        return TaskActivity(False, "terminal_default_task", str(status))
    if status in _ACTIVE_STATUSES:
        return TaskActivity(True, "active_default_task", str(status))
    raise _invalid(f"{Path(task_dir).name} default task status is unknown")
