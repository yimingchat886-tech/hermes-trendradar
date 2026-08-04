"""Shared Trellis task done-gate checks."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .git import run_git
from .io import read_json
from .paths import FILE_TASK_JSON, get_tasks_dir
from .task_utils import find_archived_task_by_name, find_task_by_name

V2_TIERS = {"parent", "child", "light"}
TERMINAL_RTM_STATUSES = {"completed", "done", "removed", "deferred"}
HISTORICAL_SETTLEMENT_IMPLEMENTATION = (
    "e5abf7bb9d214b35781c9cfd959c455f974b17c0"
)


def replacement_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def normalize_sha256(value: str) -> str:
    raw = value.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", raw):
        raise ValueError("evidence digest must be one lowercase SHA-256 digest")
    return f"sha256:{raw}"


def canonical_reachable_commit(repo_root: Path, value: str, field: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"{field} must be one full lowercase Git commit")
    code, stdout, stderr = run_git(
        ["rev-parse", "--verify", f"{value}^{{commit}}"],
        cwd=repo_root,
    )
    if code != 0 or stdout.strip() != value:
        raise ValueError(
            f"{field} is not a canonical commit: {stderr.strip() or stdout.strip()}"
        )
    code, _, _ = run_git(["merge-base", "--is-ancestor", value, "HEAD"], cwd=repo_root)
    if code != 0:
        raise ValueError(f"{field} is not reachable from HEAD")
    return value


def replacement_evidence_digest(repo_root: Path, task_dir: Path, commit: str) -> str:
    relative = task_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    code, stdout, stderr = run_git(
        ["ls-tree", "-r", "--full-tree", "-z", commit, "--", relative],
        cwd=repo_root,
    )
    if code != 0:
        raise ValueError(f"cannot read predecessor evidence tree: {stderr.strip()}")
    entries: list[dict[str, str]] = []
    for record in stdout.split("\0"):
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, object_type, object_id = header.split()
        if object_type != "blob":
            raise ValueError(
                f"predecessor evidence contains unsupported Git object: {path}"
            )
        entries.append({"mode": mode, "object": object_id, "path": path})
    if not entries:
        raise ValueError("predecessor evidence commit contains no task files")
    return replacement_digest(entries)


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=repo_root,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"historical evidence blob is unavailable: {path}")
    return result.stdout


def historical_bundle_digest(repo_root: Path, task_dir: Path, commit: str) -> str:
    """Recompute the committed immutable pilot bundle and all 23 members."""
    relative = task_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    bundle_path = f"{relative}/pilot-evidence/closeout-bundle.json"
    try:
        bundle = json.loads(_git_blob(repo_root, commit, bundle_path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("historical closeout bundle is invalid") from exc
    if not isinstance(bundle, dict) or set(bundle) != {
        "blocker_id",
        "bundle_digest",
        "digest_algorithm",
        "excluded_paths",
        "files",
        "outcome",
        "review_subject",
        "schema_version",
    }:
        raise ValueError("historical closeout bundle schema is invalid")
    files = bundle["files"]
    if not isinstance(files, list) or len(files) != 23:
        raise ValueError("historical closeout bundle must contain 23 members")
    seen: set[str] = set()
    for member in files:
        if not isinstance(member, dict) or set(member) != {"path", "sha256"}:
            raise ValueError("historical closeout bundle member is invalid")
        path = member["path"]
        digest = member["sha256"]
        if (
            not isinstance(path, str)
            or path in seen
            or not path.startswith(f"{relative}/")
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("historical closeout bundle member conflicts")
        seen.add(path)
        if sha256(_git_blob(repo_root, commit, path)).hexdigest() != digest:
            raise ValueError(f"historical closeout bundle member changed: {path}")
    payload = {
        key: value
        for key, value in bundle.items()
        if key not in {"bundle_digest", "digest_algorithm"}
    }
    digest = sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if bundle["bundle_digest"] != digest:
        raise ValueError("historical closeout bundle digest changed")
    return f"sha256:{digest}"


def terminal_task_evidence_digest(task_dir: Path) -> str:
    entries = []
    for name in (FILE_TASK_JSON, "stage-report.md", "state-events.jsonl"):
        path = task_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"successor terminal evidence is missing: {name}")
        content = path.read_bytes()
        entries.append(
            {
                "digest": sha256(content).hexdigest(),
                "path": name,
            }
        )
    return replacement_digest(entries)


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


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    code, _, _ = run_git(
        ["merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
    )
    return code == 0


def _historical_reconciliation_errors(
    task_dir: Path,
    data: dict[str, Any],
    repo_root: Path,
    events: list[dict[str, Any]],
) -> list[str]:
    cancellation = data["cancellation"]
    binding = cancellation.get("historical_reconciliation")
    if not isinstance(binding, dict) or set(binding) != {"path", "receipt_digest"}:
        return ["historical reconciliation binding is invalid"]
    path = task_dir / str(binding["path"])
    if path.is_symlink() or path.name != "historical-reconciliation.json" or not path.is_file():
        return ["historical reconciliation receipt is missing"]
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["historical reconciliation receipt is invalid"]
    fields = {
        "authorization_postdates_successor_completion",
        "authorization_ref",
        "authorized_at",
        "authorized_by",
        "bundle_digest",
        "disposition",
        "evidence_commit",
        "evidence_tree_digest",
        "parent",
        "predecessor",
        "predecessor_source_commit",
        "receipt_digest",
        "rtm_ids",
        "schema_version",
        "settlement_implementation_commit",
        "successor",
        "successor_commit",
        "successor_completed_at",
        "successor_terminal_digest",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        return ["historical reconciliation receipt schema is invalid"]
    payload = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    expected_digest = replacement_digest(payload)
    errors: list[str] = []
    if receipt["receipt_digest"] != expected_digest or binding["receipt_digest"] != expected_digest:
        errors.append("historical reconciliation receipt digest changed")
    tasks_dir = get_tasks_dir(repo_root)
    parent_dir = _find_task_anywhere(str(receipt.get("parent") or ""), tasks_dir)
    successor_dir = _find_task_anywhere(str(receipt.get("successor") or ""), tasks_dir)
    parent = read_json(parent_dir / FILE_TASK_JSON) if parent_dir else None
    successor = read_json(successor_dir / FILE_TASK_JSON) if successor_dir else None
    if (
        receipt.get("schema_version") != "historical-reconciliation-v1"
        or receipt.get("predecessor") != task_dir.name
        or receipt.get("disposition") != "child_cancelled"
        or receipt.get("rtm_ids") != cancellation.get("rtm_ids")
        or receipt.get("authorized_by") != cancellation.get("authorized_by")
        or receipt.get("authorized_at") != cancellation.get("authorized_at")
        or receipt.get("authorization_ref") != cancellation.get("authorization_ref")
        or receipt.get("successor") != cancellation.get("rtm_fulfilled_by")
        or receipt.get("successor") != cancellation.get("superseded_by")
        or receipt.get("evidence_commit") != cancellation.get("evidence_commit")
        or receipt.get("evidence_tree_digest") != cancellation.get("evidence_digest")
        or receipt.get("successor_commit") != cancellation.get("successor_commit")
        or receipt.get("successor_terminal_digest")
        != cancellation.get("successor_terminal_digest")
        or cancellation.get("rtm_disposition") != "completed"
    ):
        errors.append("historical reconciliation conflicts with cancellation evidence")
    if data.get("replacement_authorization") is not None or any(
        event.get("event") == "replacement_authorized" for event in events
    ):
        errors.append("historical reconciliation must not manufacture replacement authority")
    if not isinstance(parent, dict) or (parent.get("children") or []).count(task_dir.name) != 1:
        errors.append("historical reconciliation parent binding is invalid")
    if (
        not isinstance(successor, dict)
        or successor.get("status") != "completed"
        or successor.get("parent") != receipt.get("parent")
        or successor.get("commit") != receipt.get("successor_commit")
    ):
        errors.append("historical reconciliation successor binding is invalid")
    try:
        evidence_commit = canonical_reachable_commit(
            repo_root,
            str(receipt.get("evidence_commit") or ""),
            "historical evidence commit",
        )
        source_commit = canonical_reachable_commit(
            repo_root,
            str(receipt.get("predecessor_source_commit") or ""),
            "historical predecessor source commit",
        )
        successor_commit = canonical_reachable_commit(
            repo_root,
            str(receipt.get("successor_commit") or ""),
            "historical successor commit",
        )
        cutoff = canonical_reachable_commit(
            repo_root,
            str(receipt.get("settlement_implementation_commit") or ""),
            "historical settlement implementation commit",
        )
        if cutoff != HISTORICAL_SETTLEMENT_IMPLEMENTATION:
            raise ValueError("historical settlement implementation identity changed")
        if not all(
            (
                _is_ancestor(repo_root, source_commit, cutoff),
                _is_ancestor(repo_root, successor_commit, cutoff),
                _is_ancestor(repo_root, cutoff, evidence_commit),
            )
        ):
            raise ValueError("historical reconciliation eligibility boundary failed")
        evidence_task_dir = tasks_dir / str(receipt["predecessor"])
        tree_digest = normalize_sha256(str(receipt.get("evidence_tree_digest") or ""))
        if replacement_evidence_digest(repo_root, evidence_task_dir, evidence_commit) != tree_digest:
            raise ValueError("historical predecessor evidence tree changed")
        bundle_digest = normalize_sha256(str(receipt.get("bundle_digest") or ""))
        if historical_bundle_digest(repo_root, evidence_task_dir, evidence_commit) != bundle_digest:
            raise ValueError("historical predecessor bundle changed")
        successor_digest = normalize_sha256(
            str(receipt.get("successor_terminal_digest") or "")
        )
        if successor_dir is None or terminal_task_evidence_digest(successor_dir) != successor_digest:
            raise ValueError("historical successor terminal evidence changed")
        authorized_at = datetime.fromisoformat(
            str(receipt.get("authorized_at") or "").replace("Z", "+00:00")
        )
        completed_at = datetime.fromisoformat(
            str(receipt.get("successor_completed_at") or "").replace("Z", "+00:00")
        )
        if (
            authorized_at.tzinfo is None
            or completed_at.tzinfo is None
            or authorized_at <= completed_at
            or receipt.get("authorization_postdates_successor_completion") is not True
        ):
            raise ValueError("historical authorization does not postdate successor completion")
    except (OSError, ValueError) as exc:
        errors.append(f"historical reconciliation evidence is invalid: {exc}")
    return errors


def _replacement_cancellation_errors(
    task_dir: Path,
    data: dict[str, Any],
    repo_root: Path,
    events: list[dict[str, Any]],
) -> list[str]:
    cancellation = data["cancellation"]
    if cancellation.get("historical_reconciliation") is not None:
        return _historical_reconciliation_errors(
            task_dir,
            data,
            repo_root,
            events,
        )
    fields = (
        "rtm_fulfilled_by",
        "delivery_slot",
        "replacement_id",
        "evidence_commit",
        "evidence_digest",
        "authorization_ref",
        "successor_commit",
        "successor_terminal_digest",
    )
    if not any(cancellation.get(field) for field in fields):
        return []
    errors: list[str] = []
    if not all(cancellation.get(field) for field in fields):
        return ["successor fulfillment cancellation fields are incomplete"]
    successor_name = str(cancellation["rtm_fulfilled_by"])
    if (
        cancellation.get("superseded_by") != successor_name
        or cancellation.get("rtm_disposition") != "completed"
    ):
        errors.append("successor fulfillment disposition is inconsistent")

    authorization = data.get("replacement_authorization")
    if not isinstance(authorization, dict):
        return [*errors, "replacement authorization is missing"]
    request = {
        "authorization_ref": cancellation.get("authorization_ref"),
        "authorized_by": cancellation.get("authorized_by"),
        "delivery_slot": cancellation.get("delivery_slot"),
        "evidence_commit": cancellation.get("evidence_commit"),
        "evidence_digest": cancellation.get("evidence_digest"),
        "parent": data.get("parent"),
        "predecessor": task_dir.name,
        "reason": cancellation.get("reason"),
        "rtm_ids": cancellation.get("rtm_ids"),
        "scope": data.get("scope"),
        "successor": successor_name,
        "touches": sorted(str(value) for value in data.get("touches") or []),
    }
    slot_contract = {
        "delivery_slot": cancellation.get("delivery_slot"),
        "parent": data.get("parent"),
        "req_ids": cancellation.get("rtm_ids"),
        "scope": data.get("scope"),
        "touches": request["touches"],
    }
    replacement_id = replacement_digest(request)
    slot_digest = replacement_digest(slot_contract)
    expected_authorization = {
        **request,
        "authorized_at": authorization.get("authorized_at"),
        "fulfillment_authorized": True,
        "replacement_id": replacement_id,
        "slot_digest": slot_digest,
    }
    if authorization != expected_authorization:
        errors.append("replacement authorization does not match cancellation evidence")
    if (
        cancellation.get("replacement_id") != replacement_id
        or cancellation.get("slot_digest") != slot_digest
        or cancellation.get("replacement_authorized_at")
        != authorization.get("authorized_at")
    ):
        errors.append("successor fulfillment identity does not match authorization")

    replacement_events = [
        event for event in events if event.get("event") == "replacement_authorized"
    ]
    if len(replacement_events) != 1 or replacement_events[0].get(
        "replacement"
    ) != authorization:
        errors.append("exactly one matching replacement_authorized event is required")

    try:
        commit = canonical_reachable_commit(
            repo_root,
            str(cancellation.get("evidence_commit") or ""),
            "evidence commit",
        )
        digest = normalize_sha256(str(cancellation.get("evidence_digest") or ""))
        if replacement_evidence_digest(repo_root, task_dir, commit) != digest:
            raise ValueError("predecessor evidence digest mismatch")
    except (OSError, ValueError) as exc:
        errors.append(f"replacement evidence is invalid: {exc}")

    tasks_dir = get_tasks_dir(repo_root)
    parent_dir = _find_task_anywhere(str(data.get("parent") or ""), tasks_dir)
    successor_dir = _find_task_anywhere(successor_name, tasks_dir)
    parent = read_json(parent_dir / FILE_TASK_JSON) if parent_dir else None
    successor = read_json(successor_dir / FILE_TASK_JSON) if successor_dir else None
    if not parent_dir or not isinstance(parent, dict):
        errors.append("replacement parent is missing")
    elif (
        parent.get("tier") != "parent"
        or (parent.get("children") or []).count(task_dir.name) != 1
        or (parent.get("children") or []).count(successor_name) != 1
    ):
        errors.append("replacement parent links are invalid")
    if not successor_dir or not isinstance(successor, dict):
        errors.append("replacement successor is missing")
        return errors
    successor_machine = ((successor.get("meta") or {}).get("state_machine") or {})
    if (
        successor.get("status") != "completed"
        or successor.get("tier") != "child"
        or successor.get("parent") != data.get("parent")
        or (successor.get("meta") or {}).get("workflow_mode")
        != "harness_state_machine"
        or successor_machine.get("kind") != "child"
        or successor_machine.get("current_state")
        not in {"child_completed", "child_archived"}
        or successor.get("scope") != data.get("scope")
        or sorted(str(value) for value in successor.get("touches") or [])
        != request["touches"]
    ):
        errors.append("replacement successor lacks exact terminal child evidence")
    try:
        successor_commit = canonical_reachable_commit(
            repo_root,
            str(cancellation.get("successor_commit") or ""),
            "successor completion commit",
        )
        if successor_commit != successor.get("commit"):
            raise ValueError("successor completion commit changed")
        successor_digest = normalize_sha256(
            str(cancellation.get("successor_terminal_digest") or "")
        )
        if terminal_task_evidence_digest(successor_dir) != successor_digest:
            raise ValueError("successor terminal evidence digest changed")
    except (OSError, ValueError) as exc:
        errors.append(f"replacement successor evidence is invalid: {exc}")
    return errors


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
    all_events: list[dict[str, Any]] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors.append("state-events.jsonl contains invalid JSON")
                continue
            all_events.append(event)
    events = [event for event in all_events if event.get("event") == "task_cancelled"]
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

    errors.extend(_replacement_cancellation_errors(task_dir, data, repo_root, all_events))

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
            fulfilled_by = cancellation.get("rtm_fulfilled_by")
            child_index = _column(rtm_header, "Child")
            for rtm_id in cancellation.get("rtm_ids") or []:
                row = by_id.get(rtm_id)
                if not row:
                    errors.append(f"cancelled child RTM row is missing: {rtm_id}")
                    continue
                if status_index is None or len(row) <= status_index or row[status_index].lower() != expected_status:
                    errors.append(f"cancelled child RTM disposition mismatch: {rtm_id}")
                if evidence_index is None or len(row) <= evidence_index or str(evidence) not in row[evidence_index]:
                    errors.append(f"cancelled child RTM evidence mismatch: {rtm_id}")
                if fulfilled_by and (
                    child_index is None
                    or len(row) <= child_index
                    or row[child_index].strip("`") != fulfilled_by
                ):
                    errors.append(f"cancelled child RTM successor mismatch: {rtm_id}")
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
        errors.extend(cancellation_readiness_errors(task_dir, data, repo_root))

    return errors
