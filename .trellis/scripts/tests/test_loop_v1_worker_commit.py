from __future__ import annotations

import copy
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from loop_v1 import (
    DirtOverlapError,
    FreshnessError,
    GitStateError,
    ParentLedger,
    ParentValidationError,
    ReviewError,
    WorkerCommitError,
    accept_child_result,
    approve_start_request,
    commit_reviewed_candidate,
    create_child_worktree,
    create_start_request,
    issue_child_packet,
    observe_worker_candidate,
    record_context_revision,
    record_precommit_review,
    scan_repository_dirt,
    validate_child_candidate,
)
from loop_v1.worker_commit import _run_parent_check, release_owned_worktree


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def initialize_git_repo(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Loop Test")
    git(root, "config", "user.email", "loop@example.test")
    (root / ".gitignore").write_text(".trellis/.runtime/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src/app.txt").write_text("base\n", encoding="utf-8")
    (root / "tests/check.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", ".gitignore", "src/app.txt", "tests/check.txt")
    git(root, "commit", "-m", "base")
    head = git(root, "rev-parse", "HEAD")
    return head, git(root, "rev-parse", f"{head}^{{tree}}")


def envelope(base_head: str, dirt_digest: str) -> dict[str, object]:
    return {
        "goal": "Validate and commit one isolated child candidate.",
        "requirements": [
            {
                "requirement_id": "REQ-1",
                "required": True,
                "acceptance": ["the parent validates the exact candidate tree"],
                "dependencies": [],
            }
        ],
        "acceptance": ["parent validation and exact-tree review pass"],
        "allowed_scope": ["Loop v1 worker commit"],
        "allowed_touches": ["src/**", "tests/**"],
        "local_effects": ["write ignored SQLite state", "write local Git files"],
        "read_only_external_inputs": [],
        "resources": [{"resource_key": "repo", "mode": "exclusive", "capacity": 1}],
        "risks": ["canonical dirt overlap"],
        "prohibited_actions": ["push", "release", "read secret contents"],
        "initial_child_graph": [
            {
                "child_id": "child-a",
                "requirements": ["REQ-1"],
                "touches": ["src/**"],
                "resources": ["repo"],
                "depends_on": [],
            }
        ],
        "default_parallel": 1,
        "max_parallel": 1,
        "base_branch": "main",
        "base_head": base_head,
        "verification_policy": {"focused": True, "regression": True},
        "review_policy": {"exact_tree": True},
        "retry_policy": {"same_problem_rounds": 3},
        "approved_agent_surfaces": ["codex"],
        "worker_capacity": 1,
        "reviewer_capacity": 1,
        "token_budget": 10000,
        "cost_budget": None,
        "dirty_path_fingerprint": dirt_digest,
        "conformance_receipt": "receipt-worker-commit-fixture",
    }


def context(base_head: str, base_tree: str) -> dict[str, object]:
    start = envelope(base_head, "unused")
    return {
        "requirements": [
            {
                **start["requirements"][0],
                "revision": 1,
                "coverage_state": "uncovered",
            }
        ],
        "graph": copy.deepcopy(start["initial_child_graph"]),
        "decisions": [{"id": "DEC-1", "summary": "The parent owns Git mutation."}],
        "facts": [{"id": "FACT-1", "summary": "Loop admission is disabled."}],
        "integration": {
            "base_branch": "main",
            "base_head": base_head,
            "base_tree_id": base_tree,
            "integration_head": base_head,
            "integration_tree_id": base_tree,
        },
        "risks": ["canonical dirt overlap"],
        "prohibitions": ["push", "release", "read secret contents"],
        "context_slices": [
            {
                "slice_id": "slice-public",
                "kind": "spec",
                "source_ref": "worker-commit-contract",
                "digest": "slice-worker-commit",
                "visibility": "public",
                "excerpt": "The worker cannot stage or commit.",
            }
        ],
        "dependency_state": {"REQ-1": "ready"},
    }


def setup_runtime(
    root: Path,
    *,
    test_command: str = "git diff --check",
    initial_dirt: tuple[str, str] | None = None,
    create_worktree: bool = True,
) -> dict[str, object]:
    repo = root / "repo"
    base_head, base_tree = initialize_git_repo(repo)
    if initial_dirt is not None:
        path, contents = initial_dirt
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    dirt = scan_repository_dirt(repo)
    ledger, lease = ParentLedger.initialize(
        repo,
        "worker-commit-run",
        selector="loop_v1",
        writer_id="parent-writer",
    )
    request = create_start_request(
        ledger,
        lease,
        request_id="start-1",
        envelope=envelope(base_head, dirt["digest"]),
    )
    approve_start_request(
        ledger,
        lease,
        request_id="start-1",
        request_digest=request["request_digest"],
        response_identity="user-response-1",
        response_at="2026-07-13T18:00:00Z",
        direct_user_action=True,
    )
    canonical_context = context(base_head, base_tree)
    record_context_revision(
        ledger,
        lease,
        request_id="start-1",
        revision_id="context-1",
        reason="initial worker commit context",
        context=canonical_context,
    )
    packet = issue_child_packet(
        ledger,
        lease,
        {
            "packet_id": "packet-1",
            "child_id": "child-a",
            "requirements": ["REQ-1"],
            "forbidden_touches": ["src/forbidden.txt"],
            "tests": [test_command],
            "context_slice_ids": ["slice-public"],
            "parent_contact": "channel:worker-commit-run",
            "result_deadline": "2026-07-13T20:00:00Z",
            "result_lease": "result-lease-1",
            "attempt": 1,
            "round": 1,
        },
    )
    worktree = root / "worktrees/child-a"
    worktree_result = None
    if create_worktree:
        worktree_result = create_child_worktree(
            ledger,
            lease,
            operation_id="worktree-create-1",
            child_id="child-a",
            packet_id="packet-1",
            worktree=worktree,
            branch="loop-v1/child-a",
            integration_worktree=root / "integration",
        )
    return {
        "base_head": base_head,
        "base_tree": base_tree,
        "context": canonical_context,
        "dirt": dirt,
        "ledger": ledger,
        "lease": lease,
        "packet": packet,
        "repo": repo,
        "worktree": worktree,
        "worktree_result": worktree_result,
    }


def accepted_result(runtime: dict[str, object]) -> dict[str, object]:
    packet = runtime["packet"]
    observation = observe_worker_candidate(
        runtime["worktree"], base_head=runtime["base_head"]
    )
    result = {
        "result_id": "result-1",
        "packet_id": packet["packet_id"],
        "child_id": packet["child_id"],
        "actual_touches": observation["actual_touches"],
        "diff_identity": observation["diff_identity"],
        "base_head": observation["base_head"],
        "base_tree_id": observation["base_tree_id"],
        "result_tree_id": observation["tree_id"],
        "commands": [
            {
                "command": packet["tests"][0],
                "status": "passed",
                "output_digest": "self-check-digest",
            }
        ],
        "coverage": ["REQ-1"],
        "risks": [],
        "findings": [],
        "artifacts": [
            {"path": path, "digest": f"artifact:{index}"}
            for index, path in enumerate(observation["actual_touches"], start=1)
        ],
        **packet["identity"],
    }
    accept_child_result(runtime["ledger"], runtime["lease"], result)
    return result


def validate(runtime: dict[str, object], result: dict[str, object]):
    return validate_child_candidate(
        runtime["ledger"],
        runtime["lease"],
        validation_id="validation-1",
        result=result,
        worktree=runtime["worktree"],
    )


def child_state(runtime: dict[str, object]) -> str:
    connection = sqlite3.connect(runtime["ledger"].reference()["sqlite_uri"], uri=True)
    try:
        return connection.execute(
            "SELECT state FROM child_operations WHERE child_id = 'child-a'"
        ).fetchone()[0]
    finally:
        connection.close()


class LoopV1WorkerCommitTests(unittest.TestCase):
    def test_parent_check_uses_only_controlled_locator_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "trellis"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with mock.patch.dict(
                os.environ,
                {
                    "AMBIENT_ONLY": "must-not-leak",
                    "TRELLIS_CLI": str(executable),
                    "TRELLIS_LOOP_V1_INSTALLED_ROOT": str(root / "installed"),
                },
            ):
                result = _run_parent_check(
                    root,
                    "test -z \"$AMBIENT_ONLY\" "
                    "&& test \"$TRELLIS_LOOP_V1_INSTALLED_ROOT\" = \"$PWD/installed\" "
                    "&& test \"$(command -v trellis)\" = \"$TRELLIS_CLI\"",
                )

            self.assertEqual(result["status"], "passed")

    def test_parent_creates_isolated_worktree_and_replays_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(
                Path(tmp), initial_dirt=("notes/local.txt", "private\n")
            )
            outcome = runtime["worktree_result"]

            self.assertEqual(outcome["base_head"], runtime["base_head"])
            self.assertEqual(outcome["dirt_snapshot"]["paths"], ["notes/local.txt"])
            self.assertNotIn("private", repr(outcome["dirt_snapshot"]))
            self.assertFalse(any(outcome["worker_permissions"].values()))
            replay = create_child_worktree(
                runtime["ledger"],
                runtime["lease"],
                operation_id="worktree-create-1",
                child_id="child-a",
                packet_id="packet-1",
                worktree=runtime["worktree"],
                branch="loop-v1/child-a",
                integration_worktree=Path(tmp) / "integration",
            )
            self.assertEqual(replay, outcome)

            with self.assertRaises(WorkerCommitError):
                create_child_worktree(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="bad-worktree",
                    child_id="child-a",
                    packet_id="packet-1",
                    worktree=runtime["repo"],
                    branch="loop-v1/bad",
                )
            for blocked, protected in (
                (Path(tmp) / "integration", "integration"),
                (Path(tmp) / "worktrees/child-b", "sibling"),
            ):
                with (
                    self.subTest(protected=protected),
                    self.assertRaises(WorkerCommitError),
                ):
                    create_child_worktree(
                        runtime["ledger"],
                        runtime["lease"],
                        operation_id=f"bad-{protected}-worktree",
                        child_id="child-a",
                        packet_id="packet-1",
                        worktree=blocked,
                        branch=f"loop-v1/bad-{protected}",
                        integration_worktree=(
                            blocked if protected == "integration" else None
                        ),
                        sibling_worktrees=(
                            (blocked,) if protected == "sibling" else ()
                        ),
                    )

    def test_initial_in_scope_dirt_blocks_worktree_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(
                Path(tmp),
                initial_dirt=("src/user-file.txt", "user dirt\n"),
                create_worktree=False,
            )
            user_path = runtime["repo"] / "src/user-file.txt"
            before = user_path.read_bytes()

            with self.assertRaises(DirtOverlapError):
                create_child_worktree(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="worktree-create-1",
                    child_id="child-a",
                    packet_id="packet-1",
                    worktree=runtime["worktree"],
                    branch="loop-v1/child-a",
                )

            self.assertEqual(user_path.read_bytes(), before)
            self.assertFalse(runtime["worktree"].exists())

    def test_parent_validates_tracked_and_untracked_candidate_without_staging(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "changed\n", encoding="utf-8"
            )
            (runtime["worktree"] / "src/new.txt").write_text("new\n", encoding="utf-8")
            result = accepted_result(runtime)

            with self.assertRaises(ParentValidationError):
                validate_child_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    validation_id="validation-wrong-worktree",
                    result=result,
                    worktree=runtime["repo"],
                )

            outcome = validate(runtime, result)
            replay = validate(runtime, result)

            self.assertEqual(outcome, replay)
            self.assertEqual(outcome["actual_touches"], ["src/app.txt", "src/new.txt"])
            self.assertEqual(outcome["allowed_touches"], ["src/**"])
            self.assertEqual(outcome["forbidden_touches"], ["src/forbidden.txt"])
            self.assertEqual(outcome["untracked_files"], ["src/new.txt"])
            self.assertEqual(outcome["parent_checks"][0]["status"], "passed")
            self.assertEqual(
                git(runtime["worktree"], "diff", "--cached", "--name-only"), ""
            )
            self.assertEqual(child_state(runtime), "candidate_validated")

    def test_worker_staging_or_committing_is_rejected(self) -> None:
        for mutation in ("stage", "commit"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                runtime = setup_runtime(Path(tmp))
                (runtime["worktree"] / "src/app.txt").write_text(
                    "changed\n", encoding="utf-8"
                )
                git(runtime["worktree"], "add", "src/app.txt")
                if mutation == "commit":
                    git(runtime["worktree"], "commit", "-m", "worker must not commit")
                result = accepted_result(runtime)

                with self.assertRaises(ParentValidationError):
                    validate(runtime, result)
                self.assertEqual(child_state(runtime), "result_validated")

    def test_canonical_dirt_overlap_blocks_without_touching_user_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            result = accepted_result(runtime)
            canonical_path = runtime["repo"] / "src/app.txt"
            canonical_path.write_text("user dirt\n", encoding="utf-8")
            before = canonical_path.read_bytes()

            with self.assertRaises(DirtOverlapError):
                validate(runtime, result)

            self.assertEqual(canonical_path.read_bytes(), before)
            self.assertEqual(child_state(runtime), "result_validated")

    def test_late_canonical_dirt_rechecks_the_complete_child_scope(self) -> None:
        for boundary in ("review", "commit"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                runtime = setup_runtime(Path(tmp))
                (runtime["worktree"] / "src/app.txt").write_text(
                    "candidate\n", encoding="utf-8"
                )
                validation = validate(runtime, accepted_result(runtime))
                if boundary == "commit":
                    record_precommit_review(
                        runtime["ledger"],
                        runtime["lease"],
                        review_id="review-1",
                        validation_id=validation["validation_id"],
                        reviewer_identity="model:codex",
                        verdict="passed",
                        required_findings=[],
                        advisory_findings=[],
                        dispositions=[],
                    )
                dirt = runtime["repo"] / "src/other-user-file.txt"
                dirt.write_text("user dirt outside actual touches\n", encoding="utf-8")

                with self.assertRaises(DirtOverlapError):
                    if boundary == "review":
                        record_precommit_review(
                            runtime["ledger"],
                            runtime["lease"],
                            review_id="review-1",
                            validation_id=validation["validation_id"],
                            reviewer_identity="model:codex",
                            verdict="passed",
                            required_findings=[],
                            advisory_findings=[],
                            dispositions=[],
                        )
                    else:
                        commit_reviewed_candidate(
                            runtime["ledger"],
                            runtime["lease"],
                            operation_id="commit-1",
                            review_id="review-1",
                            message="child candidate",
                            author_name="Loop Parent",
                            author_email="parent@example.test",
                        )

    def test_parent_rejects_claimed_evidence_and_failed_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            result = accepted_result(runtime)
            result["result_tree_id"] = runtime["base_tree"]
            with self.assertRaises(ParentValidationError):
                validate(runtime, result)

        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(
                Path(tmp), test_command="test -f parent-only-marker"
            )
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            result = accepted_result(runtime)
            with self.assertRaises(ParentValidationError):
                validate(runtime, result)
            self.assertEqual(child_state(runtime), "result_validated")

    def test_stale_context_and_post_review_edit_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            result = accepted_result(runtime)
            changed_context = copy.deepcopy(runtime["context"])
            changed_context["decisions"].append(
                {"id": "DEC-2", "summary": "Invalidate prior child evidence."}
            )
            record_context_revision(
                runtime["ledger"],
                runtime["lease"],
                request_id="start-1",
                revision_id="context-2",
                reason="semantic authority change",
                context=changed_context,
            )
            with self.assertRaises(FreshnessError):
                validate(runtime, result)

        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            validation = validate(runtime, accepted_result(runtime))
            with self.assertRaises(ReviewError):
                record_precommit_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="review-1",
                    validation_id=validation["validation_id"],
                    reviewer_identity="model:codex",
                    verdict="passed",
                    required_findings=[],
                    advisory_findings=[
                        {"finding_id": "ADV-1", "summary": "Keep the test narrow."}
                    ],
                    dispositions=[],
                )
            record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[
                    {"finding_id": "ADV-1", "summary": "Keep the test narrow."}
                ],
                dispositions=[
                    {
                        "finding_id": "ADV-1",
                        "disposition": "accepted",
                        "rationale": "The focused check remains in the packet.",
                    }
                ],
            )
            (runtime["worktree"] / "src/app.txt").write_text(
                "changed after review\n", encoding="utf-8"
            )
            with self.assertRaises(ReviewError):
                commit_reviewed_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="commit-1",
                    review_id="review-1",
                    message="child candidate",
                    author_name="Loop Parent",
                    author_email="parent@example.test",
                )

    def test_required_review_finding_blocks_parent_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            validation = validate(runtime, accepted_result(runtime))
            authority_before = runtime["ledger"].authority_digest()
            with self.assertRaisesRegex(
                ReviewError, "failed review requires at least one required finding"
            ):
                record_precommit_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="review-empty-failure",
                    validation_id=validation["validation_id"],
                    reviewer_identity="model:codex",
                    verdict="failed",
                    required_findings=[],
                    advisory_findings=[],
                    dispositions=[],
                )
            self.assertEqual(runtime["ledger"].authority_digest(), authority_before)
            review = record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="failed",
                required_findings=[
                    {"finding_id": "REQ-FIND-1", "summary": "Fix correctness."}
                ],
                advisory_findings=[],
                dispositions=[],
            )

            self.assertEqual(review["status"], "review_blocked")
            self.assertEqual(child_state(runtime), "review_blocked")
            with self.assertRaises(ReviewError):
                commit_reviewed_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="commit-1",
                    review_id="review-1",
                    message="blocked child candidate",
                    author_name="Loop Parent",
                    author_email="parent@example.test",
                )

    def test_parent_commits_only_the_green_exact_tree_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            (runtime["worktree"] / "src/new.txt").write_text("new\n", encoding="utf-8")
            validation = validate(runtime, accepted_result(runtime))
            review = record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )

            outcome = commit_reviewed_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="commit-1",
                review_id=review["review_id"],
                message="child candidate",
                author_name="Loop Parent",
                author_email="parent@example.test",
            )
            replay = commit_reviewed_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="commit-1",
                review_id=review["review_id"],
                message="child candidate",
                author_name="Loop Parent",
                author_email="parent@example.test",
            )

            self.assertEqual(outcome, replay)
            self.assertEqual(outcome["tree_id"], validation["tree_id"])
            self.assertEqual(outcome["staged_paths"], validation["actual_touches"])
            self.assertEqual(
                git(runtime["worktree"], "rev-parse", "HEAD^"), runtime["base_head"]
            )
            self.assertEqual(
                git(runtime["repo"], "rev-parse", "HEAD"), runtime["base_head"]
            )
            self.assertEqual(git(runtime["worktree"], "status", "--porcelain"), "")
            self.assertEqual(child_state(runtime), "committed")

    def test_released_child_replays_from_git_authority_and_partial_state_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = setup_runtime(Path(tmp))
            (runtime["worktree"] / "src/app.txt").write_text(
                "candidate\n", encoding="utf-8"
            )
            validation = validate(runtime, accepted_result(runtime))
            review = record_precommit_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="review-1",
                validation_id=validation["validation_id"],
                reviewer_identity="model:codex",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )
            committed = commit_reviewed_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="commit-1",
                review_id=review["review_id"],
                message="child candidate",
                author_name="Loop Parent",
                author_email="parent@example.test",
            )

            released = release_owned_worktree(
                runtime["ledger"],
                operation_id="commit-1",
                runtime_root=Path(tmp) / "worktrees",
            )
            self.assertTrue(released["removed"])
            self.assertFalse(runtime["worktree"].exists())
            replay = commit_reviewed_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="commit-1",
                review_id=review["review_id"],
                message="child candidate",
                author_name="Loop Parent",
                author_email="parent@example.test",
            )
            self.assertEqual(replay, committed)
            self.assertEqual(
                create_child_worktree(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="worktree-create-1",
                    child_id="child-a",
                    packet_id="packet-1",
                    worktree=runtime["worktree"],
                    branch="loop-v1/child-a",
                    integration_worktree=Path(tmp) / "integration",
                ),
                runtime["worktree_result"],
            )

            runtime["worktree"].mkdir(parents=True)
            with self.assertRaisesRegex(GitStateError, "partial path/registration"):
                commit_reviewed_candidate(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="commit-1",
                    review_id=review["review_id"],
                    message="child candidate",
                    author_name="Loop Parent",
                    author_email="parent@example.test",
                )


if __name__ == "__main__":
    unittest.main()
