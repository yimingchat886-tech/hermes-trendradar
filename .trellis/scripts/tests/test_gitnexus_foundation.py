from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import REPO_ROOT, make_repo, write
from taskrun.authority import AuthorityError
from taskrun.gitnexus_foundation import (
    apply_gitnexus_assets,
    plan_gitnexus_assets,
    prove_gitnexus,
)


LEGACY_BLOCK = """<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **project** (123 symbols, 0 flows).

## Always Do
legacy

## Never Do
legacy

## Resources
legacy

## CLI
legacy
<!-- gitnexus:end -->"""


class GitNexusFoundationTests(unittest.TestCase):
    def test_structured_plan_preserves_user_text_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            write(target / "AGENTS.md", "user-agents\n\n" + LEGACY_BLOCK + "\n")
            write(target / "CLAUDE.md", "user-claude\n\n" + LEGACY_BLOCK + "\n")
            write(target / ".gitignore", "/build/\n")
            plan = plan_gitnexus_assets(REPO_ROOT, target, "project")
            self.assertIn(".gitnexusrc", plan["changed_paths"])
            self.assertIn(".agents/skills/gitnexus/gitnexus-cli/SKILL.md", plan["changed_paths"])
            apply_gitnexus_assets(target, plan)
            self.assertTrue((target / "AGENTS.md").read_text().startswith("user-agents\n\n"))
            self.assertNotIn("123 symbols", (target / "AGENTS.md").read_text())
            self.assertEqual(
                json.loads((target / ".gitnexusrc").read_text())["analyze"],
                {"indexOnly": True, "name": "project"},
            )
            self.assertEqual(
                plan_gitnexus_assets(REPO_ROOT, target, "project")["changed_paths"], []
            )

    def test_conflicting_repo_name_blocks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            write(
                target / ".gitnexusrc",
                json.dumps({"analyze": {"indexOnly": True, "name": "other"}}),
            )
            with self.assertRaisesRegex(AuthorityError, "registry target id"):
                plan_gitnexus_assets(REPO_ROOT, target, "project")

    def test_unknown_marker_content_blocks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            write(
                target / "AGENTS.md",
                "<!-- gitnexus:start -->\n# GitNexus — Code Intelligence\ncustom\n"
                "<!-- gitnexus:end -->\n",
            )
            with self.assertRaisesRegex(AuthorityError, "unmanaged content"):
                plan_gitnexus_assets(REPO_ROOT, target, "project")

    def test_index_only_proof_leaves_tracked_tree_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            apply_gitnexus_assets(
                target, plan_gitnexus_assets(REPO_ROOT, target, "project")
            )
            executable = Path(temp) / "gitnexus"
            write(
                executable,
                "#!/bin/sh\nmkdir -p .gitnexus\nprintf index > .gitnexus/index\nprintf '{}\\n'\n",
            )
            executable.chmod(0o755)
            proof = prove_gitnexus(
                target, "project", branch="candidate", executable=str(executable)
            )
            self.assertTrue(proof["verified"])
            self.assertTrue(proof["tracked_tree_clean"])

    def test_missing_cli_is_degraded_without_install(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            proof = prove_gitnexus(target, "project", executable="definitely-missing-gitnexus")
            self.assertFalse(proof["verified"])
            self.assertIn("no install", proof["degraded"])

    def test_index_only_proof_detects_staged_tree_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            executable = Path(temp) / "gitnexus"
            write(
                executable,
                "#!/bin/sh\nprintf changed > README.md\n"
                "git add README.md\ngit show HEAD:README.md > README.md\n",
            )
            executable.chmod(0o755)
            with self.assertRaisesRegex(AuthorityError, "tracked working tree"):
                prove_gitnexus(target, "project", executable=str(executable))


if __name__ == "__main__":
    unittest.main()
