#!/usr/bin/env python3
"""
Task CRUD operations.

Provides:
    ensure_tasks_dir   - Ensure tasks directory exists
    cmd_create         - Create a new task
    cmd_archive        - Archive completed task
    cmd_claim          - Claim task ownership
    cmd_release        - Release task ownership
    cmd_set_branch     - Set git branch for task
    cmd_set_base_branch - Set PR target branch
    cmd_set_scope      - Set scope for PR title
    cmd_add_subtask    - Link child task to parent
    cmd_remove_subtask - Unlink child task from parent
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    get_packages,
    get_session_auto_commit,
    is_monorepo,
    resolve_package,
    validate_package,
)
from .git import run_git
from .io import read_json, write_json
from .log import Colors, colored
from .paths import (
    DIR_ARCHIVE,
    DIR_TASKS,
    DIR_WORKFLOW,
    FILE_TASK_JSON,
    generate_task_date_prefix,
    get_developer,
    get_repo_root,
    get_tasks_dir,
)
from .safe_commit import (
    print_gitignore_warning,
    safe_archive_paths_to_add,
    safe_git_add,
)
from .task_utils import (
    archive_task_complete,
    find_task_by_name,
    resolve_task_dir,
    run_task_hooks,
)
from .done_gate import done_gate_errors as _done_gate_errors

HARNESS_MODE = "harness_state_machine"
V2_TIERS = {"parent", "child", "light"}
OWNERS = {"cc", "codex", "jym"}
TEMPLATE_VERSION = "v3"


# =============================================================================
# Helper Functions
# =============================================================================

def _slugify(title: str) -> str:
    """Convert title to slug (only works with ASCII)."""
    result = title.lower()
    result = re.sub(r"[^a-z0-9]", "-", result)
    result = re.sub(r"-+", "-", result)
    result = result.strip("-")
    return result


def ensure_tasks_dir(repo_root: Path) -> Path:
    """Ensure tasks directory exists."""
    tasks_dir = get_tasks_dir(repo_root)
    archive_dir = tasks_dir / "archive"

    if not tasks_dir.exists():
        tasks_dir.mkdir(parents=True)
        print(colored(f"Created tasks directory: {tasks_dir}", Colors.GREEN), file=sys.stderr)

    if not archive_dir.exists():
        archive_dir.mkdir(parents=True)

    return tasks_dir


def _find_archived_task_by_dir_name(tasks_dir: Path, dir_name: str) -> Path | None:
    """Find an archived task directory with the exact active-task dir name."""
    archive_dir = tasks_dir / DIR_ARCHIVE
    if not archive_dir.is_dir():
        return None

    for month_dir in sorted(archive_dir.iterdir()):
        if not month_dir.is_dir():
            continue
        candidate = month_dir / dir_name
        if candidate.is_dir():
            return candidate

    return None


def _repo_relative_path(path: Path, repo_root: Path) -> str:
    """Format a path relative to the repo root when possible."""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


# =============================================================================
# Sub-agent platform detection + JSONL seeding
# =============================================================================

# v3 first-class platforms that consume implement.jsonl / check.jsonl.
# Legacy adapter branches remain elsewhere for read-compatibility, but new
# task seeding is scoped to Codex and Claude Code.
_SUBAGENT_CONFIG_DIRS: tuple[str, ...] = (
    ".claude",
    ".codex",
)

_SEED_EXAMPLE = (
    "Fill with {\"file\": \"<path>\", \"reason\": \"<why>\"}. "
    "Put spec/research files only — no code paths. "
    "Run `python3 .trellis/scripts/get_context.py --mode packages` to list available specs. "
    "Delete this line once real entries are added."
)


def _has_subagent_platform(repo_root: Path) -> bool:
    """Return True if any sub-agent-capable platform is configured.

    Detected by probing well-known config directories at the repo root. Used
    only to decide whether ``task.py create`` should seed empty
    ``implement.jsonl`` / ``check.jsonl`` files.
    """
    for config_dir in _SUBAGENT_CONFIG_DIRS:
        if (repo_root / config_dir).is_dir():
            return True
    return False


def _write_seed_jsonl(path: Path) -> None:
    """Write a one-line seed JSONL file with a self-describing ``_example``.

    The seed row has no ``file`` field, so downstream consumers (hooks +
    preludes) that iterate entries via ``item.get("file")`` naturally skip
    it. The row exists purely as an in-file prompt for the AI curator.
    """
    seed = {"_example": _SEED_EXAMPLE}
    path.write_text(json.dumps(seed, ensure_ascii=False) + "\n", encoding="utf-8")


def _normalize_touches(raw: list[str] | None) -> list[str]:
    """Normalize repeated/comma-separated --touches values."""
    touches: list[str] = []
    for item in raw or []:
        for part in item.split(","):
            value = part.strip()
            if value and value not in touches:
                touches.append(value)
    return touches


def _render_template(template: str, values: dict[str, str]) -> str:
    return template.format(**values).rstrip() + "\n"


def _template_text(repo_root: Path, tier: str, name: str) -> str:
    base = repo_root / DIR_WORKFLOW / "templates"
    for version in (TEMPLATE_VERSION, "v2"):
        path = base / version / tier / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"template not found for {tier}/{name}")


def _write_template_files(task_dir: Path, repo_root: Path, tier: str, title: str, description: str) -> None:
    values = {
        "title": title.strip() or "Untitled task",
        "description": description.strip() or "TBD",
    }
    if tier == "parent":
        files = ("prd.md", "governance.md")
    else:
        files = ("prd.md", "stage-report.md")
    for name in files:
        path = task_dir / name
        if not path.exists():
            path.write_text(
                _render_template(_template_text(repo_root, tier, name), values),
                encoding="utf-8",
            )


def _init_state_if_supported(task_dir: Path, tier: str) -> None:
    if tier not in {"parent", "child"}:
        return
    try:
        from state_machine import StateMachineError, init_task

        init_task(task_dir, tier, by="system", note="task.py create")
    except (ImportError, StateMachineError) as exc:
        print(colored(f"Warning: state machine init skipped: {exc}", Colors.YELLOW), file=sys.stderr)


def _append_state_event(task_dir: Path, event: str, note: str, by: str = "agent") -> None:
    entry = {
        "event": event,
        "kind": "audit",
        "previous_state": None,
        "current_state": None,
        "by": by,
        "note": note,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    path = task_dir / "state-events.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _record_force_archive(task_dir: Path, data: dict[str, Any], task_json_path: Path, reason: str) -> None:
    notes = (data.get("notes") or "").rstrip()
    line = f"force-archive: {reason}"
    data["notes"] = f"{notes}\n{line}".strip()
    write_json(task_json_path, data)
    _append_state_event(task_dir, "force_archive", reason)


def _set_task_owner(task_dir: Path, new_owner: str, event: str, *, reason: str = "", override: bool = False) -> int:
    if new_owner not in OWNERS:
        print(colored(f"Error: invalid owner: {new_owner}", Colors.RED), file=sys.stderr)
        return 1
    if override and not reason.strip():
        print(colored("Error: --override-claim requires --reason", Colors.RED), file=sys.stderr)
        return 1

    task_json_path = task_dir / FILE_TASK_JSON
    if not task_json_path.is_file():
        print(colored(f"Error: task.json not found at {task_dir}", Colors.RED), file=sys.stderr)
        return 1

    data = read_json(task_json_path)
    if not data:
        return 1
    old_owner = data.get("owner") or "-"
    data["owner"] = new_owner
    if not write_json(task_json_path, data):
        return 1

    note = f"owner {old_owner} -> {new_owner}"
    if reason.strip():
        note = f"{note}; reason: {reason.strip()}"
    _append_state_event(task_dir, "override_claim" if override else event, note, by="task.py")
    print(colored(f"✓ Owner: {old_owner} -> {new_owner}", Colors.GREEN), file=sys.stderr)
    return 0


def _check_done_gate_or_force(
    args: argparse.Namespace,
    task_dir: Path,
    data: dict[str, Any],
    task_json_path: Path,
    repo_root: Path,
) -> bool:
    errors = _done_gate_errors(task_dir, data, repo_root)
    if not errors:
        return True

    force = getattr(args, "force_archive", False)
    reason = (getattr(args, "reason", "") or "").strip()
    if force:
        if not reason:
            print(colored("Error: --force-archive requires --reason", Colors.RED), file=sys.stderr)
            return False
        _record_force_archive(task_dir, data, task_json_path, reason)
        print(colored("Warning: done gate bypassed with --force-archive", Colors.YELLOW), file=sys.stderr)
        return True

    print(colored("Error: done gate failed", Colors.RED), file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return False


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _format_table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _update_parent_governance(parent_dir: Path, child_data: dict[str, Any], commit: str) -> bool:
    path = parent_dir / "governance.md"
    text = path.read_text(encoding="utf-8")
    title = child_data.get("title") or child_data.get("name") or ""
    child_dir_name = child_data.get("_dir_name") or child_data.get("name") or ""
    evidence = f"{child_dir_name}/stage-report.md"
    changed = False
    matched_child = False
    matched_rtm = False
    out: list[str] = []

    in_child_index = False
    in_rtm = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_child_index = line == "## Child Index"
            in_rtm = line == "## RTM"
        if line.startswith("|") and not line.startswith("|---"):
            cells = _split_table_row(line)
            if in_child_index and len(cells) >= 7 and (title in cells[0] or child_dir_name in cells[0]):
                cells[5] = "completed"
                cells[6] = commit
                line = _format_table_row(cells)
                matched_child = True
                changed = True
            elif in_rtm and len(cells) >= 4 and title in cells[1]:
                cells[2] = "completed"
                cells[3] = evidence
                line = _format_table_row(cells)
                matched_rtm = True
                changed = True
        out.append(line)

    if not matched_child:
        raise ValueError(f"parent governance Child Index row not found for {title}")
    if not matched_rtm:
        raise ValueError(f"parent governance RTM rows not found for {title}")
    new_text = "\n".join(out) + "\n"
    if changed and new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def _advance_child_to_archived(task_dir: Path) -> None:
    from state_machine import StateMachineError, apply_event, archive_transition_events, init_task

    task = read_json(task_dir / FILE_TASK_JSON) or {}
    if not (task.get("meta") or {}).get("state_machine"):
        init_task(task_dir, "child", by="system", note="soft-archive init")

    task = read_json(task_dir / FILE_TASK_JSON) or {}
    current = ((task.get("meta") or {}).get("state_machine") or {}).get("current_state")
    try:
        events = archive_transition_events("child", current)
    except StateMachineError as exc:
        raise StateMachineError(f"cannot soft-archive from state: {current}") from exc
    for event in events:
        apply_event(task_dir, event, by="system", note="task.py soft-archive")


def _advance_parent_to_archived(task_dir: Path) -> None:
    from state_machine import StateMachineError, apply_event, archive_transition_events, init_task

    task = read_json(task_dir / FILE_TASK_JSON) or {}
    if not (task.get("meta") or {}).get("state_machine"):
        init_task(task_dir, "parent", by="system", note="archive init")

    task = read_json(task_dir / FILE_TASK_JSON) or {}
    current = ((task.get("meta") or {}).get("state_machine") or {}).get("current_state")
    try:
        events = archive_transition_events("parent", current)
    except StateMachineError as exc:
        raise StateMachineError(f"cannot archive parent from state: {current}") from exc
    for event in events:
        apply_event(task_dir, event, by="system", note="task.py archive")


def _refresh_board(repo_root: Path) -> None:
    board = repo_root / DIR_WORKFLOW / "scripts" / "board.py"
    if not board.is_file():
        return
    result = subprocess.run(
        [sys.executable, str(board)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(colored(f"[WARN] BOARD refresh failed: {result.stderr.strip()}", Colors.YELLOW), file=sys.stderr)


# =============================================================================
# Command: create
# =============================================================================

def cmd_create(args: argparse.Namespace) -> int:
    """Create a new task."""
    repo_root = get_repo_root()

    if not args.title:
        print(colored("Error: title is required", Colors.RED), file=sys.stderr)
        return 1

    # Validate --package (CLI source: fail-fast)
    package: str | None = getattr(args, "package", None)
    if not is_monorepo(repo_root):
        # Single-repo: ignore --package, no package prefix
        if package:
            print(colored(f"Warning: --package ignored in single-repo project", Colors.YELLOW), file=sys.stderr)
        package = None
    elif package:
        if not validate_package(package, repo_root):
            packages = get_packages(repo_root)
            available = ", ".join(sorted(packages.keys())) if packages else "(none)"
            print(colored(f"Error: unknown package '{package}'. Available: {available}", Colors.RED), file=sys.stderr)
            return 1
    else:
        # Inferred: default_package → None (no task.json yet for create)
        package = resolve_package(repo_root=repo_root)

    tier = "child" if getattr(args, "parent", None) else getattr(args, "tier", "light")
    if tier not in V2_TIERS:
        print(colored(f"Error: invalid tier: {tier}", Colors.RED), file=sys.stderr)
        return 1

    owner = getattr(args, "owner", None) or "codex"
    if owner not in OWNERS:
        print(colored(f"Error: invalid owner: {owner}", Colors.RED), file=sys.stderr)
        return 1
    touches = _normalize_touches(getattr(args, "touches", None))

    # Default assignee to current developer
    assignee = args.assignee
    if not assignee:
        assignee = get_developer(repo_root)
        if not assignee:
            print(colored("Error: No developer set. Run init_developer.py first or use --assignee", Colors.RED), file=sys.stderr)
            return 1

    ensure_tasks_dir(repo_root)

    # Get current developer as creator
    creator = get_developer(repo_root) or assignee

    # Generate slug if not provided
    slug = args.slug or _slugify(args.title)
    if not slug:
        print(colored("Error: could not generate slug from title", Colors.RED), file=sys.stderr)
        return 1

    # Create task directory with MM-DD-slug format
    tasks_dir = get_tasks_dir(repo_root)
    date_prefix = generate_task_date_prefix()
    dir_name = f"{date_prefix}-{slug}"
    task_dir = tasks_dir / dir_name
    task_json_path = task_dir / FILE_TASK_JSON

    archived_task_dir = _find_archived_task_by_dir_name(tasks_dir, dir_name)
    if archived_task_dir:
        print(colored(f"Error: Task already archived: {dir_name}", Colors.RED), file=sys.stderr)
        print(f"Archived at: {_repo_relative_path(archived_task_dir, repo_root)}", file=sys.stderr)
        print("Use a new slug if you intend to create a new task.", file=sys.stderr)
        return 1

    if task_dir.exists():
        print(colored(f"Warning: Task directory already exists: {dir_name}", Colors.YELLOW), file=sys.stderr)
    else:
        task_dir.mkdir(parents=True)

    today = datetime.now().strftime("%Y-%m-%d")

    # Record current branch as base_branch (PR target)
    _, branch_out, _ = run_git(["branch", "--show-current"], cwd=repo_root)
    current_branch = branch_out.strip() or "main"

    task_data = {
        "id": slug,
        "name": slug,
        "title": args.title,
        "description": args.description or "",
        "status": "planning",
        "dev_type": None,
        "scope": None,
        "tier": tier,
        "owner": owner,
        "touches": touches,
        "package": package,
        "priority": args.priority,
        "creator": creator,
        "assignee": assignee,
        "createdAt": today,
        "completedAt": None,
        "branch": None,
        "base_branch": current_branch,
        "worktree_path": None,
        "commit": None,
        "pr_url": None,
        "subtasks": [],
        "children": [],
        "parent": None,
        "relatedFiles": [],
        "notes": "",
        "meta": {"workflow_mode": HARNESS_MODE},
    }

    write_json(task_json_path, task_data)

    _write_template_files(task_dir, repo_root, tier, args.title, args.description or "")

    # Seed implement.jsonl / check.jsonl for v3 sub-agent-capable platforms.
    # Agent curates real entries during planning when the task needs them.
    seeded_jsonl = False
    if _has_subagent_platform(repo_root):
        for jsonl_name in ("implement.jsonl", "check.jsonl"):
            jsonl_path = task_dir / jsonl_name
            if not jsonl_path.exists():
                _write_seed_jsonl(jsonl_path)
        seeded_jsonl = True

    # Handle --parent: establish bidirectional link
    if args.parent:
        parent_dir = resolve_task_dir(args.parent, repo_root)
        parent_json_path = parent_dir / FILE_TASK_JSON
        if not parent_json_path.is_file():
            print(colored(f"Warning: Parent task.json not found: {args.parent}", Colors.YELLOW), file=sys.stderr)
        else:
            parent_data = read_json(parent_json_path)
            if parent_data:
                # Add child to parent's children list
                parent_children = parent_data.get("children", [])
                if dir_name not in parent_children:
                    parent_children.append(dir_name)
                    parent_data["children"] = parent_children
                    write_json(parent_json_path, parent_data)

                # Set parent in child's task.json
                task_data["parent"] = parent_dir.name
                write_json(task_json_path, task_data)

                print(colored(f"Linked as child of: {parent_dir.name}", Colors.GREEN), file=sys.stderr)

    _init_state_if_supported(task_dir, tier)

    # Auto-activate the new task so the per-turn breadcrumb fires planning
    # state. Best-effort: gracefully degrade if no session identity (CLI run
    # outside an AI session) — the task is still created, the user can run
    # task.py start later. Pointer is session-scoped so this never affects
    # other AI sessions.
    try:
        from .active_task import resolve_context_key, set_active_task
        if resolve_context_key():
            try:
                rel_dir = task_dir.relative_to(repo_root).as_posix()
            except ValueError:
                rel_dir = str(task_dir)
            set_active_task(rel_dir, repo_root)
    except Exception:
        pass

    print(colored(f"Created task: {dir_name}", Colors.GREEN), file=sys.stderr)
    print("", file=sys.stderr)
    print(colored("Next steps:", Colors.BLUE), file=sys.stderr)
    print("  - Fill prd.md with requirements and acceptance criteria", file=sys.stderr)
    print("  - Fill stage-report.md before archive/soft-archive", file=sys.stderr)
    if seeded_jsonl:
        print(
            "  - Curate implement.jsonl / check.jsonl as spec/research manifests when sub-agents need context",
            file=sys.stderr,
        )
    print("  - Use /trellis:continue or phase context to decide the next step", file=sys.stderr)
    print("", file=sys.stderr)

    # Output relative path for script chaining
    print(f"{DIR_WORKFLOW}/{DIR_TASKS}/{dir_name}")

    run_task_hooks("after_create", task_json_path, repo_root)
    return 0


# =============================================================================
# Command: archive
# =============================================================================

def cmd_archive(args: argparse.Namespace) -> int:
    """Archive completed task."""
    repo_root = get_repo_root()
    task_name = args.name

    if not task_name:
        print(colored("Error: Task name is required", Colors.RED), file=sys.stderr)
        return 1

    tasks_dir = get_tasks_dir(repo_root)

    # Resolve task directory (supports task name, relative path, or absolute path)
    task_dir = resolve_task_dir(task_name, repo_root)

    if not task_dir or not task_dir.is_dir():
        print(colored(f"Error: Task not found: {task_name}", Colors.RED), file=sys.stderr)
        print("Active tasks:", file=sys.stderr)
        # Import lazily to avoid circular dependency
        from .tasks import iter_active_tasks
        for t in iter_active_tasks(tasks_dir):
            print(f"  - {t.dir_name}/", file=sys.stderr)
        return 1

    dir_name = task_dir.name
    task_json_path = task_dir / FILE_TASK_JSON

    # Update status before archiving
    today = datetime.now().strftime("%Y-%m-%d")
    # Names of child task dirs whose task.json gets modified below; passed
    # into safe_archive_paths_to_add so they're staged in this commit.
    modified_children: list[str] = []
    if task_json_path.is_file():
        data = read_json(task_json_path)
        if data:
            if not _check_done_gate_or_force(args, task_dir, data, task_json_path, repo_root):
                return 1
            data = read_json(task_json_path) or data
            if data.get("tier") == "parent" and (data.get("meta") or {}).get("workflow_mode") == HARNESS_MODE:
                try:
                    _advance_parent_to_archived(task_dir)
                except Exception as exc:
                    print(colored(f"Error: parent archive state update failed: {exc}", Colors.RED), file=sys.stderr)
                    return 1
                data = read_json(task_json_path) or data
            data["status"] = "completed"
            data["completedAt"] = today
            write_json(task_json_path, data)

            # Handle subtask relationships on archive.
            # Keep this task in its parent's children list so progress
            # counters (children_progress) stay consistent — children
            # missing from the active set are treated as completed.
            task_children = data.get("children", [])

            # If this is a parent, clear parent field in all children
            if task_children:
                for child_name in task_children:
                    child_dir_path = find_task_by_name(child_name, tasks_dir)
                    if child_dir_path:
                        child_json = child_dir_path / FILE_TASK_JSON
                        if child_json.is_file():
                            child_data = read_json(child_json)
                            if child_data:
                                child_data["parent"] = None
                                write_json(child_json, child_data)
                                modified_children.append(child_dir_path.name)

    # Clear any session that still points at this task before the path moves.
    from .active_task import clear_task_from_sessions
    clear_task_from_sessions(str(task_dir), repo_root)

    # Archive
    result = archive_task_complete(task_dir, repo_root)
    if "archived_to" in result:
        archive_dest = Path(result["archived_to"])
        year_month = archive_dest.parent.name
        print(colored(f"Archived: {dir_name} -> archive/{year_month}/", Colors.GREEN), file=sys.stderr)

        # Auto-commit unless --no-commit
        if not getattr(args, "no_commit", False):
            _refresh_board(repo_root)
            if not _auto_commit_archive(dir_name, repo_root, modified_children):
                print(
                    colored(
                        "Archive moved on disk, but git auto-commit did not complete. "
                        "Resolve `git status` before continuing.",
                        Colors.RED,
                    ),
                    file=sys.stderr,
                )
                return 1

        # Return the archive path
        print(f"{DIR_WORKFLOW}/{DIR_TASKS}/{DIR_ARCHIVE}/{year_month}/{dir_name}")

        # Run hooks with the archived path
        archived_json = archive_dest / FILE_TASK_JSON
        run_task_hooks("after_archive", archived_json, repo_root)
        return 0

    return 1


def _auto_commit_archive(
    task_name: str,
    repo_root: Path,
    modified_children: list[str] | None = None,
) -> bool:
    """Stage Trellis-owned task paths and commit after archive.

    Scoped narrowly to the archived task's source + destination paths
    plus any child task dirs whose ``task.json`` was edited (parent →
    children relationship update). Dirty changes in OTHER active task
    dirs are NOT bundled into the archive commit.

    If ``.gitignore`` blocks the paths, we warn + skip — we do NOT
    retry with ``git add -f``. The warning explicitly forbids
    ``git add -f .trellis/`` (which would fan out to caches/backups)
    and points users at ``session_auto_commit: false``.

    Honors ``session_auto_commit`` in ``.trellis/config.yaml``: when
    set to ``false``, this function returns immediately without
    touching git (the archive directory move on disk is unaffected).
    """
    if not get_session_auto_commit(repo_root):
        print(
            "[OK] session_auto_commit: false — skipping git stage/commit.",
            file=sys.stderr,
        )
        return True

    source_rel = f"{DIR_WORKFLOW}/{DIR_TASKS}/{task_name}"
    rc, tracked_out, _ = run_git(
        ["ls-files", "--", source_rel],
        cwd=repo_root,
    )
    source_was_tracked = rc == 0 and bool(tracked_out.strip())

    paths = safe_archive_paths_to_add(
        repo_root, task_name=task_name, modified_children=modified_children
    )
    if not paths:
        print("[OK] No task changes to commit.", file=sys.stderr)
        return True

    success, _, err = safe_git_add(paths, repo_root)
    if not success:
        if err and "ignored by" in err.lower():
            print_gitignore_warning(paths)
        else:
            print(
                f"[WARN] git add failed: {err.strip() if err else 'unknown error'}",
                file=sys.stderr,
            )
        return not source_was_tracked

    # Belt-and-suspenders for the phantom-delete bug: `safe_git_add` uses
    # `git add` (no -A) which only stages additions/modifications. The
    # source task directory was moved away by `shutil.move`, so its files
    # need an explicit `git rm --cached` to stage the deletions in this
    # same commit — otherwise they sit as uncommitted "phantom deletes"
    # against HEAD until something later picks them up.
    #
    # `--ignore-unmatch` makes this a no-op when the task was never tracked
    # (e.g. archiving a task that lived only in working tree).
    run_git(
        ["rm", "-r", "--cached", "--ignore-unmatch", "--", source_rel],
        cwd=repo_root,
    )

    rc, _, _ = run_git(
        ["diff", "--cached", "--quiet", "--", *paths, source_rel],
        cwd=repo_root,
    )
    if rc == 0:
        print("[OK] No task changes to commit.", file=sys.stderr)
        return True

    commit_msg = f"chore(task): archive {task_name}"
    rc, _, err = run_git(["commit", "-m", commit_msg], cwd=repo_root)
    if rc == 0:
        print(f"[OK] Auto-committed: {commit_msg}", file=sys.stderr)
        return True
    else:
        print(f"[WARN] Auto-commit failed: {err.strip()}", file=sys.stderr)
        return not source_was_tracked


# =============================================================================
# Command: soft-archive
# =============================================================================

def cmd_soft_archive(args: argparse.Namespace) -> int:
    """Soft-archive a v3 child task without moving its directory."""
    repo_root = get_repo_root()
    tasks_dir = get_tasks_dir(repo_root)
    task_dir = resolve_task_dir(args.name, repo_root)
    task_json_path = task_dir / FILE_TASK_JSON

    if not task_json_path.is_file():
        print(colored(f"Error: task.json not found: {args.name}", Colors.RED), file=sys.stderr)
        return 1

    commit = (args.commit or "").strip()
    if not commit:
        print(colored("Error: --commit is required", Colors.RED), file=sys.stderr)
        return 1

    data = read_json(task_json_path)
    if not data:
        print(colored("Error: failed to read task.json", Colors.RED), file=sys.stderr)
        return 1

    if data.get("tier") != "child":
        print(colored("Error: soft-archive only supports tier=child", Colors.RED), file=sys.stderr)
        return 1
    if (data.get("meta") or {}).get("workflow_mode") != HARNESS_MODE:
        print(colored(f"Error: workflow_mode must be {HARNESS_MODE}", Colors.RED), file=sys.stderr)
        return 1

    if not _check_done_gate_or_force(args, task_dir, data, task_json_path, repo_root):
        return 1
    data = read_json(task_json_path) or data

    parent_name = data.get("parent")
    parent_dir = find_task_by_name(parent_name, tasks_dir) if parent_name else None
    if not parent_dir:
        print(colored("Error: parent task not found", Colors.RED), file=sys.stderr)
        return 1

    try:
        governance_data = dict(data)
        governance_data["_dir_name"] = task_dir.name
        _update_parent_governance(parent_dir, governance_data, commit)
        _advance_child_to_archived(task_dir)
    except Exception as exc:
        print(colored(f"Error: soft-archive failed: {exc}", Colors.RED), file=sys.stderr)
        return 1

    data = read_json(task_json_path) or data
    data["status"] = "completed"
    data["completedAt"] = datetime.now().strftime("%Y-%m-%d")
    data["commit"] = commit
    write_json(task_json_path, data)

    print(colored(f"Soft archived: {task_dir.name}", Colors.GREEN), file=sys.stderr)
    print(f"{DIR_WORKFLOW}/{DIR_TASKS}/{task_dir.name}")
    return 0


# =============================================================================
# Command: claim / release
# =============================================================================

def cmd_claim(args: argparse.Namespace) -> int:
    """Claim ownership of a task."""
    repo_root = get_repo_root()
    task_dir = resolve_task_dir(args.name, repo_root)
    return _set_task_owner(
        task_dir,
        args.owner,
        "claim",
        reason=getattr(args, "reason", "") or "",
        override=getattr(args, "override_claim", False),
    )


def cmd_release(args: argparse.Namespace) -> int:
    """Release ownership of a task back to the coordinator by default."""
    repo_root = get_repo_root()
    task_dir = resolve_task_dir(args.name, repo_root)
    return _set_task_owner(
        task_dir,
        getattr(args, "owner", None) or "jym",
        "release",
        reason=getattr(args, "reason", "") or "",
    )


# =============================================================================
# Command: add-subtask
# =============================================================================

def cmd_add_subtask(args: argparse.Namespace) -> int:
    """Link a child task to a parent task."""
    repo_root = get_repo_root()

    parent_dir = resolve_task_dir(args.parent_dir, repo_root)
    child_dir = resolve_task_dir(args.child_dir, repo_root)

    parent_json_path = parent_dir / FILE_TASK_JSON
    child_json_path = child_dir / FILE_TASK_JSON

    if not parent_json_path.is_file():
        print(colored(f"Error: Parent task.json not found: {args.parent_dir}", Colors.RED), file=sys.stderr)
        return 1

    if not child_json_path.is_file():
        print(colored(f"Error: Child task.json not found: {args.child_dir}", Colors.RED), file=sys.stderr)
        return 1

    parent_data = read_json(parent_json_path)
    child_data = read_json(child_json_path)

    if not parent_data or not child_data:
        print(colored("Error: Failed to read task.json", Colors.RED), file=sys.stderr)
        return 1

    # Check if child already has a parent
    existing_parent = child_data.get("parent")
    if existing_parent:
        print(colored(f"Error: Child task already has a parent: {existing_parent}", Colors.RED), file=sys.stderr)
        return 1

    # Add child to parent's children list
    parent_children = parent_data.get("children", [])
    child_dir_name = child_dir.name
    if child_dir_name not in parent_children:
        parent_children.append(child_dir_name)
        parent_data["children"] = parent_children

    # Set parent in child's task.json
    child_data["parent"] = parent_dir.name

    # Write both
    write_json(parent_json_path, parent_data)
    write_json(child_json_path, child_data)

    print(colored(f"Linked: {child_dir.name} -> {parent_dir.name}", Colors.GREEN), file=sys.stderr)
    return 0


# =============================================================================
# Command: remove-subtask
# =============================================================================

def cmd_remove_subtask(args: argparse.Namespace) -> int:
    """Unlink a child task from a parent task."""
    repo_root = get_repo_root()

    parent_dir = resolve_task_dir(args.parent_dir, repo_root)
    child_dir = resolve_task_dir(args.child_dir, repo_root)

    parent_json_path = parent_dir / FILE_TASK_JSON
    child_json_path = child_dir / FILE_TASK_JSON

    if not parent_json_path.is_file():
        print(colored(f"Error: Parent task.json not found: {args.parent_dir}", Colors.RED), file=sys.stderr)
        return 1

    if not child_json_path.is_file():
        print(colored(f"Error: Child task.json not found: {args.child_dir}", Colors.RED), file=sys.stderr)
        return 1

    parent_data = read_json(parent_json_path)
    child_data = read_json(child_json_path)

    if not parent_data or not child_data:
        print(colored("Error: Failed to read task.json", Colors.RED), file=sys.stderr)
        return 1

    # Remove child from parent's children list
    parent_children = parent_data.get("children", [])
    child_dir_name = child_dir.name
    if child_dir_name in parent_children:
        parent_children.remove(child_dir_name)
        parent_data["children"] = parent_children

    # Clear parent in child's task.json
    child_data["parent"] = None

    # Write both
    write_json(parent_json_path, parent_data)
    write_json(child_json_path, child_data)

    print(colored(f"Unlinked: {child_dir.name} from {parent_dir.name}", Colors.GREEN), file=sys.stderr)
    return 0


# =============================================================================
# Command: set-branch
# =============================================================================

def cmd_set_branch(args: argparse.Namespace) -> int:
    """Set git branch for task."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)
    branch = args.branch

    if not branch:
        print(colored("Error: Missing arguments", Colors.RED))
        print("Usage: python3 task.py set-branch <task-dir> <branch-name>")
        return 1

    task_json = target_dir / FILE_TASK_JSON
    if not task_json.is_file():
        print(colored(f"Error: task.json not found at {target_dir}", Colors.RED))
        return 1

    data = read_json(task_json)
    if not data:
        return 1

    data["branch"] = branch
    write_json(task_json, data)

    print(colored(f"✓ Branch set to: {branch}", Colors.GREEN))
    return 0


# =============================================================================
# Command: set-base-branch
# =============================================================================

def cmd_set_base_branch(args: argparse.Namespace) -> int:
    """Set the base branch (PR target) for task."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)
    base_branch = args.base_branch

    if not base_branch:
        print(colored("Error: Missing arguments", Colors.RED))
        print("Usage: python3 task.py set-base-branch <task-dir> <base-branch>")
        print("Example: python3 task.py set-base-branch <dir> develop")
        print()
        print("This sets the target branch for PR (the branch your feature will merge into).")
        return 1

    task_json = target_dir / FILE_TASK_JSON
    if not task_json.is_file():
        print(colored(f"Error: task.json not found at {target_dir}", Colors.RED))
        return 1

    data = read_json(task_json)
    if not data:
        return 1

    data["base_branch"] = base_branch
    write_json(task_json, data)

    print(colored(f"✓ Base branch set to: {base_branch}", Colors.GREEN))
    print(f"  PR will target: {base_branch}")
    return 0


# =============================================================================
# Command: set-scope
# =============================================================================

def cmd_set_scope(args: argparse.Namespace) -> int:
    """Set scope for PR title."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)
    scope = args.scope

    if not scope:
        print(colored("Error: Missing arguments", Colors.RED))
        print("Usage: python3 task.py set-scope <task-dir> <scope>")
        return 1

    task_json = target_dir / FILE_TASK_JSON
    if not task_json.is_file():
        print(colored(f"Error: task.json not found at {target_dir}", Colors.RED))
        return 1

    data = read_json(task_json)
    if not data:
        return 1

    data["scope"] = scope
    write_json(task_json, data)

    print(colored(f"✓ Scope set to: {scope}", Colors.GREEN))
    return 0
