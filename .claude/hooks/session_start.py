#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def root() -> Path | None:
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".trellis").is_dir():
            return candidate
    return None


def main() -> int:
    repo = root()
    if repo is None:
        return 0
    scripts = repo / ".trellis/scripts"
    sys.path.insert(0, str(scripts))
    try:
        from taskrun import Authority
        with Authority(repo) as authority:
            tasks = authority.all(
                "SELECT task_id,work_state,closeout_state FROM tasks "
                "ORDER BY updated_at DESC,task_id"
            )
    except Exception:
        tasks = []
    print(json.dumps({"unified_intent_loop": True, "tasks": [dict(row) for row in tasks]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
