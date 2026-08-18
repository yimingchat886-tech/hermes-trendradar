from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_ROOT.parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=repo, check=False, capture_output=True, text=True
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(repo, "git", *args, check=check)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(path: Path, *, catalog: bool = False) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "UIL Test")
    git(path, "config", "user.email", "uil@example.invalid")
    write(path / "README.md", "fixture\n")
    if catalog:
        for relative in (
            ".trellis/releases/check-catalog.json",
            ".trellis/scripts/task.py",
            ".trellis/spec/project/loop-v1-overlay-manifest.json",
        ):
            source = REPO_ROOT / relative
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        overlay_path = path / ".trellis/spec/project/loop-v1-overlay-manifest.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["entries"] = [
            {
                "owner": "overlay",
                "path": ".trellis/scripts/task.py",
                "scope": "file",
            }
        ]
        overlay["deployment"]["intentional_deletions"] = []
        write(overlay_path, json.dumps(overlay, indent=2, sort_keys=True) + "\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "fixture")
    return path


def mark_verified(repo: Path, task_id: str) -> None:
    from taskrun import Authority
    from taskrun.authority import utc_now
    from taskrun.service import _task_authorization_candidate_digest

    with Authority(repo) as authority:
        run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
        actions = authority.all(
            "SELECT action_id,check_ids_json FROM actions WHERE run_id=?",
            (run["run_id"],),
        )
    for action in actions:
        record_green_checks(
            repo,
            task_id,
            action["action_id"],
            json.loads(action["check_ids_json"]),
        )
    with Authority(repo) as authority, authority.transaction():
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        task_root = Path(str(task["worktree_path"] or repo))
        authority.execute(
            """UPDATE tasks SET work_state='verified',verified_candidate_digest=?,updated_at=?
               WHERE task_id=?""",
            (
                _task_authorization_candidate_digest(task_root, task),
                utc_now(),
                task_id,
            ),
        )


def candidate_digest(repo: Path) -> str:
    from taskrun.service import _working_candidate_digest

    return _working_candidate_digest(repo)


def record_green_checks(
    repo: Path,
    task_id: str,
    action_id: str,
    check_ids: list[str],
    *,
    attempt_no: int = 1,
    phase: str = "attempt",
) -> None:
    from taskrun import record_check_result

    catalog_path = repo / ".trellis/releases/check-catalog.json"
    overlay_path = repo / ".trellis/spec/project/loop-v1-overlay-manifest.json"
    if not catalog_path.is_file():
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / catalog_path.relative_to(repo), catalog_path)
    if not overlay_path.is_file():
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / overlay_path.relative_to(repo), overlay_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    changed = False
    for version in catalog["versions"].values():
        for check_id in check_ids:
            if check_id not in version or (
                check_id == "trellis.unittest.focused"
                and not (repo / ".trellis/scripts/tests").is_dir()
            ):
                version[check_id] = {
                    "argv": ["git", "diff", "--check"],
                    "mutation": "read_only",
                    "timeout": 30,
                }
                changed = True
    if changed:
        write(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    for check_id in check_ids:
        record_check_result(
            repo,
            task_id,
            action_id=action_id,
            attempt_no=attempt_no,
            check_id=check_id,
            phase=phase,
            operation_id=f"check:{task_id}:{action_id}:{attempt_no}:{check_id}",
        )
