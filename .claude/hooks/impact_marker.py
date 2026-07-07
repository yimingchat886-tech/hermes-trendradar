#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / ".trellis").is_dir():
            return current
        current = current.parent
    return Path.cwd().resolve()


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


def tool_name(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"tool_name", "name"} and isinstance(item, str):
                return item
            found = tool_name(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = tool_name(item)
            if found:
                return found
    return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    name = tool_name(payload)
    if "gitnexus" not in name or not any(part in name for part in ("impact", "context")):
        return 0

    runtime = repo_root() / ".trellis" / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / f"impact-{session_id(payload)}.ok"
    marker.write_text("ok\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
