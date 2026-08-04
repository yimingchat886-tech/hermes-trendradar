#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Task Management Script.

Usage:
    python3 task.py create "<title>" [--slug <name>] [--tier light|child|parent] [--owner cc|codex|jym] [--touches <glob>] [--parent <dir>] [--package <pkg>] [--strategy single|loop]
    python3 task.py add-context <dir> <file> <path> [reason] # Add jsonl entry
    python3 task.py validate <dir>              # Validate jsonl files
    python3 task.py list-context <dir>          # List jsonl entries
    python3 task.py start <dir> [--taskrun-input <json>] # Admit TaskRun or activate legacy task
    python3 task.py current [--source]          # Show active task
    python3 task.py finish                      # Clear active task
    python3 task.py set-branch <dir> <branch>   # Set git branch
    python3 task.py set-base-branch <dir> <branch>  # Set PR target branch
    python3 task.py set-scope <dir> <scope>     # Set scope for PR title
    python3 task.py authorize-replacement <child> ...  # Bind one exact successor attempt
    python3 task.py reconcile-historical-replacement <child> ...  # Settle pre-implementation evidence
    python3 task.py cancel <task-dir> --reason <reason> --authorized-by <user>
    python3 task.py archive <task-dir>          # Archive completed/cancelled task
    python3 task.py archive-orphans [--check]   # Check or sweep orphaned terminal families
    python3 task.py complete-child <task-dir> --commit <hash>  # Complete Current Trellis child
    python3 task.py soft-archive <task-dir> --commit <hash>  # Legacy compatibility alias
    python3 task.py claim <task-dir> --owner codex  # Claim task ownership
    python3 task.py release <task-dir>          # Release task ownership to jym
    python3 task.py list                        # List active tasks
    python3 task.py list-archive [month]        # List archived tasks
    python3 task.py add-subtask <parent-dir> <child-dir>     # Link child to parent
    python3 task.py remove-subtask <parent-dir> <child-dir>  # Unlink child from parent
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common.log import Colors, colored
from common.paths import (
    DIR_WORKFLOW,
    DIR_SCRIPTS,
    DIR_TASKS,
    FILE_TASK_JSON,
    get_repo_root,
    get_developer,
    get_tasks_dir,
    get_current_task,
)
from common.active_task import (
    clear_active_task,
    resolve_active_task,
    resolve_context_key,
    set_active_task,
)
from common.io import read_json, write_json
from common.task_utils import resolve_task_dir, run_task_hooks
from common.tasks import iter_active_tasks, children_progress

# Import command handlers from split modules (also re-exports for plan.py compatibility)
from common.task_store import (
    cmd_create,
    cmd_authorize_replacement,
    cmd_reconcile_historical_replacement,
    cmd_cancel,
    cmd_archive,
    cmd_archive_orphans,
    cmd_archive_recover,
    cmd_complete_child,
    cmd_soft_archive,
    cmd_claim,
    cmd_release,
    cmd_set_branch,
    cmd_set_base_branch,
    cmd_set_scope,
    cmd_add_subtask,
    cmd_remove_subtask,
)
from common.task_context import (
    cmd_add_context,
    cmd_validate,
    cmd_list_context,
)


TASKRUN_MODE = "taskrun_v2"
TASKRUN_MODES = {"taskrun_v1", TASKRUN_MODE}
_TASKRUN_SINGLE_REQUIRED = {
    "actor",
    "authorization_ref",
    "reviewer_id",
    "worker_id",
}
_TASKRUN_SINGLE_OPTIONAL = {
    "action_risk",
    "attempts",
    "low_risk_mode",
    "provider_id",
}
_TASKRUN_LOOP_REQUIRED = {
    "actions",
    "actor",
    "authorization_ref",
    "reviewer_id",
    "worker_ids",
}
_TASKRUN_LOOP_OPTIONAL = _TASKRUN_SINGLE_OPTIONAL | {
    "candidate_commit_authorization_ref",
    "candidate_commit_ref",
    "concurrency",
}


def refresh_board_after(command: str, return_code: int) -> None:
    if return_code != 0 or command not in {
        "create",
        "start",
        "cancel",
        "archive",
        "complete-child",
        "soft-archive",
        "claim",
        "release",
    }:
        return
    repo_root = get_repo_root()
    board = repo_root / DIR_WORKFLOW / DIR_SCRIPTS / "board.py"
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
        print(
            colored(f"[WARN] BOARD refresh failed: {result.stderr.strip()}", Colors.YELLOW),
            file=sys.stderr,
        )


# =============================================================================
# Command: start / finish
# =============================================================================

def _read_taskrun_start_input(path_value: object, strategy: str) -> dict[str, object]:
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError("TaskRun start requires --taskrun-input")
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ValueError("TaskRun start input must be one regular JSON file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("TaskRun start input must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("TaskRun start input must contain one object")
    required, optional = {
        "single": (_TASKRUN_SINGLE_REQUIRED, _TASKRUN_SINGLE_OPTIONAL),
        "loop": (_TASKRUN_LOOP_REQUIRED, _TASKRUN_LOOP_OPTIONAL),
    }.get(strategy, (set(), set()))
    if not required:
        raise ValueError("task.json TaskRun strategy must be single or loop")
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required - optional)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if extra:
            details.append(f"unknown fields: {', '.join(extra)}")
        raise ValueError("TaskRun start input schema mismatch: " + "; ".join(details))
    return value


def _start_taskrun(
    args: argparse.Namespace,
    repo_root: Path,
    full_path: Path,
    task_dir: str,
    task: dict,
) -> int:
    strategy = (task.get("meta") or {}).get("taskrun_strategy")
    from taskrun import TaskRunError, TaskRunOperator

    try:
        direct_task_dir = get_tasks_dir(repo_root).resolve() / full_path.name
        if full_path.is_symlink() or full_path.resolve() != direct_task_dir:
            raise ValueError("TaskRun start requires one direct active task directory")
        request = _read_taskrun_start_input(
            getattr(args, "taskrun_input", None), str(strategy or "")
        )
        common = {
            "actor": request["actor"],
            "authorization_ref": request["authorization_ref"],
            "reviewer_id": request["reviewer_id"],
            "provider_id": request.get("provider_id", "local"),
            "action_risk": request.get("action_risk", "low"),
            "low_risk_mode": request.get("low_risk_mode", "aggregate"),
            "attempts": request.get("attempts", 4),
        }
        if strategy == "single":
            operator = TaskRunOperator.admit_single(
                repo_root,
                full_path.name,
                worker_id=request["worker_id"],
                **common,
            )
        else:
            operator = TaskRunOperator.admit_loop(
                repo_root,
                full_path.name,
                actions=request["actions"],
                worker_ids=request["worker_ids"],
                concurrency=request.get("concurrency", 2),
                candidate_commit_ref=request.get("candidate_commit_ref"),
                candidate_commit_authorization_ref=request.get(
                    "candidate_commit_authorization_ref"
                ),
                **common,
            )
    except (OSError, TypeError, ValueError, TaskRunError) as exc:
        print(colored(f"Error: TaskRun start rejected: {exc}", Colors.RED))
        return 1

    if resolve_context_key():
        active = set_active_task(task_dir, repo_root)
        if active:
            print(colored(f"✓ Current task set to: {task_dir}", Colors.GREEN))
            print(f"Source: {active.source}")
        else:
            print(
                colored(
                    "Warning: TaskRun admitted but the active-task pointer was not persisted",
                    Colors.YELLOW,
                )
            )
    else:
        print(
            colored(
                "ℹ Session identity not available; active-task pointer not persisted",
                Colors.YELLOW,
            )
        )
    print(colored(f"✓ TaskRun admitted: {operator.task_run_id} ({strategy})", Colors.GREEN))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Set active task."""
    repo_root = get_repo_root()
    task_input = args.dir

    if not task_input:
        print(colored("Error: task directory or name required", Colors.RED))
        return 1

    # Resolve task directory (supports task name, relative path, or absolute path)
    full_path = resolve_task_dir(task_input, repo_root)

    if not full_path.is_dir():
        print(colored(f"Error: Task not found: {task_input}", Colors.RED))
        print("Hint: Use task name (e.g., 'my-task') or full path (e.g., '.trellis/tasks/01-31-my-task')")
        return 1

    # Convert to relative path for storage
    try:
        task_dir = full_path.relative_to(repo_root).as_posix()
    except ValueError:
        task_dir = str(full_path)

    task_json_path = full_path / FILE_TASK_JSON
    task = read_json(task_json_path) if task_json_path.is_file() else None
    workflow_mode = (task.get("meta") or {}).get("workflow_mode") if task else None
    if workflow_mode in TASKRUN_MODES:
        return _start_taskrun(args, repo_root, full_path, task_dir, task)
    if getattr(args, "taskrun_input", None):
        print(colored("Error: --taskrun-input is only valid for TaskRun tasks", Colors.RED))
        return 1

    if not resolve_context_key():
        # Degraded mode: no session identity available.
        # Hook didn't inject TRELLIS_CONTEXT_ID (common on Windows + Claude Code,
        # --continue resume path, fork distribution, hooks disabled, etc.). Skip
        # per-session pointer write; AI continues based on conversation context.
        print(colored(
            "ℹ Session identity not available; active-task pointer not persisted "
            "this session (degraded mode). AI continues based on conversation context.",
            Colors.YELLOW,
        ))
        print(colored(
            "Hint: run inside an AI IDE/session that exposes session identity, "
            "or set TRELLIS_CONTEXT_ID before running task.py start.",
            Colors.YELLOW,
        ))

        # Still flip task.json status: planning → in_progress so downstream phases proceed.
        if task_json_path.is_file():
            data = read_json(task_json_path)
            if data and data.get("status") == "planning":
                data["status"] = "in_progress"
                if write_json(task_json_path, data):
                    print(colored("✓ Status: planning → in_progress (degraded)", Colors.GREEN))
            run_task_hooks("after_start", task_json_path, repo_root)
        return 0

    active = set_active_task(task_dir, repo_root)
    if active:
        print(colored(f"✓ Current task set to: {task_dir}", Colors.GREEN))
        print(f"Source: {active.source}")

        if task_json_path.is_file():
            data = read_json(task_json_path)
            if data and data.get("status") == "planning":
                data["status"] = "in_progress"
                if write_json(task_json_path, data):
                    print(colored("✓ Status: planning → in_progress", Colors.GREEN))

        print()
        print(colored("The hook will now inject context from this task's jsonl files.", Colors.BLUE))

        run_task_hooks("after_start", task_json_path, repo_root)
        return 0
    else:
        print(colored("Error: Failed to set current task", Colors.RED))
        return 1


def cmd_finish(args: argparse.Namespace) -> int:
    """Clear active task."""
    repo_root = get_repo_root()
    active = clear_active_task(repo_root)
    current = active.task_path

    if not current:
        print(colored("No current task set", Colors.YELLOW))
        return 0

    # Resolve task.json path before clearing
    task_json_path = repo_root / current / FILE_TASK_JSON

    print(colored(f"✓ Cleared current task (was: {current})", Colors.GREEN))
    print(f"Source: {active.source}")

    task = read_json(task_json_path) if task_json_path.is_file() else None
    workflow_mode = (task.get("meta") or {}).get("workflow_mode") if task else None
    if task_json_path.is_file() and workflow_mode not in TASKRUN_MODES:
        run_task_hooks("after_finish", task_json_path, repo_root)
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    """Show active task."""
    repo_root = get_repo_root()
    active = resolve_active_task(repo_root)

    if args.source:
        print(f"Current task: {active.task_path or '(none)'}")
        print(f"Source: {active.source}")
        if active.stale:
            print("State: stale")
        return 0 if active.task_path else 1

    if active.task_path:
        print(active.task_path)
        return 0

    return 1


# =============================================================================
# Command: list
# =============================================================================

def cmd_list(args: argparse.Namespace) -> int:
    """List active tasks."""
    repo_root = get_repo_root()
    tasks_dir = get_tasks_dir(repo_root)
    current_task = get_current_task(repo_root)
    developer = get_developer(repo_root)
    filter_mine = args.mine
    filter_status = args.status

    if filter_mine:
        if not developer:
            print(colored("Error: No developer set. Run init_developer.py first", Colors.RED), file=sys.stderr)
            return 1
        print(colored(f"My tasks (assignee: {developer}):", Colors.BLUE))
    else:
        print(colored("All active tasks:", Colors.BLUE))
    print()

    # Single pass: collect all tasks via shared iterator
    all_tasks = {t.dir_name: t for t in iter_active_tasks(tasks_dir)}
    all_statuses = {name: t.status for name, t in all_tasks.items()}

    # Display tasks hierarchically
    count = 0

    def _print_task(dir_name: str, indent: int = 0) -> None:
        nonlocal count
        t = all_tasks[dir_name]

        # Apply --mine filter
        if filter_mine and (t.assignee or "-") != developer:
            return

        # Apply --status filter
        if filter_status and t.status != filter_status:
            return

        relative_path = f"{DIR_WORKFLOW}/{DIR_TASKS}/{dir_name}"
        marker = ""
        if relative_path == current_task:
            marker = f" {colored('<- current', Colors.GREEN)}"

        # Children progress
        progress = children_progress(t.children, all_statuses)

        # Package tag
        pkg_tag = f" @{t.package}" if t.package else ""

        prefix = "  " * indent + "  - "

        if filter_mine:
            print(f"{prefix}{dir_name}/ ({t.status}){pkg_tag}{progress}{marker}")
        else:
            print(f"{prefix}{dir_name}/ ({t.status}){pkg_tag}{progress} [{colored(t.assignee or '-', Colors.CYAN)}]{marker}")
        count += 1

        # Print children indented
        for child_name in t.children:
            if child_name in all_tasks:
                _print_task(child_name, indent + 1)

    # Display only top-level tasks (those without a parent)
    for dir_name in sorted(all_tasks.keys()):
        if not all_tasks[dir_name].parent:
            _print_task(dir_name)

    if count == 0:
        if filter_mine:
            print("  (no tasks assigned to you)")
        else:
            print("  (no active tasks)")

    print()
    print(f"Total: {count} task(s)")
    return 0


# =============================================================================
# Command: list-archive
# =============================================================================

def cmd_list_archive(args: argparse.Namespace) -> int:
    """List archived tasks."""
    repo_root = get_repo_root()
    tasks_dir = get_tasks_dir(repo_root)
    archive_dir = tasks_dir / "archive"
    month = args.month

    print(colored("Archived tasks:", Colors.BLUE))
    print()

    if month:
        month_dir = archive_dir / month
        if month_dir.is_dir():
            print(f"[{month}]")
            for d in sorted(month_dir.iterdir()):
                if d.is_dir():
                    print(f"  - {d.name}/")
        else:
            print(f"  No archives for {month}")
    else:
        if archive_dir.is_dir():
            for month_dir in sorted(archive_dir.iterdir()):
                if month_dir.is_dir():
                    month_name = month_dir.name
                    count = sum(1 for d in month_dir.iterdir() if d.is_dir())
                    print(f"[{month_name}] - {count} task(s)")

    return 0


# =============================================================================
# Help
# =============================================================================

def show_usage() -> None:
    """Show usage help."""
    print("""Task Management Script

Usage:
  python3 task.py create <title>                     Create new light task directory
  python3 task.py create <title> --tier parent       Create new parent task directory
  python3 task.py create <title> --package <pkg>     Create task for a specific package
  python3 task.py create <title> --parent <dir>      Create task as child of parent
  python3 task.py add-context <dir> <jsonl> <path> [reason]  Add entry to jsonl
  python3 task.py validate <dir>                     Validate jsonl files
  python3 task.py list-context <dir>                 List jsonl entries
  python3 task.py start <dir> [--taskrun-input <json>]  Admit TaskRun or activate legacy task
  python3 task.py current [--source]                 Show active task
  python3 task.py finish                             Clear active task
  python3 task.py set-branch <dir> <branch>          Set git branch
  python3 task.py set-base-branch <dir> <branch>     Set PR target branch
  python3 task.py set-scope <dir> <scope>            Set scope for PR title
  python3 task.py cancel <task-dir> --reason <reason> --authorized-by <user>
                                                    Cancel task with audit evidence
  python3 task.py archive <task-dir>                 Archive completed/cancelled task
  python3 task.py archive-orphans --check            Read-only orphan family plan
  python3 task.py archive-recover <transaction-id>  Recover an archive transaction
  python3 task.py complete-child <task-dir> --commit <hash>  Complete Current Trellis child
  python3 task.py soft-archive <task-dir> --commit <hash>  Legacy compatibility alias
  python3 task.py claim <task-dir> --owner codex     Claim task ownership
  python3 task.py release <task-dir>                 Release task ownership to jym
  python3 task.py add-subtask <parent> <child>       Link child task to parent
  python3 task.py remove-subtask <parent> <child>    Unlink child from parent
  python3 task.py list [--mine] [--status <status>]  List tasks
  python3 task.py list-archive [YYYY-MM]             List archived tasks

Monorepo options:
  --package <pkg>      Package name (validated against config.yaml packages)

List options:
  --mine, -m           Show only tasks assigned to current developer
  --status, -s <s>     Filter by status (planning, running, in_progress, review, completed, cancelled)

Examples:
  python3 task.py create "Add login feature" --slug add-login
  python3 task.py create "Parallel repair" --slug parallel-repair --strategy loop
  python3 task.py create "Add login feature" --slug add-login --package cli
  python3 task.py create "Child task" --slug child --parent .trellis/tasks/01-21-parent
  python3 task.py add-context <dir> implement .trellis/spec/cli/backend/auth.md "Auth guidelines"
  python3 task.py set-branch <dir> task/add-login
  python3 task.py start .trellis/tasks/01-21-add-login --taskrun-input /tmp/start.json
  python3 task.py current --source
  python3 task.py finish
  python3 task.py cancel add-login --reason "superseded" --authorized-by jym
  python3 task.py archive add-login
  python3 task.py add-subtask parent-task child-task  # Link existing tasks
  python3 task.py remove-subtask parent-task child-task
  python3 task.py list                               # List all active tasks
  python3 task.py list --mine                        # List my tasks only
  python3 task.py list --mine --status in_progress   # List my in-progress tasks
""")


# =============================================================================
# Main Entry
# =============================================================================

def main() -> int:
    """CLI entry point."""
    # Deprecation guard: `init-context` was removed in v0.5.0-beta.12.
    # Detect early so argparse doesn't mask the real reason with a generic
    # "invalid choice" error.
    if len(sys.argv) >= 2 and sys.argv[1] == "init-context":
        print(
            colored(
                "Error: `task.py init-context` was removed in v0.5.0-beta.12.",
                Colors.RED,
            ),
            file=sys.stderr,
        )
        print(
            "implement.jsonl / check.jsonl are now seeded on `task.py create` for",
            file=sys.stderr,
        )
        print(
            "sub-agent-capable platforms and curated by the AI during planning when needed.",
            file=sys.stderr,
        )
        print("See .trellis/workflow.md planning artifact guidance or run:", file=sys.stderr)
        print(
            "  python3 ./.trellis/scripts/get_context.py --mode phase --step 1",
            file=sys.stderr,
        )
        print(
            "Use `task.py add-context <dir> implement|check <path> <reason>` to append entries.",
            file=sys.stderr,
        )
        return 2

    parser = argparse.ArgumentParser(
        description="Task Management Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # create
    p_create = subparsers.add_parser("create", help="Create new task")
    p_create.add_argument("title", help="Task title")
    p_create.add_argument("--slug", "-s", help="Task slug")
    p_create.add_argument("--assignee", "-a", help="Assignee developer")
    p_create.add_argument("--priority", "-p", default="P2", help="Priority (P0-P3)")
    p_create.add_argument("--description", "-d", help="Task description")
    p_create.add_argument("--parent", help="Parent task directory (establishes subtask link)")
    p_create.add_argument("--package", help="Package name for monorepo projects")
    p_create.add_argument("--tier", choices=["light", "child", "parent"], default="light",
                          help="task tier (default: light; --parent forces child)")
    p_create.add_argument("--owner", choices=["cc", "codex", "jym"], default="codex",
                          help="task owner")
    p_create.add_argument("--touches", action="append", default=[],
                          help="Expected touched path glob; repeat or comma-separate")
    p_create.add_argument("--workflow-mode",
                          help="Legacy parent selector; rejected after TaskRun cutover")
    p_create.add_argument("--strategy", choices=["single", "loop"],
                          help="TaskRun execution strategy (default: single)")

    # add-context
    p_add = subparsers.add_parser("add-context", help="Add context entry")
    p_add.add_argument("dir", help="Task directory")
    p_add.add_argument("file", help="JSONL file (implement|check)")
    p_add.add_argument("path", help="File path to add")
    p_add.add_argument("reason", nargs="?", help="Reason for adding")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate context files")
    p_validate.add_argument("dir", help="Task directory")

    # list-context
    p_listctx = subparsers.add_parser("list-context", help="List context entries")
    p_listctx.add_argument("dir", help="Task directory")

    # start
    p_start = subparsers.add_parser("start", help="Set active task")
    p_start.add_argument("dir", help="Task directory")
    p_start.add_argument("--taskrun-input",
                         help="Exact JSON start request for a TaskRun task")

    # current
    p_current = subparsers.add_parser("current", help="Show active task")
    p_current.add_argument("--source", action="store_true",
                           help="Show active task source")

    # finish
    subparsers.add_parser("finish", help="Clear active task")

    # set-branch
    p_branch = subparsers.add_parser("set-branch", help="Set git branch")
    p_branch.add_argument("dir", help="Task directory")
    p_branch.add_argument("branch", help="Branch name")

    # set-base-branch
    p_base = subparsers.add_parser("set-base-branch", help="Set PR target branch")
    p_base.add_argument("dir", help="Task directory")
    p_base.add_argument("base_branch", help="Base branch name (PR target)")

    # set-scope
    p_scope = subparsers.add_parser("set-scope", help="Set scope")
    p_scope.add_argument("dir", help="Task directory")
    p_scope.add_argument("scope", help="Scope name")

    # archive
    p_archive = subparsers.add_parser("archive", help="Archive task")
    p_archive.add_argument("name", help="Task directory or name")
    p_archive.add_argument("--no-commit", action="store_true", help="Skip auto git commit after archive")
    p_archive.add_argument("--force-archive", action="store_true", help="Bypass done gate with audit reason")
    p_archive.add_argument("--reason", default="", help="Required with --force-archive")

    p_archive_recover = subparsers.add_parser(
        "archive-recover",
        help="Recover an incomplete archive transaction",
    )
    p_archive_recover.add_argument("transaction_id", help="Exact archive transaction ID")

    p_archive_orphans = subparsers.add_parser(
        "archive-orphans",
        help="Atomically sweep terminal children whose Current Trellis parents are archived",
    )
    p_archive_orphans.add_argument(
        "--check",
        action="store_true",
        help="Report eligible families, moves, and blockers without mutation",
    )
    p_archive_orphans.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip auto git commit after archive",
    )

    # authorize-replacement / cancel
    p_replacement = subparsers.add_parser(
        "authorize-replacement",
        help="Authorize one exact Current Trellis child replacement",
    )
    p_replacement.add_argument("name", help="Predecessor child directory or name")
    p_replacement.add_argument("--reason", required=True, help="Attributable replacement reason")
    p_replacement.add_argument("--authorized-by", required=True, help="User who authorized replacement")
    p_replacement.add_argument("--authorization-ref", required=True, help="Exact direct authorization reference")
    p_replacement.add_argument("--delivery-slot", required=True, help="Frozen parent delivery slot")
    p_replacement.add_argument("--superseded-by", required=True, help="Exact successor child")
    p_replacement.add_argument("--rtm-id", action="append", default=[], required=True,
                               help="Exact parent RTM requirement ID; repeat for multiple rows")
    p_replacement.add_argument("--evidence-commit", required=True,
                               help="Full reachable predecessor evidence commit")
    p_replacement.add_argument("--evidence-digest", required=True,
                               help="SHA-256 digest of the predecessor task tree at the evidence commit")

    p_historical = subparsers.add_parser(
        "reconcile-historical-replacement",
        help="Record one post-hoc pre-implementation replacement settlement",
    )
    p_historical.add_argument("name", help="Historical predecessor child")
    p_historical.add_argument("--parent", required=True, help="Exact promotion parent")
    p_historical.add_argument("--successor", required=True, help="Exact completed successor")
    p_historical.add_argument("--rtm-id", action="append", default=[], required=True,
                              help="Exact parent RTM requirement ID")
    p_historical.add_argument("--evidence-commit", required=True,
                              help="Full reachable predecessor evidence commit")
    p_historical.add_argument("--bundle-digest", required=True,
                              help="Immutable closeout bundle SHA-256")
    p_historical.add_argument("--settlement-implementation", required=True,
                              help="Accepted generic settlement implementation commit")
    p_historical.add_argument("--reason", required=True,
                              help="Attributable historical terminal reason")
    p_historical.add_argument("--authorized-by", required=True,
                              help="Direct user authorizing reconciliation")
    p_historical.add_argument("--authorization-ref", required=True,
                              help="Exact post-hoc direct authorization reference")

    p_cancel = subparsers.add_parser("cancel", help="Record an authorized terminal cancellation")
    p_cancel.add_argument("name", help="Task directory or name")
    p_cancel.add_argument("--reason", required=True, help="Non-empty cancellation reason")
    p_cancel.add_argument("--authorized-by", required=True, help="User who explicitly authorized cancellation")
    p_cancel.add_argument("--superseded-by", help="Optional superseding task or product reference")
    p_cancel.add_argument("--rtm-disposition", choices=["removed", "deferred"],
                          help="Required for linked child cancellation")
    p_cancel.add_argument("--rtm-id", action="append", default=[],
                          help="Exact parent RTM requirement ID; repeat for multiple rows")
    p_cancel.add_argument("--rtm-fulfilled-by", help="Exact completed successor fulfilling the RTM rows")
    p_cancel.add_argument("--delivery-slot", help="Frozen delivery slot from replacement authorization")
    p_cancel.add_argument("--replacement-id", help="Stable replacement authorization identity")
    p_cancel.add_argument("--evidence-commit", help="Full predecessor evidence commit")
    p_cancel.add_argument("--evidence-digest", help="Bound predecessor evidence digest")
    p_cancel.add_argument("--authorization-ref", help="Original replacement authorization reference")

    # complete-child / legacy soft-archive alias
    p_complete = subparsers.add_parser(
        "complete-child",
        help="Complete a Current Trellis child task",
    )
    p_complete.add_argument("name", help="Task directory or name")
    p_complete.add_argument("--commit", required=True, help="Commit hash to record")
    p_complete.add_argument("--force-archive", action="store_true", help="Bypass done gate with audit reason")
    p_complete.add_argument("--reason", default="", help="Required with --force-archive")

    p_soft = subparsers.add_parser(
        "soft-archive",
        help="Compatibility alias for historical child tasks",
    )
    p_soft.add_argument("name", help="Task directory or name")
    p_soft.add_argument("--commit", required=True, help="Commit hash to record")
    p_soft.add_argument("--force-archive", action="store_true", help="Bypass done gate with audit reason")
    p_soft.add_argument("--reason", default="", help="Required with --force-archive")

    # claim
    p_claim = subparsers.add_parser("claim", help="Claim task ownership")
    p_claim.add_argument("name", help="Task directory or name")
    p_claim.add_argument("--owner", choices=["cc", "codex", "jym"], required=True, help="New task owner")
    p_claim.add_argument("--override-claim", action="store_true", help="Record an override claim event")
    p_claim.add_argument("--reason", default="", help="Required with --override-claim")

    # release
    p_release = subparsers.add_parser("release", help="Release task ownership")
    p_release.add_argument("name", help="Task directory or name")
    p_release.add_argument("--owner", choices=["cc", "codex", "jym"], default="jym", help="Owner after release")
    p_release.add_argument("--reason", default="", help="Release reason")

    # list
    p_list = subparsers.add_parser("list", help="List tasks")
    p_list.add_argument("--mine", "-m", action="store_true", help="My tasks only")
    p_list.add_argument("--status", "-s", help="Filter by status")

    # add-subtask
    p_addsub = subparsers.add_parser("add-subtask", help="Link child task to parent")
    p_addsub.add_argument("parent_dir", help="Parent task directory")
    p_addsub.add_argument("child_dir", help="Child task directory")

    # remove-subtask
    p_rmsub = subparsers.add_parser("remove-subtask", help="Unlink child task from parent")
    p_rmsub.add_argument("parent_dir", help="Parent task directory")
    p_rmsub.add_argument("child_dir", help="Child task directory")

    # list-archive
    p_listarch = subparsers.add_parser("list-archive", help="List archived tasks")
    p_listarch.add_argument("month", nargs="?", help="Month (YYYY-MM)")

    args = parser.parse_args()

    if not args.command:
        show_usage()
        return 1

    commands = {
        "create": cmd_create,
        "add-context": cmd_add_context,
        "validate": cmd_validate,
        "list-context": cmd_list_context,
        "start": cmd_start,
        "current": cmd_current,
        "finish": cmd_finish,
        "set-branch": cmd_set_branch,
        "set-base-branch": cmd_set_base_branch,
        "set-scope": cmd_set_scope,
        "authorize-replacement": cmd_authorize_replacement,
        "reconcile-historical-replacement": cmd_reconcile_historical_replacement,
        "cancel": cmd_cancel,
        "archive": cmd_archive,
        "archive-orphans": cmd_archive_orphans,
        "archive-recover": cmd_archive_recover,
        "complete-child": cmd_complete_child,
        "soft-archive": cmd_soft_archive,
        "claim": cmd_claim,
        "release": cmd_release,
        "add-subtask": cmd_add_subtask,
        "remove-subtask": cmd_remove_subtask,
        "list": cmd_list,
        "list-archive": cmd_list_archive,
    }

    if args.command in commands:
        return_code = commands[args.command](args)
        refresh_board_after(args.command, return_code)
        return return_code
    else:
        show_usage()
        return 1


if __name__ == "__main__":
    sys.exit(main())
