#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / ".trellis").is_dir():
            return current
        current = current.parent
    return Path.cwd().resolve()


def main() -> int:
    root = repo_root()
    board = root / ".trellis" / "scripts" / "board.py"
    if not board.is_file():
        return 0
    result = subprocess.run(
        [sys.executable, str(board), "--summary", "--max-lines", "10"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
