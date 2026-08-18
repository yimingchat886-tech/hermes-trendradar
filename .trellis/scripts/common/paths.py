"""Stable repository and developer paths."""

from __future__ import annotations

from pathlib import Path


DIR_WORKFLOW = ".trellis"
DIR_WORKSPACE = "workspace"
DIR_TASKS = "tasks"
FILE_DEVELOPER = ".developer"
FILE_JOURNAL_PREFIX = "journal-"


def get_repo_root(start_path: Path | None = None) -> Path:
    current = (start_path or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / DIR_WORKFLOW).is_dir():
            return candidate
    return current


def get_developer(repo_root: Path | None = None) -> str | None:
    path = (repo_root or get_repo_root()) / DIR_WORKFLOW / FILE_DEVELOPER
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("name="):
                return line.partition("=")[2].strip() or None
    except OSError:
        return None
    return None


def check_developer(repo_root: Path | None = None) -> bool:
    return get_developer(repo_root) is not None


def get_tasks_dir(repo_root: Path | None = None) -> Path:
    return (repo_root or get_repo_root()) / DIR_WORKFLOW / DIR_TASKS


def get_workspace_dir(repo_root: Path | None = None) -> Path | None:
    root = repo_root or get_repo_root()
    developer = get_developer(root)
    return root / DIR_WORKFLOW / DIR_WORKSPACE / developer if developer else None
