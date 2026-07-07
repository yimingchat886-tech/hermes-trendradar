#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

MODE = "BLOCK"
SOURCE_PREFIXES = (".trellis/scripts/", ".codex/hooks/")
EXEMPT_PREFIXES = ("docs/", ".agents/", ".claude/", ".trellis/spec/", ".trellis/tasks/", ".trellis/templates/")


def repo_root() -> Path:
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / ".trellis").is_dir():
            return current
        current = current.parent
    return Path.cwd().resolve()


def hook_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


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


def session_id(payload: dict[str, Any]) -> str:
    for key in ("TRELLIS_CONTEXT_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    for key in ("session_id", "transcript_path"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return Path(value).stem
    return "default"


def needs_impact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith(EXEMPT_PREFIXES):
        return False
    return normalized.startswith(SOURCE_PREFIXES)


def main() -> int:
    root = repo_root()
    payload = hook_payload()
    targets = find_paths(payload)
    if not any(needs_impact(path) for path in targets):
        return 0

    marker = root / ".trellis" / ".runtime" / f"impact-{session_id(payload)}.ok"
    if marker.is_file():
        return 0

    print(f"G2 impact gate: run GitNexus impact/context first; missing {marker}", file=sys.stderr)
    return 2 if MODE == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
