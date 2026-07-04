from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{cmd} failed\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def seed_repo(tmp_path: Path) -> None:
    (tmp_path / ".trellis" / "tasks").mkdir(parents=True)
    (tmp_path / ".trellis" / ".developer").write_text("name=tester\n", encoding="utf-8")
    shutil.copytree(
        ROOT / ".trellis" / "scripts",
        tmp_path / ".trellis" / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        ROOT / ".trellis" / "templates",
        tmp_path / ".trellis" / "templates",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def write_task(root: Path, name: str, *, status: str = "in_progress", state: str = "child_waiting_completion_signal") -> Path:
    task_dir = root / ".trellis" / "tasks" / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "name": name,
                "title": name,
                "status": status,
                "tier": "child",
                "owner": "codex",
                "assignee": "tester",
                "children": [],
                "meta": {"state_machine": {"current_state": state}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return task_dir


def test_board_summary_lists_owner_waiting_and_stale(tmp_path: Path) -> None:
    seed_repo(tmp_path)
    old = write_task(tmp_path, "old-task")
    write_task(tmp_path, "fresh-task", state="child_plan_draft")
    old_ts = time.time() - 72 * 60 * 60
    for path in old.rglob("*"):
        os.utime(path, (old_ts, old_ts))
    os.utime(old, (old_ts, old_ts))

    board = tmp_path / ".trellis" / "scripts" / "board.py"
    summary = run([sys.executable, str(board), "--summary", "--max-lines", "10"], tmp_path).stdout

    assert "owner=tester" in summary
    assert "waiting=1" in summary
    assert "old-task" in summary
    assert "Stale >48h: old-task" in summary


def test_task_py_create_and_archive_refresh_board(tmp_path: Path) -> None:
    seed_repo(tmp_path)
    task_py = tmp_path / ".trellis" / "scripts" / "task.py"
    run([sys.executable, str(task_py), "create", "Tmp Task", "--slug", "tmp-board", "--tier", "light"], tmp_path)
    task_dir = next((tmp_path / ".trellis" / "tasks").glob("*tmp-board"))

    board_path = tmp_path / "BOARD.md"
    assert "tmp-board" in board_path.read_text(encoding="utf-8")

    (task_dir / "stage-report.md").write_text(
        "# Stage Report: Tmp Task\n\n## Acceptance\n\n- [x] done\n",
        encoding="utf-8",
    )
    run([sys.executable, str(task_py), "archive", str(task_dir), "--no-commit"], tmp_path)

    board = board_path.read_text(encoding="utf-8")
    assert "tmp-board" in board
    assert "Recent Archives (7d)" in board
