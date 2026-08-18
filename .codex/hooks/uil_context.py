from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def find_root(value: str | None) -> Path | None:
    path = Path(value or os.getcwd()).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".trellis").is_dir():
            return candidate
    return None


def load_tasks(root: Path) -> list[dict[str, Any]]:
    scripts = root / ".trellis/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from taskrun import Authority
        with Authority(root) as authority:
            rows = authority.all(
                "SELECT * FROM tasks ORDER BY updated_at DESC,task_id"
            )
            tasks = []
            for row in rows:
                run = authority.one(
                    "SELECT * FROM runs WHERE task_id=?", (row["task_id"],)
                )
                tasks.append({"task": dict(row), "run": dict(run) if run else None})
            return tasks
    except Exception:
        return []


def select_task(tasks: list[dict[str, Any]], payload: object) -> dict[str, Any] | None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    named = [
        item for item in tasks
        if item["task"]["task_id"] in text
        or item["task"]["task_dir_name"] in text
    ]
    if len(named) == 1:
        return named[0]
    live = [
        item for item in tasks
        if item["task"]["work_state"] in {"ready", "running", "human_blocked", "verified"}
        or item["task"]["closeout_state"] == "cleanup_pending"
    ]
    return live[0] if len(live) == 1 else None


def context(root: Path, payload: object) -> str:
    tasks = load_tasks(root)
    selected = select_task(tasks, payload)
    if selected:
        task = selected["task"]
        run = selected["run"] or {}
        task_line = (
            f"Task: {task['task_id']} | work={task['work_state']} | "
            f"closeout={task['closeout_state']} | archive={task['archive_state']} | "
            f"run={run.get('run_id', 'not-started')} | strategy={run.get('strategy', '-')}"
        )
    elif tasks:
        task_line = "Task: ambiguous; use the exact task ID from the conversation."
    else:
        task_line = "Task: none or cutover authority not initialized."
    return f"""<codex-mode>inline</codex-mode>

<workflow-state>
Unified Intent Loop v1 is active.
Authority: <git-common-dir>/trellis/harness.sqlite3
{task_line}
Use task.py plan|run|status|resume|close|cancel.
BOARD and task.json are projections. Legacy writers are disabled.
Do not infer commit, push, publication, deployment, activation, or real target authority.
</workflow-state>"""


def read_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}
