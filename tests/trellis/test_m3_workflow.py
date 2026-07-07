from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(cmd: list[str], cwd: Path, *, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, input=stdin, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{cmd} failed\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], repo, check=check)


def seed_git_repo(repo: Path) -> None:
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def test_trellis_pr_requires_acknowledgements_review_and_slug_title(tmp_path: Path) -> None:
    seed_git_repo(tmp_path)
    task_dir = tmp_path / ".trellis" / "tasks" / "m3"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps({"name": "workflow-v2-m3-pr-gates", "touches": ["src/**", "migrations/**"]}),
        encoding="utf-8",
    )
    src = tmp_path / "src" / "app.py"
    src.parent.mkdir()
    src.write_text("def keep():\n    return 1\n\n\ndef drop():\n    return 2\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")

    git(tmp_path, "switch", "-c", "feature")
    src.write_text("def keep():\n    return 1\n", encoding="utf-8")
    migration = tmp_path / "migrations" / "001.sql"
    migration.parent.mkdir()
    migration.write_text("create table example(id integer);\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "feature without slug")

    script = ROOT / ".trellis" / "scripts" / "trellis_pr.sh"
    blocked_delete = run(["bash", str(script), str(task_dir)], tmp_path, check=False)
    assert blocked_delete.returncode == 2
    assert "--ack-deletions" in blocked_delete.stderr

    blocked_migration = run(["bash", str(script), str(task_dir), "--ack-deletions"], tmp_path, check=False)
    assert blocked_migration.returncode == 2
    assert "--ack-migrations" in blocked_migration.stderr

    blocked_review = run(
        ["bash", str(script), str(task_dir), "--ack-deletions", "--ack-migrations"],
        tmp_path,
        check=False,
    )
    assert blocked_review.returncode == 2
    assert "--reviewed" in blocked_review.stderr

    blocked_title = run(
        [
            "bash",
            str(script),
            str(task_dir),
            "--ack-deletions",
            "--ack-migrations",
            "--reviewed",
            "checked",
        ],
        tmp_path,
        check=False,
    )
    assert blocked_title.returncode == 2
    assert "[workflow-v2-m3-pr-gates]" in blocked_title.stderr

    ok = run(
        [
            "bash",
            str(script),
            str(task_dir),
            "--ack-deletions",
            "--ack-migrations",
            "--reviewed",
            "checked",
            "--title",
            "[workflow-v2-m3-pr-gates] feature",
        ],
        tmp_path,
    )
    assert "trellis_pr ok" in ok.stdout
    assert (tmp_path / ".trellis" / ".runtime" / "pr-ok-workflow-v2-m3-pr-gates").is_file()


def test_impact_gate_blocks_source_until_marker_and_marker_hook_writes_it(tmp_path: Path) -> None:
    (tmp_path / ".trellis" / ".runtime").mkdir(parents=True)
    gate = ROOT / ".claude" / "hooks" / "impact_gate.py"
    marker = ROOT / ".claude" / "hooks" / "impact_marker.py"

    source_payload = json.dumps({"session_id": "s1", "tool_input": {"file_path": ".trellis/scripts/task.py"}})
    blocked = run([sys.executable, str(gate)], tmp_path, stdin=source_payload, check=False)
    assert blocked.returncode == 2
    assert "G2 impact gate" in blocked.stderr

    docs_payload = json.dumps({"session_id": "s1", "tool_input": {"file_path": "docs/readme.md"}})
    assert run([sys.executable, str(gate)], tmp_path, stdin=docs_payload).returncode == 0

    impact_payload = json.dumps({"session_id": "s1", "tool_name": "mcp__gitnexus__impact"})
    assert run([sys.executable, str(marker)], tmp_path, stdin=impact_payload).returncode == 0
    assert run([sys.executable, str(gate)], tmp_path, stdin=source_payload).returncode == 0


def test_claim_guard_blocks_cross_owner_and_audits_override(tmp_path: Path) -> None:
    task_dir = tmp_path / ".trellis" / "tasks" / "m6-child"
    task_dir.mkdir(parents=True)
    (tmp_path / ".trellis" / ".developer").write_text("name=cc\n", encoding="utf-8")
    (task_dir / "task.json").write_text(
        json.dumps({"status": "in_progress", "owner": "codex", "touches": [".claude/hooks/**"]}),
        encoding="utf-8",
    )
    (task_dir / "state-events.jsonl").write_text("", encoding="utf-8")
    guard = ROOT / ".claude" / "hooks" / "claim_guard.py"

    payload = json.dumps({"tool_input": {"file_path": ".claude/hooks/claim_guard.py"}})
    blocked = run([sys.executable, str(guard)], tmp_path, stdin=payload, check=False)
    assert blocked.returncode == 2
    assert "G5 claim guard" in blocked.stderr

    override_payload = json.dumps(
        {"tool_input": {"file_path": ".claude/hooks/claim_guard.py", "override_claim": "manual handoff"}}
    )
    allowed = run([sys.executable, str(guard)], tmp_path, stdin=override_payload)
    assert allowed.returncode == 0
    events = (task_dir / "state-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "override_claim"' in events
    assert "manual handoff" in events


def test_pre_push_blocks_stale_main_and_private_runtime_paths(tmp_path: Path) -> None:
    seed_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "switch", "-c", "feature")
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "feature")

    git(tmp_path, "switch", "main")
    (tmp_path / "README.md").write_text("base\nmain\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "main moves")
    git(tmp_path, "switch", "feature")

    hook = ROOT / ".trellis" / "scripts" / "hooks" / "pre-push"
    stale = run(["bash", str(hook)], tmp_path, check=False)
    assert stale.returncode == 2
    assert "does not contain latest main" in stale.stderr

    git(tmp_path, "merge", "main", "--no-edit")
    (tmp_path / ".trellis" / ".runtime").mkdir(parents=True)
    (tmp_path / ".trellis" / ".runtime" / "leak").write_text("private\n", encoding="utf-8")
    git(tmp_path, "add", ".trellis/.runtime/leak")
    git(tmp_path, "commit", "-m", "leak runtime")

    private = run(["bash", str(hook)], tmp_path, check=False)
    assert private.returncode == 2
    assert "private Trellis runtime path" in private.stderr
