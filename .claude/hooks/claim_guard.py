#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Any

MODE = "WARN"


def repo_root() -> Path:
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / ".trellis").is_dir():
            return current
        current = current.parent
    return Path.cwd().resolve()


def developer(root: Path) -> str:
    for key in ("TRELLIS_OWNER", "CLAUDE_OWNER", "TRELLIS_DEVELOPER"):
        if os.environ.get(key):
            return os.environ[key].strip()
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


def main() -> int:
    root = repo_root()
    who = developer(root)
    payload = load_hook_input()
    targets = [Path(p).as_posix() for p in find_paths(payload)]
    if not targets:
        return 0

    for task_name, data in iter_active_cards(root) or []:
        owner = data.get("owner")
        if not owner or owner == who:
            continue
        for pattern in data.get("touches") or []:
            for target in targets:
                if fnmatch.fnmatch(target, pattern):
                    print(f"G5 claim guard: {target} belongs to {task_name} owner={owner}; current={who}", file=sys.stderr)
                    return 2 if MODE == "BLOCK" else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
