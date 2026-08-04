from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common import archive_transaction
from common.archive_transaction import (
    ArchiveTransactionError,
    archive_paths_transaction,
    mark_archive_transaction_recovery_required,
    recover_archive_transaction,
)
from common.task_store import (
    _archive_orphaned_current_trellis_families,
    _orphaned_current_trellis_family_plan,
)


class ArchiveTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Archive Test")
        self._git("config", "user.email", "archive@example.invalid")
        self.child = self.repo / ".trellis" / "tasks" / "07-29-child"
        self.parent = self.repo / ".trellis" / "tasks" / "07-29-parent"
        for path in (self.child, self.parent):
            path.mkdir(parents=True)
            (path / "task.json").write_text(f'{{"name":"{path.name}"}}\n', encoding="utf-8")
        board_script = self.repo / ".trellis" / "scripts" / "board.py"
        board_script.parent.mkdir(parents=True)
        board_script.write_text(
            "from pathlib import Path\n"
            "Path('BOARD.md').write_text('generated\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        (self.repo / "BOARD.md").write_text("before\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "fixture")
        self.index = Path(self._git("rev-parse", "--git-path", "index").stdout.strip())
        if not self.index.is_absolute():
            self.index = self.repo / self.index
        self.index_before = self.index.read_bytes()
        self.moves = [
            (
                self.child,
                self.repo / ".trellis" / "tasks" / "archive" / "2026-07" / self.child.name,
            ),
            (
                self.parent,
                self.repo / ".trellis" / "tasks" / "archive" / "2026-07" / self.parent.name,
            ),
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def _run(self, transaction_id: str = "family-test") -> dict[str, object]:
        return archive_paths_transaction(
            self.repo,
            transaction_id=transaction_id,
            moves=self.moves,
            commit_message="chore(task): archive family",
            family_root=self.parent.name,
        )

    def _assert_rolled_back(self, transaction_id: str) -> None:
        self.assertTrue(self.child.is_dir())
        self.assertTrue(self.parent.is_dir())
        self.assertFalse(self.moves[0][1].exists())
        self.assertFalse(self.moves[1][1].exists())
        self.assertEqual((self.repo / "BOARD.md").read_bytes(), b"before\n")
        self.assertEqual(self.index.read_bytes(), self.index_before)
        journal = archive_transaction._read_journal(
            self.repo
            / ".trellis"
            / ".runtime"
            / "archive-transactions"
            / f"{transaction_id}.json"
        )
        self.assertEqual(journal["phase"], "rolled_back")

    def test_success_moves_children_first_and_commits(self) -> None:
        result = self._run()

        self.assertEqual(result["phase"], "committed")
        self.assertFalse(self.child.exists())
        self.assertFalse(self.parent.exists())
        self.assertTrue(self.moves[0][1].is_dir())
        self.assertTrue(self.moves[1][1].is_dir())
        message = self._git("show", "-s", "--format=%B", "HEAD").stdout
        self.assertIn("Archive-Transaction: family-test", message)
        self.assertEqual(
            [move["source"] for move in result["moves"]],
            [
                self.child.relative_to(self.repo).as_posix(),
                self.parent.relative_to(self.repo).as_posix(),
            ],
        )

    def test_move_failure_rolls_back_exact_preimages(self) -> None:
        original = archive_transaction.shutil.move

        def fail_parent(source: str, destination: str) -> object:
            if Path(source) == self.parent:
                raise archive_transaction.shutil.Error("injected move failure")
            return original(source, destination)

        with mock.patch("common.archive_transaction.shutil.move", side_effect=fail_parent):
            with self.assertRaises(archive_transaction.shutil.Error):
                self._run("move-failure")

        self._assert_rolled_back("move-failure")

    def test_board_failure_rolls_back_exact_preimages(self) -> None:
        board = self.repo / ".trellis" / "scripts" / "board.py"
        board.write_text("raise SystemExit('injected board failure')\n", encoding="utf-8")
        self._git("add", str(board.relative_to(self.repo)))
        self._git("commit", "-m", "failing board fixture")
        self.index_before = self.index.read_bytes()

        with self.assertRaisesRegex(ArchiveTransactionError, "injected board failure"):
            self._run("board-failure")

        self._assert_rolled_back("board-failure")

    def test_stage_and_commit_failures_roll_back(self) -> None:
        original = archive_transaction._git
        for command in ("add", "commit"):
            transaction_id = f"{command}-failure"

            def fail_selected(
                root: Path,
                *args: str,
                check: bool = True,
                selected: str = command,
            ) -> subprocess.CompletedProcess[str]:
                if args[0] == selected:
                    raise ArchiveTransactionError(f"injected {selected} failure")
                return original(root, *args, check=check)

            with mock.patch("common.archive_transaction._git", side_effect=fail_selected):
                with self.assertRaisesRegex(ArchiveTransactionError, f"injected {command} failure"):
                    self._run(transaction_id)
            self._assert_rolled_back(transaction_id)

    def test_incomplete_rollback_blocks_then_public_recovery_finishes(self) -> None:
        original = archive_transaction.shutil.move

        def fail_forward_and_reverse(source: str, destination: str) -> object:
            if Path(source) in {self.parent, self.moves[0][1]}:
                raise archive_transaction.shutil.Error("injected rollback failure")
            return original(source, destination)

        with mock.patch(
            "common.archive_transaction.shutil.move",
            side_effect=fail_forward_and_reverse,
        ):
            with self.assertRaisesRegex(ArchiveTransactionError, "rollback is incomplete"):
                self._run("rollback-failure")

        with self.assertRaisesRegex(ArchiveTransactionError, "recovery is required"):
            self._run("blocked-by-recovery")

        result = recover_archive_transaction(self.repo, "rollback-failure")
        self.assertEqual(result["phase"], "rolled_back")
        self._assert_rolled_back("rollback-failure")

    def test_commit_success_journal_failure_recovers_without_second_commit(self) -> None:
        original = archive_transaction._write_journal
        failed = False

        def fail_committed(path: Path, value: dict[str, object]) -> None:
            nonlocal failed
            if value["phase"] == "committed" and not failed:
                failed = True
                raise OSError("injected journal failure")
            original(path, value)

        base = self._git("rev-parse", "HEAD").stdout.strip()
        with mock.patch("common.archive_transaction._write_journal", side_effect=fail_committed):
            with self.assertRaisesRegex(OSError, "injected journal failure"):
                self._run("journal-failure")
        committed = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(committed, base)

        result = recover_archive_transaction(self.repo, "journal-failure")
        self.assertEqual(result["phase"], "committed")
        self.assertEqual(result["git_commit"], committed)
        self.assertEqual(self._git("rev-list", "--count", f"{base}..HEAD").stdout.strip(), "1")

    def test_committed_cleanup_recovery_survives_later_head(self) -> None:
        committed = self._run("cleanup-recovery")["git_commit"]
        mark_archive_transaction_recovery_required(
            self.repo,
            "cleanup-recovery",
            "injected cleanup failure",
        )
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        self._git("add", "later.txt")
        self._git("commit", "-m", "later commit")

        result = recover_archive_transaction(self.repo, "cleanup-recovery")

        self.assertEqual(result["phase"], "committed")
        self.assertEqual(result["git_commit"], committed)

    def test_orphan_sweep_archives_multiple_families_in_one_transaction(self) -> None:
        tasks = self.repo / ".trellis" / "tasks"
        archived = tasks / "archive" / "2026-07"
        family_names = ["07-20-family-a", "07-21-family-b"]
        child_names = ["07-20-child-a", "07-21-child-b"]
        for family_name, child_name in zip(family_names, child_names, strict=True):
            family = archived / family_name
            child = tasks / child_name
            family.mkdir(parents=True)
            child.mkdir()
            (family / "task.json").write_text(
                '{"status":"completed","tier":"parent","children":["'
                + child_name
                + '"],"meta":{"workflow_mode":"harness_state_machine",'
                '"state_machine":{"current_state":"parent_archived"}}}\n',
                encoding="utf-8",
            )
            (child / "task.json").write_text(
                '{"status":"completed","tier":"child","parent":"'
                + family_name
                + '","meta":{"workflow_mode":"harness_state_machine",'
                '"state_machine":{"current_state":"child_archived"}}}\n',
                encoding="utf-8",
            )

        result = _archive_orphaned_current_trellis_families(
            self.repo,
            no_commit=True,
        )

        self.assertEqual(result["phase"], "committed")
        self.assertEqual(result["family_roots"], family_names)
        for child_name in child_names:
            self.assertFalse((tasks / child_name).exists())
            self.assertTrue((archived / child_name).is_dir())

    def test_mixed_recursive_legacy_family_is_planned_and_moved_without_rewrite(
        self,
    ) -> None:
        tasks = self.repo / ".trellis" / "tasks"
        archived = tasks / "archive" / "2026-07"
        root = archived / "07-20-mixed-root"
        legacy = tasks / "07-20-legacy-child"
        current = tasks / "07-20-current-child"
        nested = tasks / "07-20-nested-child"
        cancelled = tasks / "07-20-cancelled-child"
        for path in (root, legacy, current, nested, cancelled):
            path.mkdir(parents=True)

        root_data = {
            "children": [legacy.name, current.name, cancelled.name],
            "meta": {
                "state_machine": {"current_state": "parent_archived"},
                "workflow_mode": "harness_state_machine",
            },
            "status": "completed",
            "tier": "parent",
        }
        legacy_data = {
            "children": [],
            "meta": {
                "state_machine": {"current_state": "child_archived"},
                "workflow_mode": "harness_state_machine",
            },
            "parent": None,
            "status": "completed",
            "tier": "child",
        }
        current_data = {
            "children": [nested.name],
            "meta": {
                "state_machine": {
                    "current_state": "child_completed",
                    "schema_version": 2,
                },
                "workflow_mode": "harness_state_machine",
            },
            "parent": root.name,
            "status": "completed",
            "tier": "child",
        }
        nested_data = {
            "children": [],
            "meta": {
                "state_machine": {"current_state": "child_archived"},
                "workflow_mode": "harness_state_machine",
            },
            "parent": current.name,
            "status": "completed",
            "tier": "child",
        }
        authorized_at = "2026-07-20T10:00:00Z"
        cancellation = {
            "authorized_at": authorized_at,
            "authorized_by": "jym",
            "preserved": {
                "branch": None,
                "children": [],
                "commit": None,
                "parent": root.name,
                "worktree_path": None,
            },
            "reason": "superseded",
            "retained_files": [],
        }
        cancelled_data = {
            "branch": None,
            "cancelledAt": "2026-07-20",
            "cancellation": cancellation,
            "children": [],
            "commit": None,
            "meta": {
                "state_machine": {
                    "current_state": "child_cancelled",
                    "last_event": "task_cancelled",
                    "schema_version": 2,
                },
                "workflow_mode": "harness_state_machine",
            },
            "parent": root.name,
            "status": "cancelled",
            "tier": "child",
            "worktree_path": None,
        }
        for path, data in (
            (root, root_data),
            (legacy, legacy_data),
            (current, current_data),
            (nested, nested_data),
            (cancelled, cancelled_data),
        ):
            (path / "task.json").write_text(
                json.dumps(data, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (root / "governance.md").write_text(
            "# Governance\n\n"
            "## Child Index\n\n"
            "| Child | Status |\n"
            "|---|---|\n"
            f"| {legacy.name} | completed |\n"
            f"| {current.name} | completed |\n"
            f"| {cancelled.name} | cancelled |\n\n"
            "## RTM\n\n"
            "| REQ-ID | Child | Status | Evidence |\n"
            "|---|---|---|---|\n",
            encoding="utf-8",
        )
        (cancelled / "state-events.jsonl").write_text(
            json.dumps(
                {
                    "authorized_by": "jym",
                    "by": "jym",
                    "created_at": authorized_at,
                    "event": "task_cancelled",
                    "reason": "superseded",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = {
            path.name: (path / "task.json").read_bytes()
            for path in (legacy, current, nested, cancelled)
        }
        board_before = (self.repo / "BOARD.md").read_bytes()
        index_before = self.index.read_bytes()

        report, _ = _orphaned_current_trellis_family_plan(self.repo)

        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["eligible_families"], [root.name])
        self.assertEqual(
            [Path(move["source"]).name for move in report["planned_moves"]],
            [legacy.name, nested.name, current.name, cancelled.name],
        )
        self.assertEqual((self.repo / "BOARD.md").read_bytes(), board_before)
        self.assertEqual(self.index.read_bytes(), index_before)
        self.assertFalse(
            (self.repo / ".trellis" / ".runtime" / "archive-transactions").exists()
        )

        _archive_orphaned_current_trellis_families(self.repo, no_commit=True)

        for name, task_bytes in before.items():
            destination = archived / name
            self.assertTrue(destination.is_dir())
            self.assertEqual((destination / "task.json").read_bytes(), task_bytes)

    def test_read_only_orphan_plan_reports_nonterminal_descendant(self) -> None:
        tasks = self.repo / ".trellis" / "tasks"
        archived = tasks / "archive" / "2026-07"
        root = archived / "07-22-blocked-root"
        child = tasks / "07-22-active-child"
        root.mkdir(parents=True)
        child.mkdir()
        (root / "task.json").write_text(
            '{"children":["07-22-active-child"],'
            '"meta":{"state_machine":{"current_state":"parent_archived"},'
            '"workflow_mode":"harness_state_machine"},'
            '"status":"completed","tier":"parent"}\n',
            encoding="utf-8",
        )
        (child / "task.json").write_text(
            '{"children":[],"meta":{"state_machine":{'
            '"current_state":"child_plan_draft","schema_version":2},'
            '"workflow_mode":"harness_state_machine"},'
            '"parent":"07-22-blocked-root","status":"in_progress","tier":"child"}\n',
            encoding="utf-8",
        )
        board_before = (self.repo / "BOARD.md").read_bytes()
        index_before = self.index.read_bytes()

        report, _ = _orphaned_current_trellis_family_plan(self.repo)

        self.assertEqual(report["eligible_families"], [])
        self.assertEqual(report["planned_moves"], [])
        self.assertEqual(len(report["blockers"]), 1)
        self.assertIn(".trellis/tasks/07-22-active-child", report["blockers"][0]["error"])
        self.assertIn("status='in_progress'", report["blockers"][0]["error"])
        self.assertIn("state='child_plan_draft'", report["blockers"][0]["error"])
        self.assertEqual((self.repo / "BOARD.md").read_bytes(), board_before)
        self.assertEqual(self.index.read_bytes(), index_before)
        self.assertFalse(
            (self.repo / ".trellis" / ".runtime" / "archive-transactions").exists()
        )

    def test_orphan_plan_rejects_ambiguous_relationship_shapes(self) -> None:
        def write_task(
            path: Path,
            *,
            tier: str,
            parent: str | None,
            children: list[str],
        ) -> None:
            path.mkdir(parents=True)
            (path / "task.json").write_text(
                json.dumps(
                    {
                        "children": children,
                        "meta": {
                            "state_machine": {
                                "current_state": f"{tier}_archived",
                            },
                            "workflow_mode": "harness_state_machine",
                        },
                        "parent": parent,
                        "status": "completed",
                        "tier": tier,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        cases = {
            "missing": "missing or ambiguous",
            "multiple_owners": "ambiguous owners",
            "parent_conflict": "parent conflicts",
            "duplicate_location": "missing or ambiguous",
            "cycle": "family cycle",
            "cross_month": "spans archive months",
        }
        for case, expected in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                tasks = repo / ".trellis" / "tasks"
                month = tasks / "archive" / "2026-07"
                root = month / "07-23-root"
                child = tasks / "07-23-child"
                write_task(
                    root,
                    tier="parent",
                    parent=None,
                    children=["07-23-missing"] if case == "missing" else [child.name],
                )
                if case != "missing":
                    write_task(
                        child,
                        tier="child",
                        parent="other-parent" if case == "parent_conflict" else root.name,
                        children=["07-23-nested"] if case == "cycle" else [],
                    )
                if case == "multiple_owners":
                    other = tasks / "07-23-other-owner"
                    write_task(
                        other,
                        tier="parent",
                        parent=None,
                        children=[child.name],
                    )
                elif case == "duplicate_location":
                    write_task(
                        month / child.name,
                        tier="child",
                        parent=root.name,
                        children=[],
                    )
                elif case == "cycle":
                    nested = tasks / "07-23-nested"
                    write_task(
                        nested,
                        tier="child",
                        parent=child.name,
                        children=[root.name],
                    )
                elif case == "cross_month":
                    target = tasks / "archive" / "2026-06" / child.name
                    target.parent.mkdir(parents=True)
                    child.rename(target)

                report, _ = _orphaned_current_trellis_family_plan(repo)

                self.assertEqual(report["eligible_families"], [])
                self.assertEqual(report["planned_moves"], [])
                self.assertEqual(len(report["blockers"]), 1)
                self.assertIn(expected, report["blockers"][0]["error"])


if __name__ == "__main__":
    unittest.main()
