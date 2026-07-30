#!/usr/bin/env python3
"""
Task CRUD operations.

Provides:
    ensure_tasks_dir   - Ensure tasks directory exists
    cmd_create         - Create a new task
    cmd_cancel         - Record an authorized terminal cancellation
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
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import (
    get_loop_v1_admission,
    get_packages,
    get_session_auto_commit,
    is_monorepo,
    resolve_package,
    validate_package,
)
from .git import run_git
from .io import read_json, write_bytes_atomic, write_json
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
    find_archived_task_by_name,
    find_task_by_name,
    resolve_task_dir,
    run_task_hooks,
)
from .done_gate import (
    cancellation_gate_errors as _cancellation_gate_errors,
    cancellation_readiness_errors as _cancellation_readiness_errors,
    done_gate_errors as _done_gate_errors,
)

HARNESS_MODE = "harness_state_machine"
CURRENT_TRELLIS_SELECTOR = "current_trellis"
LOOP_V1_MODE = "loop_v1"
LOOP_V4_MODE = "loop_v4"
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


def _find_archived_task(tasks_dir: Path, task_name: str) -> Path | None:
    """Resolve an archived task by exact directory name or slug suffix."""
    target = Path(task_name.replace("\\", "/")).name
    exact = find_archived_task_by_name(target, tasks_dir)
    if exact:
        return exact
    archive_dir = tasks_dir / DIR_ARCHIVE
    if not archive_dir.is_dir():
        return None
    for month_dir in sorted(archive_dir.iterdir()):
        if not month_dir.is_dir():
            continue
        for candidate in month_dir.iterdir():
            if candidate.is_dir() and candidate.name.endswith(f"-{target}"):
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


def _resolve_create_workflow_mode(
    args: argparse.Namespace,
    repo_root: Path,
    tier: str,
    parent_data: dict[str, Any] | None,
) -> str:
    """Resolve the stored workflow mode before create performs any writes."""
    raw_requested = getattr(args, "workflow_mode", None)
    requested = str(raw_requested).strip() if raw_requested is not None else None
    if requested == "":
        raise ValueError("--workflow-mode requires a non-empty value")

    if requested == LOOP_V4_MODE:
        raise ValueError("workflow mode 'loop_v4' is historical and cannot create new tasks")

    if tier != "parent" and requested:
        raise ValueError("--workflow-mode is parent-only; children inherit their parent mode")

    if tier == "child":
        meta = parent_data.get("meta") if parent_data else None
        inherited = meta.get("workflow_mode") if isinstance(meta, dict) else None
        if inherited == LOOP_V4_MODE:
            raise ValueError("parent workflow mode 'loop_v4' is historical and cannot create new tasks")
        return inherited if isinstance(inherited, str) and inherited else HARNESS_MODE

    if tier != "parent":
        return HARNESS_MODE

    admission_enabled, parent_default = get_loop_v1_admission(repo_root)
    if requested is None and admission_enabled and parent_default != LOOP_V1_MODE:
        raise ValueError(
            "enabled Loop v1 admission requires parent_default 'loop_v1'; "
            "use explicit --workflow-mode current_trellis to override"
        )
    selected = requested or parent_default
    if selected == CURRENT_TRELLIS_SELECTOR:
        return HARNESS_MODE
    if selected != LOOP_V1_MODE:
        raise ValueError(
            f"unknown workflow mode '{selected}'; expected current_trellis or loop_v1"
        )
    if not admission_enabled:
        raise ValueError("Loop v1 admission is disabled")
    from loop_v1.qualification import operation_qualification

    qualification = operation_qualification(repo_root)
    if not qualification.valid:
        detail = "; ".join(qualification.issues) or "unknown qualification failure"
        raise ValueError(f"Loop v1 runtime is not qualified: {detail}")
    return LOOP_V1_MODE


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


def _init_state_if_supported(task_dir: Path, tier: str, workflow_mode: str) -> None:
    if workflow_mode != HARNESS_MODE or tier not in {"parent", "child"}:
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


def _table_columns(cells: list[str]) -> dict[str, int]:
    return {value.strip().lower(): index for index, value in enumerate(cells)}


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _normalize_rtm_ids(raw: list[str] | None) -> list[str]:
    ids: list[str] = []
    for item in raw or []:
        value = item.strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def _cancellation_evidence(task_name: str, reason: str, superseded_by: str | None) -> str:
    evidence = f"cancelled {task_name}: {reason}"
    if superseded_by:
        evidence += f"; superseded by {superseded_by}"
    return evidence


def _retained_files(task_dir: Path) -> list[str]:
    return sorted(
        path.relative_to(task_dir).as_posix()
        for path in task_dir.rglob("*")
        if path.is_file()
        and path.relative_to(task_dir).as_posix() not in {FILE_TASK_JSON, "state-events.jsonl"}
    )


def _cancellation_request_matches(existing: dict[str, Any], request: dict[str, Any]) -> bool:
    fields = ("reason", "authorized_by", "superseded_by", "rtm_disposition", "rtm_ids")
    return all(existing.get(field) == request.get(field) for field in fields)


def _update_parent_governance_for_cancel(
    parent_dir: Path,
    child_name: str,
    *,
    disposition: str,
    rtm_ids: list[str],
    evidence: str,
) -> bool:
    """Update only the exact child row and explicitly named parent RTM rows."""
    path = parent_dir / "governance.md"
    if not path.is_file():
        raise ValueError("parent governance.md is missing")

    original = path.read_text(encoding="utf-8")
    out: list[str] = []
    section = ""
    child_columns: dict[str, int] = {}
    rtm_columns: dict[str, int] = {}
    child_matches = 0
    found_rtm: set[str] = set()

    for line in original.splitlines():
        if line.startswith("## "):
            section = line
        if not line.startswith("|"):
            out.append(line)
            continue

        cells = _split_table_row(line)
        if _is_table_separator(cells):
            out.append(line)
            continue

        if section == "## Child Index":
            if not child_columns:
                child_columns = _table_columns(cells)
                out.append(line)
                continue
            child_index = child_columns.get("child", 0)
            status_index = child_columns.get("status")
            if len(cells) > child_index and cells[child_index].strip("`") == child_name:
                child_matches += 1
                if status_index is None or len(cells) <= status_index:
                    raise ValueError("parent Child Index status column is missing")
                if cells[status_index].lower() in {"completed", "done"}:
                    raise ValueError(f"parent Child Index row is already completed: {child_name}")
                cells[status_index] = "cancelled"
                line = _format_table_row(cells)
        elif section == "## RTM":
            if not rtm_columns:
                rtm_columns = _table_columns(cells)
                out.append(line)
                continue
            if not cells:
                out.append(line)
                continue
            rtm_id = cells[0].strip("`")
            if rtm_id in rtm_ids:
                status_index = rtm_columns.get("status")
                evidence_index = rtm_columns.get("evidence")
                if status_index is None or evidence_index is None:
                    raise ValueError("parent RTM requires Status and Evidence columns")
                if len(cells) <= max(status_index, evidence_index):
                    raise ValueError(f"parent RTM row is malformed: {rtm_id}")
                current = cells[status_index].lower()
                if current in {"completed", "done"}:
                    raise ValueError(f"parent RTM row is already completed: {rtm_id}")
                if current in {"removed", "deferred"} and current != disposition:
                    raise ValueError(f"parent RTM row has conflicting disposition: {rtm_id}")
                cells[status_index] = disposition
                if evidence not in cells[evidence_index]:
                    prior = cells[evidence_index].strip()
                    cells[evidence_index] = f"{prior}; {evidence}" if prior and prior != "-" else evidence
                found_rtm.add(rtm_id)
                line = _format_table_row(cells)
        out.append(line)

    if child_matches != 1:
        raise ValueError(f"parent Child Index row not found exactly once: {child_name}")
    missing = [rtm_id for rtm_id in rtm_ids if rtm_id not in found_rtm]
    if missing:
        raise ValueError(f"parent RTM rows not found: {', '.join(missing)}")

    updated = "\n".join(out) + "\n"
    if updated == original:
        return False
    write_bytes_atomic(path, updated.encode("utf-8"))
    return True


def _update_parent_governance(parent_dir: Path, child_data: dict[str, Any], commit: str) -> bool:
    path = parent_dir / "governance.md"
    text = path.read_text(encoding="utf-8")
    title = child_data.get("title") or child_data.get("name") or ""
    child_dir_name = child_data.get("_dir_name") or child_data.get("name") or ""
    evidence = f"{child_dir_name}/stage-report.md"
    changed = False
    child_matches = 0
    matched_rtm = False
    out: list[str] = []
    owner_aliases: dict[str, set[str]] = {}
    owner_statuses: dict[str, str] = {}
    current_aliases = {value for value in (title, child_dir_name) if value}
    terminal_child_statuses = {"completed", "done", "cancelled"}

    in_child_index = False
    in_rtm = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_child_index = line == "## Child Index"
            in_rtm = line == "## RTM"
        if line.startswith("|") and not line.startswith("|---"):
            cells = _split_table_row(line)
            if in_child_index and len(cells) >= 7:
                row_name = cells[0].strip("`")
                if row_name.lower() != "child":
                    row_data = read_json(parent_dir.parent / row_name / FILE_TASK_JSON) or {}
                    row_title = row_data.get("title") or row_data.get("name") or ""
                    for alias in {row_name, row_title} - {""}:
                        owner_aliases.setdefault(alias, set()).add(row_name)
                    if row_name == child_dir_name or (not child_dir_name and row_name == title):
                        original_cells = cells.copy()
                        child_matches += 1
                        cells[5] = "completed"
                        cells[6] = commit
                        line = _format_table_row(cells)
                        changed = changed or cells != original_cells
                    owner_statuses[row_name] = cells[5].strip().lower()
            elif in_rtm and len(cells) >= 4:
                raw_owners = [part.strip().strip("`") for part in cells[1].split("+")]
                if current_aliases.intersection(raw_owners):
                    resolved_owners: list[str] = []
                    for owner in raw_owners:
                        matches = owner_aliases.get(owner, set())
                        if len(matches) != 1:
                            raise ValueError(
                                f"parent governance RTM owner is unresolved or ambiguous: {owner}"
                            )
                        resolved_owners.append(next(iter(matches)))
                    if len(set(resolved_owners)) != len(resolved_owners):
                        raise ValueError("parent governance RTM owner is duplicated")

                    original_cells = cells.copy()
                    if len(resolved_owners) == 1:
                        cells[2] = "completed"
                        cells[3] = evidence
                    else:
                        if (
                            cells[2].strip().lower() not in {"removed", "deferred"}
                            and all(
                                owner_statuses.get(owner) in terminal_child_statuses
                                for owner in resolved_owners
                            )
                        ):
                            cells[2] = "completed"
                        prior_evidence = cells[3].strip()
                        evidence_items = {
                            item.strip() for item in prior_evidence.split(";") if item.strip()
                        }
                        if evidence not in evidence_items:
                            cells[3] = (
                                f"{prior_evidence}; {evidence}"
                                if prior_evidence and prior_evidence != "-"
                                else evidence
                            )
                    line = _format_table_row(cells)
                    matched_rtm = True
                    changed = changed or cells != original_cells
        out.append(line)

    if child_matches != 1:
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

    parent_dir: Path | None = None
    parent_json_path: Path | None = None
    parent_data: dict[str, Any] | None = None
    if getattr(args, "parent", None):
        parent_dir = resolve_task_dir(args.parent, repo_root)
        parent_json_path = parent_dir / FILE_TASK_JSON
        if parent_json_path.is_file():
            parent_data = read_json(parent_json_path)

    try:
        workflow_mode = _resolve_create_workflow_mode(args, repo_root, tier, parent_data)
    except ValueError as exc:
        print(colored(f"Error: {exc}", Colors.RED), file=sys.stderr)
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

    archived_task_dir = find_archived_task_by_name(dir_name, tasks_dir)
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
        "meta": {"workflow_mode": workflow_mode},
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
    if args.parent and parent_dir is not None and parent_json_path is not None:
        if not parent_json_path.is_file():
            print(colored(f"Warning: Parent task.json not found: {args.parent}", Colors.YELLOW), file=sys.stderr)
        elif parent_data:
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

    _init_state_if_supported(task_dir, tier, workflow_mode)

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
# Command: cancel
# =============================================================================

def cmd_cancel(args: argparse.Namespace) -> int:
    """Record one authorized, auditable terminal task cancellation."""
    repo_root = get_repo_root()
    tasks_dir = get_tasks_dir(repo_root)
    task_dir = resolve_task_dir(args.name, repo_root)
    task_json_path = task_dir / FILE_TASK_JSON
    if not task_json_path.is_file():
        print(colored(f"Error: task.json not found: {args.name}", Colors.RED), file=sys.stderr)
        return 1

    reason = (args.reason or "").strip()
    authorized_by = (args.authorized_by or "").strip()
    superseded_by = (getattr(args, "superseded_by", "") or "").strip() or None
    disposition = (getattr(args, "rtm_disposition", "") or "").strip() or None
    rtm_ids = sorted(_normalize_rtm_ids(getattr(args, "rtm_id", None)))
    if not reason or not authorized_by:
        print(colored("Error: --reason and --authorized-by must be non-empty", Colors.RED), file=sys.stderr)
        return 1

    data = read_json(task_json_path)
    if not data:
        print(colored("Error: failed to read task.json", Colors.RED), file=sys.stderr)
        return 1
    if (data.get("meta") or {}).get("workflow_mode") == LOOP_V1_MODE:
        if disposition or rtm_ids:
            print(
                colored(
                    "Error: pre-admission Loop cancellation does not accept RTM arguments",
                    Colors.RED,
                ),
                file=sys.stderr,
            )
            return 1
        try:
            from loop_v1.pre_admission import (
                PreAdmissionCancellationError,
                cancel_pre_admission_parent,
            )

            cancel_pre_admission_parent(
                task_dir,
                repo_root,
                actor=authorized_by,
                reason=reason,
                superseded_by=superseded_by,
            )
        except (OSError, PreAdmissionCancellationError) as exc:
            print(
                colored(f"Error: Loop v1 pre-admission cancellation rejected: {exc}", Colors.RED),
                file=sys.stderr,
            )
            return 1

        from .active_task import clear_task_from_sessions

        clear_task_from_sessions(str(task_dir), repo_root)
        run_task_hooks("after_cancel", task_json_path, repo_root)
        print(colored(f"Cancelled: {task_dir.name}", Colors.GREEN), file=sys.stderr)
        print(_repo_relative_path(task_dir, repo_root))
        return 0
    tier = data.get("tier")
    if tier not in V2_TIERS:
        print(colored(f"Error: unsupported task tier: {tier}", Colors.RED), file=sys.stderr)
        return 1
    if data.get("status") == "completed":
        print(colored("Error: completed tasks cannot be cancelled", Colors.RED), file=sys.stderr)
        return 1

    parent_dir: Path | None = None
    if tier == "child":
        parent_name = data.get("parent")
        parent_dir = find_task_by_name(parent_name, tasks_dir) if parent_name else None
        if not parent_dir and data.get("status") == "cancelled" and parent_name:
            parent_dir = _find_archived_task(tasks_dir, parent_name)
        if not parent_dir:
            print(colored("Error: linked child parent is missing or archived", Colors.RED), file=sys.stderr)
            return 1
        if disposition not in {"removed", "deferred"} or not rtm_ids:
            print(
                colored(
                    "Error: linked child cancellation requires --rtm-disposition removed|deferred "
                    "and at least one --rtm-id",
                    Colors.RED,
                ),
                file=sys.stderr,
            )
            return 1
    elif disposition or rtm_ids:
        print(colored("Error: RTM arguments are only valid for linked child cancellation", Colors.RED), file=sys.stderr)
        return 1

    if tier == "parent":
        readiness = _cancellation_readiness_errors(task_dir, data, repo_root)
        if readiness:
            print(colored("Error: parent cancellation readiness failed", Colors.RED), file=sys.stderr)
            for error in readiness:
                print(f"  - {error}", file=sys.stderr)
            return 1

    request = {
        "reason": reason,
        "authorized_by": authorized_by,
        "superseded_by": superseded_by,
        "rtm_disposition": disposition,
        "rtm_ids": rtm_ids,
    }
    existing = data.get("cancellation")
    if data.get("status") == "cancelled":
        if not isinstance(existing, dict) or not _cancellation_request_matches(existing, request):
            print(colored("Error: cancelled task metadata conflicts with the request", Colors.RED), file=sys.stderr)
            return 1
        cancellation = existing
        errors = _cancellation_gate_errors(task_dir, data, repo_root)
        if errors:
            print(colored("Error: existing cancellation evidence is invalid", Colors.RED), file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        print(colored(f"Already cancelled: {task_dir.name}", Colors.GREEN), file=sys.stderr)
        print(_repo_relative_path(task_dir, repo_root))
        return 0
    else:
        authorized_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        cancellation = {
            **request,
            "authorized_at": authorized_at,
            "preserved": {
                field: data.get(field)
                for field in ("branch", "worktree_path", "commit", "children", "parent")
            },
            "retained_files": _retained_files(task_dir),
        }
        if tier == "child":
            cancellation["rtm_evidence"] = _cancellation_evidence(
                task_dir.name,
                reason,
                superseded_by,
            )

    if parent_dir:
        try:
            _update_parent_governance_for_cancel(
                parent_dir,
                task_dir.name,
                disposition=str(cancellation["rtm_disposition"]),
                rtm_ids=list(cancellation["rtm_ids"]),
                evidence=str(cancellation["rtm_evidence"]),
            )
        except (OSError, ValueError) as exc:
            print(colored(f"Error: parent cancellation disposition failed: {exc}", Colors.RED), file=sys.stderr)
            return 1

    try:
        from state_machine import cancel_task

        cancel_task(task_dir, by=authorized_by, cancellation=cancellation)
    except Exception as exc:
        print(colored(f"Error: cancellation state update failed: {exc}", Colors.RED), file=sys.stderr)
        return 1

    cancelled_data = read_json(task_json_path) or {}
    errors = _cancellation_gate_errors(task_dir, cancelled_data, repo_root)
    if errors:
        print(colored("Error: cancellation validation failed", Colors.RED), file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    from .active_task import clear_task_from_sessions

    clear_task_from_sessions(str(task_dir), repo_root)
    run_task_hooks("after_cancel", task_json_path, repo_root)
    print(colored(f"Cancelled: {task_dir.name}", Colors.GREEN), file=sys.stderr)
    print(_repo_relative_path(task_dir, repo_root))
    return 0


# =============================================================================
# Command: archive
# =============================================================================

def _terminal_family_member(task_dir: Path, data: dict[str, Any], tier: str) -> bool:
    if data.get("tier") != tier:
        return False
    machine = ((data.get("meta") or {}).get("state_machine") or {})
    state = machine.get("current_state")
    if data.get("status") == "completed":
        return state == f"{tier}_archived"
    if data.get("status") == "cancelled":
        return state == f"{tier}_cancelled"
    return False


def _current_trellis_family_moves(
    repo_root: Path,
    parent_dir: Path,
) -> tuple[str, str, list[tuple[Path, Path]]]:
    tasks_dir = get_tasks_dir(repo_root)
    parent_data = read_json(parent_dir / FILE_TASK_JSON)
    if not parent_data or not _terminal_family_member(
        parent_dir,
        parent_data,
        "parent",
    ):
        raise ValueError("Current Trellis parent is not terminal")
    parent_name = parent_dir.name
    parent_active = parent_dir.parent.resolve() == tasks_dir.resolve()
    if parent_active:
        month = datetime.now().strftime("%Y-%m")
        archived_parent = tasks_dir / DIR_ARCHIVE / month / parent_name
    else:
        try:
            month = parent_dir.parent.name
            if parent_dir.parent.parent.resolve() != (
                tasks_dir / DIR_ARCHIVE
            ).resolve():
                raise ValueError
        except ValueError as exc:
            raise ValueError("Current Trellis parent archive location is invalid") from exc
        archived_parent = parent_dir

    moves: list[tuple[Path, Path]] = []
    for child_name in parent_data.get("children") or []:
        if not isinstance(child_name, str) or not child_name:
            raise ValueError("Current Trellis parent child identity is invalid")
        active = tasks_dir / child_name
        archived_matches = [
            path
            for path in (tasks_dir / DIR_ARCHIVE).glob(f"*/{child_name}")
            if path.is_dir()
        ]
        if active.is_dir() and archived_matches:
            raise ValueError(f"Current Trellis child location is ambiguous: {child_name}")
        if active.is_dir():
            child_dir = active
        elif len(archived_matches) == 1:
            child_dir = archived_matches[0]
        else:
            raise ValueError(f"Current Trellis child is missing or ambiguous: {child_name}")
        child_data = read_json(child_dir / FILE_TASK_JSON)
        if (
            not child_data
            or child_data.get("parent") != parent_name
            or not _terminal_family_member(child_dir, child_data, "child")
        ):
            raise ValueError(f"Current Trellis child is not terminal: {child_name}")
        if child_dir == active:
            destination = tasks_dir / DIR_ARCHIVE / month / child_name
            if destination.exists():
                raise ValueError(f"Current Trellis child destination exists: {child_name}")
            moves.append((child_dir, destination))
        elif child_dir.parent.name != month:
            raise ValueError(f"Current Trellis family spans archive months: {child_name}")

    if parent_active:
        if archived_parent.exists():
            raise ValueError("Current Trellis parent archive destination exists")
        moves.append((parent_dir, archived_parent))
    return parent_name, month, moves


def _archive_current_trellis_family(
    repo_root: Path,
    parent_dir: Path,
    *,
    no_commit: bool,
) -> dict[str, object]:
    from .active_task import clear_task_from_sessions
    from .archive_transaction import archive_paths_transaction

    parent_name, month, moves = _current_trellis_family_moves(
        repo_root,
        parent_dir,
    )
    if not moves:
        return {
            "family_root": parent_name,
            "phase": "committed",
            "transaction_id": None,
        }
    for source, _ in moves:
        clear_task_from_sessions(str(source), repo_root)
    identity = {
        "family_root": parent_name,
        "month": month,
        "sources": [source.name for source, _ in moves],
    }
    transaction_id = (
        "family-"
        + sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    )
    return archive_paths_transaction(
        repo_root,
        transaction_id=transaction_id,
        moves=moves,
        commit_message=f"chore(task): archive {parent_name} family",
        commit_enabled=not no_commit,
        family_root=parent_name,
    )


def _archive_orphaned_current_trellis_families(
    repo_root: Path,
    *,
    no_commit: bool,
) -> dict[str, object]:
    from .active_task import clear_task_from_sessions
    from .archive_transaction import archive_paths_transaction

    tasks_dir = get_tasks_dir(repo_root)
    moves: list[tuple[Path, Path]] = []
    family_roots: list[str] = []
    seen_sources: set[Path] = set()
    for parent_dir in sorted((tasks_dir / DIR_ARCHIVE).glob("*/*")):
        parent_data = read_json(parent_dir / FILE_TASK_JSON)
        if (
            not parent_data
            or parent_data.get("tier") != "parent"
            or (parent_data.get("meta") or {}).get("workflow_mode") != HARNESS_MODE
            or not _terminal_family_member(parent_dir, parent_data, "parent")
        ):
            continue
        parent_name, _, family_moves = _current_trellis_family_moves(
            repo_root,
            parent_dir,
        )
        if not family_moves:
            continue
        for source, destination in family_moves:
            resolved = source.resolve()
            if resolved in seen_sources:
                raise ValueError(
                    f"Current Trellis child belongs to multiple families: {source.name}"
                )
            seen_sources.add(resolved)
            moves.append((source, destination))
        family_roots.append(parent_name)
    if not moves:
        return {
            "family_roots": [],
            "phase": "committed",
            "transaction_id": None,
        }
    for source, _ in moves:
        clear_task_from_sessions(str(source), repo_root)
    identity = {
        "family_roots": family_roots,
        "moves": [
            source.relative_to(repo_root).as_posix()
            for source, _ in moves
        ],
    }
    transaction_id = (
        "family-orphan-sweep-"
        + sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    )
    result = archive_paths_transaction(
        repo_root,
        transaction_id=transaction_id,
        moves=moves,
        commit_message="chore(task): archive orphaned Current Trellis families",
        commit_enabled=not no_commit,
        family_root="current-trellis-orphan-sweep",
    )
    result["family_roots"] = family_roots
    return result


def cmd_archive_orphans(args: argparse.Namespace) -> int:
    """Atomically sweep terminal children whose Current Trellis parents are archived."""
    repo_root = get_repo_root()
    try:
        result = _archive_orphaned_current_trellis_families(
            repo_root,
            no_commit=getattr(args, "no_commit", False),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            colored(f"Error: Current Trellis orphan sweep failed: {exc}", Colors.RED),
            file=sys.stderr,
        )
        return 1
    print(
        colored(
            f"Archived orphaned families: {len(result['family_roots'])} ({result['phase']})",
            Colors.GREEN,
        ),
        file=sys.stderr,
    )
    return 0


def cmd_archive_recover(args: argparse.Namespace) -> int:
    """Recover one incomplete archive transaction by exact journal identity."""
    from .archive_transaction import (
        ArchiveTransactionError,
        recover_archive_transaction,
    )

    repo_root = get_repo_root()
    try:
        result = recover_archive_transaction(repo_root, args.transaction_id)
    except (OSError, ArchiveTransactionError) as exc:
        print(
            colored(f"Error: archive recovery failed: {exc}", Colors.RED),
            file=sys.stderr,
        )
        return 1
    print(
        colored(
            f"Archive transaction {args.transaction_id}: {result['phase']}",
            Colors.GREEN,
        ),
        file=sys.stderr,
    )
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    """Archive a validated completed or formally cancelled task."""
    repo_root = get_repo_root()
    task_name = args.name

    if not task_name:
        print(colored("Error: Task name is required", Colors.RED), file=sys.stderr)
        return 1

    tasks_dir = get_tasks_dir(repo_root)

    # Resolve task directory (supports task name, relative path, or absolute path)
    task_dir = resolve_task_dir(task_name, repo_root)

    if not task_dir or not task_dir.is_dir():
        target = Path(task_name.replace("\\", "/")).name
        exact_archived = find_archived_task_by_name(target, tasks_dir, require_unique=True)
        archived = exact_archived or _find_archived_task(tasks_dir, task_name)
        archived_json = archived / FILE_TASK_JSON if archived else None
        archived_data = read_json(archived_json) if archived_json and archived_json.is_file() else None
        if ((archived_data or {}).get("meta") or {}).get("workflow_mode") == LOOP_V1_MODE:
            print(
                colored(
                    "Error: Loop v1 task archive is controlled by loop_v1.orchestrator",
                    Colors.RED,
                ),
                file=sys.stderr,
            )
            return 1
        if archived and (archived_data or {}).get("status") == "cancelled":
            if (
                (archived_data or {}).get("tier") == "parent"
                and ((archived_data or {}).get("meta") or {}).get("workflow_mode")
                == HARNESS_MODE
            ):
                try:
                    _archive_current_trellis_family(
                        repo_root,
                        archived,
                        no_commit=getattr(args, "no_commit", False),
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    print(
                        colored(f"Error: Current Trellis family sweep failed: {exc}", Colors.RED),
                        file=sys.stderr,
                    )
                    return 1
            print(colored(f"Already archived cancelled task: {archived.name}", Colors.GREEN), file=sys.stderr)
            print(_repo_relative_path(archived, repo_root))
            return 0
        if (
            exact_archived
            and (archived_data or {}).get("status") == "completed"
            and (archived_data or {}).get("tier") == "parent"
        ):
            if (
                ((archived_data or {}).get("meta") or {}).get("workflow_mode")
                == HARNESS_MODE
            ):
                try:
                    _archive_current_trellis_family(
                        repo_root,
                        exact_archived,
                        no_commit=getattr(args, "no_commit", False),
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    print(
                        colored(f"Error: Current Trellis family sweep failed: {exc}", Colors.RED),
                        file=sys.stderr,
                    )
                    return 1
            print(colored(f"Already archived parent task: {archived.name}", Colors.GREEN), file=sys.stderr)
            print(_repo_relative_path(archived, repo_root))
            return 0
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
    if task_json_path.is_file():
        data = read_json(task_json_path)
        if data:
            if (data.get("meta") or {}).get("workflow_mode") == LOOP_V1_MODE:
                print(
                    colored(
                        "Error: Loop v1 task archive is controlled by loop_v1.orchestrator",
                        Colors.RED,
                    ),
                    file=sys.stderr,
                )
                return 1
            if data.get("tier") == "child" and data.get("parent"):
                print(
                    colored(
                        "Error: linked Current Trellis children archive with their parent family",
                        Colors.RED,
                    ),
                    file=sys.stderr,
                )
                return 1
            was_cancelled = data.get("status") == "cancelled"
            if was_cancelled and getattr(args, "force_archive", False):
                print(
                    colored("Error: --force-archive cannot bypass cancelled-task validation", Colors.RED),
                    file=sys.stderr,
                )
                return 1
            if not _check_done_gate_or_force(args, task_dir, data, task_json_path, repo_root):
                return 1
            data = read_json(task_json_path) or data
            if (
                not was_cancelled
                and data.get("tier") == "parent"
                and (data.get("meta") or {}).get("workflow_mode") == HARNESS_MODE
            ):
                try:
                    _advance_parent_to_archived(task_dir)
                except Exception as exc:
                    print(colored(f"Error: parent archive state update failed: {exc}", Colors.RED), file=sys.stderr)
                    return 1
                data = read_json(task_json_path) or data
            if not was_cancelled:
                data["status"] = "completed"
                data["completedAt"] = today
                write_json(task_json_path, data)
            if (
                data.get("tier") == "parent"
                and (data.get("meta") or {}).get("workflow_mode") == HARNESS_MODE
            ):
                if getattr(args, "force_archive", False):
                    print(
                        colored(
                            "Error: --force-archive cannot bypass Current Trellis family validation",
                            Colors.RED,
                        ),
                        file=sys.stderr,
                    )
                    return 1
                try:
                    result = _archive_current_trellis_family(
                        repo_root,
                        task_dir,
                        no_commit=getattr(args, "no_commit", False),
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    print(
                        colored(f"Error: Current Trellis family archive failed: {exc}", Colors.RED),
                        file=sys.stderr,
                    )
                    return 1
                archive_dest = (
                    tasks_dir
                    / DIR_ARCHIVE
                    / datetime.now().strftime("%Y-%m")
                    / dir_name
                )
                print(
                    colored(
                        f"Archived family: {dir_name} ({result['phase']})",
                        Colors.GREEN,
                    ),
                    file=sys.stderr,
                )
                print(_repo_relative_path(archive_dest, repo_root))
                run_task_hooks(
                    "after_archive",
                    archive_dest / FILE_TASK_JSON,
                    repo_root,
                )
                return 0

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
            if not _auto_commit_archive(dir_name, repo_root):
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
    from .active_task import clear_task_from_sessions

    clear_task_from_sessions(str(task_dir), repo_root)

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
