from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import git, make_repo, write
from taskrun.downstream_registry import (
    load_registry,
    normalize_git_origin,
    preflight_registry,
    registry_snapshot,
)
from taskrun.authority import AuthorityError


class DownstreamRegistryTests(unittest.TestCase):
    def test_tracked_registry_has_the_four_confirmed_targets(self) -> None:
        root = Path(__file__).resolve().parents[3]
        snapshot = registry_snapshot(root)
        self.assertEqual(
            [target["id"] for target in snapshot["targets"]],
            ["qivance-music", "hermes-trendradar", "rag-v2", "familios"],
        )
        self.assertEqual(len(snapshot["digest"]), 64)

    def test_origin_normalization_unifies_https_and_ssh(self) -> None:
        expected = "github.com/yimingchat886-tech/familios"
        self.assertEqual(normalize_git_origin("https://github.com/yimingchat886-tech/FamiliOS.git"), expected)
        self.assertEqual(normalize_git_origin("git@github.com:yimingchat886-tech/FamiliOS.git"), expected)
        self.assertEqual(normalize_git_origin("ssh://git@github.com/yimingchat886-tech/FamiliOS.git"), expected)

    def test_duplicate_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            value = {
                "schema_version": 1,
                "targets": [
                    {"id": "one", "root": "/tmp/one", "expected_origin": "github.com/acme/one"},
                    {"id": "two", "root": "/tmp/one", "expected_origin": "github.com/acme/two"},
                ],
            }
            write(root / ".trellis/deploy/targets.json", json.dumps(value))
            with self.assertRaisesRegex(AuthorityError, "must be unique"):
                load_registry(root)

    def test_preflight_binds_git_identity_and_allows_unrelated_dirt(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp) / "controller"
            target = make_repo(Path(temp) / "target")
            git(target, "remote", "add", "origin", "git@github.com:acme/project.git")
            write(target / "local-wip.txt", "preserve\n")
            value = {
                "schema_version": 1,
                "targets": [
                    {
                        "id": "project",
                        "root": str(target),
                        "expected_origin": "github.com/acme/project",
                    }
                ],
            }
            write(root / ".trellis/deploy/targets.json", json.dumps(value))
            result = preflight_registry(root)
            self.assertEqual(result["targets"][0]["dirty_entries"], 1)
            self.assertEqual(result["targets"][0]["head"], git(target, "rev-parse", "HEAD").stdout.strip())

    def test_linked_worktree_operation_blocks_preflight(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            primary = make_repo(base / "primary")
            target = base / "linked"
            git(primary, "remote", "add", "origin", "https://github.com/acme/project.git")
            git(primary, "worktree", "add", "-b", "linked", str(target))
            merge_head = Path(
                git(target, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
            )
            merge_head.write_text("0" * 40 + "\n", encoding="ascii")
            controller = base / "controller"
            write(
                controller / ".trellis/deploy/targets.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "targets": [
                            {
                                "expected_origin": "github.com/acme/project",
                                "id": "project",
                                "root": str(target),
                            }
                        ],
                    }
                ),
            )
            with self.assertRaisesRegex(AuthorityError, "MERGE_HEAD"):
                preflight_registry(controller)


if __name__ == "__main__":
    unittest.main()
