from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import git, make_repo
from taskrun import Authority, OperationConflict, authority_path, plan_task


class UnifiedAuthorityTests(unittest.TestCase):
    def test_common_dir_authority_survives_worktree_removal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            planned = plan_task(root, title="Fix board status", request="Use SQLite status.")
            task = planned["task"]
            self.assertNotIn("tier", task)
            self.assertEqual(task["work_state"], "ready")
            database = authority_path(root)
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)

            linked = Path(temp) / "linked"
            git(root, "worktree", "add", "-b", "codex/linked", str(linked))
            self.assertEqual(authority_path(linked), database)
            with Authority(linked) as authority:
                self.assertEqual(authority.task_snapshot(task["task_id"])["task"]["title"], "Fix board status")
            git(root, "worktree", "remove", str(linked))
            self.assertTrue(database.is_file())

    def test_operation_replay_is_idempotent_and_conflicting_input_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            with Authority(root, create=True) as authority:
                first = authority.record_event("op-1", "fixture", {"value": 1})
                replay = authority.record_event("op-1", "fixture", {"value": 1})
                self.assertEqual(first, replay)
                with self.assertRaises(OperationConflict):
                    authority.record_event("op-1", "fixture", {"value": 2})
                authority.verify_event_chain()


if __name__ == "__main__":
    unittest.main()
