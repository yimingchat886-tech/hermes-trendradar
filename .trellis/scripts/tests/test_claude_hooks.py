from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
HOOKS = REPO / ".claude" / "hooks"


def run_hook(name: str, repo: Path, payload: dict | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        cwd=repo,
        input=json.dumps(payload or {}),
        text=True,
        capture_output=True,
        env=merged_env,
        check=False,
    )


def make_repo(root: Path) -> None:
    (root / ".trellis" / "tasks").mkdir(parents=True)


def write_task(root: Path, name: str, owner: str, touches: list[str], status: str = "in_progress") -> None:
    task = root / ".trellis" / "tasks" / name
    task.mkdir(parents=True, exist_ok=True)
    (task / "task.json").write_text(
        json.dumps(
            {
                "name": name,
                "owner": owner,
                "status": status,
                "touches": touches,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class ClaudeHookWiringTests(unittest.TestCase):
    def test_settings_registers_only_repo_relative_hook_commands(self) -> None:
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))

        commands = [
            hook["command"]
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        ]

        self.assertEqual(
            commands,
            [
                "python3 .claude/hooks/session_start.py",
                "python3 .claude/hooks/claim_guard.py",
                "python3 .claude/hooks/impact_gate.py",
                "python3 .claude/hooks/scope_gate.py",
                "python3 .claude/hooks/impact_marker.py",
            ],
        )
        self.assertEqual(
            sorted((event, entry["matcher"]) for event, entries in settings["hooks"].items() for entry in entries),
            [
                ("PostToolUse", "mcp__gitnexus__impact|mcp__gitnexus__context"),
                ("PreToolUse", "Bash"),
                ("PreToolUse", "Edit|Write"),
                ("SessionStart", "startup|resume|clear|compact"),
            ],
        )
        tracked_local = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".claude/settings.local.json"],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(tracked_local.returncode, 0)
        self.assertTrue(all(command.startswith("python3 .claude/hooks/") for command in commands))

    def test_session_start_prints_compact_board_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_repo(repo)
            scripts = repo / ".trellis" / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            (scripts / "board.py").write_text(
                "import sys\n"
                "assert sys.argv[-3:] == ['--summary', '--max-lines', '10']\n"
                "print('BOARD active=1 waiting=0')\n",
                encoding="utf-8",
            )

            result = run_hook("session_start.py", repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("BOARD active=1 waiting=0", result.stdout)

    def test_claim_guard_blocks_cross_owner_hidden_dot_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_repo(repo)
            write_task(repo, "claude-task", "codex", [".claude/hooks/**"])

            result = run_hook(
                "claim_guard.py",
                repo,
                {"tool_input": {"file_path": ".claude/hooks/claim_guard.py"}},
                {"TRELLIS_OWNER": "cc"},
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(".claude/hooks/claim_guard.py belongs to claude-task", result.stderr)

    def test_claim_guard_override_writes_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_repo(repo)
            write_task(repo, "claude-task", "codex", [".claude/hooks/**"])

            result = run_hook(
                "claim_guard.py",
                repo,
                {"tool_input": {"file_path": ".claude/hooks/claim_guard.py"}},
                {"TRELLIS_OWNER": "cc", "TRELLIS_OVERRIDE_CLAIM_REASON": "pairing"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = (repo / ".trellis" / "tasks" / "claude-task" / "state-events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "override_claim"', events)
            self.assertIn("reason=pairing", events)

    def test_impact_gate_marker_flow_and_v3_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_repo(repo)
            payload = {"tool_input": {"file_path": ".trellis/scripts/task.py"}, "session_id": "abc"}

            blocked = run_hook("impact_gate.py", repo, payload)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("G2 impact gate", blocked.stderr)

            exempt = run_hook(
                "impact_gate.py",
                repo,
                {"tool_input": {"file_path": ".trellis/tasks/current/task.json"}, "session_id": "abc"},
            )
            self.assertEqual(exempt.returncode, 0, exempt.stderr)

            marked = run_hook("impact_marker.py", repo, {"tool_name": "mcp__gitnexus__impact", "session_id": "abc"})
            self.assertEqual(marked.returncode, 0, marked.stderr)

            allowed = run_hook("impact_gate.py", repo, payload)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_scope_gate_warns_without_blocking_git_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            make_repo(repo)

            result = run_hook("scope_gate.py", repo, {"tool_input": {"command": "git commit -m test"}})

            self.assertEqual(result.returncode, 0)
            self.assertIn("G3 scope gate", result.stderr)


if __name__ == "__main__":
    unittest.main()
