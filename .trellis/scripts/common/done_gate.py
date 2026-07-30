"""Shared Trellis task done-gate checks."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import read_json
from .paths import FILE_TASK_JSON, get_tasks_dir
from .task_utils import find_archived_task_by_name, find_task_by_name

V2_TIERS = {"parent", "child", "light"}
TERMINAL_RTM_STATUSES = {"completed", "done", "removed", "deferred"}


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


def _table(text: str, section: str) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in _section_body(text, section).splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    header = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines[2:]
        if line.strip(" |-")
    ]
    return header, rows


def _column(header: list[str], name: str) -> int | None:
    wanted = name.lower()
    for index, value in enumerate(header):
        if value.lower() == wanted:
            return index
    return None


def _find_task_anywhere(task_name: str, tasks_dir: Path) -> Path | None:
    active = find_task_by_name(task_name, tasks_dir)
    if active:
        return active
    return find_archived_task_by_name(task_name, tasks_dir, require_unique=True)


def cancellation_readiness_errors(
    task_dir: Path,
    data: dict[str, Any],
    repo_root: Path,
) -> list[str]:
    """Check whether child and RTM truth permits a parent cancellation."""

    if data.get("tier") != "parent":
        return []

    errors: list[str] = []
    tasks_dir = get_tasks_dir(repo_root)
    children = data.get("children") or []
    for child in children:
        child_dir = _find_task_anywhere(child, tasks_dir)
        child_json = child_dir / FILE_TASK_JSON if child_dir else None
        child_data = read_json(child_json) if child_json and child_json.is_file() else None
        status = (child_data or {}).get("status")
        if status not in {"completed", "cancelled"}:
            errors.append(f"child lacks terminal completion/cancellation disposition: {child}")
        elif status == "cancelled":
            child_errors = cancellation_gate_errors(child_dir, child_data, repo_root)
            if child_errors:
                errors.append(f"child cancellation evidence is invalid: {child}")

    governance = task_dir / "governance.md"
    if not governance.is_file():
        errors.append("governance.md is missing")
        return errors

    text = governance.read_text(encoding="utf-8")
    rtm_header, rtm_rows = _table(text, "## RTM")
    status_index = _column(rtm_header, "Status")
    if status_index is None:
        errors.append("RTM status column is missing")
    else:
        for row in rtm_rows:
            if len(row) <= status_index:
                errors.append("RTM row is missing a status")
                continue
            if row[status_index].lower() not in TERMINAL_RTM_STATUSES:
                errors.append(f"RTM row lacks terminal disposition: {row[0]}")

    child_header, child_rows = _table(text, "## Child Index")
    child_status_index = _column(child_header, "Status")
    if children and child_status_index is None:
        errors.append("Child Index status column is missing")
    elif child_status_index is not None:
        for child in children:
            matches = [row for row in child_rows if row and row[0].strip("`") == child]
            if len(matches) != 1:
                errors.append(f"Child Index row not found exactly once: {child}")
                continue
            row = matches[0]
            status = row[child_status_index].lower() if len(row) > child_status_index else ""
            if status not in {"completed", "cancelled"}:
                errors.append(f"Child Index row lacks terminal disposition: {child}")
    return errors


def cancellation_gate_errors(task_dir: Path, data: dict[str, Any], repo_root: Path) -> list[str]:
    """Validate durable cancellation evidence without completion requirements."""

    errors: list[str] = []
    if data.get("status") != "cancelled":
        errors.append("task status is not cancelled")
        return errors

    cancellation = data.get("cancellation")
    if not isinstance(cancellation, dict):
        errors.append("task cancellation metadata is missing")
        return errors

    for field in ("reason", "authorized_by", "authorized_at"):
        if not str(cancellation.get(field) or "").strip():
            errors.append(f"cancellation metadata missing {field}")
    authorized_at = str(cancellation.get("authorized_at") or "")
    try:
        parsed_at = datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            raise ValueError
    except ValueError:
        errors.append("cancellation authorized_at is not a timezone-aware ISO timestamp")
    if data.get("cancelledAt") != authorized_at[:10]:
        errors.append("cancelledAt does not match cancellation authorized_at")

    tier = data.get("tier")
    machine = ((data.get("meta") or {}).get("state_machine") or {})
    if tier in {"parent", "child"}:
        expected = f"{tier}_cancelled"
        if machine.get("current_state") != expected or machine.get("last_event") != "task_cancelled":
            errors.append(f"state machine is not at terminal {expected}")

    events_path = task_dir / "state-events.jsonl"
    events: list[dict[str, Any]] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors.append("state-events.jsonl contains invalid JSON")
                continue
            if event.get("event") == "task_cancelled":
                events.append(event)
    if len(events) != 1:
        errors.append("exactly one task_cancelled event is required")
    else:
        event = events[0]
        for field in ("reason", "authorized_by", "superseded_by", "rtm_disposition", "rtm_ids"):
            if event.get(field) != cancellation.get(field) and not (
                event.get(field) in (None, "", []) and cancellation.get(field) in (None, "", [])
            ):
                errors.append(f"task_cancelled event conflicts on {field}")
        if event.get("by") != cancellation.get("authorized_by"):
            errors.append("task_cancelled event actor does not match authorization")
        if event.get("created_at") != cancellation.get("authorized_at"):
            errors.append("task_cancelled event time does not match authorization")

    preserved = cancellation.get("preserved")
    if not isinstance(preserved, dict):
        errors.append("cancellation preserved task snapshot is missing")
    else:
        for field in ("branch", "worktree_path", "commit", "children", "parent"):
            if data.get(field) != preserved.get(field):
                errors.append(f"cancelled task changed preserved field: {field}")

    retained_files = cancellation.get("retained_files")
    if not isinstance(retained_files, list):
        errors.append("cancellation retained_files snapshot is missing")
    else:
        for raw_path in retained_files:
            path = Path(str(raw_path))
            if path.is_absolute() or ".." in path.parts or not (task_dir / path).is_file():
                errors.append(f"retained evidence file is missing or invalid: {raw_path}")

    errors.extend(cancellation_readiness_errors(task_dir, data, repo_root))

    if tier == "child":
        parent_name = data.get("parent")
        parent_dir = _find_task_anywhere(parent_name, get_tasks_dir(repo_root)) if parent_name else None
        governance = parent_dir / "governance.md" if parent_dir else None
        if not governance or not governance.is_file():
            errors.append("cancelled child parent governance is missing")
        else:
            text = governance.read_text(encoding="utf-8")
            child_header, child_rows = _table(text, "## Child Index")
            child_status_index = _column(child_header, "Status")
            matches = [row for row in child_rows if row and row[0].strip("`") == task_dir.name]
            if len(matches) != 1 or child_status_index is None:
                errors.append("cancelled child Child Index row is missing")
            elif len(matches[0]) <= child_status_index or matches[0][child_status_index].lower() != "cancelled":
                errors.append("cancelled child Child Index row is not cancelled")

            rtm_header, rtm_rows = _table(text, "## RTM")
            status_index = _column(rtm_header, "Status")
            evidence_index = _column(rtm_header, "Evidence")
            by_id = {row[0].strip("`"): row for row in rtm_rows if row}
            expected_status = cancellation.get("rtm_disposition")
            evidence = cancellation.get("rtm_evidence")
            for rtm_id in cancellation.get("rtm_ids") or []:
                row = by_id.get(rtm_id)
                if not row:
                    errors.append(f"cancelled child RTM row is missing: {rtm_id}")
                    continue
                if status_index is None or len(row) <= status_index or row[status_index].lower() != expected_status:
                    errors.append(f"cancelled child RTM disposition mismatch: {rtm_id}")
                if evidence_index is None or len(row) <= evidence_index or str(evidence) not in row[evidence_index]:
                    errors.append(f"cancelled child RTM evidence mismatch: {rtm_id}")
    return errors


def done_gate_errors(task_dir: Path, data: dict[str, Any], repo_root: Path) -> list[str]:
    tier = data.get("tier")
    if tier not in V2_TIERS:
        return []
    if data.get("status") == "cancelled":
        return cancellation_gate_errors(task_dir, data, repo_root)

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
        parent_dir = _find_task_anywhere(parent_name, tasks_dir) if parent_name else None
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
            child_dir = _find_task_anywhere(child, tasks_dir)
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
