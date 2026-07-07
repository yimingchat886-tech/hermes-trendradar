#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE = "BLOCK"


def repo_root() -> Path:
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / ".trellis").is_dir():
            return current
        current = current.parent
    return Path.cwd().resolve()


def developer(root: Path) -> str:
    for key in ("TRELLIS_OWNER", "CLAUDE_OWNER", "TRELLIS_DEVELOPER"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    dev = root / ".trellis" / ".developer"
    if dev.is_file():
        for line in dev.read_text(encoding="utf-8").splitlines():
            if line.startswith("name="):
                return line.split("=", 1)[1].strip()
    return "unknown"


def find_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path"} and isinstance(item, str):
                paths.append(item)
            else:
                paths.extend(find_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(find_paths(item))
    return paths


def find_override_reason(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.replace("-", "_") in {"override_claim", "override_claim_reason"}:
                return item.strip() if isinstance(item, str) else ""
            found = find_override_reason(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_override_reason(item)
            if found:
                return found
    return ""


def load_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def iter_active_cards(root: Path):
    tasks = root / ".trellis" / "tasks"
    if not tasks.is_dir():
        return
    for task_json in tasks.glob("*/task.json"):
        try:
            data = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") in {"completed", "cancelled"}:
            continue
        yield task_json.parent.name, data


def normalize_target(root: Path, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def append_override_event(root: Path, task_name: str, note: str) -> None:
    event = {
        "by": "claim_guard.py",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "current_state": None,
        "event": "override_claim",
        "kind": "audit",
        "note": note,
        "previous_state": None,
    }
    path = root / ".trellis" / "tasks" / task_name / "state-events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    root = repo_root()
    who = developer(root)
    payload = load_hook_input()
    targets = [normalize_target(root, path) for path in find_paths(payload)]
    if not targets:
        return 0

    override_reason = os.environ.get("TRELLIS_OVERRIDE_CLAIM_REASON", "").strip()
    override_reason = override_reason or find_override_reason(payload)

    for task_name, data in iter_active_cards(root) or []:
        owner = data.get("owner")
        if not owner or owner == who:
            continue
        for pattern in data.get("touches") or []:
            for target in targets:
                if fnmatch.fnmatch(target, pattern):
                    if override_reason:
                        note = (
                            f"{target} belongs to {task_name} owner={owner}; "
                            f"current={who}; reason={override_reason}"
                        )
                        append_override_event(root, task_name, note)
                        return 0
                    print(f"G5 claim guard: {target} belongs to {task_name} owner={owner}; current={who}", file=sys.stderr)
                    return 2 if MODE == "BLOCK" else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
