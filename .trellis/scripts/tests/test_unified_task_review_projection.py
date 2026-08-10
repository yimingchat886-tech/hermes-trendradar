from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _uil_helpers import candidate_digest, make_repo, record_green_checks, write
from taskrun import (
    Authority,
    AuthorityError,
    claim_actions,
    close_finding,
    plan_task,
    rebuild_projections,
    record_attempt,
    record_review,
    run_task,
    task_status,
)


class ReviewProjectionTests(unittest.TestCase):
    def _candidate(self, root: Path, title: str) -> tuple[str, str]:
        task_id = plan_task(root, title=title, request="Change a high-risk boundary.")["task"]["task_id"]
        actions = [{
            "action_id": "core", "kind": "implement", "dependencies": [],
            "requirement_ids": [f"{task_id.upper()}-REQ-001"],
            "touches": ["core/**"], "check_ids": ["trellis.diff.check"], "risk": "high",
        }]
        run_task(root, task_id, actions=actions)
        claim_actions(root, task_id, "worker")
        record_green_checks(root, task_id, "core", ["trellis.diff.check"])
        candidate = candidate_digest(root)
        record_attempt(
            root, task_id, "core", passed=True, root_cause_fingerprint=None,
            candidate_digest=candidate, result={"passed": True}, operation_id=f"attempt:{task_id}",
        )
        return task_id, candidate

    def test_advisory_does_not_block_and_projections_rebuild(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            task_id, candidate = self._candidate(root, "Review advisory")
            with self.assertRaisesRegex(AuthorityError, "does not match"):
                record_review(
                    root,
                    task_id,
                    candidate_digest="0" * 64,
                    reviewer_id="stale-reviewer",
                    findings=[],
                    semantic=False,
                    operation_id="review:stale",
                )
            result = record_review(
                root, task_id, candidate_digest=candidate, reviewer_id="readonly-reviewer",
                findings=[{"category": "style", "severity": "blocking", "scope": ["core/**"]}],
                semantic=False, operation_id="review:advisory",
            )
            self.assertEqual(result["work_state"], "verified")
            with Authority(root) as authority:
                before = {
                    table: [tuple(row) for row in authority.all(f"SELECT * FROM {table}")]
                    for table in ("tasks", "runs", "actions", "events")
                }
            rebuild_projections(root)
            task_dir = task_status(root, task_id)["task"]["task_dir_name"]
            (root / "BOARD.md").unlink()
            (root / ".trellis/tasks" / task_dir / "task.json").unlink()
            self.assertEqual(task_status(root, task_id)["task"]["work_state"], "verified")
            rebuild_projections(root)
            self.assertTrue((root / "BOARD.md").is_file())
            with Authority(root) as authority:
                after = {
                    table: [tuple(row) for row in authority.all(f"SELECT * FROM {table}")]
                    for table in ("tasks", "runs", "actions", "events")
                }
            self.assertEqual(before, after)

    def test_blocking_findings_persist_and_third_review_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            task_id, candidate = self._candidate(root, "Review blocker")
            finding = {"category": "correctness", "severity": "blocking", "scope": ["core/**"], "requirement_ids": ["REVIEW-BLOCKER-REQ-001"]}
            first = record_review(
                root, task_id, candidate_digest=candidate, reviewer_id="reviewer-a",
                findings=[finding], semantic=True, operation_id="review:first",
            )
            self.assertEqual(first["work_state"], "running")
            with Authority(root) as authority:
                finding_id = authority.one("SELECT finding_id FROM findings")["finding_id"]
            with self.assertRaisesRegex(AuthorityError, "post-review"):
                close_finding(
                    root,
                    finding_id,
                    targeted_checks=[f"check:{task_id}:core:1:trellis.diff.check"],
                    full_regression=f"check:{task_id}:core:1:trellis.diff.check",
                    delta_paths=["core/fix.py"],
                )
            write(root / "core/fix.py", "fixed = True\n")
            record_green_checks(
                root,
                task_id,
                "core",
                ["trellis.diff.check"],
                attempt_no=2,
                phase="finding_closure",
            )
            record_green_checks(
                root,
                task_id,
                "core",
                ["trellis.diff.check"],
                attempt_no=3,
                phase="finding_closure",
            )
            close_finding(
                root,
                finding_id,
                targeted_checks=[f"check:{task_id}:core:2:trellis.diff.check"],
                full_regression=f"check:{task_id}:core:3:trellis.diff.check",
                delta_paths=["core/fix.py"],
            )
            second = record_review(
                root, task_id, candidate_digest=candidate_digest(root), reviewer_id="reviewer-b",
                findings=[finding], semantic=True, operation_id="review:second",
            )
            self.assertEqual(second["work_state"], "human_blocked")
            third = record_review(
                root, task_id, candidate_digest=candidate_digest(root), reviewer_id="reviewer-c",
                findings=[], semantic=True, operation_id="review:third",
            )
            self.assertEqual(third["rejected"], "third_review_prohibited")
            self.assertEqual(task_status(root, task_id)["task"]["work_state"], "human_blocked")

    def test_each_later_finding_requires_a_fresh_full_regression(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            task_id, candidate = self._candidate(root, "Review two blockers")
            findings = [
                {
                    "category": "correctness",
                    "severity": "blocking",
                    "scope": ["core/**"],
                    "requirement_ids": [f"TWO-BLOCKERS-REQ-00{index}"],
                }
                for index in (1, 2)
            ]
            record_review(
                root,
                task_id,
                candidate_digest=candidate,
                reviewer_id="reviewer-a",
                findings=findings,
                semantic=True,
                operation_id="review:two-blockers",
            )
            write(root / "core/fix.py", "fixed = True\n")
            for attempt in (2, 3, 4):
                record_green_checks(
                    root,
                    task_id,
                    "core",
                    ["trellis.diff.check"],
                    attempt_no=attempt,
                    phase="finding_closure",
                )
            with Authority(root) as authority:
                finding_ids = [
                    row["finding_id"]
                    for row in authority.all(
                        "SELECT finding_id FROM findings ORDER BY finding_id"
                    )
                ]
            close_finding(
                root,
                finding_ids[0],
                targeted_checks=[
                    f"check:{task_id}:core:2:trellis.diff.check"
                ],
                full_regression=f"check:{task_id}:core:3:trellis.diff.check",
                delta_paths=["core/fix.py"],
            )
            with self.assertRaisesRegex(AuthorityError, "fresh full regression"):
                close_finding(
                    root,
                    finding_ids[1],
                    targeted_checks=[
                        f"check:{task_id}:core:4:trellis.diff.check"
                    ],
                    full_regression=f"check:{task_id}:core:3:trellis.diff.check",
                    delta_paths=["core/fix.py"],
                )
            record_green_checks(
                root,
                task_id,
                "core",
                ["trellis.diff.check"],
                attempt_no=5,
                phase="finding_closure",
            )
            close_finding(
                root,
                finding_ids[1],
                targeted_checks=[
                    f"check:{task_id}:core:4:trellis.diff.check"
                ],
                full_regression=f"check:{task_id}:core:5:trellis.diff.check",
                delta_paths=["core/fix.py"],
            )


if __name__ == "__main__":
    unittest.main()
