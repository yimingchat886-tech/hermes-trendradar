from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import REPO_ROOT, git, make_repo, write
from taskrun import (
    Authority,
    AuthorityError,
    build_release,
    close_task,
    plan_task,
    qualify_release,
    resolve_check,
    run_check,
    run_task,
    sync_targets,
)
from taskrun.release import load_catalog


class ReleaseSyncTests(unittest.TestCase):
    def test_release_identity_is_payload_bound_and_checks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "source", catalog=True)
            write(root / "managed.txt", "one\n")
            overlay_path = (
                root / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            )
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            overlay["entries"].append(
                {"owner": "overlay", "path": "managed.txt", "scope": "file"}
            )
            write(overlay_path, json.dumps(overlay, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(AuthorityError, "overlay ownership"):
                build_release(
                    root,
                    managed_paths=[".trellis/scripts/task.py"],
                    semantic_qualification={"passed": True},
                )
            first = build_release(
                root, managed_paths=["managed.txt", ".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "a" * 64},
            )
            same = build_release(
                root, managed_paths=["managed.txt", ".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "a" * 64},
            )
            self.assertEqual(first, same)
            qualify_release(root, first)
            write(root / "notes.md", "non-managed\n")
            unchanged = build_release(
                root, managed_paths=["managed.txt", ".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "a" * 64},
            )
            self.assertEqual(first["release_id"], unchanged["release_id"])
            write(root / "managed.txt", "two\n")
            changed = build_release(
                root, managed_paths=["managed.txt", ".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "a" * 64},
            )
            self.assertNotEqual(first["release_id"], changed["release_id"])
            task_id = plan_task(
                root,
                title="Use immutable old release",
                request="Sync the already-qualified release artifact.",
                task_kind="sync",
            )["task"]["task_id"]
            run_task(root, task_id)
            target = make_repo(Path(temp) / "target")
            write(target / ".trellis/.version", "0.6.14\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "target version")
            old = sync_targets(
                root,
                task_id,
                [target],
                release=first,
                check_ids=("trellis.diff.check",),
            )
            with Authority(root) as authority:
                run_id = authority.one(
                    "SELECT run_id FROM runs WHERE task_id=?", (task_id,)
                )["run_id"]
                slot = authority.one(
                    "SELECT worktree_path FROM target_slots WHERE run_id=?",
                    (run_id,),
                )
            self.assertEqual(old["aggregate"], "verified")
            slot_root = Path(slot["worktree_path"])
            self.assertEqual((slot_root / "managed.txt").read_text(), "one\n")
            manifest = slot_root / ".trellis/releases" / f"{first['release_id']}.json"
            self.assertEqual(json.loads(manifest.read_text()), first)
            target_common = Path(
                git(target, "rev-parse", "--git-common-dir").stdout.strip()
            )
            if not target_common.is_absolute():
                target_common = target / target_common
            artifact = (
                target_common
                / "trellis/releases/artifacts"
                / first["release_id"]
                / "overlay/managed.txt"
            )
            self.assertEqual(artifact.read_text(), "one\n")
            manifest.unlink()
            artifact.unlink()
            replayed = sync_targets(
                root,
                task_id,
                [target],
                release=first,
                check_ids=("trellis.diff.check",),
            )
            self.assertEqual(replayed["aggregate"], "verified")
            self.assertEqual(json.loads(manifest.read_text()), first)
            self.assertEqual(artifact.read_text(), "one\n")
            with self.assertRaises(AuthorityError):
                qualify_release(root, {**changed, "semantic_qualification": {"passed": False}})
            with self.assertRaisesRegex(AuthorityError, "Unsafe release path"):
                build_release(
                    root,
                    managed_paths=["managed.txt", ".trellis/scripts/task.py"],
                    intentional_deletions=["../outside"],
                    semantic_qualification={"passed": True},
                )
            catalog = load_catalog(root)
            resolve_check(catalog, "trellis.task_cli.help", version="0.6.12", phase="post", role="target")
            with self.assertRaises(AuthorityError):
                resolve_check(catalog, "trellis.task_cli.help", version="9.9", phase="post", role="target")

    def test_read_only_check_detects_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            resolved = {
                "argv": ["python3", "-c", "open('mutated.txt','w').write('x')"],
                "argv_digest": "x", "check_id": "mutates", "input_digest": "y",
                "environment_allowlist": ["PATH"], "mutation": "read_only",
                "phase": "post", "timeout": 10,
            }
            result = run_check(root, resolved)
            self.assertFalse(result["passed"])
            self.assertTrue(result["mutation_detected"])

    def test_rag_fixture_upgrades_0612_to_0614_before_checks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            fixture = json.loads(
                (
                    REPO_ROOT
                    / ".trellis/scripts/tests/fixtures/rag-v2-0.6.12/fixture.json"
                ).read_text()
            )
            source = make_repo(Path(temp) / "source")
            overlay = json.loads(
                (
                    REPO_ROOT
                    / ".trellis/spec/project/loop-v1-overlay-manifest.json"
                ).read_text()
            )
            managed_paths = [
                entry["path"]
                for entry in overlay["entries"]
                if entry["owner"] == "overlay" and entry["scope"] != "external"
            ]
            for raw in managed_paths:
                origin = REPO_ROOT / raw
                destination = source / raw
                if origin.is_dir():
                    shutil.copytree(origin, destination, dirs_exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origin, destination)
            smoke = source / ".trellis/scripts/tests/test_fixture_upgrade_smoke.py"
            write(
                smoke,
                "import pathlib\nimport unittest\n\n"
                "class FixtureUpgradeSmoke(unittest.TestCase):\n"
                "    def test_full_base_overlay_and_preservation(self):\n"
                "        root = pathlib.Path.cwd()\n"
                "        self.assertEqual((root / '.trellis/.version').read_text().strip(), '0.6.15')\n"
                "        self.assertTrue((root / '.trellis/scripts/taskrun/service.py').is_file())\n"
                "        self.assertFalse((root / '.trellis/scripts/loop_v1').exists())\n"
                "        self.assertEqual((root / 'project-owned.txt').read_text(), 'preserve\\n')\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
            )
            catalog_path = source / ".trellis/releases/check-catalog.json"
            catalog = json.loads(catalog_path.read_text())
            catalog["versions"]["0.6.15"]["trellis.unittest.focused"] = {
                "environment": {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": ".trellis/scripts:.trellis/scripts/tests",
                    "TMPDIR": "/tmp",
                },
                "argv": [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    ".trellis/scripts/tests",
                    "-p",
                    "test_fixture_upgrade_smoke.py",
                    "-q",
                ],
                "mutation": "read_only",
                "timeout": 60,
            }
            write(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
            git(source, "add", "-f", "--", *managed_paths)
            git(source, "commit", "-m", "full Harness overlay")
            release = build_release(
                source,
                managed_paths=managed_paths,
                semantic_qualification={"passed": True, "suite_digest": "c" * 64},
            )
            self.assertIn(
                ".trellis/scripts/common/task_store.py",
                {
                    item["path"]
                    for item in release["trellis_base_artifact"]["payload"]
                },
            )
            qualify_release(source, release)
            task_id = plan_task(
                source, title="Upgrade RAG fixture", request="Upgrade the fixture.", task_kind="sync"
            )["task"]["task_id"]
            run_task(source, task_id)
            target = make_repo(Path(temp) / fixture["name"])
            write(target / ".trellis/.version", fixture["from_version"] + "\n")
            write(target / ".trellis/scripts/task.py", "print('old')\n")
            write(target / ".trellis/scripts/loop_v1/legacy.py", "legacy = True\n")
            write(target / ".trellis/templates/v3/legacy.md", "legacy\n")
            write(target / "project-owned.txt", "preserve\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "old trellis")

            result = sync_targets(
                source, task_id, [target], release=release, check_ids=fixture["check_ids"]
            )
            self.assertEqual(result["aggregate"], "verified")
            with Authority(source) as authority:
                run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
                slot = authority.one(
                    "SELECT worktree_path FROM target_slots WHERE run_id=?", (run["run_id"],)
                )
            self.assertEqual(
                (Path(slot["worktree_path"]) / ".trellis/.version").read_text().strip(),
                fixture["to_version"],
            )
            candidate = Path(slot["worktree_path"])
            self.assertTrue((candidate / ".trellis/scripts/taskrun/service.py").is_file())
            self.assertFalse((candidate / ".trellis/scripts/loop_v1").exists())
            self.assertFalse(
                (candidate / ".trellis/scripts/common/task_store.py").exists()
            )
            self.assertFalse((candidate / ".trellis/templates/v3").exists())
            self.assertEqual((candidate / "project-owned.txt").read_text(), "preserve\n")
            receipt = json.loads(
                (candidate / ".trellis/deploy/adoption.json").read_text()
            )
            self.assertEqual(receipt["check_ids"], fixture["check_ids"])
            self.assertEqual(
                receipt["trellis_base_payload_digest"],
                release["trellis_base_artifact"]["payload_digest"],
            )

    def test_three_target_partial_sync_preserves_unrelated_dirt_and_retries_failed_only(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('ok')\n")
            release = build_release(
                source, managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "b" * 64},
            )
            qualify_release(source, release)
            task_id = plan_task(
                source, title="Sync three targets", request="Sync named targets.", task_kind="sync",
            )["task"]["task_id"]
            run_task(source, task_id)

            targets = []
            for index, version in enumerate(("0.6.12", "0.6.12", "0.6.12"), start=1):
                target = make_repo(base / f"target-{index}")
                write(target / ".trellis/.version", version + "\n")
                write(target / ".trellis/scripts/task.py", "print('old')\n")
                from _uil_helpers import git
                git(target, "add", ".")
                git(target, "commit", "-m", "trellis")
                targets.append(target)
            write(targets[0] / "user-note.txt", "keep me\n")
            with Authority(source) as authority:
                run_id = authority.one(
                    "SELECT run_id FROM runs WHERE task_id=?", (task_id,)
                )["run_id"]
            failed_worktree = base / f".target-2-{run_id[4:12]}-target-2"
            write(failed_worktree, "materialization blocker\n")
            result = sync_targets(source, task_id, targets, release=release)
            self.assertEqual(result["aggregate"], "partial")
            self.assertEqual(result["slots"]["target-1"], "verified")
            self.assertEqual(result["slots"]["target-2"], "failed")
            self.assertEqual(result["slots"]["target-3"], "verified")
            self.assertEqual((targets[0] / "user-note.txt").read_text(), "keep me\n")
            self.assertFalse((targets[0] / ".trellis/tasks").exists())

            with Authority(source) as authority:
                run = authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
                slot = authority.one(
                    "SELECT worktree_path FROM target_slots WHERE run_id=? AND target_id='target-2'",
                    (run["run_id"],),
                )
            Path(slot["worktree_path"]).unlink()
            retried = sync_targets(source, task_id, targets, release=release)
            self.assertEqual(retried["aggregate"], "verified")

            blocked = make_repo(base / "blocked")
            write(blocked / ".trellis/.version", "0.6.12\n")
            write(blocked / ".trellis/scripts/task.py", "dirty\n")
            with self.assertRaisesRegex(AuthorityError, "overlap"):
                sync_targets(source, task_id, [blocked], release=release)

    def test_target_closeout_failure_is_partitioned_and_retriable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('ok')\n")
            git(source, "add", ".")
            git(source, "commit", "-m", "release payload")
            release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "d" * 64},
            )
            qualify_release(source, release)

            task_root = base / "source-task"
            task_branch = "codex/sync-source"
            git(source, "worktree", "add", "-b", task_branch, str(task_root), "HEAD")
            task_id = plan_task(
                task_root,
                title="Close two targets",
                request="Close each synchronized target independently.",
                task_kind="sync",
                task_branch=task_branch,
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)

            targets = []
            for index in (1, 2):
                target = make_repo(base / f"target-{index}")
                write(target / ".gitignore", ".trellis/.version\n")
                write(target / ".trellis/.version", "0.6.12\n")
                write(target / ".trellis/scripts/task.py", "print('old')\n")
                git(target, "add", ".")
                git(target, "commit", "-m", "old trellis")
                targets.append(target)
            sync_targets(task_root, task_id, targets, release=release)

            git(targets[1], "checkout", "-b", "unrelated-user-branch")
            partial = close_task(
                task_root, task_id, authorization_ref="test-close-targets"
            )
            self.assertEqual(partial["partitions"]["target:target-1"], "clean")
            self.assertEqual(
                partial["partitions"]["target:target-2"], "cleanup_pending"
            )
            with Authority(source) as authority:
                run_id = authority.one(
                    "SELECT run_id FROM runs WHERE task_id=?", (task_id,)
                )["run_id"]
                slots = authority.all(
                    "SELECT * FROM target_slots WHERE run_id=? ORDER BY target_id",
                    (run_id,),
                )
            self.assertFalse(
                Path(
                    next(
                        slot["worktree_path"]
                        for slot in slots
                        if slot["target_id"] == "target-1"
                    )
                ).exists()
            )

            target_2_slot = next(
                slot for slot in slots if slot["target_id"] == "target-2"
            )
            git(targets[1], "checkout", "main")
            second_release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "e" * 64},
            )
            qualify_release(source, second_release)
            self.assertNotEqual(second_release["release_id"], release["release_id"])
            write(
                Path(target_2_slot["worktree_path"]) / ".trellis/scripts/task.py",
                "print('tampered')\n",
            )
            repaired = sync_targets(
                task_root, task_id, [targets[1]], release=second_release
            )
            self.assertEqual(repaired["aggregate"], "verified")
            closed = close_task(
                task_root, task_id, authorization_ref="test-close-targets"
            )
            self.assertEqual(closed.get("errors", {}), {})
            self.assertEqual(closed["task"]["closeout_state"], "clean")
            self.assertFalse(task_root.exists())
            with Authority(source) as authority:
                slots = authority.all(
                    "SELECT state FROM target_slots WHERE run_id=?", (run_id,)
                )
            self.assertTrue(all(slot["state"] == "closed" for slot in slots))
            self.assertTrue(
                all(
                    git(target, "ls-files", ".trellis/.version").stdout.strip()
                    == ".trellis/.version"
                    for target in targets
                )
            )
            for item in (release, second_release):
                manifest = f".trellis/releases/{item['release_id']}.json"
                self.assertEqual(
                    git(targets[1], "ls-files", manifest).stdout.strip(), manifest
                )

    def test_target_cleanup_fault_replays_to_closed_state(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('ok')\n")
            git(source, "add", ".")
            git(source, "commit", "-m", "release payload")
            release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "1" * 64},
            )
            qualify_release(source, release)
            task_root = base / "source-task"
            git(source, "worktree", "add", "-b", "codex/cleanup-replay", str(task_root))
            task_id = plan_task(
                task_root,
                title="Replay target cleanup",
                request="Keep logical archive terminal during cleanup retry.",
                task_kind="sync",
                task_branch="codex/cleanup-replay",
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)
            target = make_repo(base / "target")
            write(target / ".trellis/.version", "0.6.12\n")
            write(target / ".trellis/scripts/task.py", "print('old')\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "old trellis")
            sync_targets(task_root, task_id, [target], release=release)

            commit_partial = close_task(
                task_root,
                task_id,
                authorization_ref="test-cleanup-replay",
                fault_step="scoped_commit:after",
            )
            self.assertEqual(
                commit_partial["partitions"]["target:target"],
                "cleanup_pending",
            )
            with Authority(source) as authority:
                self.assertEqual(
                    authority.one("SELECT state FROM target_slots")["state"],
                    "closeout_failed",
                )
            partial = close_task(
                task_root,
                task_id,
                authorization_ref="test-cleanup-replay",
                fault_step="remove_worktree",
            )
            self.assertEqual(
                partial["partitions"]["target:target"], "cleanup_pending"
            )
            with Authority(source) as authority:
                slot = authority.one("SELECT state FROM target_slots")
                self.assertEqual(slot["state"], "cleanup_pending")
                self.assertIsNotNone(
                    authority.one(
                        """SELECT 1 FROM closeout_steps
                           WHERE partition_id='target:target'
                             AND step_name='logical_archive' AND state='completed'"""
                    )
                )
            closed = close_task(
                task_root,
                task_id,
                authorization_ref="test-cleanup-replay",
            )
            self.assertEqual(closed["task"]["closeout_state"], "clean")
            with Authority(source) as authority:
                self.assertEqual(
                    authority.one("SELECT state FROM target_slots")["state"],
                    "closed",
                )

    def test_current_source_sync_qualifies_before_target_write(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('current')\n")
            overlay_path = (
                source / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            )
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
            catalog_path = source / ".trellis/releases/check-catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["versions"]["0.6.15"]["trellis.unittest.focused"]["argv"] = [
                "python3",
                "-c",
                "raise SystemExit(1)",
            ]
            write(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
            git(source, "add", ".trellis")
            git(source, "commit", "-m", "current source")
            task_id = plan_task(
                source,
                title="Sync current source",
                request="Qualify current source before target writes.",
                task_kind="sync",
            )["task"]["task_id"]
            run_task(source, task_id)

            target = make_repo(base / "target")
            write(target / ".trellis/.version", "0.6.14\n")
            write(target / ".trellis/scripts/task.py", "print('old')\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "old target")
            target_head = git(target, "rev-parse", "HEAD").stdout.strip()
            with self.assertRaisesRegex(AuthorityError, "qualification failed"):
                sync_targets(source, task_id, [target], current_source=True)
            self.assertEqual(git(target, "rev-parse", "HEAD").stdout.strip(), target_head)
            self.assertEqual(
                len(
                    [
                        line
                        for line in git(target, "worktree", "list", "--porcelain").stdout.splitlines()
                        if line.startswith("worktree ")
                    ]
                ),
                1,
            )

            catalog["versions"]["0.6.15"]["trellis.unittest.focused"]["argv"] = [
                "python3",
                "-c",
                "pass",
            ]
            write(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
            git(source, "add", str(catalog_path.relative_to(source)))
            git(source, "commit", "-m", "fix qualification")
            synced = sync_targets(source, task_id, [target], current_source=True)
            repeated = sync_targets(source, task_id, [target], current_source=True)
            self.assertEqual(synced["aggregate"], "verified")
            self.assertEqual(synced["release_id"], repeated["release_id"])

    def test_target_managed_symlink_blocks_before_worktree_creation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('release')\n")
            release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "e" * 64},
            )
            qualify_release(source, release)
            task_id = plan_task(
                source,
                title="Reject target symlink",
                request="Do not follow a target managed symlink.",
                task_kind="sync",
            )["task"]["task_id"]
            run_task(source, task_id)

            target = make_repo(base / "target")
            outside = base / "outside.txt"
            write(outside, "preserve\n")
            write(target / ".trellis/.version", "0.6.14\n")
            link = target / ".trellis/scripts/task.py"
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside)
            git(target, "add", ".")
            git(target, "commit", "-m", "managed symlink")

            with self.assertRaisesRegex(AuthorityError, "symlink"):
                sync_targets(source, task_id, [target], release=release)
            self.assertEqual(outside.read_text(), "preserve\n")
            self.assertEqual(
                len(
                    [
                        line
                        for line in git(target, "worktree", "list", "--porcelain").stdout.splitlines()
                        if line.startswith("worktree ")
                    ]
                ),
                1,
            )

    def test_target_release_artifact_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(source / ".trellis/scripts/task.py", "print('release')\n")
            release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "3" * 64},
            )
            qualify_release(source, release)
            task_id = plan_task(
                source,
                title="Reject artifact symlink",
                request="Do not write release artifacts through a symlink.",
                task_kind="sync",
            )["task"]["task_id"]
            run_task(source, task_id)
            target = make_repo(base / "target")
            write(target / ".trellis/.version", "0.6.14\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "target version")
            outside = base / "outside-artifacts"
            outside.mkdir()
            (target / ".git/trellis").symlink_to(outside, target_is_directory=True)

            result = sync_targets(source, task_id, [target], release=release)
            self.assertEqual(result["slots"]["target"], "failed")
            self.assertEqual(list(outside.iterdir()), [])
            with Authority(source) as authority:
                error = authority.one("SELECT error FROM target_slots")["error"]
            self.assertIn("symlink parent", error)

    def test_retry_rechecks_final_symlink_in_retained_slot_worktree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            base = Path(temp)
            source = make_repo(base / "source", catalog=True)
            write(
                source / ".trellis/scripts/task.py",
                "import pathlib, sys\n"
                "raise SystemExit(0 if pathlib.Path('allow-check').exists() else 1)\n",
            )
            release = build_release(
                source,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "2" * 64},
            )
            qualify_release(source, release)
            task_id = plan_task(
                source,
                title="Retry slot symlink",
                request="Never follow a retained candidate symlink.",
                task_kind="sync",
            )["task"]["task_id"]
            run_task(source, task_id)
            target = make_repo(base / "target")
            write(target / ".trellis/.version", "0.6.14\n")
            git(target, "add", ".")
            git(target, "commit", "-m", "target version")
            first = sync_targets(source, task_id, [target], release=release)
            self.assertEqual(first["slots"]["target"], "failed")
            with Authority(source) as authority:
                slot = authority.one("SELECT worktree_path FROM target_slots")
            slot_root = Path(slot["worktree_path"])
            outside = base / "outside.txt"
            write(outside, "preserve\n")
            managed = slot_root / ".trellis/scripts/task.py"
            managed.unlink()
            managed.symlink_to(outside)
            write(slot_root / "allow-check", "allow\n")

            retried = sync_targets(source, task_id, [target], release=release)
            self.assertEqual(retried["slots"]["target"], "failed")
            self.assertEqual(outside.read_text(), "preserve\n")
            with Authority(source) as authority:
                error = authority.one("SELECT error FROM target_slots")["error"]
            self.assertIn("symlink", error)


if __name__ == "__main__":
    unittest.main()
