from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _uil_helpers import git, make_repo, mark_verified, write
from taskrun import (
    Authority,
    AuthorityError,
    CLOSEOUT_STEPS,
    append_binding,
    close_task,
    plan_task,
    run_task,
)
from taskrun.service import _discard_cancelled_paths, _scoped_commit_paths


class CloseoutTests(unittest.TestCase):
    def test_default_closeout_commits_merges_and_cleans_one_task_worktree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_root = Path(temp) / "task"
            git(root, "worktree", "add", "-b", "codex/close-task", str(task_root))
            task_id = plan_task(
                task_root,
                title="Close actual task",
                request="Change source and close locally.",
                task_branch="codex/close-task",
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)
            write(root / "docs/base-note.md", "base advanced\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "non-overlapping base drift")
            write(task_root / "src/app.py", "done = True\n")
            mark_verified(task_root, task_id)

            closed = close_task(
                task_root, task_id, authorization_ref="user:complete-task"
            )
            self.assertEqual(closed["task"]["closeout_state"], "clean")
            self.assertFalse(task_root.exists())
            self.assertEqual(git_refs(root), ["refs/heads/main"])
            committed = set(
                filter(
                    None,
                    git(root, "show", "--format=", "--name-only", "HEAD").stdout.splitlines(),
                )
            )
            self.assertIn("src/app.py", committed)
            self.assertTrue(any(path.endswith("/prd.md") for path in committed))
            self.assertTrue(any(path.endswith("/run-summary.json") for path in committed))
            self.assertNotIn("BOARD.md", committed)
            self.assertTrue(all(not path.endswith("/task.json") for path in committed))

            with Authority(root) as authority, authority.transaction():
                authority.execute(
                    """UPDATE closeout_steps SET state='failed'
                       WHERE task_id=? AND step_name IN ('remove_worktree','remove_merged_local_branch')""",
                    (task_id,),
                )
                authority.execute(
                    "UPDATE tasks SET closeout_state='cleanup_pending' WHERE task_id=?",
                    (task_id,),
                )
            replayed = close_task(
                root, task_id, authorization_ref="user:complete-task"
            )
            self.assertEqual(replayed["task"]["closeout_state"], "clean")

    def test_overlapping_base_drift_blocks_before_commit(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_root = Path(temp) / "task"
            git(root, "worktree", "add", "-b", "codex/conflict", str(task_root))
            task_id = plan_task(
                task_root,
                title="Detect overlap",
                request="Change the shared README.",
                task_branch="codex/conflict",
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)
            write(task_root / "README.md", "task change\n")
            write(root / "README.md", "base change\n")
            git(root, "add", "README.md")
            git(root, "commit", "-m", "base overlap")
            mark_verified(task_root, task_id)
            with self.assertRaisesRegex(AuthorityError, "overlaps"):
                close_task(task_root, task_id, authorization_ref="user:complete-task")
            self.assertTrue(task_root.exists())
            self.assertIn("refs/heads/codex/conflict", git_refs(root))

    def test_post_verified_worktree_change_blocks_closeout(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(
                root, title="Freeze verified candidate", request="Close unchanged work."
            )["task"]["task_id"]
            run_task(root, task_id)
            mark_verified(root, task_id)
            write(root / "src/unverified.py", "changed = True\n")
            with self.assertRaisesRegex(AuthorityError, "candidate changed"):
                close_task(root, task_id, authorization_ref="user:complete-task")

    def test_candidate_change_after_closeout_fault_invalidates_old_authorization(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(
                root, title="Freeze replay", request="Do not bless replay drift."
            )["task"]["task_id"]
            run_task(root, task_id)
            mark_verified(root, task_id)
            handlers = {
                step: (lambda step=step: {"step": step})
                for step in CLOSEOUT_STEPS
            }
            with self.assertRaisesRegex(AuthorityError, "Injected closeout fault"):
                close_task(
                    root,
                    task_id,
                    authorization_ref="user:complete-task",
                    handlers=handlers,
                    fault_step="full_reverify",
                )
            write(root / "src/unverified.py", "changed = True\n")
            with self.assertRaisesRegex(AuthorityError, "outside the authorized saga"):
                close_task(
                    root,
                    task_id,
                    authorization_ref="user:complete-task",
                    handlers=handlers,
                )

    def test_binding_revision_resets_only_pre_effect_closeout(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(
                root,
                title="Revise pre-effect closeout",
                request="Restart closeout for an accepted revision.",
            )["task"]["task_id"]
            run_task(root, task_id)
            mark_verified(root, task_id)
            handlers = {
                step: (lambda step=step: {"step": step})
                for step in CLOSEOUT_STEPS
            }
            with self.assertRaisesRegex(AuthorityError, "Injected closeout fault"):
                close_task(
                    root,
                    task_id,
                    authorization_ref="user:complete-task",
                    handlers=handlers,
                    fault_step="scoped_commit",
                )
            revision = Path(temp) / "revision.md"
            write(
                revision,
                "# Revision\n\n## Requirements\n\n"
                "- `REVISE-CLOSEOUT-REQ-002` [owner: codex]: Restart safely.\n",
            )
            write(root / "src/staged.py", "staged = True\n")
            git(root, "add", "src/staged.py")
            with self.assertRaisesRegex(AuthorityError, "with staged changes"):
                append_binding(
                    root,
                    task_id,
                    prd_source=revision,
                    accepted_commit=git(root, "rev-parse", "HEAD").stdout.strip(),
                    operation_id="binding:staged-closeout",
                )
            git(root, "restore", "--staged", "src/staged.py")
            self.assertEqual(
                append_binding(
                    root,
                    task_id,
                    prd_source=revision,
                    accepted_commit=git(root, "rev-parse", "HEAD").stdout.strip(),
                    operation_id="binding:pre-effect-closeout",
                ),
                2,
            )
            with Authority(root) as authority:
                task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
                self.assertEqual(task["closeout_state"], "not_started")
                self.assertEqual(task["work_state"], "human_blocked")
                self.assertEqual(
                    authority.one(
                        "SELECT COUNT(*) AS count FROM closeout_steps WHERE task_id=?",
                        (task_id,),
                    )["count"],
                    0,
                )

    def test_commit_effect_without_outer_receipt_is_recovered_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_root = Path(temp) / "task"
            git(root, "worktree", "add", "-b", "codex/commit-replay", str(task_root))
            task_id = plan_task(
                task_root,
                title="Recover commit",
                request="Recover a committed external effect.",
                task_branch="codex/commit-replay",
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)
            write(task_root / "src/recovered.py", "recovered = True\n")
            mark_verified(task_root, task_id)
            with self.assertRaisesRegex(AuthorityError, "after scoped_commit"):
                close_task(
                    task_root,
                    task_id,
                    authorization_ref="user:complete-task",
                    fault_step="scoped_commit:after",
                )
            revision = Path(temp) / "revision.md"
            write(
                revision,
                "# Revision\n\n## Requirements\n\n"
                "- `REVISE-COMMITTED-REQ-002` [owner: codex]: Do not rewrite.\n",
            )
            with self.assertRaisesRegex(AuthorityError, "after Git effects begin"):
                append_binding(
                    task_root,
                    task_id,
                    prd_source=revision,
                    accepted_commit=git(task_root, "rev-parse", "HEAD").stdout.strip(),
                    operation_id="binding:post-effect-closeout",
                )
            committed = git(task_root, "rev-parse", "HEAD").stdout.strip()
            closed = close_task(
                task_root,
                task_id,
                authorization_ref="user:complete-task",
            )
            self.assertEqual(closed["task"]["closeout_state"], "clean")
            self.assertEqual(git(root, "rev-parse", "HEAD").stdout.strip(), committed)
            self.assertFalse(task_root.exists())

    def test_cancelled_scope_discards_only_named_task_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            write(root / "src/tracked.py", "old\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "tracked")
            write(root / "src/tracked.py", "new\n")
            write(root / "src/untracked.py", "temporary\n")
            write(root / "user-note.txt", "preserve\n")
            _discard_cancelled_paths(root, ["src/tracked.py", "src/untracked.py"])
            self.assertEqual((root / "src/tracked.py").read_text(), "old\n")
            self.assertFalse((root / "src/untracked.py").exists())
            self.assertEqual((root / "user-note.txt").read_text(), "preserve\n")

    def test_scoped_commit_rejects_unowned_changes_and_projections(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(root, title="Scoped commit", request="Change source only.")[
                "task"
            ]["task_id"]
            run_task(
                root,
                task_id,
                actions=[{
                    "action_id": "source",
                    "kind": "implement",
                    "dependencies": [],
                    "requirement_ids": ["SCOPED-COMMIT-REQ-001"],
                    "touches": ["src/**"],
                    "check_ids": ["source.check"],
                    "risk": "low",
                }],
            )
            with Authority(root) as authority:
                task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
                task_json = f".trellis/tasks/{task['task_dir_name']}/task.json"
                self.assertEqual(
                    _scoped_commit_paths(
                        authority,
                        task,
                        ["src/app.py", task_json, "BOARD.md"],
                    ),
                    ["src/app.py"],
                )
                with self.assertRaisesRegex(AuthorityError, "outside Task action scopes"):
                    _scoped_commit_paths(authority, task, ["README.md"])

    def test_every_fault_boundary_replays_in_the_same_saga(self) -> None:
        for fault_step in CLOSEOUT_STEPS:
            with self.subTest(step=fault_step), tempfile.TemporaryDirectory(dir="/tmp") as temp:
                root = make_repo(Path(temp) / "repo")
                task_id = plan_task(
                    root, title=f"Close {fault_step}", request="Close with replay."
                )["task"]["task_id"]
                run_task(root, task_id)
                mark_verified(root, task_id)
                handlers = {
                    step: (lambda step=step: {"step": step}) for step in CLOSEOUT_STEPS
                }
                with self.assertRaisesRegex(AuthorityError, "Injected closeout fault"):
                    close_task(
                        root,
                        task_id,
                        authorization_ref="user-closeout",
                        handlers=handlers,
                        fault_step=fault_step,
                    )
                with Authority(root) as authority:
                    task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
                    expected = (
                        "cleanup_pending"
                        if CLOSEOUT_STEPS.index(fault_step) > CLOSEOUT_STEPS.index("record_completion")
                        else "running"
                    )
                    self.assertEqual(task["closeout_state"], expected)
                    self.assertEqual(authority.one("SELECT COUNT(*) count FROM tasks")["count"], 1)
                closed = close_task(
                    root, task_id, authorization_ref="user-closeout", handlers=handlers
                )
                self.assertEqual(closed["task"]["work_state"], "completed")
                self.assertEqual(closed["task"]["closeout_state"], "clean")
                self.assertEqual(closed["task"]["archive_state"], "logical_archived")
                self.assertTrue(
                    (root / ".trellis/tasks" / closed["task"]["task_dir_name"]).is_dir()
                )
                with Authority(root) as authority:
                    self.assertEqual(
                        authority.one(
                            "SELECT COUNT(*) count FROM closeout_steps WHERE state='completed'"
                        )["count"],
                        len(CLOSEOUT_STEPS),
                    )
                self.assertEqual(git_refs(root), ["refs/heads/main"])


def git_refs(root: Path) -> list[str]:
    return sorted(line for line in git(root, "for-each-ref", "--format=%(refname)", "refs/heads").stdout.splitlines() if line)


if __name__ == "__main__":
    unittest.main()
