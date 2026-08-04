"""Canonical read-only activity classification across Trellis task modes."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .active_task import resolve_task_ref


HARNESS_MODE = "harness_state_machine"
LOOP_V1_MODE = "loop_v1"
TASKRUN_MODES = {"taskrun_v1", "taskrun_v2"}
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


def _proof_object(value: object, keys: set[str], detail: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalid(detail)
    return value


def _proof_digest(value: object, *, prefixed: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    digest = value[7:] if prefixed and value.startswith("sha256:") else value
    if prefixed and not value.startswith("sha256:"):
        return False
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _git_output(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
    )
    if result.returncode != 0:
        raise _invalid("TaskRun terminal proof Git evidence is unavailable")
    return result.stdout


def _git_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo_root,
            capture_output=True,
        ).returncode
        == 0
    )


def taskrun_authority_is_absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo == timezone.utc
    except ValueError:
        return False


def taskrun_pre_admission_cancellation_errors(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
) -> list[str]:
    """Validate an explicit TaskRun cancellation before SQLite admission."""
    errors: list[str] = []
    meta = task.get("meta")
    cancellation = task.get("cancellation")
    if not isinstance(meta, dict) or meta.get("workflow_mode") not in TASKRUN_MODES:
        errors.append("TaskRun workflow mode is invalid")
    if isinstance(meta, dict) and meta.get("task_run") is not None:
        errors.append("pre-admission TaskRun cancellation has an authority projection")
    if task.get("status") != "cancelled" or task.get("completedAt") is not None:
        errors.append("pre-admission TaskRun cancellation status conflicts")
    if (task_dir / "state-events.jsonl").exists():
        errors.append("pre-admission TaskRun cancellation has an HSM event stream")
    required = {
        "authority_kind",
        "authorized_at",
        "authorized_by",
        "pre_admission",
        "reason",
        "superseded_by",
    }
    if not isinstance(cancellation, dict) or set(cancellation) != required:
        errors.append("pre-admission TaskRun cancellation metadata is invalid")
    else:
        authorized_at = cancellation.get("authorized_at")
        if (
            cancellation.get("authority_kind") != "taskrun-pre-admission-v1"
            or cancellation.get("pre_admission") is not True
            or not _valid_utc_timestamp(authorized_at)
            or task.get("cancelledAt") != authorized_at[:10]
            or any(
                not isinstance(cancellation.get(field), str)
                or not str(cancellation[field]).strip()
                for field in ("authorized_by", "reason")
            )
            or (
                cancellation.get("superseded_by") is not None
                and (
                    not isinstance(cancellation["superseded_by"], str)
                    or not cancellation["superseded_by"].strip()
                )
            )
        ):
            errors.append("pre-admission TaskRun cancellation metadata conflicts")
    try:
        from taskrun import taskrun_id_for_task, taskrun_path

        run_id = taskrun_id_for_task(task_dir.name, task)
        run_path = taskrun_path(repo_root, run_id)
        if run_path.parent.exists():
            errors.append("pre-admission TaskRun cancellation has runtime authority residue")
    except Exception:
        errors.append("pre-admission TaskRun identity is invalid")
    return errors


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_taskrun_terminal_proof(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
    run: Any,
    terminal_projection_commit: str,
) -> dict[str, Any]:
    """Build the v1 evidence-only proof from one closed TaskRun authority."""
    root = Path(repo_root).resolve()
    snapshot = run.snapshot()
    terminal = snapshot.get("terminal")
    events = run.events()
    tail = events[-4:]
    expected_types = [
        "terminal_recorded",
        "close_prepared",
        "closed",
        "projection_checkpointed",
    ]
    if (
        not snapshot.get("closed")
        or not isinstance(terminal, dict)
        or [event.get("event_type") for event in tail] != expected_types
        or run.task_projection_bytes() != (task_dir / "task.json").read_bytes()
    ):
        raise TaskStateInvalid("TASK_STATE_INVALID: TaskRun is not proof-ready")
    task_bytes = (task_dir / "task.json").read_bytes()
    task_digest = sha256(task_bytes).hexdigest()
    task_checkpoint = run.projection_status("task_json")
    close_checkpoint = run.projection_status("close")
    if any(checkpoint.get("status") != "current" for checkpoint in (task_checkpoint, close_checkpoint)):
        raise TaskStateInvalid("TASK_STATE_INVALID: TaskRun projections are not current")
    seed = snapshot.get("task_json_seed") or {}
    base_commit = (
        (((seed.get("meta") or {}).get("execution") or {}).get("start_envelope") or {})
        .get("identities", {})
        .get("base")
    ) or (terminal.get("evidence") or {}).get("base_commit")
    disposition = terminal.get("disposition")
    work_commit = task.get("commit") if disposition == "completed" else base_commit
    if not all(
        isinstance(value, str) and len(value) == 40
        for value in (base_commit, work_commit, terminal_projection_commit)
    ):
        raise TaskStateInvalid("TASK_STATE_INVALID: TaskRun Git identities are incomplete")
    sqlite_bytes = run.path.read_bytes()
    checkpoint = lambda value: {
        key: value[key]
        for key in ("digest", "source_position", "status", "updated_at")
    }
    source_tree = _git_output(root, "rev-parse", f"{terminal_projection_commit}^{{tree}}").decode().strip()
    work_tree = _git_output(root, "rev-parse", f"{work_commit}^{{tree}}").decode().strip()
    terminal_evidence = terminal.get("evidence") or {}
    return {
        "classification": "closed/evidence-only",
        "disposition": {
            "closed": True,
            "created_at": terminal["created_at"],
            "terminal_event_digest": tail[0]["event_digest"],
            "terminal_evidence_digest": f"sha256:{_json_digest(terminal_evidence)}",
            "value": disposition,
        },
        "git": {
            "base_commit": base_commit,
            "commit_reconciliation_digest": f"sha256:{_json_digest(terminal_evidence.get('commit_reconciliation'))}",
            "source_worktree_head": terminal_projection_commit,
            "source_worktree_tree": source_tree,
            "terminal_projection_commit": terminal_projection_commit,
            "terminal_projection_commit_reachable_from_source_head": True,
            "work_commit": work_commit,
            "work_commit_reachable_from_source_head": True,
            "work_tree": work_tree,
        },
        "projection": {
            "authority_projection_sha256": f"sha256:{task_digest}",
            "board_checkpoint_status": run.projection_status("board")["status"],
            "close_checkpoint": checkpoint(close_checkpoint),
            "close_projection_digest": f"sha256:{close_checkpoint['digest']}",
            "task_json_checkpoint": checkpoint(task_checkpoint),
            "task_json_matches_authority": True,
            "task_run_state": "closed",
            "task_status": disposition,
        },
        "run": {
            "authority_digest": f"sha256:{run.authority_digest()}",
            "authority_sqlite_sha256": f"sha256:{sha256(sqlite_bytes).hexdigest()}",
            "authority_sqlite_size": len(sqlite_bytes),
            "task_run_id": run.task_run_id,
        },
        "schema_version": "taskrun-terminal-proof-v1",
        "task": {
            "task_dir_name": task_dir.name,
            "task_id": task.get("id"),
            "task_json_sha256": f"sha256:{task_digest}",
        },
        "terminal_event_chain": {
            "event_count": len(events),
            "event_type_counts": dict(Counter(event["event_type"] for event in events)),
            "first_event_digest": events[0]["event_digest"],
            "head_event_digest": events[-1]["event_digest"],
            "head_position": events[-1]["position"],
            "terminal_to_head": [
                {
                    key: event[key]
                    for key in (
                        "created_at",
                        "event_digest",
                        "event_type",
                        "position",
                        "previous_digest",
                    )
                }
                for event in tail
            ],
        },
    }


def validate_taskrun_terminal_proof(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
    projection: dict[str, Any],
) -> TaskActivity:
    """Validate one committed, read-only replacement for retired SQLite bytes."""
    proof_path = task_dir / "terminal-proof.json"
    if proof_path.is_symlink() or not proof_path.is_file():
        raise _invalid(f"{task_dir.name} TaskRun terminal proof is missing")
    try:
        relative = proof_path.relative_to(repo_root).as_posix()
        proof_bytes = proof_path.read_bytes()
        committed_proof = _git_output(repo_root, "show", f"HEAD:{relative}")
        proof = json.loads(proof_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _invalid(f"{task_dir.name} TaskRun terminal proof is invalid") from exc
    if committed_proof != proof_bytes:
        raise _invalid(f"{task_dir.name} TaskRun terminal proof is not committed")
    proof = _proof_object(
        proof,
        {
            "classification",
            "disposition",
            "git",
            "projection",
            "run",
            "schema_version",
            "task",
            "terminal_event_chain",
        },
        f"{task_dir.name} TaskRun terminal proof schema is invalid",
    )
    if (
        proof["schema_version"] != "taskrun-terminal-proof-v1"
        or proof["classification"] != "closed/evidence-only"
        or task.get("status") not in {"cancelled", "completed"}
        or projection.get("state") != "closed"
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof is not closed")

    task_proof = _proof_object(
        proof["task"],
        {"task_dir_name", "task_id", "task_json_sha256"},
        f"{task_dir.name} TaskRun terminal proof task binding is invalid",
    )
    task_bytes = (task_dir / "task.json").read_bytes()
    task_digest = f"sha256:{sha256(task_bytes).hexdigest()}"
    if (
        task_proof["task_dir_name"] != task_dir.name
        or task_proof["task_id"] != task.get("id")
        or task_proof["task_json_sha256"] != task_digest
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof task binding conflicts")

    run = _proof_object(
        proof["run"],
        {
            "authority_digest",
            "authority_sqlite_sha256",
            "authority_sqlite_size",
            "task_run_id",
        },
        f"{task_dir.name} TaskRun terminal proof run binding is invalid",
    )
    if (
        run["task_run_id"] != projection.get("id")
        or not _proof_digest(run["authority_digest"], prefixed=True)
        or not _proof_digest(run["authority_sqlite_sha256"], prefixed=True)
        or type(run["authority_sqlite_size"]) is not int
        or run["authority_sqlite_size"] <= 0
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof run binding conflicts")

    chain = _proof_object(
        proof["terminal_event_chain"],
        {
            "event_count",
            "event_type_counts",
            "first_event_digest",
            "head_event_digest",
            "head_position",
            "terminal_to_head",
        },
        f"{task_dir.name} TaskRun terminal proof event chain is invalid",
    )
    counts = chain["event_type_counts"]
    tail = chain["terminal_to_head"]
    if (
        type(chain["event_count"]) is not int
        or type(chain["head_position"]) is not int
        or chain["event_count"] != chain["head_position"]
        or not isinstance(counts, dict)
        or not counts
        or any(
            not isinstance(name, str)
            or not name
            or type(count) is not int
            or count <= 0
            for name, count in counts.items()
        )
        or sum(counts.values()) != chain["event_count"]
        or not _proof_digest(chain["first_event_digest"])
        or not _proof_digest(chain["head_event_digest"])
        or not isinstance(tail, list)
        or len(tail) != 4
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof event chain conflicts")
    expected_types = (
        "terminal_recorded",
        "close_prepared",
        "closed",
        "projection_checkpointed",
    )
    for offset, (event, expected_type) in enumerate(zip(tail, expected_types)):
        event = _proof_object(
            event,
            {"created_at", "event_digest", "event_type", "position", "previous_digest"},
            f"{task_dir.name} TaskRun terminal proof event is invalid",
        )
        if (
            event["event_type"] != expected_type
            or type(event["position"]) is not int
            or event["position"] != chain["head_position"] - 3 + offset
            or not isinstance(event["created_at"], str)
            or not event["created_at"]
            or not _proof_digest(event["event_digest"])
            or not _proof_digest(event["previous_digest"])
            or (offset and event["previous_digest"] != tail[offset - 1]["event_digest"])
            or counts.get(expected_type) != 1
        ):
            raise _invalid(f"{task_dir.name} TaskRun terminal proof event chain conflicts")
    if tail[-1]["event_digest"] != chain["head_event_digest"]:
        raise _invalid(f"{task_dir.name} TaskRun terminal proof chain head conflicts")

    disposition = _proof_object(
        proof["disposition"],
        {"closed", "created_at", "terminal_event_digest", "terminal_evidence_digest", "value"},
        f"{task_dir.name} TaskRun terminal proof disposition is invalid",
    )
    if (
        disposition["value"] != task.get("status")
        or disposition["closed"] is not True
        or not isinstance(disposition["created_at"], str)
        or not disposition["created_at"]
        or disposition["terminal_event_digest"] != tail[0]["event_digest"]
        or not _proof_digest(disposition["terminal_evidence_digest"], prefixed=True)
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof disposition conflicts")

    projection_proof = _proof_object(
        proof["projection"],
        {
            "authority_projection_sha256",
            "board_checkpoint_status",
            "close_checkpoint",
            "close_projection_digest",
            "task_json_checkpoint",
            "task_json_matches_authority",
            "task_run_state",
            "task_status",
        },
        f"{task_dir.name} TaskRun terminal proof projection is invalid",
    )
    checkpoints = []
    for key in ("task_json_checkpoint", "close_checkpoint"):
        checkpoint = _proof_object(
            projection_proof[key],
            {"digest", "source_position", "status", "updated_at"},
            f"{task_dir.name} TaskRun terminal proof checkpoint is invalid",
        )
        if (
            checkpoint["source_position"] != chain["head_position"]
            or checkpoint["status"] != "current"
            or not _proof_digest(checkpoint["digest"])
            or not isinstance(checkpoint["updated_at"], str)
            or not checkpoint["updated_at"]
        ):
            raise _invalid(f"{task_dir.name} TaskRun terminal proof checkpoint conflicts")
        checkpoints.append(checkpoint)
    checkpoint_digest = checkpoints[0]["digest"]
    if (
        checkpoints[1]["digest"] != checkpoint_digest
        or projection_proof["task_status"] != task.get("status")
        or projection_proof["task_run_state"] != "closed"
        or projection_proof["task_json_matches_authority"] is not True
        or projection_proof["authority_projection_sha256"] != task_digest
        or projection_proof["close_projection_digest"] != f"sha256:{checkpoint_digest}"
        or projection_proof["authority_projection_sha256"]
        != projection_proof["close_projection_digest"]
        or projection_proof["board_checkpoint_status"] != "missing"
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof projection conflicts")

    git = _proof_object(
        proof["git"],
        {
            "base_commit",
            "commit_reconciliation_digest",
            "source_worktree_head",
            "source_worktree_tree",
            "terminal_projection_commit",
            "terminal_projection_commit_reachable_from_source_head",
            "work_commit",
            "work_commit_reachable_from_source_head",
            "work_tree",
        },
        f"{task_dir.name} TaskRun terminal proof Git binding is invalid",
    )
    commit_keys = ("base_commit", "source_worktree_head", "terminal_projection_commit", "work_commit")
    tree_keys = ("source_worktree_tree", "work_tree")
    expected_work_commit = (
        task.get("commit") if task.get("status") == "completed" else git["base_commit"]
    )
    if (
        any(not isinstance(git[key], str) or len(git[key]) != 40 for key in commit_keys)
        or any(not isinstance(git[key], str) or len(git[key]) != 40 for key in tree_keys)
        or not _proof_digest(git["commit_reconciliation_digest"], prefixed=True)
        or git["work_commit"] != expected_work_commit
        or git["work_commit_reachable_from_source_head"] is not True
        or git["terminal_projection_commit_reachable_from_source_head"] is not True
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof Git binding conflicts")
    relative_task = (
        Path(".trellis") / "tasks" / task_dir.name / "task.json"
    ).as_posix()
    if (
        _git_output(repo_root, "rev-parse", f'{git["work_commit"]}^{{tree}}').decode().strip()
        != git["work_tree"]
        or _git_output(repo_root, "rev-parse", f'{git["source_worktree_head"]}^{{tree}}').decode().strip()
        != git["source_worktree_tree"]
        or _git_output(
            repo_root,
            "show",
            f'{git["terminal_projection_commit"]}:{relative_task}',
        )
        != task_bytes
        or not _git_ancestor(repo_root, git["base_commit"], git["work_commit"])
        or not _git_ancestor(repo_root, git["work_commit"], git["source_worktree_head"])
        or not _git_ancestor(
            repo_root,
            git["terminal_projection_commit"],
            git["source_worktree_head"],
        )
        or not _git_ancestor(repo_root, git["source_worktree_head"], "HEAD")
    ):
        raise _invalid(f"{task_dir.name} TaskRun terminal proof Git evidence conflicts")
    return TaskActivity(False, "verified_taskrun_evidence_only", "closed")


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
    from state_machine import state_schema_version, valid_states

    try:
        schema_version = state_schema_version(machine)
    except Exception as exc:
        raise _invalid(f"{task_dir.name} state machine schema is unsupported") from exc
    if state not in valid_states(tier, schema_version):
        raise _invalid(f"{task_dir.name} state machine state is unknown")
    if state.endswith("_cancelled"):
        if status != "cancelled":
            raise _invalid(f"{task_dir.name} cancelled state conflicts with status")
        return TaskActivity(False, "terminal_hsm_cancellation", state)
    if state.endswith("_archived") or state == "child_completed":
        if status != "completed":
            raise _invalid(f"{task_dir.name} completed state conflicts with status")
        if _session_points_to(task_dir, repo_root):
            return TaskActivity(True, "terminal_task_has_active_session", state)
        if tier == "child":
            events = _event_names(task_dir)
            required = {
                "child_completed"
                if schema_version == 2
                else "child_archive_completed",
                "commit_created",
                "completion_signal_received",
            }
            if not required.issubset(events):
                raise _invalid(f"{task_dir.name} child completion events are incomplete")
            if not _reachable_commit(repo_root, task.get("commit")):
                raise _invalid(f"{task_dir.name} child completion commit is unreachable")
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


def _classify_taskrun(
    task_dir: Path,
    repo_root: Path,
    task: dict[str, Any],
) -> TaskActivity:
    meta = task.get("meta")
    if not isinstance(meta, dict) or meta.get("taskrun_strategy") not in {
        "single",
        "loop",
    }:
        raise _invalid(f"{task_dir.name} TaskRun strategy is invalid")
    if (task_dir / "state-events.jsonl").exists():
        raise _invalid(f"{task_dir.name} TaskRun has a competing HSM event stream")
    projection = meta.get("task_run")
    if projection is None:
        if task.get("status") == "planning":
            return TaskActivity(True, "taskrun_planning", "planning")
        if task.get("status") == "cancelled":
            if taskrun_pre_admission_cancellation_errors(task_dir, repo_root, task):
                raise _invalid(
                    f"{task_dir.name} pre-admission TaskRun cancellation is invalid"
                )
            return TaskActivity(
                False,
                "verified_taskrun_pre_admission_cancellation",
                "cancelled",
            )
        raise _invalid(f"{task_dir.name} unadmitted TaskRun status conflicts")
    if (
        not isinstance(projection, dict)
        or projection.get("authority") != "sqlite"
        or projection.get("projection") is not True
        or not isinstance(projection.get("id"), str)
    ):
        raise _invalid(f"{task_dir.name} TaskRun projection binding is invalid")
    try:
        from taskrun import TaskRun, taskrun_path

        if taskrun_authority_is_absent(taskrun_path(repo_root, projection["id"])):
            return validate_taskrun_terminal_proof(
                task_dir, repo_root, task, projection
            )
        run = TaskRun.open(repo_root, projection["id"])
        if run.task_projection_bytes() != (task_dir / "task.json").read_bytes():
            raise _invalid(f"{task_dir.name} TaskRun projection conflicts")
        snapshot = run.snapshot()
    except TaskStateInvalid:
        raise
    except Exception as exc:
        raise _invalid(f"{task_dir.name} TaskRun authority is invalid") from exc
    state = str(projection.get("state") or snapshot.get("status") or "unknown")
    return TaskActivity(
        snapshot.get("terminal") is None,
        "active_taskrun" if snapshot.get("terminal") is None else "terminal_taskrun",
        state,
    )


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
    if mode in TASKRUN_MODES:
        return _classify_taskrun(Path(task_dir), Path(repo_root), task)
    status = task.get("status")
    if status in _TERMINAL_STATUSES:
        return TaskActivity(False, "terminal_default_task", str(status))
    if status in _ACTIVE_STATUSES:
        return TaskActivity(True, "active_default_task", str(status))
    raise _invalid(f"{Path(task_dir).name} default task status is unknown")
