#!/usr/bin/env python3
"""
Task JSONL context management.

Provides:
    cmd_add_context   - Add entry to JSONL context file
    cmd_validate      - Validate JSONL context files
    cmd_list_context  - List JSONL context entries

Note:
    ``cmd_init_context`` was removed in v0.5.0-beta.12. JSONL context files
    are now seeded at ``task.py create`` time with a self-describing
    ``_example`` line; the AI agent curates real entries during planning when
    the task needs sub-agent/spec context. See ``.trellis/workflow.md`` for the
    current planning artifact contract.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .io import read_json
from .log import Colors, colored
from .paths import FILE_TASK_JSON, get_repo_root, get_tasks_dir
from .task_activity import (
    TaskStateInvalid,
    classify_task_activity,
    taskrun_authority_is_absent,
    validate_taskrun_terminal_proof,
)
from .task_utils import resolve_task_dir
from .done_gate import _find_task_anywhere, cancellation_gate_errors, done_gate_errors

V2_TIERS = {"parent", "child", "light"}
HARNESS_MODE = "harness_state_machine"
LOOP_V1_MODE = "loop_v1"
TASKRUN_MODES = {"taskrun_v1", "taskrun_v2"}


# =============================================================================
# Command: add-context
# =============================================================================

def cmd_add_context(args: argparse.Namespace) -> int:
    """Add entry to JSONL context file."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)

    jsonl_name = args.file
    path = args.path
    reason = args.reason or "Added manually"

    if not target_dir.is_dir():
        print(colored(f"Error: Directory not found: {target_dir}", Colors.RED))
        return 1

    # Support shorthand
    if not jsonl_name.endswith(".jsonl"):
        jsonl_name = f"{jsonl_name}.jsonl"

    jsonl_file = target_dir / jsonl_name
    full_path = repo_root / path

    entry_type = "file"
    if full_path.is_dir():
        entry_type = "directory"
        if not path.endswith("/"):
            path = f"{path}/"
    elif not full_path.is_file():
        print(colored(f"Error: Path not found: {path}", Colors.RED))
        return 1

    # Check if already exists
    if jsonl_file.is_file():
        content = jsonl_file.read_text(encoding="utf-8")
        if f'"{path}"' in content:
            print(colored(f"Warning: Entry already exists for {path}", Colors.YELLOW))
            return 0

    # Add entry
    entry: dict
    if entry_type == "directory":
        entry = {"file": path, "type": "directory", "reason": reason}
    else:
        entry = {"file": path, "reason": reason}

    with jsonl_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(colored(f"Added {entry_type}: {path}", Colors.GREEN))
    return 0


# =============================================================================
# Command: validate
# =============================================================================

def cmd_validate(args: argparse.Namespace) -> int:
    """Validate JSONL context files."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)

    if not target_dir.is_dir():
        print(colored("Error: task directory required", Colors.RED))
        return 1

    print(colored("=== Validating Context Files ===", Colors.BLUE))
    print(f"Target dir: {target_dir}")
    print()

    total_errors = 0
    for jsonl_name in ["implement.jsonl", "check.jsonl"]:
        jsonl_file = target_dir / jsonl_name
        errors = _validate_jsonl(jsonl_file, repo_root)
        total_errors += errors

    total_errors += _validate_v2_task(target_dir, repo_root)

    print()
    if total_errors == 0:
        print(colored("✓ All validations passed", Colors.GREEN))
        return 0
    else:
        print(colored(f"✗ Validation failed ({total_errors} errors)", Colors.RED))
        return 1


def _validate_jsonl(jsonl_file: Path, repo_root: Path) -> int:
    """Validate a single JSONL file.

    Seed rows (no ``file`` field — typically ``{"_example": "..."}``) are
    skipped silently; they are self-describing comments, not real entries.
    """
    file_name = jsonl_file.name
    errors = 0

    if not jsonl_file.is_file():
        print(f"  {colored(f'{file_name}: not found (skipped)', Colors.YELLOW)}")
        return 0

    line_num = 0
    real_entries = 0
    for line in jsonl_file.read_text(encoding="utf-8").splitlines():
        line_num += 1
        if not line.strip():
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            print(f"  {colored(f'{file_name}:{line_num}: Invalid JSON', Colors.RED)}")
            errors += 1
            continue

        file_path = data.get("file")
        entry_type = data.get("type", "file")

        if not file_path:
            # Seed / comment row — skip silently
            continue

        real_entries += 1
        full_path = repo_root / file_path
        if entry_type == "directory":
            if not full_path.is_dir():
                print(f"  {colored(f'{file_name}:{line_num}: Directory not found: {file_path}', Colors.RED)}")
                errors += 1
        else:
            if not full_path.is_file():
                print(f"  {colored(f'{file_name}:{line_num}: File not found: {file_path}', Colors.RED)}")
                errors += 1

    if errors == 0:
        print(f"  {colored(f'{file_name}: ✓ ({real_entries} entries)', Colors.GREEN)}")
    else:
        print(f"  {colored(f'{file_name}: ✗ ({errors} errors)', Colors.RED)}")

    return errors


def _section_body(text: str, header: str) -> str:
    marker = f"{header}\n"
    start = text.find(marker)
    if start == -1:
        return ""
    body_start = start + len(marker)
    next_header = text.find("\n## ", body_start)
    if next_header == -1:
        return text[body_start:].strip()
    return text[body_start:next_header].strip()


def _subsection_body(text: str, header_prefix: str) -> str:
    match = re.search(rf"^### {re.escape(header_prefix)}.*$", text, re.MULTILINE)
    if not match:
        return ""
    body_start = match.end() + 1
    next_header = re.search(r"^##{2,3} ", text[body_start:], re.MULTILINE)
    if next_header:
        return text[body_start:body_start + next_header.start()].strip()
    return text[body_start:].strip()


def _meaningful(body: str) -> bool:
    lines = [line.strip().lower() for line in body.splitlines() if line.strip()]
    placeholders = {"tbd", "todo", "pending implementation.", "- [ ] tbd"}
    return any(line not in placeholders for line in lines)


def _loop_v1_validation_errors(
    data: dict, repo_root: Path, task_dir: Path
) -> list[str]:
    """Validate the generic task record without taking over Loop authority."""
    errors: list[str] = []
    tier = data.get("tier")
    meta = data.get("meta") or {}

    if tier not in {"parent", "child"}:
        errors.append("Loop v1 workflow_mode is only valid for parent or child tasks")
    if isinstance(meta, dict) and "state_machine" in meta:
        errors.append("Loop v1 task must not declare HSM state-machine metadata")

    if data.get("status") == "cancelled":
        from loop_v1.pre_admission import pre_admission_cancellation_errors

        errors.extend(pre_admission_cancellation_errors(task_dir, data, repo_root))
        return errors

    if tier == "parent":
        try:
            from loop_v1.qualification import operation_qualification

            qualification = operation_qualification(repo_root)
        except Exception as exc:
            errors.append(f"Loop v1 qualification check failed: {exc}")
        else:
            if not qualification.enforced or not qualification.valid:
                detail = "; ".join(qualification.issues) or "qualification is unenforced"
                errors.append(f"Loop v1 runtime is not qualified: {detail}")

    return errors


def _taskrun_validation_errors(
    data: dict, repo_root: Path, task_dir: Path
) -> list[str]:
    """Validate one TaskRun planning record or exact SQLite projection."""
    errors: list[str] = []
    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        return ["TaskRun task.json meta must be an object"]
    if "state_machine" in meta:
        errors.append("TaskRun task must not declare HSM state-machine metadata")
    if (task_dir / "state-events.jsonl").exists():
        errors.append("TaskRun task must not contain an HSM state-event stream")
    strategy = meta.get("taskrun_strategy")
    if strategy not in {"single", "loop"}:
        errors.append("TaskRun taskrun_strategy must be single or loop")
    projection = meta.get("task_run")
    if projection is None:
        if data.get("status") == "cancelled":
            try:
                classify_task_activity(task_dir, repo_root)
            except TaskStateInvalid as exc:
                errors.append(str(exc))
        elif data.get("status") != "planning":
            errors.append("unadmitted TaskRun task must remain planning or be cancelled")
        return errors
    if (
        not isinstance(projection, dict)
        or projection.get("authority") != "sqlite"
        or projection.get("projection") is not True
        or not isinstance(projection.get("id"), str)
    ):
        return errors + ["TaskRun projection binding is invalid"]
    try:
        from taskrun import TaskRun, taskrun_path

        if taskrun_authority_is_absent(taskrun_path(repo_root, projection["id"])):
            validate_taskrun_terminal_proof(task_dir, repo_root, data, projection)
            return errors
        run = TaskRun.open(repo_root, projection["id"])
        expected = run.task_projection_bytes()
        snapshot = run.snapshot()
    except Exception as exc:
        errors.append(f"TaskRun authority validation failed: {exc}")
        return errors
    if expected != (task_dir / FILE_TASK_JSON).read_bytes():
        errors.append("TaskRun task projection does not match SQLite authority")
    execution = snapshot.get("execution") or {}
    config = execution.get("config") if isinstance(execution, dict) else None
    if not isinstance(config, dict) or config.get("strategy") != strategy:
        errors.append("TaskRun strategy projection conflicts with SQLite authority")
    return errors


def _validate_v2_task(target_dir: Path, repo_root: Path) -> int:
    task_json = target_dir / FILE_TASK_JSON
    data = read_json(task_json)
    if not data:
        return 0
    tier = data.get("tier")
    if tier not in V2_TIERS:
        return 0

    errors: list[str] = []
    try:
        classify_task_activity(target_dir, repo_root)
    except TaskStateInvalid as exc:
        errors.append(str(exc))
    meta = data.get("meta") or {}
    workflow_mode = meta.get("workflow_mode") if isinstance(meta, dict) else None
    if workflow_mode == LOOP_V1_MODE:
        errors.extend(_loop_v1_validation_errors(data, repo_root, target_dir))
    elif workflow_mode in TASKRUN_MODES:
        errors.extend(_taskrun_validation_errors(data, repo_root, target_dir))
    elif workflow_mode != HARNESS_MODE:
        errors.append(
            "task.json meta.workflow_mode must be harness_state_machine, loop_v1, taskrun_v1, or taskrun_v2"
        )

    if data.get("status") == "cancelled":
        if workflow_mode == HARNESS_MODE:
            for error in cancellation_gate_errors(target_dir, data, repo_root):
                if error not in errors:
                    errors.append(error)
        elif workflow_mode == LOOP_V1_MODE:
            cancellation = data.get("cancellation")
            if not isinstance(cancellation, dict) or cancellation.get("pre_admission") is not True:
                errors.append("Loop v1 cancellation validation is controlled by loop_v1.orchestrator")
        if errors:
            print(f"  {colored('task metadata: ✗', Colors.RED)}")
            for error in errors:
                print(f"    - {error}")
        else:
            print(f"  {colored('task metadata: ✓', Colors.GREEN)}")
        return len(errors)

    if workflow_mode in TASKRUN_MODES:
        if isinstance(meta, dict) and meta.get("task_run") is None:
            prd_path = target_dir / "prd.md"
            if not prd_path.is_file() or prd_path.is_symlink():
                errors.append("task.json and prd.md must be regular files")
            else:
                from taskrun.operator import OperatorError, task_prd_preflight

                try:
                    task_prd_preflight(
                        repo_root,
                        data,
                        prd_path.read_text(encoding="utf-8"),
                    )
                except (OperatorError, UnicodeDecodeError) as exc:
                    errors.append(str(exc))
        if errors:
            print(f"  {colored('task metadata: ✗', Colors.RED)}")
            for error in errors:
                print(f"    - {error}")
        else:
            print(f"  {colored('task metadata: ✓', Colors.GREEN)}")
        return len(errors)

    if tier == "parent":
        governance = target_dir / "governance.md"
        if not governance.is_file():
            errors.append("parent governance.md missing")
        else:
            text = governance.read_text(encoding="utf-8")
            for header in ("## Child Index", "## RTM", "## External Review", "## Boundary Pass"):
                if header not in text:
                    errors.append(f"parent governance.md missing {header}")
            if not _meaningful(_section_body(text, "## Boundary Pass")):
                errors.append("parent Boundary Pass is empty")
            if not _meaningful(_subsection_body(text, "PRD Review")):
                errors.append("parent External Review / PRD Review is empty")

    elif tier == "child":
        prd = (target_dir / "prd.md").read_text(encoding="utf-8") if (target_dir / "prd.md").is_file() else ""
        if not re.search(r"\b[A-Z0-9]+-[A-Z0-9-]+-\d{3}\b", prd):
            errors.append("child prd.md missing REQ-ID")
        if "Verification Commands" not in prd and "验证命令" not in prd:
            errors.append("child prd.md missing verification command section")
        if not data.get("owner"):
            errors.append("child task.json missing owner")
        if not (target_dir / "stage-report.md").is_file():
            errors.append("child stage-report.md missing")
        parent_name = data.get("parent")
        parent_dir = _find_task_anywhere(parent_name, get_tasks_dir(repo_root)) if parent_name else None
        parent_json = parent_dir / FILE_TASK_JSON if parent_dir else None
        parent = read_json(parent_json) if parent_json and parent_json.is_file() else None
        if not parent or target_dir.name not in (parent.get("children") or []):
            errors.append("child/parent bidirectional link is missing")

    else:
        prd = (target_dir / "prd.md").read_text(encoding="utf-8") if (target_dir / "prd.md").is_file() else ""
        for header in ("## What will change?", "## Why now?", "## How will it be verified?"):
            if not _meaningful(_section_body(prd, header)):
                errors.append(f"light prd.md missing answer: {header}")

    if workflow_mode == HARNESS_MODE:
        for error in done_gate_errors(target_dir, data, repo_root):
            if error not in errors:
                errors.append(error)

    if errors:
        print(f"  {colored('task metadata: ✗', Colors.RED)}")
        for error in errors:
            print(f"    - {error}")
    else:
        print(f"  {colored('task metadata: ✓', Colors.GREEN)}")
    return len(errors)


# =============================================================================
# Command: list-context
# =============================================================================

def cmd_list_context(args: argparse.Namespace) -> int:
    """List JSONL context entries."""
    repo_root = get_repo_root()
    target_dir = resolve_task_dir(args.dir, repo_root)

    if not target_dir.is_dir():
        print(colored("Error: task directory required", Colors.RED))
        return 1

    print(colored("=== Context Files ===", Colors.BLUE))
    print()

    for jsonl_name in ["implement.jsonl", "check.jsonl"]:
        jsonl_file = target_dir / jsonl_name
        if not jsonl_file.is_file():
            continue

        print(colored(f"[{jsonl_name}]", Colors.CYAN))

        count = 0
        seed_only = True
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            file_path = data.get("file")
            if not file_path:
                # Seed / comment row — don't count as a real entry
                continue
            seed_only = False

            count += 1
            entry_type = data.get("type", "file")
            reason = data.get("reason", "-")

            if entry_type == "directory":
                print(f"  {colored(f'{count}.', Colors.GREEN)} [DIR] {file_path}")
            else:
                print(f"  {colored(f'{count}.', Colors.GREEN)} {file_path}")
            print(f"     {colored('→', Colors.YELLOW)} {reason}")

        if seed_only:
            print(f"  {colored('(no curated entries yet — only seed row)', Colors.YELLOW)}")

        print()

    return 0
