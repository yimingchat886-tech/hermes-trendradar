#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def find_command(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "command" and isinstance(item, str):
                return item
            found = find_command(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_command(item)
            if found:
                return found
    return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    command = find_command(payload)
    if "git commit" not in command:
        return 0

    marker = Path(".trellis/.runtime/scope-check.ok")
    if not marker.is_file():
        print("G3 scope gate: git pre-commit will block until scope-check.ok exists.", file=sys.stderr)
        return 0

    actual = subprocess.run(
        [".trellis/scripts/mark_scope_ok.sh", "--print"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    expected = ""
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("fingerprint="):
            expected = line.split("=", 1)[1]
            break
    if expected != actual:
        print("G3 scope gate: staged diff changed after scope approval.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
