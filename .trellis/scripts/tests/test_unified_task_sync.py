from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _uil_helpers import REPO_ROOT, git, make_repo, write
from taskrun import Authority, build_release, plan_task, qualify_release, run_task
from taskrun.authority import AuthorityError, digest
from taskrun.downstream_registry import preflight_registry
from taskrun.release import sync_targets
from taskrun.service import _target_closeout_handler


def registered_fixture(root: Path, count: int = 4) -> list[Path]:
    targets: list[Path] = []
    rows = []
    for index in range(count):
        target = make_repo(root.parent / f"target-{index}")
        write(target / ".trellis/.version", "0.6.14\n")
        git(target, "add", ".")
        git(target, "commit", "-m", "target version")
        origin = f"https://github.com/acme/project-{index}.git"
        git(target, "remote", "add", "origin", origin)
        targets.append(target)
        rows.append(
            {
                "expected_origin": f"github.com/acme/project-{index}",
                "id": f"registered-{index}",
                "root": str(target),
            }
        )
    write(
        root / ".trellis/deploy/targets.json",
        json.dumps({"schema_version": 1, "targets": rows}, indent=2) + "\n",
    )
    return targets


def qualified_release(root: Path) -> tuple[str, dict[str, object]]:
    release = build_release(
        root,
        managed_paths=[".trellis/scripts/task.py"],
        semantic_qualification={"passed": True, "suite_digest": "a" * 64},
    )
    qualify_release(root, release)
    task_id = plan_task(
        root,
        title="Registered fixture sync",
        request="Apply one qualified fixture release to the exact registry.",
        task_kind="sync",
    )["task"]["task_id"]
    run_task(root, task_id)
    return task_id, release


class RegisteredSyncTests(unittest.TestCase):
    def test_failed_slot_keeps_registry_and_gitnexus_plan_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            source = make_repo(Path(temp) / "source", catalog=True)
            shutil.copytree(
                REPO_ROOT / ".claude/skills/gitnexus",
                source / ".claude/skills/gitnexus",
            )
            targets = registered_fixture(source, count=1)
            task_id, release = qualified_release(source)
            registry = preflight_registry(source)
            with patch(
                "taskrun.release.prove_gitnexus",
                return_value={"degraded": "fixture failure", "verified": False},
            ):
                result = sync_targets(
                    source,
                    task_id,
                    targets,
                    release=release,
                    check_ids=("trellis.diff.check",),
                    registry=registry,
                )
            self.assertEqual(result["aggregate"], "partial")
            with Authority(source) as authority:
                receipt = json.loads(authority.one("SELECT receipt_json FROM target_slots")["receipt_json"])
            self.assertEqual(receipt["registry_digest"], registry["digest"])
            self.assertEqual(
                receipt["registry_snapshot_digest"], digest(registry["targets"])
            )
            self.assertIn(".gitnexusrc", receipt["gitnexus_changed_paths"])

    def test_target_closeout_rebuilds_primary_gitnexus_index(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            target = make_repo(Path(temp) / "target")
            receipt = {
                "gitnexus_asset_digest": "a" * 64,
                "target_branch": "main",
            }
            slot = {
                "branch": "codex/fixture",
                "receipt_json": json.dumps(receipt),
                "run_id": "run-fixture",
                "target_id": "registered-0",
                "target_root": str(target),
                "worktree_path": str(target),
            }

            class Recorder:
                call: tuple[str, tuple[object, ...]] | None = None

                def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
                    self.call = (statement, parameters)

            authority = Recorder()
            proof = {"output_digest": "proof", "verified": True}
            with patch(
                "taskrun.gitnexus_foundation.prove_gitnexus", return_value=proof
            ) as prove:
                result = _target_closeout_handler(authority, slot, "record_completion")
            prove.assert_called_once_with(target.resolve(), "registered-0")
            self.assertEqual(result["gitnexus_primary_proof"], proof)
            persisted = json.loads(authority.call[1][0])
            self.assertEqual(persisted["gitnexus_primary_proof"], proof)

    def test_registry_identity_failure_blocks_every_target_before_write(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            source = make_repo(Path(temp) / "source", catalog=True)
            targets = registered_fixture(source)
            git(targets[-1], "remote", "set-url", "origin", "https://github.com/acme/wrong.git")
            before = [git(target, "show-ref").stdout for target in targets]
            with self.assertRaisesRegex(AuthorityError, "origin mismatch"):
                preflight_registry(source)
            self.assertEqual([git(target, "show-ref").stdout for target in targets], before)

    def test_target_specific_gitnexus_receipts_and_partial_retry(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            source = make_repo(Path(temp) / "source", catalog=True)
            shutil.copytree(
                REPO_ROOT / ".claude/skills/gitnexus",
                source / ".claude/skills/gitnexus",
            )
            targets = registered_fixture(source)
            task_id, release = qualified_release(source)
            registry = preflight_registry(source)
            calls: list[str] = []

            def first_proof(_root: Path, target_id: str, **_kwargs: object) -> dict[str, object]:
                calls.append(target_id)
                if target_id == "registered-1":
                    return {"degraded": "fixture failure", "target_id": target_id, "verified": False}
                return {"output_digest": target_id, "target_id": target_id, "verified": True}

            with patch("taskrun.release.prove_gitnexus", side_effect=first_proof):
                first = sync_targets(
                    source,
                    task_id,
                    targets,
                    release=release,
                    check_ids=("trellis.diff.check",),
                    registry=registry,
                )
            self.assertEqual(first["aggregate"], "partial")
            self.assertEqual(first["slots"]["registered-1"], "failed")

            with patch(
                "taskrun.release.prove_gitnexus",
                return_value={"output_digest": "retry", "verified": True},
            ):
                second = sync_targets(
                    source,
                    task_id,
                    targets,
                    release=release,
                    check_ids=("trellis.diff.check",),
                    registry=registry,
                )
            self.assertEqual(second["aggregate"], "verified")
            with Authority(source) as authority:
                run_id = authority.one(
                    "SELECT run_id FROM runs WHERE task_id=?", (task_id,)
                )["run_id"]
                slots = authority.all(
                    "SELECT target_id,worktree_path,receipt_json FROM target_slots WHERE run_id=? ORDER BY target_id",
                    (run_id,),
                )
            self.assertEqual(len(slots), 4)
            for slot in slots:
                receipt = json.loads(slot["receipt_json"])
                self.assertEqual(receipt["registry_digest"], registry["digest"])
                self.assertIn(".gitnexusrc", receipt["gitnexus_changed_paths"])
                config = json.loads(
                    (Path(slot["worktree_path"]) / ".gitnexusrc").read_text()
                )
                self.assertEqual(config["analyze"]["name"], slot["target_id"])
                expected_attempt = 2 if slot["target_id"] == "registered-1" else 1
                self.assertEqual(receipt["sync_attempt"], expected_attempt)


if __name__ == "__main__":
    unittest.main()
