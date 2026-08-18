from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _uil_helpers import candidate_digest, git, make_repo, record_green_checks, write
from taskrun import (
    Authority,
    AuthorityError,
    OperationConflict,
    append_binding,
    claim_actions,
    plan_task,
    record_attempt,
    record_check_result,
    run_task,
)


class UnifiedLoopTests(unittest.TestCase):
    def test_passed_attempt_requires_every_bound_check(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(root, title="Checked attempt", request="Run bound checks.")[
                "task"
            ]["task_id"]
            run_task(root, task_id)
            claim_actions(root, task_id, "worker")
            with self.assertRaisesRegex(AuthorityError, "bound checks"):
                record_attempt(
                    root,
                    task_id,
                    "implement",
                    passed=True,
                    root_cause_fingerprint=None,
                    candidate_digest=candidate_digest(root),
                    result={"passed": True},
                    operation_id="attempt:unchecked",
                )
            record_green_checks(
                root,
                task_id,
                "implement",
                ["trellis.unittest.focused", "trellis.diff.check"],
            )
            result = record_attempt(
                root,
                task_id,
                "implement",
                passed=True,
                root_cause_fingerprint=None,
                candidate_digest=candidate_digest(root),
                result={"passed": True},
                operation_id="attempt:checked",
            )
            self.assertEqual(result["work_state"], "verified")

    def test_default_loop_stagnates_in_place_and_escalates(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(root, title="Fix parser", request="Fix the parser.")["task"]["task_id"]
            started = run_task(root, task_id)
            run_id = started["run"]["run_id"]
            self.assertEqual(started["run"]["strategy"], "loop")
            self.assertEqual(run_task(root, task_id)["run"]["run_id"], run_id)
            self.assertEqual(claim_actions(root, task_id, "worker-a"), ["implement"])
            record_attempt(
                root, task_id, "implement", passed=False,
                root_cause_fingerprint="same", candidate_digest=None,
                result={"error": "first"}, operation_id="attempt-1",
            )
            claim_actions(root, task_id, "worker-a")
            second = record_attempt(
                root, task_id, "implement", passed=False,
                root_cause_fingerprint="same", candidate_digest=None,
                result={"error": "second"}, operation_id="attempt-2",
            )
            self.assertEqual(second["work_state"], "human_blocked")
            with Authority(root) as authority:
                run = authority.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
                self.assertEqual(run["execution_mode"], "delegated")
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM tasks")["count"], 1)

    def test_single_resumes_same_run_as_loop_and_binding_revises_in_place(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(root, title="One pass", request="Try one pass.")["task"]["task_id"]
            single = run_task(root, task_id, single=True)
            run_id = single["run"]["run_id"]
            claim_actions(root, task_id, "worker-a")
            record_attempt(
                root, task_id, "implement", passed=False,
                root_cause_fingerprint="failed", candidate_digest=None,
                result={"error": "failed"}, operation_id="single-attempt",
            )
            resumed = run_task(root, task_id)
            self.assertEqual(resumed["run"]["run_id"], run_id)
            self.assertEqual(resumed["run"]["strategy"], "loop")

            revision = root / "revision.md"
            write(
                revision,
                "# Revision\n\n## Requirements\n\n"
                "- `ONE-PASS-REQ-002` [owner: codex]: Revise in place.\n",
            )
            generation = append_binding(
                root, task_id, prd_source=revision,
                accepted_commit=git(root, "rev-parse", "HEAD").stdout.strip(),
                operation_id="binding-2",
            )
            self.assertEqual(generation, 2)
            revised = run_task(root, task_id)
            with Authority(root) as authority:
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM tasks")["count"], 1)
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM runs")["count"], 1)
                self.assertEqual(authority.one("SELECT COUNT(*) count FROM bindings")["count"], 2)
                self.assertTrue(
                    any(
                        action["action_id"].startswith("g2:")
                        for action in revised["actions"]
                    )
                )
                old = authority.one(
                    """SELECT status FROM actions
                       WHERE run_id=? AND binding_generation=1""",
                    (run_id,),
                )
                self.assertEqual(old["status"], "superseded")
            self.assertEqual(claim_actions(root, task_id, "worker-b"), ["g2:implement"])

    def test_run_operation_replay_rejects_changed_actions(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(
                root, title="Replay run", request="Bind the action graph."
            )["task"]["task_id"]
            first = [{
                "action_id": "a", "kind": "implement", "dependencies": [],
                "requirement_ids": ["REPLAY-RUN-REQ-001"], "touches": ["src/a.py"],
                "check_ids": ["trellis.diff.check"], "risk": "low",
            }]
            second = [{**first[0], "touches": ["src/b.py"]}]
            run_task(root, task_id, actions=first, operation_id="run:stable")
            with self.assertRaises(OperationConflict):
                run_task(root, task_id, actions=second, operation_id="run:stable")

    def test_check_evidence_is_executed_by_the_catalog_adapter(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            catalog_path = root / ".trellis/releases/check-catalog.json"
            catalog = json.loads(catalog_path.read_text())
            for version in catalog["versions"].values():
                version["fixture.fail"] = {
                    "argv": ["python3", "-c", "raise SystemExit(7)"],
                    "mutation": "read_only",
                    "timeout": 30,
                }
            write(catalog_path, json.dumps(catalog, indent=2, sort_keys=True) + "\n")
            git(root, "add", str(catalog_path.relative_to(root)))
            git(root, "commit", "-m", "failing logical adapter")
            task_id = plan_task(
                root, title="Execute check", request="Trust only executed evidence."
            )["task"]["task_id"]
            run_task(
                root,
                task_id,
                actions=[{
                    "action_id": "verify", "kind": "verify", "dependencies": [],
                    "requirement_ids": ["EXECUTE-CHECK-REQ-001"], "touches": ["src/**"],
                    "check_ids": ["fixture.fail"], "risk": "low",
                }],
            )
            claim_actions(root, task_id, "worker")
            result = record_check_result(
                root,
                task_id,
                action_id="verify",
                attempt_no=1,
                check_id="fixture.fail",
                phase="attempt",
                operation_id="check:executed-failure",
            )
            self.assertFalse(result["passed"])
            with self.assertRaisesRegex(AuthorityError, "bound checks"):
                record_attempt(
                    root,
                    task_id,
                    "verify",
                    passed=True,
                    root_cause_fingerprint=None,
                    candidate_digest=candidate_digest(root),
                    result={"claimed": "passed"},
                    operation_id="attempt:forged",
                )

    def test_binding_revision_uses_task_worktree_and_terminal_tasks_stay_terminal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_root = Path(temp) / "task"
            git(root, "worktree", "add", "-b", "codex/revise-linked", str(task_root))
            task_id = plan_task(
                task_root,
                title="Revise linked",
                request="Keep the PRD in the task worktree.",
                task_branch="codex/revise-linked",
                worktree_path=task_root,
            )["task"]["task_id"]
            run_task(task_root, task_id)
            revision = Path(temp) / "revision.md"
            write(
                revision,
                "# Revision\n\n## Requirements\n\n"
                "- `REVISE-LINKED-REQ-002` [owner: codex]: Use the linked worktree.\n",
            )
            append_binding(
                root,
                task_id,
                prd_source=revision,
                accepted_commit=git(root, "rev-parse", "HEAD").stdout.strip(),
                operation_id="binding:linked",
            )
            with Authority(root) as authority, authority.transaction():
                task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
                self.assertEqual(
                    (task_root / task["prd_path"]).read_text(),
                    revision.read_text(),
                )
                self.assertFalse((root / task["prd_path"]).exists())
                authority.execute(
                    "UPDATE tasks SET work_state='completed' WHERE task_id=?",
                    (task_id,),
                )
            write(revision, revision.read_text() + "\nTerminal change.\n")
            with self.assertRaisesRegex(AuthorityError, "Terminal task"):
                append_binding(
                    root,
                    task_id,
                    prd_source=revision,
                    accepted_commit=git(root, "rev-parse", "HEAD").stdout.strip(),
                    operation_id="binding:terminal",
                )

    def test_overlapping_write_actions_are_not_claimed_together(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            task_id = plan_task(root, title="Action graph", request="Run an action graph.")["task"]["task_id"]
            actions = [
                {"action_id": "a", "kind": "implement", "dependencies": [], "requirement_ids": ["ACTION-GRAPH-REQ-001"], "touches": ["src/**"], "check_ids": ["check.a"], "risk": "low"},
                {"action_id": "b", "kind": "implement", "dependencies": [], "requirement_ids": ["ACTION-GRAPH-REQ-001"], "touches": ["src/x.py"], "check_ids": ["check.b"], "risk": "low"},
                {"action_id": "read", "kind": "research", "dependencies": [], "requirement_ids": ["ACTION-GRAPH-REQ-001"], "touches": ["src/**"], "check_ids": ["check.read"], "risk": "low"},
            ]
            run_task(root, task_id, actions=actions)
            claimed = claim_actions(root, task_id, "worker-a")
            self.assertIn("a", claimed)
            self.assertIn("read", claimed)
            self.assertNotIn("b", claimed)
            self.assertEqual(claim_actions(root, task_id, "worker-a"), claimed)


if __name__ == "__main__":
    unittest.main()
