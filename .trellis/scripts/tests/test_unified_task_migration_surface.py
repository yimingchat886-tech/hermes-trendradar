from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import REPO_ROOT, git, make_repo, write
from taskrun import (
    Authority,
    AuthorityError,
    apply_cutover,
    build_release,
    legacy_inventory,
    migration_plan,
)
from taskrun.migration import BASELINE_RESIDUALS, reject_partial_rollback
from taskrun.release import repository_snapshot


class MigrationSurfaceTests(unittest.TestCase):
    def _legacy_repo(self, root: Path) -> str:
        for residual in BASELINE_RESIDUALS:
            write(
                root / ".trellis/tasks" / residual / "task.json",
                json.dumps({"status": "running", "tier": "child"}),
            )
        bootstrap = "08-10-unified-intent-loop-v1-hard-cutover"
        task = {
            "id": "unified-intent-loop-v1-hard-cutover", "title": "UIL v1",
            "base_branch": "main", "branch": "codex/uil", "worktree_path": None,
        }
        write(root / ".trellis/tasks" / bootstrap / "task.json", json.dumps(task))
        write(
            root / ".trellis/tasks" / bootstrap / "prd.md",
            "# UIL\n\n## Requirements\n\n"
            "- `UIL-REQ-001` [owner: codex]: One task.\n",
        )
        start = {"actions": [{
            "action_id": "ACT-10-authority", "dependencies": [],
            "requirement_ids": ["UIL-REQ-001"], "touches": [".trellis/**"],
            "checks": ["trellis.diff.check"],
        }]}
        write(root / ".trellis/tasks" / bootstrap / "taskrun-start.json", json.dumps(start))
        git(root, "add", ".")
        git(root, "commit", "-m", "legacy and bootstrap")
        return bootstrap

    def test_dry_run_is_deterministic_and_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            bootstrap = self._legacy_repo(root)
            release = build_release(
                root,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "f" * 64},
            )
            write(
                root / ".trellis/.runtime/taskrun/runs/old/authority.sqlite3",
                "legacy-db-bytes",
            )
            before = repository_snapshot(root)
            first = {"inventory": legacy_inventory(root, bootstrap_task_dir=bootstrap), "plan": migration_plan(root, bootstrap_task_dir=bootstrap)}
            second = {"inventory": legacy_inventory(root, bootstrap_task_dir=bootstrap), "plan": migration_plan(root, bootstrap_task_dir=bootstrap)}
            self.assertEqual(first, second)
            self.assertEqual(repository_snapshot(root), before)
            marker = apply_cutover(
                root, bootstrap_task_dir=bootstrap, first_release_id=release["release_id"]
            )
            shutil.rmtree(root / ".trellis/migration/unified-intent-loop-v1")
            replay = apply_cutover(
                root, bootstrap_task_dir=bootstrap, first_release_id=release["release_id"]
            )
            self.assertEqual(marker, replay)
            self.assertTrue((root / ".trellis/migration/unified-intent-loop-v1/cutover-marker.json").is_file())
            with Authority(root) as authority:
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM tasks")["count"], 1)
                rows = authority.all("SELECT disposition FROM legacy_records")
                self.assertEqual(len(rows), 4)
                self.assertTrue(all(row["disposition"] == "sealed_superseded" for row in rows))
            report = json.loads(
                (root / ".trellis/migration/unified-intent-loop-v1/cutover-report.json").read_text()
            )
            common = Path(git(root, "rev-parse", "--git-common-dir").stdout.strip())
            if not common.is_absolute():
                common = root / common
            backup_root = common / report["backup_ref"]
            manifest = json.loads((backup_root / "backup-manifest.json").read_text())
            backup = backup_root / manifest["files"][0]["target"]
            self.assertEqual(backup.read_text(), "legacy-db-bytes")
            with self.assertRaises(AuthorityError):
                reject_partial_rollback(root)

    def test_inventory_includes_untracked_legacy_work_from_linked_worktrees(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            linked = Path(temp) / "linked"
            git(root, "worktree", "add", "-b", "codex/linked", str(linked))
            task = linked / ".trellis/tasks/08-09-untracked-live-task"
            write(
                task / "task.json",
                json.dumps({"id": "untracked-live-task", "status": "running"}),
            )
            write(
                task / "prd.md",
                "# Live\n\n## Requirements\n\n"
                "- `LIVE-REQ-001` [owner: codex]: Preserve this work.\n",
            )
            inventory = legacy_inventory(root)
            row = next(
                entry
                for entry in inventory["entries"]
                if entry["path"].endswith("08-09-untracked-live-task")
            )
            self.assertEqual(row["disposition"], "sealed_candidate_attachment")
            self.assertEqual(row["variants"][0]["worktree"], str(linked))

    def test_archived_terminal_wins_over_stale_linked_candidate(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_name = "08-09-terminal-in-another-worktree"
            task = root / ".trellis/tasks" / task_name
            write(
                task / "task.json",
                json.dumps({"id": "terminal-in-another-worktree", "status": "running"}),
            )
            write(task / "prd.md", "# Terminal elsewhere\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "running task")
            linked = Path(temp) / "linked-terminal"
            git(root, "worktree", "add", "-b", "codex/linked-terminal", str(linked))
            archive = root / ".trellis/tasks/archive/2026-08" / task_name
            archive.parent.mkdir(parents=True)
            task.rename(archive)
            archived = json.loads((archive / "task.json").read_text())
            archived["status"] = "completed"
            write(archive / "task.json", json.dumps(archived))
            git(root, "add", ".")
            git(root, "commit", "-m", "archive terminal task")
            linked_task = linked / ".trellis/tasks" / task_name / "task.json"
            stale = json.loads(linked_task.read_text())
            stale["title"] = "stale candidate"
            write(linked_task, json.dumps(stale))

            matching = [
                row
                for row in legacy_inventory(root)["entries"]
                if row["path"].endswith(task_name)
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["disposition"], "sealed_terminal")
            self.assertEqual(len(matching[0]["variants"]), 2)

    def test_legacy_writer_is_zero_write_and_returns_stable_error(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            before = repository_snapshot(root)
            script = REPO_ROOT / ".trellis/scripts/task.py"
            for command in (
                "create",
                "start",
                "add-subtask",
                "complete-child",
                "soft-archive",
                "archive",
                "archive-recover",
                "archive-orphans",
                "loop-v1",
            ):
                with self.subTest(command=command):
                    result = subprocess.run(
                        ["python3", str(script), command, "anything"],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("LEGACY_WRITE_DISABLED", result.stderr)
                    self.assertEqual(repository_snapshot(root), before)

    def test_running_legacy_requires_explicit_in_place_continuation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            bootstrap = self._legacy_repo(root)
            legacy = "08-09-continue-real-work"
            write(
                root / ".trellis/tasks" / legacy / "task.json",
                json.dumps(
                    {
                        "id": "continue-real-work",
                        "title": "Continue real work",
                        "status": "running",
                        "base_branch": "main",
                        "branch": "codex/continue-real-work",
                        "touches": ["src/**"],
                    }
                ),
            )
            write(
                root / ".trellis/tasks" / legacy / "prd.md",
                "# Continue\n\n## Requirements\n\n"
                "- `CONTINUE-REQ-001` [owner: codex]: Preserve the intent.\n",
            )
            git(root, "add", ".")
            git(root, "commit", "-m", "running legacy")
            linked = Path(temp) / "linked-unreadable"
            git(
                root,
                "worktree",
                "add",
                "-b",
                "codex/continue-real-work",
                str(linked),
            )
            write(linked / "src/candidate.py", "candidate = True\n")
            git(linked, "add", "src/candidate.py")
            git(linked, "commit", "-m", "unique candidate")
            (linked / ".trellis/tasks" / legacy / "task.json").unlink()
            release = build_release(
                root,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "e" * 64},
            )
            with self.assertRaisesRegex(AuthorityError, "explicit continuation"):
                apply_cutover(
                    root,
                    bootstrap_task_dir=bootstrap,
                    first_release_id=release["release_id"],
                )
            apply_cutover(
                root,
                bootstrap_task_dir=bootstrap,
                first_release_id=release["release_id"],
                continue_legacy=[legacy],
            )
            with Authority(root) as authority:
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM tasks")["count"], 2)
                imported = authority.one(
                    "SELECT work_state FROM tasks WHERE task_id='continue-real-work'"
                )
                self.assertEqual(imported["work_state"], "human_blocked")
                self.assertIsNotNone(
                    authority.one("SELECT 1 FROM actions WHERE action_id='imported_candidate'")
                )

    def test_running_legacy_reachable_from_base_without_scoped_dirt_is_sealed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            legacy = root / ".trellis/tasks/08-09-already-merged"
            write(
                legacy / "task.json",
                json.dumps(
                    {
                        "id": "already-merged",
                        "status": "running",
                        "base_branch": "main",
                        "branch": "codex/already-merged",
                        "touches": ["src/**"],
                    }
                ),
            )
            write(legacy / "prd.md", "# Already merged\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "merged legacy record")
            git(root, "branch", "codex/already-merged")

            inventory = legacy_inventory(root)
            row = next(
                entry
                for entry in inventory["entries"]
                if entry["path"].endswith("08-09-already-merged")
            )
            self.assertEqual(row["disposition"], "sealed_superseded")
            self.assertTrue(
                all(
                    variant["candidate_reachable_from_base"] is True
                    and variant["candidate_unique"] is False
                    and not variant["scoped_dirty_paths"]
                    for variant in row["variants"]
                )
            )

    def test_active_import_graph_contains_no_legacy_writer(self) -> None:
        scripts = REPO_ROOT / ".trellis/scripts"
        removed = (
            scripts / "loop_v1",
            scripts / "downstream_deployer",
            scripts / "state_machine.py",
            scripts / "common/archive_transaction.py",
            scripts / "common/task_store.py",
        )
        self.assertTrue(
            all(
                not path.is_file() and not any(path.rglob("*.py"))
                if path.is_dir()
                else not path.exists()
                for path in removed
            )
        )
        task_source = (scripts / "task.py").read_text(encoding="utf-8")
        self.assertNotIn("from common.task_store", task_source)
        self.assertNotIn("from loop_v1", task_source)


if __name__ == "__main__":
    unittest.main()
