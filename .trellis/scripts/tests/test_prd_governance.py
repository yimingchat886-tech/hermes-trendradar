from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from prd import PrdError, generate, inspect_binding, resolve_requirement_ids


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


class PrdGovernanceTests(unittest.TestCase):
    def _repo(
        self,
        root: Path,
        contract: str,
        *,
        source: str = "docs/PRD/product.md",
        task_owner: str | None = None,
    ) -> tuple[Path, str]:
        git(root, "init", "-b", "main")
        git(root, "config", "user.name", "PRD Test")
        git(root, "config", "user.email", "prd@example.invalid")
        path = root / source
        path.parent.mkdir(parents=True)
        path.write_text(contract, encoding="utf-8")
        if task_owner:
            (path.parent / "task.json").write_text(
                json.dumps({"owner": task_owner}) + "\n", encoding="utf-8"
            )
        git(root, "add", ".")
        git(root, "commit", "-m", "accepted contract")
        return path, git(root, "rev-parse", "HEAD")

    def _binding(self, root: Path, commit: str, source: str, req_ids: str) -> Path:
        path = root / "binding.md"
        path.write_text(
            "# Work\n\n"
            "## Accepted PRD Binding\n\n"
            f"- Git commit: `{commit}`\n"
            f"- PRD paths: `{source}`\n"
            f"- REQ IDs: {req_ids}\n",
            encoding="utf-8",
        )
        return path

    def test_projections_are_deletable_deterministic_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, commit = self._repo(
                root,
                "# Product\n\n## Requirements\n\n"
                "- `APP-REQ-002` [owner: learning]: Second requirement text.\n"
                "- `APP-REQ-001` [owner: learning]: First requirement text.\n",
            )
            binding = self._binding(
                root,
                commit,
                "docs/PRD/product.md",
                "`APP-REQ-001`,\n  `APP-REQ-002`",
            )
            output = root / "generated"

            projection = generate(root, binding, output)
            first = {path.name: path.read_bytes() for path in output.iterdir()}
            shutil.rmtree(output)
            generate(root, binding, output)
            second = {path.name: path.read_bytes() for path in output.iterdir()}

            self.assertEqual(first, second)
            self.assertEqual(set(first), {"README.md", "RTM.md", "manifest.json"})
            self.assertEqual(
                set(projection["binding"]), {"git_commit", "prd_paths", "req_ids"}
            )
            self.assertNotIn(b"first requirement text", b"".join(first.values()).lower())

    def test_integrity_gate_reports_only_mechanical_contract_errors(self) -> None:
        cases = (
            (
                "missing REQ refs",
                "- `APP-REQ-001` [owner: learning]: Requirement.\n",
                "`APP-REQ-999`",
            ),
            (
                "duplicate REQ IDs",
                "- `APP-REQ-001` [owner: learning]: One.\n"
                "- `APP-REQ-001` [owner: learning]: Two.\n",
                "`APP-REQ-001`",
            ),
            (
                "missing owner",
                "- `APP-REQ-001`: Requirement.\n",
                "`APP-REQ-001`",
            ),
        )
        for expected, contract, req_ids in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, commit = self._repo(root, f"# Product\n\n## Requirements\n\n{contract}")
                binding = self._binding(root, commit, "docs/PRD/product.md", req_ids)
                with self.assertRaisesRegex(PrdError, expected):
                    inspect_binding(root, binding)

    def test_task_contract_owner_is_read_from_accepted_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = ".trellis/tasks/07-31-parent/prd.md"
            _, commit = self._repo(
                root,
                "# Parent\n\n## Requirements\n\n- `APP-REQ-001`: Requirement.\n",
                source=source,
                task_owner="codex",
            )
            binding = self._binding(root, commit, source, "`APP-REQ-001`")

            projection = inspect_binding(root, binding)

            self.assertEqual(projection["requirements"][0]["owner"], "codex")

    def test_shared_resolver_consumes_declarations_and_reference_only_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = (
                "# Product\n\n## Requirements\n\n"
                "- `APP-REQ-002` [owner: learning]: Second.\n"
                "- `APP-REQ-001` [owner: learning]: First.\n"
            )
            _, commit = self._repo(root, contract)
            binding = self._binding(
                root,
                commit,
                "docs/PRD/product.md",
                "`APP-REQ-002`, `APP-REQ-001`",
            )

            self.assertEqual(
                resolve_requirement_ids(root, contract),
                ["APP-REQ-001", "APP-REQ-002"],
            )
            self.assertEqual(
                resolve_requirement_ids(root, binding.read_text(encoding="utf-8")),
                ["APP-REQ-001", "APP-REQ-002"],
            )

    def test_shared_resolver_rejects_deprecated_split_and_template_placeholders(
        self,
    ) -> None:
        cases = (
            (
                "deprecated ## REQ-ID",
                "# Work\n\n## REQ-ID\n\n- APP-REQ-001: Old form.\n",
            ),
            (
                "unresolved REQ IDs",
                "# Work\n\n## Requirements\n\n"
                "- `TODO-REQ-001` [owner: TODO]: Placeholder.\n",
            ),
            (
                "missing owner",
                "# Work\n\n## Requirements\n\n"
                "- `APP-REQ-001` [owner: TODO]: Placeholder.\n",
            ),
        )
        for expected, text in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(
                PrdError, expected
            ):
                resolve_requirement_ids(Path("."), text)

    def test_accepted_source_drift_requires_a_new_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract, commit = self._repo(
                root,
                "# Product\n\n## Requirements\n\n"
                "- `APP-REQ-001` [owner: learning]: Requirement.\n",
            )
            binding = self._binding(root, commit, "docs/PRD/product.md", "`APP-REQ-001`")
            contract.write_text(contract.read_text(encoding="utf-8") + "\nDrift.\n", encoding="utf-8")

            with self.assertRaisesRegex(PrdError, "re-propose"):
                inspect_binding(root, binding)

    def test_unrelated_head_change_does_not_rebind_running_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, commit = self._repo(
                root,
                "# Product\n\n## Requirements\n\n"
                "- `APP-REQ-001` [owner: learning]: Requirement.\n",
            )
            binding = self._binding(root, commit, "docs/PRD/product.md", "`APP-REQ-001`")
            (root / "notes.md").write_text("Later implementation evidence.\n", encoding="utf-8")
            git(root, "add", "notes.md")
            git(root, "commit", "-m", "implementation evidence")

            projection = inspect_binding(root, binding)

            self.assertEqual(projection["binding"]["git_commit"], commit)
            self.assertNotEqual(git(root, "rev-parse", "HEAD"), commit)

    def test_public_contracts_keep_successor_and_loop_boundaries(self) -> None:
        spec = (REPO_ROOT / ".trellis/spec/project/prd-governance.md").read_text(
            encoding="utf-8"
        )
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        handoff = (REPO_ROOT / "HANDOFF.md").read_text(encoding="utf-8")

        self.assertIn("A material post-start PRD revision defaults to a successor", spec)
        self.assertIn("Loop v1 work requires a new admission", spec)
        self.assertIn("defaults to a successor", readme)
        self.assertIn("requires a new admission rather than", readme)
        self.assertIn("Generated PRD views are not", handoff)


if __name__ == "__main__":
    unittest.main()
