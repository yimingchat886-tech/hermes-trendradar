"""Shared Trellis task done-gate checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import read_json
from .paths import FILE_TASK_JSON, get_tasks_dir
from .task_utils import find_task_by_name

V2_TIERS = {"parent", "child", "light"}


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


def _subsection_body(text: str, header: str) -> str:
    marker = f"{header}"
    start = text.find(marker)
    if start == -1:
        return ""
    line_end = text.find("\n", start)
    if line_end == -1:
        return ""
    body_start = line_end + 1
    next_header = text.find("\n### ", body_start)
    next_section = text.find("\n## ", body_start)
    candidates = [i for i in (next_header, next_section) if i != -1]
    end = min(candidates) if candidates else len(text)
    return text[body_start:end].strip()


def _meaningful(body: str) -> bool:
    stripped = body.strip()
    if not stripped:
        return False
    placeholders = {"tbd", "todo", "pending implementation.", "- [ ] tbd"}
    lines = [line.strip().lower() for line in stripped.splitlines() if line.strip()]
    return any(line not in placeholders for line in lines)


def done_gate_errors(task_dir: Path, data: dict[str, Any], repo_root: Path) -> list[str]:
    tier = data.get("tier")
    if tier not in V2_TIERS:
        return []

    errors: list[str] = []
    stage_report = task_dir / "stage-report.md"
    if tier in {"child", "light"}:
        if not stage_report.is_file():
            errors.append("missing stage-report.md")
        else:
            acceptance = _section_body(stage_report.read_text(encoding="utf-8"), "## Acceptance")
            if not _meaningful(acceptance):
                errors.append("stage-report.md ## Acceptance is empty or still template text")

    tasks_dir = get_tasks_dir(repo_root)
    if tier == "child":
        parent_name = data.get("parent")
        parent_dir = find_task_by_name(parent_name, tasks_dir) if parent_name else None
        if not parent_dir:
            errors.append("child parent is missing")
        else:
            governance = parent_dir / "governance.md"
            if not governance.is_file():
                errors.append("parent governance.md is missing")
            else:
                review = _subsection_body(governance.read_text(encoding="utf-8"), "### PRD Review")
                if not _meaningful(review):
                    errors.append("parent External Review / PRD Review is empty")

    if tier == "parent":
        children = data.get("children") or []
        for child in children:
            child_dir = find_task_by_name(child, tasks_dir)
            child_json = child_dir / FILE_TASK_JSON if child_dir else None
            child_data = read_json(child_json) if child_json and child_json.is_file() else None
            status = (child_data or {}).get("status")
            if status not in {"completed", "cancelled"}:
                errors.append(f"child not completed/cancelled: {child}")

        governance = task_dir / "governance.md"
        if governance.is_file():
            rtm = _section_body(governance.read_text(encoding="utf-8"), "## RTM").lower()
            if "| planned |" in rtm:
                errors.append("RTM still contains planned rows")
        else:
            errors.append("governance.md is missing")

    return errors
