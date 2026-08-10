#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from uil_context import context, find_root, read_input


def main() -> int:
    if os.environ.get("TRELLIS_HOOKS") == "0" or os.environ.get("TRELLIS_DISABLE_HOOKS") == "1":
        return 0
    data = read_input()
    root = find_root(str(data.get("cwd") or os.getcwd()))
    if root is None:
        return 0
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context(root, data),
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
