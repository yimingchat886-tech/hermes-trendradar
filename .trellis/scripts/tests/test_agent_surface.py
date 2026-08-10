from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from _uil_helpers import REPO_ROOT, git, make_repo


class AgentSurfaceTests(unittest.TestCase):
    def test_public_cli_and_check_agent_are_narrow(self) -> None:
        result = subprocess.run(
            ["python3", ".trellis/scripts/task.py", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        for command in ("plan", "run", "status", "resume", "close", "cancel"):
            self.assertIn(command, result.stdout)
        for command in ("action-claim", "complete-child", "archive-recover", "loop-v1"):
            self.assertNotIn(command, result.stdout)

        reviewer = tomllib.loads(
            (REPO_ROOT / ".codex/agents/trellis-check.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(reviewer["sandbox_mode"], "read-only")
        self.assertFalse(reviewer["features"]["multi_agent"])

    def test_active_guidance_has_no_legacy_writer_command(self) -> None:
        paths = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "HANDOFF.md",
            REPO_ROOT / ".trellis/workflow.md",
            *(REPO_ROOT / ".agents/skills").glob("trellis-*/SKILL.md"),
        ]
        forbidden = (
            "task.py create",
            "task.py start",
            "task.py archive",
            "task.py complete-child",
            "task.py add-subtask",
        )
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in paths if path.is_file()
        )
        self.assertTrue(all(command not in text for command in forbidden))

    def test_claude_settings_reference_only_managed_hook_files(self) -> None:
        settings = json.loads(
            (REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (
                REPO_ROOT
                / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            ).read_text(encoding="utf-8")
        )
        managed = {
            entry["path"]
            for entry in manifest["entries"]
            if entry["owner"] == "overlay"
        }
        commands = {
            hook["command"]
            for groups in settings["hooks"].values()
            for group in groups
            for hook in group["hooks"]
        }
        hook_paths = {command.removeprefix("python3 ") for command in commands}
        self.assertTrue(all((REPO_ROOT / path).is_file() for path in hook_paths))
        self.assertLessEqual(hook_paths, managed)

    def test_bootstrap_action_checks_are_catalog_backed(self) -> None:
        catalog = json.loads(
            (REPO_ROOT / ".trellis/releases/check-catalog.json").read_text(
                encoding="utf-8"
            )
        )
        required = {
            "uil.agent_surface.text",
            "uil.authority.full",
            "uil.candidate_review",
            "uil.closeout.fault_matrix",
            "uil.full_regression",
            "uil.legacy.zero_write",
            "uil.migration.dry_run",
            "uil.migration.replay",
            "uil.release_adapter.full",
            "uil.review.full",
            "uil.sync.partial_dirty",
            "uil.task_loop.full",
        }
        for checks in catalog["versions"].values():
            self.assertLessEqual(required, checks.keys())

    def test_run_from_base_creates_one_isolated_task_worktree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / ".trellis/scripts/task.py"),
                    "run",
                    "--title",
                    "Isolated public run",
                    "--request",
                    "Create the task workspace.",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            snapshot = json.loads(result.stdout)
            worktree = Path(snapshot["task"]["worktree_path"])
            self.assertNotEqual(worktree, root)
            self.assertTrue(worktree.is_dir())
            self.assertEqual(
                git(worktree, "branch", "--show-current").stdout.strip(),
                snapshot["task"]["task_branch"],
            )


if __name__ == "__main__":
    unittest.main()
