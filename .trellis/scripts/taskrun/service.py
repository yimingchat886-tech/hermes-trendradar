"""Unified task, action, review, projection, and closeout operations."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from prd import PrdError, resolve_requirement_ids

from .authority import (
    Authority,
    AuthorityError,
    canonical_json,
    digest,
    git,
    git_common_dir,
    utc_now,
)


SOFT_ATTEMPT_LIMIT = 4
HARD_ATTEMPT_LIMIT = 8
STAGNATION_ROUNDS = 2
BLOCKING_CATEGORIES = {
    "compatibility",
    "correctness",
    "data_loss",
    "evidence",
    "req",
    "security",
}
CLOSEOUT_STEPS = (
    "freeze_candidate",
    "classify_base_drift",
    "reconcile_task_branch",
    "full_reverify",
    "prepare_git_artifacts",
    "scoped_commit",
    "local_merge_to_base",
    "record_completion",
    "logical_archive",
    "clear_runtime_and_pointers",
    "remove_worktree",
    "remove_merged_local_branch",
    "finalize_journal_projection",
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise AuthorityError("Task title must contain an ASCII letter or digit")
    return slug[:72]


def _task_dir_name(task_id: str) -> str:
    return f"{datetime.now(timezone.utc):%m-%d}-{task_id}"


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD^{commit}").stdout.strip()


def _working_candidate_digest(
    repo: Path,
    *,
    exclude: Sequence[str] = (),
) -> str:
    root = Path(repo).resolve()
    excluded = set(exclude)
    rows: list[tuple[str, str]] = []
    for raw in sorted(
        filter(
            None,
            git(root, "ls-files", "-co", "--exclude-standard", "-z").stdout.split(
                "\0"
            ),
        )
    ):
        if raw in excluded:
            continue
        path = root / raw
        if path.is_symlink():
            rows.append((raw, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            rows.append((raw, sha256(path.read_bytes()).hexdigest()))
    return digest({"files": rows, "head": _head(root)})


def _task_authorization_candidate_digest(
    repo: Path,
    task: Mapping[str, object],
) -> str:
    prefix = f".trellis/tasks/{task['task_dir_name']}"
    return _working_candidate_digest(
        repo,
        exclude=(
            "BOARD.md",
            f"{prefix}/run-summary.json",
            f"{prefix}/task.json",
        ),
    )


def _dirty_candidate_digest(repo: Path) -> str:
    root = Path(repo).resolve()
    untracked: list[tuple[str, str]] = []
    for raw in sorted(
        filter(
            None,
            git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(
                "\0"
            ),
        )
    ):
        path = root / raw
        if path.is_symlink():
            untracked.append((raw, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            untracked.append((raw, sha256(path.read_bytes()).hexdigest()))
    return digest(
        {
            "tracked_diff": git(root, "diff", "--binary", "HEAD", "--").stdout,
            "untracked": untracked,
        }
    )


def _branch(repo: Path) -> str:
    result = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() or "HEAD"


def _minimal_prd(title: str, request: str, requirement_id: str) -> str:
    return (
        f"# {title}\n\n"
        "## Intent Snapshot\n\n"
        f"{request.strip()}\n\n"
        "## Requirements\n\n"
        f"- `{requirement_id}` [owner: codex]: {request.strip()}\n\n"
        "## Logical Checks\n\n"
        "- `trellis.unittest.focused`\n"
        "- `trellis.diff.check`\n"
    )


def _isolated_task_root(
    repo_root: Path,
    task_id: str,
    base_branch: str,
    task_branch: str,
    requested: Path | None,
) -> Path:
    root = Path(repo_root).resolve()
    if _branch(root) == task_branch:
        return root
    registered = _branch_worktree(root, task_branch)
    if registered:
        if requested and registered != requested.resolve():
            raise AuthorityError("Task branch already uses another worktree")
        return registered
    target = (requested or root.parent / f".{root.name}-{task_id}").resolve()
    if target.exists():
        raise AuthorityError(f"Task worktree path already exists: {target}")
    branch_exists = not git(
        root,
        "show-ref",
        "--verify",
        f"refs/heads/{task_branch}",
        check=False,
    ).returncode
    if branch_exists:
        git(root, "worktree", "add", str(target), task_branch)
    else:
        git(root, "worktree", "add", "-b", task_branch, str(target), base_branch)
    return target


def _write_if_changed(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise AuthorityError(f"Refusing symlink projection: {path}")
    if path.is_file() and path.read_bytes() == data:
        return
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def plan_task(
    repo_root: Path,
    *,
    title: str,
    request: str | None = None,
    prd_source: Path | None = None,
    task_id: str | None = None,
    task_kind: str = "code",
    base_branch: str = "main",
    task_branch: str | None = None,
    worktree_path: Path | None = None,
    accepted_commit: str | None = None,
    operation_id: str | None = None,
    isolate: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    task_id = _slug(task_id or title)
    task_branch = task_branch or f"codex/{task_id}"
    if isolate:
        root = _isolated_task_root(
            root,
            task_id,
            base_branch,
            task_branch,
            worktree_path,
        )
        worktree_path = root
    task_dir_name = _task_dir_name(task_id)
    task_dir = root / ".trellis/tasks" / task_dir_name
    prd_path = task_dir / "prd.md"
    if prd_source:
        source = Path(prd_source).resolve()
        if not source.is_file() or source.is_symlink():
            raise AuthorityError("PRD source must be one regular file")
        prd = source.read_text(encoding="utf-8")
    elif request:
        prd = _minimal_prd(title, request, f"{task_id.upper()}-REQ-001")
    else:
        raise AuthorityError("Task planning requires request text or a PRD")
    try:
        requirement_ids = resolve_requirement_ids(root, prd, default_owner="codex")
    except PrdError as exc:
        raise AuthorityError(str(exc)) from exc
    prd_bytes = prd.encode("utf-8")
    prd_digest = sha256(prd_bytes).hexdigest()
    operation_id = operation_id or f"plan:{task_id}:{prd_digest}"
    commit = git(root, "rev-parse", f"{accepted_commit or 'HEAD'}^{{commit}}").stdout.strip()
    now = utc_now()
    with Authority(root, create=True) as authority, authority.transaction():
        existing = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if existing:
            if existing["prd_digest"] != prd_digest:
                raise AuthorityError("Task already exists with a different PRD")
            return authority.task_snapshot(task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        _write_if_changed(prd_path, prd_bytes)
        authority.execute(
            """INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                task_dir_name,
                title.strip(),
                task_kind,
                prd_path.relative_to(root).as_posix(),
                prd_digest,
                1,
                base_branch,
                task_branch,
                str(worktree_path.resolve()) if worktree_path else None,
                "ready",
                "not_started",
                "open",
                None,
                now,
                now,
            ),
        )
        binding = {
            "base_commit": _head(root),
            "requirement_ids": requirement_ids,
            "source": "accepted" if prd_source else "intent_snapshot",
        }
        authority.execute(
            "INSERT INTO bindings VALUES(?,?,?,?,?,?)",
            (task_id, 1, prd_digest, commit, now, canonical_json(binding)),
        )
        authority.record_event(
            operation_id,
            "task_planned",
            {"binding_generation": 1, "task_dir_name": task_dir_name},
            task_id=task_id,
            operation_input={"commit": commit, "prd_digest": prd_digest, "title": title},
        )
        return authority.task_snapshot(task_id)


def _normalize_actions(
    requirement_ids: Sequence[str],
    actions: Sequence[Mapping[str, object]] | None,
) -> list[dict[str, Any]]:
    if not actions:
        return [
            {
                "action_id": "implement",
                "kind": "implement",
                "dependencies": [],
                "requirement_ids": list(requirement_ids),
                "touches": ["**"],
                "check_ids": ["trellis.unittest.focused", "trellis.diff.check"],
                "risk": "low",
            }
        ]
    known_requirements = set(requirement_ids)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in actions:
        action = dict(raw)
        action_id = str(action.get("action_id") or "").strip()
        if not action_id or action_id in seen:
            raise AuthorityError("Action IDs must be non-empty and unique")
        seen.add(action_id)
        item = {
            "action_id": action_id,
            "kind": str(action.get("kind") or "implement"),
            "dependencies": sorted(set(action.get("dependencies") or [])),
            "requirement_ids": sorted(set(action.get("requirement_ids") or [])),
            "touches": sorted(set(action.get("touches") or [])),
            "check_ids": sorted(set(action.get("check_ids") or [])),
            "risk": str(action.get("risk") or "low"),
        }
        if (
            not item["requirement_ids"]
            or not item["touches"]
            or not item["check_ids"]
            or item["risk"] not in {"low", "medium", "high"}
        ):
            raise AuthorityError("Every action needs requirements, touches, checks, and risk")
        if not set(item["requirement_ids"]).issubset(known_requirements):
            raise AuthorityError("Action references a requirement outside the accepted binding")
        normalized.append(item)
    for action in normalized:
        if not set(action["dependencies"]).issubset(seen - {action["action_id"]}):
            raise AuthorityError("Action dependency is unknown or self-referential")
    unresolved = {action["action_id"]: set(action["dependencies"]) for action in normalized}
    resolved: set[str] = set()
    while unresolved:
        ready = {action_id for action_id, deps in unresolved.items() if deps <= resolved}
        if not ready:
            raise AuthorityError("Action graph contains a dependency cycle")
        resolved.update(ready)
        unresolved = {
            action_id: deps for action_id, deps in unresolved.items() if action_id not in ready
        }
    return normalized


def _execution_mode(actions: Sequence[Mapping[str, object]]) -> str:
    modules = {
        str(touch).split("/", 1)[0]
        for action in actions
        for touch in action["touches"]
        if str(touch) not in {"**", "*"}
    }
    return "delegated" if len(actions) > 3 or len(modules) > 1 else "compact"


def run_task(
    repo_root: Path,
    task_id: str,
    *,
    single: bool = False,
    actions: Sequence[Mapping[str, object]] | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    strategy = "single" if single else "loop"
    with Authority(root) as authority, authority.transaction():
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not task:
            raise AuthorityError(f"Unknown task: {task_id}")
        if task["work_state"] in {"completed", "cancelled"}:
            raise AuthorityError("Terminal task cannot be resumed")
        binding = authority.one(
            "SELECT * FROM bindings WHERE task_id=? AND generation=?",
            (task_id, task["active_binding_generation"]),
        )
        binding_payload = json.loads(binding["payload_json"])
        run_id = f"run-{digest({'task_id': task_id})}"
        now = utc_now()
        existing = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        generation = int(task["active_binding_generation"])
        latest_generation = 0
        if existing:
            latest_generation = int(
                authority.one(
                    "SELECT COALESCE(MAX(binding_generation),0) AS generation FROM actions WHERE run_id=?",
                    (existing["run_id"],),
                )["generation"]
            )
        needs_actions = not existing or latest_generation < generation
        normalized = _normalize_actions(binding_payload["requirement_ids"], actions)
        if generation > 1:
            id_map = {
                action["action_id"]: f"g{generation}:{action['action_id']}"
                for action in normalized
            }
            normalized = [
                {
                    **action,
                    "action_id": id_map[action["action_id"]],
                    "dependencies": [id_map[item] for item in action["dependencies"]],
                }
                for action in normalized
            ]
        operation_input = {
            "actions": normalized,
            "generation": generation,
            "strategy": strategy,
        }
        if operation_id:
            replay = authority.one(
                "SELECT event_type FROM events WHERE operation_id=?",
                (operation_id,),
            )
            if replay:
                authority.replay_event(
                    operation_id,
                    replay["event_type"],
                    operation_input,
                )
                return authority.task_snapshot(task_id)
        if existing and not needs_actions:
            persisted = [
                {
                    "action_id": row["action_id"],
                    "check_ids": json.loads(row["check_ids_json"]),
                    "dependencies": json.loads(row["dependencies_json"]),
                    "kind": row["kind"],
                    "requirement_ids": json.loads(row["requirement_ids_json"]),
                    "risk": row["risk"],
                    "touches": json.loads(row["touches_json"]),
                }
                for row in authority.all(
                    """SELECT * FROM actions WHERE run_id=? AND binding_generation=?
                       ORDER BY action_id""",
                    (existing["run_id"], generation),
                )
            ]
            if canonical_json(persisted) != canonical_json(
                sorted(normalized, key=lambda item: item["action_id"])
            ):
                raise AuthorityError(
                    "Action graph cannot change without a new binding generation"
                )
        if existing:
            run_id = existing["run_id"]
            if existing["strategy"] == "single" and not single:
                authority.execute(
                    "UPDATE runs SET strategy='loop',updated_at=? WHERE run_id=?",
                    (now, run_id),
                )
            elif strategy != existing["strategy"] and single:
                raise AuthorityError("A running Loop cannot be narrowed to single")
        else:
            authority.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    task_id,
                    strategy,
                    _execution_mode(normalized),
                    SOFT_ATTEMPT_LIMIT,
                    HARD_ATTEMPT_LIMIT,
                    STAGNATION_ROUNDS,
                    now,
                    now,
                ),
            )
        if needs_actions and existing:
            authority.execute(
                """UPDATE actions SET status='superseded',claimed_by=NULL,
                   claim_kind=NULL,updated_at=?
                   WHERE run_id=? AND binding_generation<? AND status!='passed'""",
                (now, run_id, generation),
            )
        for action in normalized if needs_actions else ():
            authority.execute(
                """INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    action["action_id"],
                    generation,
                    action["kind"],
                    canonical_json(action["dependencies"]),
                    canonical_json(action["requirement_ids"]),
                    canonical_json(action["touches"]),
                    canonical_json(action["check_ids"]),
                    action["risk"],
                    "pending",
                    None,
                    None,
                    now,
                    now,
                ),
            )
        event_type: str | None = None
        default_operation: str | None = None
        if not existing:
            event_type = "run_started"
            default_operation = f"run:{run_id}:start"
        elif needs_actions:
            event_type = "run_revised"
            default_operation = f"run:{run_id}:binding:{generation}"
        elif existing["strategy"] == "single" and not single:
            event_type = "run_resumed"
            default_operation = f"run:{run_id}:resume-loop"
        elif task["work_state"] != "running":
            event_type = "run_resumed"
            default_operation = (
                f"run:{run_id}:resume:{task['work_state']}:{generation}"
            )
        if event_type:
            authority.execute(
                "UPDATE tasks SET work_state='running',verified_candidate_digest=NULL,updated_at=? WHERE task_id=?",
                (now, task_id),
            )
        else:
            event_type = "run_unchanged"
            default_operation = (
                f"run:{run_id}:unchanged:{generation}:{strategy}:{digest(normalized)}"
            )
        authority.record_event(
            operation_id or str(default_operation),
            event_type,
            {"generation": generation, "run_id": run_id, "strategy": strategy},
            task_id=task_id,
            run_id=run_id,
            operation_input=operation_input,
        )
        return authority.task_snapshot(task_id)


def append_binding(
    repo_root: Path,
    task_id: str,
    *,
    prd_source: Path,
    accepted_commit: str,
    operation_id: str,
) -> int:
    root = Path(repo_root).resolve()
    source = Path(prd_source)
    text = source.read_text(encoding="utf-8")
    prd_bytes = text.encode("utf-8")
    prd_digest = sha256(prd_bytes).hexdigest()
    with Authority(root) as authority, authority.transaction():
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not task:
            raise AuthorityError(f"Unknown task: {task_id}")
        if task["work_state"] in {"completed", "cancelled"} or task[
            "archive_state"
        ] == "logical_archived":
            raise AuthorityError("Terminal task cannot accept a binding revision")
        task_root = Path(str(task["worktree_path"] or authority.repo_root)).resolve()
        if not task_root.is_dir():
            raise AuthorityError("Task worktree is unavailable")
        accepted_commit = git(
            task_root, "rev-parse", f"{accepted_commit}^{{commit}}"
        ).stdout.strip()
        try:
            requirements = resolve_requirement_ids(
                task_root, text, default_owner="codex"
            )
        except PrdError as exc:
            raise AuthorityError(str(exc)) from exc
        operation_input = {
            "accepted_commit": accepted_commit,
            "prd_digest": prd_digest,
        }
        existing_event = authority.one(
            "SELECT event_type FROM events WHERE operation_id=?",
            (operation_id,),
        )
        if existing_event:
            replay = authority.replay_event(
                operation_id,
                existing_event["event_type"],
                operation_input,
            )
            return int(replay["generation"])
        if task["prd_digest"] == prd_digest:
            generation = int(task["active_binding_generation"])
            authority.record_event(
                operation_id,
                "binding_unchanged",
                {"generation": generation, "prd_digest": prd_digest},
                task_id=task_id,
                operation_input=operation_input,
            )
            return generation
        generation = int(task["active_binding_generation"]) + 1
        task_path = task_root / task["prd_path"]
        _write_if_changed(task_path, prd_bytes)
        now = utc_now()
        authority.execute(
            "INSERT INTO bindings VALUES(?,?,?,?,?,?)",
            (
                task_id,
                generation,
                prd_digest,
                accepted_commit,
                now,
                canonical_json(
                    {"base_commit": _head(task_root), "requirement_ids": requirements}
                ),
            ),
        )
        run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
        if run:
            authority.execute(
                """UPDATE actions SET status='superseded',claimed_by=NULL,
                   claim_kind=NULL,updated_at=?
                   WHERE run_id=? AND binding_generation<? AND status!='passed'""",
                (now, run["run_id"], generation),
            )
        authority.execute(
            """UPDATE tasks SET prd_digest=?,active_binding_generation=?,
               work_state='human_blocked',verified_candidate_digest=NULL,
               updated_at=? WHERE task_id=?""",
            (prd_digest, generation, now, task_id),
        )
        authority.record_event(
            operation_id,
            "binding_accepted",
            {"generation": generation, "prd_digest": prd_digest},
            task_id=task_id,
            operation_input=operation_input,
        )
        return generation


def _touch_prefix(pattern: str) -> str:
    parts = re.split(r"[*?[]", pattern, maxsplit=1)
    return parts[0].rstrip("/")


def touches_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    for first in left:
        for second in right:
            if fnmatch.fnmatch(first, second) or fnmatch.fnmatch(second, first):
                return True
            first_prefix, second_prefix = _touch_prefix(first), _touch_prefix(second)
            if not first_prefix or not second_prefix:
                return True
            if first_prefix == second_prefix:
                return True
            if first_prefix.startswith(second_prefix + "/") or second_prefix.startswith(first_prefix + "/"):
                return True
    return False


def claim_actions(
    repo_root: Path,
    task_id: str,
    worker_id: str,
    *,
    operation_id: str | None = None,
) -> list[str]:
    with Authority(repo_root) as authority, authority.transaction():
        run = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        if not run:
            raise AuthorityError("Task has not been run")
        task = authority.one(
            "SELECT active_binding_generation FROM tasks WHERE task_id=?",
            (task_id,),
        )
        actions = authority.all(
            """SELECT * FROM actions WHERE run_id=? AND binding_generation=?
               ORDER BY action_id""",
            (run["run_id"], task["active_binding_generation"]),
        )
        owned = [
            row["action_id"]
            for row in actions
            if row["status"] == "running" and row["claimed_by"] == worker_id
        ]
        if owned:
            return owned
        states = {row["action_id"]: row["status"] for row in actions}
        active = [row for row in actions if row["status"] == "running" and row["claim_kind"] == "write"]
        claimed: list[str] = []
        for row in actions:
            if row["status"] != "pending":
                continue
            if any(states.get(dep) != "passed" for dep in json.loads(row["dependencies_json"])):
                continue
            touches = json.loads(row["touches_json"])
            kind = "read" if row["kind"] in {"research", "verify"} else "write"
            if kind == "write" and any(
                touches_overlap(touches, json.loads(other["touches_json"])) for other in active
            ):
                continue
            authority.execute(
                """UPDATE actions SET status='running',claimed_by=?,claim_kind=?,updated_at=?
                   WHERE run_id=? AND action_id=?""",
                (worker_id, kind, utc_now(), run["run_id"], row["action_id"]),
            )
            claimed.append(row["action_id"])
            if kind == "write":
                active.append(row)
        if claimed:
            authority.record_event(
                operation_id
                or f"claim:{run['run_id']}:{worker_id}:{digest(claimed)}",
                "actions_claimed",
                {"actions": claimed, "worker_id": worker_id},
                task_id=task_id,
                run_id=run["run_id"],
                operation_input={"actions": claimed, "worker_id": worker_id},
            )
        return claimed


def record_attempt(
    repo_root: Path,
    task_id: str,
    action_id: str,
    *,
    passed: bool,
    root_cause_fingerprint: str | None,
    candidate_digest: str | None,
    result: Mapping[str, object],
    operation_id: str,
) -> dict[str, Any]:
    if passed and not candidate_digest:
        raise AuthorityError("Passed attempt requires an exact candidate digest")
    with Authority(repo_root) as authority, authority.transaction():
        operation_input = {
            "action_id": action_id,
            "candidate_digest": candidate_digest,
            "passed": passed,
            "result": dict(result),
            "root_cause_fingerprint": root_cause_fingerprint,
        }
        replay = authority.replay_event(
            operation_id, "attempt_recorded", operation_input
        )
        if replay is not None:
            return replay
        run = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        if not run:
            raise AuthorityError("Task has not been run")
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        action = authority.one(
            "SELECT * FROM actions WHERE run_id=? AND action_id=?",
            (run["run_id"], action_id),
        )
        if not action:
            raise AuthorityError(f"Unknown action: {action_id}")
        if action["binding_generation"] != task["active_binding_generation"]:
            raise AuthorityError("Historical binding action is not executable")
        count = authority.one(
            "SELECT COUNT(*) AS count FROM attempts WHERE run_id=? AND action_id=?",
            (run["run_id"], action_id),
        )["count"]
        attempt_no = int(count) + 1
        if run["strategy"] == "single" and attempt_no > 1:
            raise AuthorityError("single strategy allows one attempt")
        if attempt_no > run["hard_attempt_limit"]:
            raise AuthorityError("hard attempt limit reached")
        if passed:
            from .release import repository_snapshot

            task_root = Path(str(task["worktree_path"] or authority.repo_root))
            actual_candidate = _working_candidate_digest(task_root)
            if candidate_digest and candidate_digest != actual_candidate:
                raise AuthorityError("Attempt candidate digest does not match the worktree")
            candidate_digest = actual_candidate
            required_checks = set(json.loads(action["check_ids_json"]))
            current_snapshot = repository_snapshot(task_root)
            passed_checks = {
                row["check_id"]
                for row in authority.all(
                    """SELECT check_id,after_digest FROM check_results
                       WHERE run_id=? AND action_id=? AND attempt_no=? AND passed=1""",
                    (run["run_id"], action_id, attempt_no),
                )
                if row["after_digest"] == current_snapshot
            }
            missing = sorted(required_checks - passed_checks)
            if missing:
                raise AuthorityError(
                    "Attempt cannot pass before bound checks are green: " + ", ".join(missing)
                )
        authority.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?)",
            (
                run["run_id"],
                action_id,
                attempt_no,
                root_cause_fingerprint,
                candidate_digest,
                canonical_json(dict(result)),
                utc_now(),
            ),
        )
        state = "passed" if passed else "pending"
        authority.execute(
            """UPDATE actions SET status=?,claimed_by=NULL,claim_kind=NULL,updated_at=?
               WHERE run_id=? AND action_id=?""",
            (state, utc_now(), run["run_id"], action_id),
        )
        work_state = "running"
        if not passed:
            if attempt_no >= 2 and run["execution_mode"] == "compact":
                authority.execute(
                    "UPDATE runs SET execution_mode='delegated',updated_at=? WHERE run_id=?",
                    (utc_now(), run["run_id"]),
                )
            recent = authority.all(
                """SELECT root_cause_fingerprint FROM attempts
                   WHERE run_id=? AND action_id=? ORDER BY attempt_no DESC LIMIT ?""",
                (run["run_id"], action_id, run["stagnation_rounds"]),
            )
            stagnant = (
                len(recent) == run["stagnation_rounds"]
                and root_cause_fingerprint
                and all(row["root_cause_fingerprint"] == root_cause_fingerprint for row in recent)
            )
            if stagnant or attempt_no >= run["hard_attempt_limit"] or run["strategy"] == "single":
                work_state = "human_blocked"
        else:
            pending = authority.one(
                """SELECT COUNT(*) AS count FROM actions
                   WHERE run_id=? AND binding_generation=? AND status!='passed'""",
                (run["run_id"], task["active_binding_generation"]),
            )["count"]
            if pending == 0:
                high_risk = authority.one(
                    """SELECT COUNT(*) AS count FROM actions
                       WHERE run_id=? AND binding_generation=? AND risk='high'""",
                    (run["run_id"], task["active_binding_generation"]),
                )["count"]
                work_state = "running" if high_risk else "verified"
        verified_candidate = (
            _task_authorization_candidate_digest(task_root, task)
            if work_state == "verified"
            else None
        )
        authority.execute(
            """UPDATE tasks SET work_state=?,verified_candidate_digest=?,updated_at=?
               WHERE task_id=?""",
            (work_state, verified_candidate, utc_now(), task_id),
        )
        payload = {
            "action_id": action_id,
            "attempt": attempt_no,
            "passed": passed,
            "work_state": work_state,
        }
        authority.record_event(
            operation_id,
            "attempt_recorded",
            payload,
            task_id=task_id,
            run_id=run["run_id"],
            operation_input=operation_input,
        )
        return payload


def record_check_result(
    repo_root: Path,
    task_id: str,
    *,
    action_id: str | None,
    attempt_no: int,
    check_id: str,
    phase: str,
    operation_id: str,
) -> dict[str, Any]:
    from .release import load_catalog, load_overlay, repository_snapshot, resolve_check, run_check

    root = Path(repo_root).resolve()
    if attempt_no < 1:
        raise AuthorityError("Check attempt number must be positive")
    with Authority(root) as authority:
        run = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        if not run:
            raise AuthorityError("Task has not been run")
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        action = (
            authority.one(
                "SELECT * FROM actions WHERE run_id=? AND action_id=?",
                (run["run_id"], action_id),
            )
            if action_id
            else None
        )
        if action_id and (
            not action
            or action["binding_generation"] != task["active_binding_generation"]
        ):
            raise AuthorityError("Check action is not active")
        if (
            phase == "attempt"
            and action
            and check_id not in json.loads(action["check_ids_json"])
        ):
            raise AuthorityError("Attempt check is not bound to the action")
        task_root = Path(str(task["worktree_path"] or authority.repo_root))
    catalog = load_catalog(task_root)
    version = str(load_overlay(task_root)["trellis_base_version"])
    resolved = resolve_check(
        catalog,
        check_id,
        version=version,
        phase=phase,
        role="source",
    )
    operation_input = {
        "action_id": action_id,
        "attempt_no": attempt_no,
        "check_id": check_id,
        "input_digest": resolved["input_digest"],
        "phase": phase,
        "resolved_argv": resolved["argv"],
    }
    with Authority(root) as authority, authority.transaction():
        replay = authority.replay_event(
            operation_id,
            "check_recorded",
            operation_input,
        )
        if replay is not None:
            return replay
    result = run_check(task_root, resolved)
    with Authority(root) as authority, authority.transaction():
        replay = authority.replay_event(
            operation_id,
            "check_recorded",
            operation_input,
        )
        if replay is not None:
            return replay
        if repository_snapshot(task_root) != result["after_digest"]:
            raise AuthorityError("Candidate changed before check evidence was recorded")
        values = (
            operation_id,
            run["run_id"],
            action_id,
            attempt_no,
            check_id,
            result["input_digest"],
            canonical_json(result["argv"]),
            result["argv_digest"],
            phase,
            result["return_code"],
            result["output_digest"],
            result["before_digest"],
            result["after_digest"],
            int(result["passed"]),
            utc_now(),
        )
        authority.execute(
            """INSERT INTO check_results(
                 operation_id,run_id,action_id,attempt_no,check_id,input_digest,
                 argv_json,argv_digest,phase,
                 return_code,output_digest,before_digest,after_digest,passed,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(operation_id) DO NOTHING""",
            values,
        )
        authority.record_event(
            operation_id,
            "check_recorded",
            {
                "action_id": action_id,
                "attempt_no": attempt_no,
                "check_id": check_id,
                "passed": result["passed"],
            },
            task_id=task_id,
            run_id=run["run_id"],
            operation_input=operation_input,
        )
        return dict(result)


def record_review(
    repo_root: Path,
    task_id: str,
    *,
    candidate_digest: str,
    reviewer_id: str,
    findings: Sequence[Mapping[str, object]],
    semantic: bool,
    operation_id: str,
) -> dict[str, Any]:
    with Authority(repo_root) as authority, authority.transaction():
        operation_input = {
            "candidate": candidate_digest,
            "findings": [dict(item) for item in findings],
            "reviewer_id": reviewer_id,
            "semantic": semantic,
        }
        replay = authority.replay_event(
            operation_id, "candidate_reviewed", operation_input
        )
        if replay is not None:
            return replay
        run = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        if not run:
            raise AuthorityError("Task has not been run")
        pending = authority.one(
            """SELECT COUNT(*) AS count FROM actions
               WHERE run_id=? AND binding_generation=(
                 SELECT active_binding_generation FROM tasks WHERE task_id=?
               ) AND status!='passed'""",
            (run["run_id"], task_id),
        )["count"]
        if pending:
            raise AuthorityError("Model review requires a complete deterministic candidate")
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        task_root = Path(str(task["worktree_path"] or authority.repo_root))
        actual_candidate = _working_candidate_digest(task_root)
        if candidate_digest != actual_candidate:
            raise AuthorityError("Review candidate digest does not match the worktree")
        from .release import repository_snapshot

        current_snapshot = repository_snapshot(task_root)
        stale_actions = authority.all(
            """SELECT actions.action_id FROM actions
               WHERE actions.run_id=? AND actions.binding_generation=?
                 AND (SELECT candidate_digest FROM attempts
                      WHERE attempts.run_id=actions.run_id
                        AND attempts.action_id=actions.action_id
                      ORDER BY attempt_no DESC LIMIT 1) != ?
                 AND NOT EXISTS(
                   SELECT 1 FROM check_results
                   WHERE check_results.run_id=actions.run_id
                     AND check_results.action_id=actions.action_id
                     AND check_results.phase='finding_closure'
                     AND check_results.passed=1
                     AND check_results.after_digest=?
                 )""",
            (
                run["run_id"],
                task["active_binding_generation"],
                actual_candidate,
                current_snapshot,
            ),
        )
        if stale_actions:
            raise AuthorityError(
                "Model review requires checks against the exact final candidate"
            )
        review_count = authority.one(
            "SELECT COUNT(*) AS count FROM reviews WHERE run_id=?",
            (run["run_id"],),
        )["count"]
        review_no = int(review_count) + 1
        if review_no > 2:
            authority.execute(
                "UPDATE tasks SET work_state='human_blocked',updated_at=? WHERE task_id=?",
                (utc_now(), task_id),
            )
            return {
                "blocking": 0,
                "rejected": "third_review_prohibited",
                "review_no": review_no,
                "work_state": "human_blocked",
            }
        normalized: list[dict[str, Any]] = []
        blocking = 0
        for raw in findings:
            item = dict(raw)
            category = str(item.get("category") or "correctness")
            severity = str(item.get("severity") or "advisory")
            if category not in BLOCKING_CATEGORIES:
                severity = "advisory"
            if severity == "blocking":
                blocking += 1
            scope = sorted(set(item.get("scope") or []))
            requirements = sorted(set(item.get("requirement_ids") or []))
            finding_id = "finding-" + digest(
                {"category": category, "requirements": requirements, "scope": scope}
            )
            normalized_item = {
                "category": category,
                "finding_id": finding_id,
                "requirement_ids": requirements,
                "scope": scope,
                "severity": severity,
                "source": str(item.get("source") or "model"),
            }
            normalized.append(normalized_item)
            authority.execute(
                """INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(finding_id) DO UPDATE SET
                     source=excluded.source,
                     severity=excluded.severity,
                     category=excluded.category,
                     requirement_ids_json=excluded.requirement_ids_json,
                     scope_json=excluded.scope_json,
                     opened_candidate_digest=excluded.opened_candidate_digest,
                     status='open',
                     closure_evidence_json='{}'""",
                (
                    finding_id, run["run_id"], normalized_item["source"], severity,
                    category, canonical_json(requirements), canonical_json(scope),
                    candidate_digest, "open", "{}",
                ),
            )
        authority.execute(
            "INSERT INTO reviews VALUES(?,?,?,?,?,?,?)",
            (
                run["run_id"], candidate_digest, review_no, reviewer_id,
                canonical_json(normalized), int(semantic), utc_now(),
            ),
        )
        blocking = authority.one(
            "SELECT COUNT(*) AS count FROM findings WHERE run_id=? AND status='open' AND severity='blocking'",
            (run["run_id"],),
        )["count"]
        state = "verified" if not blocking else ("human_blocked" if review_no == 2 else "running")
        authority.execute(
            """UPDATE tasks SET work_state=?,verified_candidate_digest=?,updated_at=?
               WHERE task_id=?""",
            (
                state,
                _task_authorization_candidate_digest(task_root, task)
                if state == "verified"
                else None,
                utc_now(),
                task_id,
            ),
        )
        payload = {"blocking": blocking, "review_no": review_no, "work_state": state}
        authority.record_event(
            operation_id,
            "candidate_reviewed",
            payload,
            task_id=task_id,
            run_id=run["run_id"],
            operation_input=operation_input,
        )
        return payload


def close_finding(
    repo_root: Path,
    finding_id: str,
    *,
    targeted_checks: Sequence[str],
    full_regression: str,
    delta_paths: Sequence[str],
) -> None:
    if not targeted_checks or not full_regression:
        raise AuthorityError("Finding closure requires targeted and full checks")
    with Authority(repo_root) as authority, authority.transaction():
        finding = authority.one("SELECT * FROM findings WHERE finding_id=?", (finding_id,))
        if not finding:
            raise AuthorityError(f"Unknown finding: {finding_id}")
        review = authority.one(
            """SELECT created_at FROM reviews
               WHERE run_id=? AND candidate_digest=? ORDER BY review_no DESC LIMIT 1""",
            (finding["run_id"], finding["opened_candidate_digest"]),
        )
        proof_checks: dict[str, Mapping[str, object]] = {}
        for operation_id in [*targeted_checks, full_regression]:
            check = authority.one(
                """SELECT passed,created_at,after_digest FROM check_results
                   WHERE run_id=? AND operation_id=?""",
                (finding["run_id"], operation_id),
            )
            if (
                not check
                or not check["passed"]
                or (review and check["created_at"] <= review["created_at"])
            ):
                raise AuthorityError(
                    "Finding closure requires green post-review check results"
                )
            proof_checks[operation_id] = check
        allowed = json.loads(finding["scope_json"])
        if allowed and any(not any(fnmatch.fnmatch(path, pattern) for pattern in allowed) for path in delta_paths):
            raise AuthorityError("Finding fix expanded outside its allowed delta")
        task = authority.one(
            """SELECT tasks.* FROM tasks JOIN runs ON runs.task_id=tasks.task_id
               WHERE runs.run_id=?""",
            (finding["run_id"],),
        )
        task_root = Path(str(task["worktree_path"] or authority.repo_root))
        from .release import repository_snapshot

        current_snapshot = repository_snapshot(task_root)
        if any(
            check["after_digest"] != current_snapshot
            for check in proof_checks.values()
        ):
            raise AuthorityError(
                "Finding closure checks do not bind the current candidate"
            )
        task_prefix = f".trellis/tasks/{task['task_dir_name']}/"
        actual_delta = [
            path
            for path in _changed_paths(task_root)
            if path != "BOARD.md" and not path.startswith(task_prefix)
        ]
        if set(actual_delta) - set(delta_paths):
            raise AuthorityError("Finding delta evidence omits changed worktree paths")
        historical = authority.all(
            """SELECT finding_id,closure_evidence_json FROM findings
               WHERE run_id=? AND severity='blocking' AND status='fixed'
                 AND finding_id!=? ORDER BY finding_id""",
            (finding["run_id"], finding_id),
        )
        for prior in historical:
            prior_evidence = json.loads(prior["closure_evidence_json"])
            prior_check = authority.one(
                """SELECT passed,created_at FROM check_results
                   WHERE run_id=? AND operation_id=?""",
                (finding["run_id"], prior_evidence.get("full_regression")),
            )
            if (
                not prior_check
                or not prior_check["passed"]
                or proof_checks[full_regression]["created_at"]
                <= prior_check["created_at"]
            ):
                raise AuthorityError(
                    "Historical blocking finding requires a fresh full regression"
                )
        evidence = {
            "delta_paths": sorted(actual_delta),
            "full_regression": full_regression,
            "historical_reverified": {
                row["finding_id"]: full_regression for row in historical
            },
            "targeted_checks": sorted(targeted_checks),
        }
        authority.execute(
            "UPDATE findings SET status='fixed',closure_evidence_json=? WHERE finding_id=?",
            (canonical_json(evidence), finding_id),
        )
        open_blockers = authority.one(
            "SELECT COUNT(*) AS count FROM findings WHERE run_id=? AND status='open' AND severity='blocking'",
            (finding["run_id"],),
        )["count"]
        pending = authority.one(
            """SELECT COUNT(*) AS count FROM actions
               WHERE run_id=? AND binding_generation=? AND status!='passed'""",
            (finding["run_id"], task["active_binding_generation"]),
        )["count"]
        if not open_blockers and not pending:
            candidate = _task_authorization_candidate_digest(task_root, task)
            authority.execute(
                """UPDATE tasks SET work_state='verified',verified_candidate_digest=?,updated_at=?
                   WHERE task_id=?""",
                (candidate, utc_now(), task["task_id"]),
            )
        authority.record_event(
            f"finding:close:{finding_id}:{digest(evidence)}",
            "finding_closed",
            evidence,
            task_id=task["task_id"],
            run_id=finding["run_id"],
            operation_input=evidence,
        )


def task_status(repo_root: Path, task_id: str | None = None) -> dict[str, Any]:
    with Authority(repo_root) as authority:
        authority.verify_event_chain()
        if task_id:
            return authority.task_snapshot(task_id)
        rows = authority.all("SELECT task_id FROM tasks ORDER BY created_at,task_id")
        return {"tasks": [authority.task_snapshot(row["task_id"]) for row in rows]}


def rebuild_projections(repo_root: Path) -> dict[str, str]:
    root = Path(repo_root).resolve()
    with Authority(root) as authority, authority.transaction():
        tasks = authority.all("SELECT * FROM tasks ORDER BY created_at,task_id")
        board_lines = ["# Task Board", "", "Generated projection. SQLite is authoritative.", ""]
        for task in tasks:
            run = authority.one("SELECT * FROM runs WHERE task_id=?", (task["task_id"],))
            projection = {
                "archive_state": task["archive_state"],
                "closeout_state": task["closeout_state"],
                "id": task["task_id"],
                "run_id": run["run_id"] if run else None,
                "strategy": run["strategy"] if run else None,
                "title": task["title"],
                "work_state": task["work_state"],
            }
            path = root / ".trellis/tasks" / task["task_dir_name"] / "task.json"
            data = (json.dumps(projection, indent=2, sort_keys=True) + "\n").encode()
            _write_if_changed(path, data)
            board_lines.append(
                f"- `{task['task_id']}`: {task['work_state']} / "
                f"{task['closeout_state']} / {task['archive_state']}"
            )
        board = ("\n".join(board_lines) + "\n").encode()
        _write_if_changed(root / "BOARD.md", board)
        seq = authority.one("SELECT COALESCE(MAX(seq),0) AS seq FROM events")["seq"]
        authority.execute(
            "INSERT OR REPLACE INTO projections VALUES(?,?,?,?)",
            ("BOARD.md", seq, sha256(board).hexdigest(), utc_now()),
        )
        return {"BOARD.md": sha256(board).hexdigest(), "tasks": str(len(tasks))}


def cancel_task(repo_root: Path, task_id: str, *, authorization_ref: str) -> dict[str, Any]:
    with Authority(repo_root) as authority, authority.transaction():
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not task:
            raise AuthorityError(f"Unknown task: {task_id}")
        if task["work_state"] in {"completed", "cancelled"}:
            return authority.task_snapshot(task_id)
        authority.execute(
            "UPDATE tasks SET work_state='cancelled',updated_at=? WHERE task_id=?",
            (utc_now(), task_id),
        )
        authority.record_event(
            f"cancel:{task_id}:{digest(authorization_ref)}",
            "task_cancelled",
            {"authorization_ref_digest": digest(authorization_ref)},
            task_id=task_id,
        )
        return authority.task_snapshot(task_id)


def _closeout_summary(authority: Authority, task_id: str) -> dict[str, Any]:
    snapshot = authority.task_snapshot(task_id)
    run_id = snapshot["run"]["run_id"]
    checks = authority.all(
        """SELECT action_id,attempt_no,check_id,argv_digest,return_code,output_digest
           FROM check_results WHERE run_id=? ORDER BY check_result_id""",
        (run_id,),
    )
    reviews = authority.all(
        "SELECT candidate_digest,review_no,findings_json FROM reviews WHERE run_id=? ORDER BY review_no",
        (run_id,),
    )
    return {
        "binding_generation": snapshot["task"]["active_binding_generation"],
        "checks": [dict(row) for row in checks],
        "reviews": [dict(row) for row in reviews],
        "run_id": run_id,
        "task_id": task_id,
    }


def _branch_worktree(repo_root: Path, branch: str) -> Path | None:
    current: Path | None = None
    for line in git(repo_root, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}":
            return current
    return None


def _control_worktree(repo_root: Path, task_root: Path) -> Path | None:
    for line in git(repo_root, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            candidate = Path(line.removeprefix("worktree "))
            if candidate != task_root:
                return candidate
    return None


def _closeout_roots(
    authority: Authority,
    task: Mapping[str, object],
) -> tuple[Path, Path | None, Path | None]:
    task_root = Path(str(task["worktree_path"] or authority.repo_root))
    merged = authority.one(
        """SELECT evidence_json FROM closeout_steps
           WHERE task_id=? AND partition_id='source' AND step_name='local_merge_to_base'""",
        (task["task_id"],),
    )
    evidence = json.loads(merged["evidence_json"]) if merged else {}
    base_root = Path(evidence["base_root"]) if evidence.get("base_root") else None
    control_root = (
        Path(evidence["control_root"]) if evidence.get("control_root") else None
    )
    if base_root is None and authority.repo_root.is_dir():
        base_root = _branch_worktree(authority.repo_root, str(task["base_branch"]))
    if control_root is None and authority.repo_root.is_dir():
        control_root = _control_worktree(authority.repo_root, task_root)
    return task_root, base_root, control_root


def _changed_paths(repo_root: Path) -> list[str]:
    tracked = git(repo_root, "diff", "--name-only", "-z", "HEAD", "--").stdout
    untracked = git(
        repo_root, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout
    paths = set(filter(None, (tracked + untracked).split("\0")))
    version = Path(repo_root) / ".trellis/.version"
    if version.is_file() and git(
        repo_root,
        "ls-files",
        "--error-unmatch",
        "--",
        ".trellis/.version",
        check=False,
    ).returncode:
        paths.add(".trellis/.version")
    return sorted(paths)


def _scoped_commit_paths(
    authority: Authority,
    task: Mapping[str, object],
    changed: Sequence[str],
) -> list[str]:
    run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task["task_id"],))
    patterns = {
        pattern
        for row in authority.all(
            """SELECT touches_json FROM actions
               WHERE run_id=? AND binding_generation=?""",
            (run["run_id"], task["active_binding_generation"]),
        )
        for pattern in json.loads(row["touches_json"])
    }
    task_prefix = f".trellis/tasks/{task['task_dir_name']}/"
    projections = {"BOARD.md", f"{task_prefix}task.json"}
    task_assets = {
        f"{task_prefix}prd.md",
        f"{task_prefix}run-summary.json",
    }
    owned = [
        path
        for path in changed
        if path in task_assets
        or (not path.startswith(task_prefix) and any(fnmatch.fnmatch(path, pattern) for pattern in patterns))
    ]
    blockers = sorted(set(changed) - set(owned) - projections)
    if blockers:
        raise AuthorityError(
            "Closeout found changes outside Task action scopes: " + ", ".join(blockers)
        )
    return sorted(owned)


def _discard_cancelled_paths(repo_root: Path, paths: Sequence[str]) -> None:
    for raw in paths:
        path = Path(repo_root) / raw
        tracked = git(
            repo_root, "ls-files", "--error-unmatch", "--", raw, check=False
        ).returncode == 0
        if tracked:
            git(repo_root, "restore", "--staged", "--worktree", "--", raw)
        elif path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _update_running_step_evidence(
    authority: Authority,
    task_id: str,
    partition_id: str,
    values: Mapping[str, object],
) -> None:
    row = authority.one(
        """SELECT evidence_json FROM closeout_steps
           WHERE task_id=? AND partition_id=? AND step_name='scoped_commit'""",
        (task_id, partition_id),
    )
    evidence = json.loads(row["evidence_json"]) if row else {}
    evidence.update(values)
    with authority.transaction():
        authority.execute(
            """UPDATE closeout_steps SET evidence_json=?,updated_at=?
               WHERE task_id=? AND partition_id=? AND step_name='scoped_commit'""",
            (
                canonical_json(evidence),
                utc_now(),
                task_id,
                partition_id,
            ),
        )


def _commit_matches(repo_root: Path, plan: Mapping[str, object]) -> bool:
    if _head(repo_root) == plan["parent"]:
        return False
    return (
        git(repo_root, "rev-parse", "HEAD^{tree}").stdout.strip()
        == plan["tree"]
        and git(repo_root, "show", "-s", "--format=%P", "HEAD").stdout.strip()
        == plan["parent"]
        and git(repo_root, "show", "-s", "--format=%s", "HEAD").stdout.strip()
        == plan["message"]
        and not _changed_paths(repo_root)
    )


def _default_closeout_handler(
    authority: Authority,
    task: Mapping[str, object],
    step: str,
) -> Mapping[str, object]:
    task_root, base_root, control_root = _closeout_roots(authority, task)
    task_id = str(task["task_id"])
    if step == "freeze_candidate":
        return {
            "head": _head(task_root),
            "tree": git(task_root, "rev-parse", "HEAD^{tree}").stdout.strip(),
            "working_candidate": _working_candidate_digest(task_root),
        }
    if step == "classify_base_drift":
        base = str(task["base_branch"])
        task_branch = str(task["task_branch"])
        merge_base = git(task_root, "merge-base", base, task_branch).stdout.strip()
        base_paths = set(
            filter(
                None,
                git(
                    task_root,
                    "diff",
                    "--name-only",
                    "-z",
                    f"{merge_base}..{base}",
                ).stdout.split("\0"),
            )
        )
        task_paths = set(
            filter(
                None,
                git(
                    task_root,
                    "diff",
                    "--name-only",
                    "-z",
                    f"{merge_base}..{task_branch}",
                ).stdout.split("\0"),
            )
        ) | set(_changed_paths(task_root))
        base_dirty = set(_changed_paths(base_root)) if base_root else set()
        overlap = sorted(
            path
            for path in base_paths | base_dirty
            if touches_overlap([path], sorted(task_paths))
        )
        if overlap:
            raise AuthorityError(
                "Base drift overlaps the task candidate: " + ", ".join(overlap)
            )
        classification = "none" if not base_paths else "non_overlapping"
        return {
            "base_dirty": sorted(base_dirty),
            "base_paths": sorted(base_paths),
            "classification": classification,
            "merge_base": merge_base,
        }
    if step == "reconcile_task_branch":
        drift = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=? AND partition_id='source' AND step_name='classify_base_drift'""",
            (task_id,),
        )
        evidence = json.loads(drift["evidence_json"])
        if evidence["classification"] == "non_overlapping":
            git(task_root, "merge", "--no-edit", str(task["base_branch"]))
        return {"head": _head(task_root)}
    if step == "full_reverify":
        if task["work_state"] == "cancelled":
            return {"cancelled": True, "failed_checks": "not_applicable"}
        run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
        if task["task_kind"] == "sync":
            slots = authority.all(
                "SELECT target_id,state,receipt_json FROM target_slots WHERE run_id=?",
                (run["run_id"],),
            )
            if not slots or any(slot["state"] != "closed" for slot in slots):
                raise AuthorityError("Sync closeout requires every target partition closed")
            return {
                "candidate_digest": _working_candidate_digest(task_root),
                "target_receipts_digest": digest(
                    [
                        (slot["target_id"], json.loads(slot["receipt_json"]))
                        for slot in slots
                    ]
                ),
            }
        required = {
            check_id
            for action in authority.all(
                """SELECT check_ids_json FROM actions
                   WHERE run_id=? AND binding_generation=?""",
                (run["run_id"], task["active_binding_generation"]),
            )
            for check_id in json.loads(action["check_ids_json"])
        }
        results = []
        catalog_path = task_root / ".trellis/releases/check-catalog.json"
        if catalog_path.is_file():
            from .release import load_catalog, load_overlay, resolve_check, run_check

            catalog = load_catalog(task_root)
            version = str(load_overlay(task_root)["trellis_base_version"])
            results = [
                run_check(
                    task_root,
                    resolve_check(
                        catalog,
                        check_id,
                        version=version,
                        phase="closeout_reverify",
                        role="source",
                    ),
                )
                for check_id in sorted(required)
            ]
        else:
            for check_id in sorted(required):
                prior = authority.one(
                    """SELECT argv_json FROM check_results
                       WHERE run_id=? AND check_id=? AND passed=1
                       ORDER BY check_result_id DESC LIMIT 1""",
                    (run["run_id"], check_id),
                )
                if not prior:
                    raise AuthorityError(f"Missing bound check evidence: {check_id}")
                before = _working_candidate_digest(task_root)
                result = subprocess.run(
                    json.loads(prior["argv_json"]),
                    cwd=task_root,
                    check=False,
                    capture_output=True,
                    timeout=180,
                )
                after = _working_candidate_digest(task_root)
                results.append(
                    {
                        "check_id": check_id,
                        "mutation_detected": before != after,
                        "passed": result.returncode == 0 and before == after,
                        "return_code": result.returncode,
                    }
                )
        if not all(result["passed"] for result in results):
            raise AuthorityError("Bound deterministic closeout checks failed")
        return {
            "candidate_digest": _working_candidate_digest(task_root),
            "checks": results,
        }
    if step == "prepare_git_artifacts":
        summary = _closeout_summary(authority, task_id)
        path = task_root / ".trellis/tasks" / str(task["task_dir_name"]) / "run-summary.json"
        _write_if_changed(path, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode())
        return {
            "candidate_digest": _working_candidate_digest(task_root),
            "authorization_candidate_digest": _task_authorization_candidate_digest(
                task_root, task
            ),
            "run_summary_digest": sha256(path.read_bytes()).hexdigest(),
        }
    if step == "scoped_commit":
        prepared = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=? AND partition_id='source'
                 AND step_name='prepare_git_artifacts'""",
            (task_id,),
        )
        prepared_evidence = json.loads(prepared["evidence_json"])
        expected = prepared_evidence["authorization_candidate_digest"]
        current_step = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=? AND partition_id='source' AND step_name='scoped_commit'""",
            (task_id,),
        )
        running_evidence = json.loads(current_step["evidence_json"])
        plan = running_evidence.get("commit_plan")
        if isinstance(plan, Mapping) and _commit_matches(task_root, plan):
            from .release import bind_qualified_releases_to_head

            bind_qualified_releases_to_head(authority, task_root)
            return {
                "commit": _head(task_root),
                "paths": list(plan["paths"]),
                "recovered_external_effect": True,
            }
        if _task_authorization_candidate_digest(task_root, task) != expected:
            raise AuthorityError("Task candidate changed after closeout verification")
        paths = _scoped_commit_paths(authority, task, _changed_paths(task_root))
        if task["work_state"] == "cancelled":
            task_prefix = f".trellis/tasks/{task['task_dir_name']}/"
            _discard_cancelled_paths(
                task_root, [path for path in paths if not path.startswith(task_prefix)]
            )
            paths = _scoped_commit_paths(authority, task, _changed_paths(task_root))
        if paths:
            normal = [path for path in paths if not path.startswith(".trellis/tasks/")]
            if normal:
                git(task_root, "add", "--", *normal)
            task_assets = [path for path in paths if path.startswith(".trellis/tasks/")]
            if task_assets:
                git(task_root, "add", "-f", "--", *task_assets)
            scope_marker = task_root / ".trellis/scripts/mark_scope_ok.sh"
            if scope_marker.is_file():
                marked = subprocess.run(
                    [str(scope_marker)], cwd=task_root, check=False, capture_output=True, text=True
                )
                if marked.returncode:
                    raise AuthorityError(marked.stderr.strip() or "Scope marker failed")
            message = f"feat(task): {task['title']}"
            plan = {
                "authorization_candidate_digest": _task_authorization_candidate_digest(
                    task_root, task
                ),
                "message": message,
                "parent": _head(task_root),
                "paths": paths,
                "tree": git(task_root, "write-tree").stdout.strip(),
            }
            _update_running_step_evidence(
                authority,
                task_id,
                "source",
                {"commit_plan": plan},
            )
            git(task_root, "commit", "-m", message)
        from .release import bind_qualified_releases_to_head

        bind_qualified_releases_to_head(authority, task_root)
        return {"commit": _head(task_root), "paths": paths}
    if step == "local_merge_to_base":
        task_oid = git(task_root, "rev-parse", f"{task['task_branch']}^{{commit}}").stdout.strip()
        base_oid = git(task_root, "rev-parse", f"{task['base_branch']}^{{commit}}").stdout.strip()
        if git(task_root, "merge-base", "--is-ancestor", base_oid, task_oid, check=False).returncode:
            raise AuthorityError("Local merge is not a fast-forward")
        if base_root:
            git(base_root, "merge", "--ff-only", str(task["task_branch"]))
        else:
            git(
                task_root,
                "update-ref",
                f"refs/heads/{task['base_branch']}",
                task_oid,
                base_oid,
            )
        return {
            "base_commit": task_oid,
            "base_root": str(base_root) if base_root else None,
            "control_root": str(control_root) if control_root else None,
        }
    if step == "record_completion":
        authority.execute(
            "UPDATE tasks SET work_state='completed',updated_at=? WHERE task_id=?",
            (utc_now(), task_id),
        )
        return {"work_state": "completed"}
    if step == "logical_archive":
        authority.execute(
            "UPDATE tasks SET archive_state='logical_archived',updated_at=? WHERE task_id=?",
            (utc_now(), task_id),
        )
        return {"archive_state": "logical_archived"}
    if step == "clear_runtime_and_pointers":
        pointer = git_common_dir(base_root or task_root) / "trellis/current-task"
        if pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == task_id:
            pointer.unlink()
        return {"pointer_cleared": True}
    if step == "remove_worktree":
        worktree = task["worktree_path"]
        if worktree:
            if control_root is None:
                raise AuthorityError("A separate control worktree is required for cleanup")
            registered = _branch_worktree(control_root, str(task["task_branch"]))
            if registered and registered != Path(str(worktree)):
                raise AuthorityError("Task branch is registered to an unexpected worktree")
            if registered:
                git(control_root, "worktree", "remove", str(worktree))
        return {"worktree_removed": bool(worktree)}
    if step == "remove_merged_local_branch":
        if control_root is None:
            raise AuthorityError("A separate control worktree is required for cleanup")
        branch = str(task["task_branch"])
        if not git(
            control_root,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        ).returncode:
            git(control_root, "branch", "-d", branch)
        return {"branch_removed": str(task["task_branch"])}
    if step == "finalize_journal_projection":
        if base_root is None:
            return {"deferred": "base_branch_not_checked_out"}
        return rebuild_projections(base_root)
    raise AuthorityError(f"Unknown closeout step: {step}")


def _target_closeout_handler(
    authority: Authority,
    slot: Mapping[str, object],
    step: str,
) -> Mapping[str, object]:
    target = Path(str(slot["target_root"]))
    worktree = Path(str(slot["worktree_path"]))
    branch = str(slot["branch"])
    receipt = json.loads(str(slot["receipt_json"]))
    base_branch = str(receipt["target_branch"])
    if step == "freeze_candidate":
        return {"changed_paths": _changed_paths(worktree), "head": _head(worktree)}
    if step == "classify_base_drift":
        if _branch(target) != base_branch:
            raise AuthorityError("Target base branch changed after candidate planning")
        planned = [
            *json.loads(str(slot["managed_paths_json"])),
            ".trellis/deploy/adoption.json",
        ]
        base_paths = set(
            filter(
                None,
                git(
                    target,
                    "diff",
                    "--name-only",
                    "-z",
                    f"{receipt['target_base']}..{base_branch}",
                ).stdout.split("\0"),
            )
        )
        base_dirty = set(_changed_paths(target))
        if git(
            target,
            "ls-files",
            "--error-unmatch",
            "--",
            ".trellis/.version",
            check=False,
        ).returncode:
            base_dirty.discard(".trellis/.version")
        overlap = sorted(
            path
            for path in base_paths | base_dirty
            if touches_overlap([path], planned)
        )
        if overlap:
            raise AuthorityError(
                "Target base drift overlaps the candidate: " + ", ".join(overlap)
            )
        return {
            "base_dirty": sorted(base_dirty),
            "base_paths": sorted(base_paths),
            "classification": "non_overlapping" if base_paths else "none",
        }
    if step == "reconcile_task_branch":
        drift = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=(SELECT task_id FROM runs WHERE run_id=?)
                 AND partition_id=? AND step_name='classify_base_drift'""",
            (slot["run_id"], f"target:{slot['target_id']}"),
        )
        evidence = json.loads(drift["evidence_json"])
        if evidence["classification"] == "non_overlapping":
            git(worktree, "merge", "--no-edit", base_branch)
        return {"head": _head(worktree)}
    if step == "full_reverify":
        from .release import (
            _dirty_paths,
            _index_digest,
            _target_version,
            load_catalog,
            repository_snapshot,
            resolve_check,
            run_check,
        )

        release_row = authority.one(
            "SELECT manifest_json FROM release_bindings WHERE release_id=? AND qualified=1",
            (receipt["release_id"],),
        )
        if not release_row:
            raise AuthorityError("Target release is no longer qualified")
        release = json.loads(release_row["manifest_json"])
        catalog = load_catalog(authority.repo_root)
        checks = [
            run_check(
                worktree,
                resolve_check(
                    catalog,
                    check_id,
                    version=_target_version(worktree),
                    phase="closeout_reverify",
                    role="target",
                ),
            )
            for check_id in receipt["check_ids"]
        ]
        if not all(check["passed"] for check in checks):
            raise AuthorityError("Target closeout checks failed")
        receipt["candidate_tree"] = digest(
            {
                "repository": repository_snapshot(
                    worktree, exclude=(".trellis/deploy/adoption.json",)
                ),
                "trellis_base_version": release["trellis_base_version"],
            }
        )
        receipt["check_results_digest"] = digest(checks)
        receipt["dirty_closeout_digest"] = digest(_dirty_paths(target))
        receipt["index_closeout_digest"] = _index_digest(target)
        adoption = worktree / ".trellis/deploy/adoption.json"
        _write_if_changed(
            adoption, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        )
        authority.execute(
            """UPDATE target_slots SET receipt_json=?,error=NULL,updated_at=?
               WHERE run_id=? AND target_id=?""",
            (
                canonical_json(receipt),
                utc_now(),
                slot["run_id"],
                slot["target_id"],
            ),
        )
        return {
            "candidate_tree": receipt["candidate_tree"],
            "checks": len(checks),
            "working_candidate": _working_candidate_digest(worktree),
        }
    if step == "prepare_git_artifacts":
        path = worktree / ".trellis/deploy/adoption.json"
        return {
            "adoption_digest": sha256(path.read_bytes()).hexdigest(),
            "candidate_digest": _working_candidate_digest(worktree),
            "authorization_candidate_digest": _working_candidate_digest(
                worktree,
                exclude=(".trellis/deploy/adoption.json",),
            ),
        }
    if step == "scoped_commit":
        prepared = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=(SELECT task_id FROM runs WHERE run_id=?)
                 AND partition_id=? AND step_name='prepare_git_artifacts'""",
            (slot["run_id"], f"target:{slot['target_id']}"),
        )
        prepared_evidence = json.loads(prepared["evidence_json"])
        expected = prepared_evidence["authorization_candidate_digest"]
        partition_id = f"target:{slot['target_id']}"
        current_step = authority.one(
            """SELECT evidence_json FROM closeout_steps
               WHERE task_id=(SELECT task_id FROM runs WHERE run_id=?)
                 AND partition_id=? AND step_name='scoped_commit'""",
            (slot["run_id"], partition_id),
        )
        running_evidence = json.loads(current_step["evidence_json"])
        plan = running_evidence.get("commit_plan")
        if isinstance(plan, Mapping) and _commit_matches(worktree, plan):
            return {
                "commit": _head(worktree),
                "paths": list(plan["paths"]),
                "recovered_external_effect": True,
            }
        if _working_candidate_digest(
            worktree,
            exclude=(".trellis/deploy/adoption.json",),
        ) != expected:
            raise AuthorityError("Target candidate changed after closeout verification")
        changed = _changed_paths(worktree)
        planned = json.loads(str(slot["managed_paths_json"]))
        allowed = [
            path
            for path in changed
            if path == ".trellis/deploy/adoption.json"
            or touches_overlap([path], planned)
        ]
        blockers = sorted(set(changed) - set(allowed))
        if blockers:
            raise AuthorityError(
                "Target closeout found unowned candidate paths: " + ", ".join(blockers)
            )
        if allowed:
            regular = [path for path in allowed if path != ".trellis/.version"]
            if regular:
                git(worktree, "add", "-A", "--", *regular)
            if ".trellis/.version" in allowed:
                git(worktree, "add", "-f", "--", ".trellis/.version")
            message = "chore(trellis): adopt Harness release"
            plan = {
                "authorization_candidate_digest": _working_candidate_digest(
                    worktree,
                    exclude=(".trellis/deploy/adoption.json",),
                ),
                "message": message,
                "parent": _head(worktree),
                "paths": sorted(allowed),
                "tree": git(worktree, "write-tree").stdout.strip(),
            }
            task_id = authority.one(
                "SELECT task_id FROM runs WHERE run_id=?",
                (slot["run_id"],),
            )["task_id"]
            _update_running_step_evidence(
                authority,
                task_id,
                partition_id,
                {"commit_plan": plan},
            )
            git(worktree, "commit", "-m", message)
        return {"commit": _head(worktree), "paths": sorted(allowed)}
    if step == "local_merge_to_base":
        if _branch(target) != base_branch:
            raise AuthorityError("Target base branch changed before local merge")
        candidate = git(target, "rev-parse", f"{branch}^{{commit}}").stdout.strip()
        git(target, "merge", "--ff-only", branch)
        return {"base_commit": candidate, "base_branch": base_branch}
    if step == "record_completion":
        authority.execute(
            "UPDATE target_slots SET state='merged',updated_at=? WHERE run_id=? AND target_id=?",
            (utc_now(), slot["run_id"], slot["target_id"]),
        )
        return {"state": "merged"}
    if step == "logical_archive":
        authority.execute(
            "UPDATE target_slots SET state='closed',updated_at=? WHERE run_id=? AND target_id=?",
            (utc_now(), slot["run_id"], slot["target_id"]),
        )
        return {"state": "closed"}
    if step == "clear_runtime_and_pointers":
        return {"runtime": "none"}
    if step == "remove_worktree":
        registered = _branch_worktree(target, branch)
        if registered and registered != worktree:
            raise AuthorityError("Target branch is registered to an unexpected worktree")
        if registered:
            git(target, "worktree", "remove", str(worktree))
        return {"worktree_removed": True}
    if step == "remove_merged_local_branch":
        if not git(
            target,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        ).returncode:
            git(target, "branch", "-d", branch)
        return {"branch_removed": branch}
    if step == "finalize_journal_projection":
        return {"projection": "adoption.json"}
    raise AuthorityError(f"Unknown target closeout step: {step}")


def _partition_candidate_state(
    authority: Authority,
    task: Mapping[str, object],
    partition_id: str,
) -> dict[str, str] | None:
    if partition_id == "source":
        root = Path(str(task["worktree_path"] or authority.repo_root))
        exclude = (
            "BOARD.md",
            f".trellis/tasks/{task['task_dir_name']}/run-summary.json",
            f".trellis/tasks/{task['task_dir_name']}/task.json",
        )
        base_branch = str(task["base_branch"])
    else:
        target_id = partition_id.removeprefix("target:")
        run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task["task_id"],))
        slot = authority.one(
            "SELECT * FROM target_slots WHERE run_id=? AND target_id=?",
            (run["run_id"], target_id),
        )
        if not slot:
            raise AuthorityError(f"Unknown target closeout partition: {partition_id}")
        root = Path(str(slot["worktree_path"]))
        exclude = (".trellis/deploy/adoption.json",)
        base_branch = str(json.loads(slot["receipt_json"])["target_branch"])
    if not root.is_dir():
        return None
    return {
        "base_commit": git(root, "rev-parse", f"{base_branch}^{{commit}}").stdout.strip(),
        "candidate_digest": _working_candidate_digest(root, exclude=exclude),
        "dirty_digest": _dirty_candidate_digest(root),
        "head": _head(root),
    }


def _reconcile_effect_matches(
    repo_root: Path,
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> bool:
    if (
        before.get("base_commit") != after.get("base_commit")
        or before.get("dirty_digest") != after.get("dirty_digest")
    ):
        return False
    old_head = str(before.get("head"))
    base = str(before.get("base_commit"))
    current = str(after.get("head"))
    if current == base:
        return not git(
            repo_root,
            "merge-base",
            "--is-ancestor",
            old_head,
            base,
            check=False,
        ).returncode
    parents = git(repo_root, "show", "-s", "--format=%P", current).stdout.split()
    return parents == [old_head, base]


def _validate_closeout_preimage(
    authority: Authority,
    task: Mapping[str, object],
    partition_id: str,
    step: str,
    evidence: Mapping[str, object],
) -> None:
    actual = _partition_candidate_state(authority, task, partition_id)
    expected = evidence.get("input_candidate_state")
    if actual is None:
        if CLOSEOUT_STEPS.index(step) >= CLOSEOUT_STEPS.index("remove_worktree"):
            return
        raise AuthorityError("Closeout candidate worktree disappeared")
    if not isinstance(expected, Mapping):
        raise AuthorityError("Closeout step is missing its candidate preimage")
    if actual["candidate_digest"] == expected.get("candidate_digest"):
        return
    root = (
        Path(str(task["worktree_path"] or authority.repo_root))
        if partition_id == "source"
        else Path(
            str(
                authority.one(
                    """SELECT worktree_path FROM target_slots
                       WHERE run_id=(SELECT run_id FROM runs WHERE task_id=?)
                         AND target_id=?""",
                    (task["task_id"], partition_id.removeprefix("target:")),
                )["worktree_path"]
            )
        )
    )
    if step == "reconcile_task_branch" and _reconcile_effect_matches(
        root, expected, actual
    ):
        return
    if step == "scoped_commit":
        return
    raise AuthorityError("Closeout candidate changed outside the authorized saga")


def _expected_partition_candidate(
    authority: Authority,
    task: Mapping[str, object],
    partition_id: str,
    ordinal: int,
) -> str | None:
    prior = authority.one(
        """SELECT evidence_json FROM closeout_steps
           WHERE task_id=? AND partition_id=? AND state='completed' AND ordinal<?
           ORDER BY ordinal DESC LIMIT 1""",
        (task["task_id"], partition_id, ordinal),
    )
    if prior:
        return json.loads(prior["evidence_json"]).get("output_candidate_digest")
    if partition_id == "source":
        return task["verified_candidate_digest"]
    slot = authority.one(
        """SELECT receipt_json FROM target_slots
           WHERE run_id=(SELECT run_id FROM runs WHERE task_id=?) AND target_id=?""",
        (task["task_id"], partition_id.removeprefix("target:")),
    )
    return json.loads(slot["receipt_json"]).get("working_candidate_digest")


def _run_closeout_saga(
    authority: Authority,
    task: Mapping[str, object],
    *,
    authorization_ref: str,
    partition_id: str,
    handlers: Mapping[str, Callable[[], Mapping[str, object]]],
    fault_step: str | None,
    affects_task: bool,
) -> None:
    task_id = str(task["task_id"])
    if affects_task:
        with authority.transaction():
            authority.execute(
                "UPDATE tasks SET closeout_state='running',updated_at=? WHERE task_id=?",
                (utc_now(), task_id),
            )
    for ordinal, step in enumerate(CLOSEOUT_STEPS, start=1):
        step_input = {"authorization": digest(authorization_ref), "step": step}
        with authority.transaction():
            current = authority.one(
                "SELECT * FROM closeout_steps WHERE task_id=? AND partition_id=? AND step_name=?",
                (task_id, partition_id, step),
            )
            if current and current["state"] == "completed":
                continue
            if current and current["input_digest"] != digest(step_input):
                raise AuthorityError("Closeout step authorization changed on replay")
        if current:
            _validate_closeout_preimage(
                authority,
                task,
                partition_id,
                step,
                json.loads(current["evidence_json"]),
            )
            with authority.transaction():
                authority.execute(
                    """UPDATE closeout_steps SET state='running',updated_at=?
                       WHERE task_id=? AND partition_id=? AND step_name=?""",
                    (utc_now(), task_id, partition_id, step),
                )
        else:
            candidate_state = _partition_candidate_state(
                authority, task, partition_id
            )
            expected = _expected_partition_candidate(
                authority, task, partition_id, ordinal
            )
            if (
                expected
                and candidate_state
                and candidate_state["candidate_digest"] != expected
            ):
                raise AuthorityError(
                    "Closeout candidate changed after the previous completed step"
                )
            with authority.transaction():
                authority.execute(
                    """INSERT INTO closeout_steps VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        task_id,
                        partition_id,
                        step,
                        ordinal,
                        "running",
                        digest(
                            {
                                "partition": partition_id,
                                "task": task_id,
                                **step_input,
                            }
                        ),
                        digest(step_input),
                        canonical_json(
                            {"input_candidate_state": candidate_state}
                        ),
                        utc_now(),
                    ),
                )
        try:
            if fault_step == step:
                raise AuthorityError(f"Injected closeout fault at {step}")
            evidence = (
                handlers[step]()
                if step in handlers
                else _default_closeout_handler(authority, task, step)
            )
            if fault_step == f"{step}:after":
                raise AuthorityError(
                    f"Injected closeout fault after {step} external effect"
                )
        except Exception:
            with authority.transaction():
                if affects_task:
                    completed = authority.one(
                        "SELECT work_state FROM tasks WHERE task_id=?", (task_id,)
                    )["work_state"] == "completed"
                    authority.execute(
                        "UPDATE tasks SET closeout_state=?,updated_at=? WHERE task_id=?",
                        ("cleanup_pending" if completed else "running", utc_now(), task_id),
                    )
                authority.execute(
                    """UPDATE closeout_steps SET state='failed',updated_at=?
                       WHERE task_id=? AND partition_id=? AND step_name=?""",
                    (utc_now(), task_id, partition_id, step),
                )
            raise
        with authority.transaction():
            step_row = authority.one(
                """SELECT evidence_json FROM closeout_steps
                   WHERE task_id=? AND partition_id=? AND step_name=?""",
                (task_id, partition_id, step),
            )
            completed_evidence = json.loads(step_row["evidence_json"])
            completed_evidence.update(dict(evidence))
            output_state = _partition_candidate_state(
                authority, task, partition_id
            )
            if output_state:
                completed_evidence["output_candidate_digest"] = output_state[
                    "candidate_digest"
                ]
            if affects_task and step == "record_completion":
                authority.execute(
                    "UPDATE tasks SET work_state='completed',updated_at=? WHERE task_id=?",
                    (utc_now(), task_id),
                )
            elif affects_task and step == "logical_archive":
                authority.execute(
                    "UPDATE tasks SET archive_state='logical_archived',updated_at=? WHERE task_id=?",
                    (utc_now(), task_id),
                )
            elif affects_task and step == "full_reverify" and output_state:
                authority.execute(
                    """UPDATE tasks SET verified_candidate_digest=?,updated_at=?
                       WHERE task_id=?""",
                    (
                        output_state["candidate_digest"],
                        utc_now(),
                        task_id,
                    ),
                )
            authority.execute(
                """UPDATE closeout_steps SET state='completed',evidence_json=?,updated_at=?
                   WHERE task_id=? AND partition_id=? AND step_name=?""",
                (
                    canonical_json(completed_evidence),
                    utc_now(),
                    task_id,
                    partition_id,
                    step,
                ),
            )
    with authority.transaction():
        if affects_task:
            authority.execute(
                """UPDATE tasks SET closeout_state='clean',archive_state='logical_archived',
                   updated_at=? WHERE task_id=?""",
                (utc_now(), task_id),
            )
        authority.record_event(
            f"close:{task_id}:{partition_id}:{digest(authorization_ref)}",
            "closeout_completed",
            {"partition": partition_id},
            task_id=task_id,
        )


def close_task(
    repo_root: Path,
    task_id: str,
    *,
    authorization_ref: str,
    partition_id: str = "source",
    handlers: Mapping[str, Callable[[], Mapping[str, object]]] | None = None,
    fault_step: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    handlers = handlers or {}
    with Authority(root) as authority:
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not task:
            raise AuthorityError(f"Unknown task: {task_id}")
        if task["work_state"] not in {"verified", "completed", "cancelled"}:
            raise AuthorityError("Closeout requires VERIFIED, completed, or cancelled work")
        partitions: dict[str, object] = {}
        if task["task_kind"] == "sync" and partition_id == "source" and not handlers:
            run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
            slots = authority.all(
                "SELECT * FROM target_slots WHERE run_id=? ORDER BY target_id",
                (run["run_id"],),
            )
            failures: dict[str, str] = {}
            for slot in slots:
                target_partition = f"target:{slot['target_id']}"
                target_handlers = {
                    step: (
                        lambda step=step, slot=dict(slot): _target_closeout_handler(
                            authority, slot, step
                        )
                    )
                    for step in CLOSEOUT_STEPS
                }
                try:
                    _run_closeout_saga(
                        authority,
                        task,
                        authorization_ref=authorization_ref,
                        partition_id=target_partition,
                        handlers=target_handlers,
                        fault_step=fault_step,
                        affects_task=False,
                    )
                    with authority.transaction():
                        authority.execute(
                            """UPDATE target_slots SET state='closed',error=NULL,updated_at=?
                               WHERE run_id=? AND target_id=?""",
                            (utc_now(), slot["run_id"], slot["target_id"]),
                        )
                    partitions[target_partition] = "clean"
                except Exception as exc:
                    failures[target_partition] = str(exc)
                    partitions[target_partition] = "cleanup_pending"
                    with authority.transaction():
                        archived = authority.one(
                            """SELECT 1 FROM closeout_steps
                               WHERE task_id=? AND partition_id=?
                                 AND step_name='logical_archive' AND state='completed'""",
                            (task_id, target_partition),
                        )
                        authority.execute(
                            """UPDATE target_slots SET state=?,error=?,updated_at=?
                               WHERE run_id=? AND target_id=?""",
                            (
                                "cleanup_pending" if archived else "closeout_failed",
                                str(exc),
                                utc_now(),
                                slot["run_id"],
                                slot["target_id"],
                            ),
                        )
            if failures:
                with authority.transaction():
                    authority.execute(
                        "UPDATE tasks SET closeout_state='cleanup_pending',updated_at=? WHERE task_id=?",
                        (utc_now(), task_id),
                    )
                snapshot = authority.task_snapshot(task_id)
                return {**snapshot, "partitions": partitions, "errors": failures}
        _run_closeout_saga(
            authority,
            task,
            authorization_ref=authorization_ref,
            partition_id=partition_id,
            handlers=handlers,
            fault_step=None if task["task_kind"] == "sync" else fault_step,
            affects_task=partition_id == "source",
        )
        snapshot = authority.task_snapshot(task_id)
        return {**snapshot, "partitions": partitions}
