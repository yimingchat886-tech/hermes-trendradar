from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from board import build_board
from common.task_activity import (
    TaskStateInvalid,
    classify_task_activity,
)
from test_loop_v1_task_projection import terminal_fixture


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def hsm_terminal_fixture(
    root: Path,
    *,
    schema_version: int = 1,
) -> tuple[Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Task Activity")
    git(repo, "config", "user.email", "activity@example.invalid")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "fixture")
    commit = git(repo, "rev-parse", "HEAD")
    task = repo / ".trellis" / "tasks" / "terminal-child"
    task.mkdir(parents=True)
    data = {
        "commit": commit,
        "id": "terminal-child",
        "meta": {
            "state_machine": {
                "current_state": (
                    "child_completed"
                    if schema_version == 2
                    else "child_archived"
                ),
                "kind": "child",
                **({"schema_version": 2} if schema_version == 2 else {}),
            },
            "workflow_mode": "harness_state_machine",
        },
        "name": "terminal-child",
        "status": "completed",
        "tier": "child",
    }
    (task / "task.json").write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )
    (task / "stage-report.md").write_text(
        "# Stage Report\n\n## Acceptance\n\n- [x] Verified.\n\n"
        "## Verification\n\n- unittest passed.\n",
        encoding="utf-8",
    )
    events = (
        "completion_signal_received",
        "commit_created",
        "child_completed" if schema_version == 2 else "child_archive_completed",
    )
    (task / "state-events.jsonl").write_text(
        "".join(json.dumps({"event": event}) + "\n" for event in events),
        encoding="utf-8",
    )
    return repo, task


def taskrun_terminal_proof_fixture(
    root: Path,
    *,
    mutation: tuple[tuple[str, ...], object] | None = None,
    commit_proof: bool = True,
) -> tuple[Path, Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Task Activity")
    git(repo, "config", "user.email", "activity@example.invalid")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "fixture")
    work_commit = git(repo, "rev-parse", "HEAD")
    work_tree = git(repo, "rev-parse", "HEAD^{tree}")

    task = repo / ".trellis/tasks/08-01-terminal-proof-fixture"
    task.mkdir(parents=True)
    run_id = "task-terminal-proof-fixture"
    task_value = {
        "commit": work_commit,
        "completedAt": "2026-08-02",
        "id": "terminal-proof-fixture",
        "meta": {
            "task_run": {
                "authority": "sqlite",
                "id": run_id,
                "projection": True,
                "state": "closed",
            },
            "taskrun_strategy": "single",
            "workflow_mode": "taskrun_v1",
        },
        "name": "terminal-proof-fixture",
        "status": "completed",
        "tier": "light",
    }
    task_path = task / "task.json"
    task_path.write_text(json.dumps(task_value, indent=2) + "\n", encoding="utf-8")
    (task / "prd.md").write_text(
        "# Terminal proof fixture\n\n"
        "## REQ-ID\n\n- FIXTURE-REQ-001: Fixture requirement.\n\n"
        "## Verification Commands\n\n- `python3 -m unittest fixture`\n",
        encoding="utf-8",
    )
    git(repo, "add", ".trellis/tasks")
    git(repo, "commit", "-m", "terminal projection")
    projection_commit = git(repo, "rev-parse", "HEAD")
    source_tree = git(repo, "rev-parse", "HEAD^{tree}")
    task_digest = sha256(task_path.read_bytes()).hexdigest()
    tail_digests = [character * 64 for character in "bcde"]
    event_types = [
        "terminal_recorded",
        "close_prepared",
        "closed",
        "projection_checkpointed",
    ]
    event_time = "2026-08-02T00:00:00Z"
    checkpoint = {
        "digest": task_digest,
        "source_position": 4,
        "status": "current",
        "updated_at": event_time,
    }
    proof = {
        "classification": "closed/evidence-only",
        "disposition": {
            "closed": True,
            "created_at": event_time,
            "terminal_event_digest": tail_digests[0],
            "terminal_evidence_digest": "sha256:" + "9" * 64,
            "value": "completed",
        },
        "git": {
            "base_commit": work_commit,
            "commit_reconciliation_digest": "sha256:" + "8" * 64,
            "source_worktree_head": projection_commit,
            "source_worktree_tree": source_tree,
            "terminal_projection_commit": projection_commit,
            "terminal_projection_commit_reachable_from_source_head": True,
            "work_commit": work_commit,
            "work_commit_reachable_from_source_head": True,
            "work_tree": work_tree,
        },
        "projection": {
            "authority_projection_sha256": f"sha256:{task_digest}",
            "board_checkpoint_status": "missing",
            "close_checkpoint": dict(checkpoint),
            "close_projection_digest": f"sha256:{task_digest}",
            "task_json_checkpoint": dict(checkpoint),
            "task_json_matches_authority": True,
            "task_run_state": "closed",
            "task_status": "completed",
        },
        "run": {
            "authority_digest": "sha256:" + "6" * 64,
            "authority_sqlite_sha256": "sha256:" + "7" * 64,
            "authority_sqlite_size": 4096,
            "task_run_id": run_id,
        },
        "schema_version": "taskrun-terminal-proof-v1",
        "task": {
            "task_dir_name": task.name,
            "task_id": task_value["id"],
            "task_json_sha256": f"sha256:{task_digest}",
        },
        "terminal_event_chain": {
            "event_count": 4,
            "event_type_counts": {event_type: 1 for event_type in event_types},
            "first_event_digest": tail_digests[0],
            "head_event_digest": tail_digests[-1],
            "head_position": 4,
            "terminal_to_head": [
                {
                    "created_at": event_time,
                    "event_digest": digest,
                    "event_type": event_type,
                    "position": position,
                    "previous_digest": (
                        "a" * 64 if position == 1 else tail_digests[position - 2]
                    ),
                }
                for position, (event_type, digest) in enumerate(
                    zip(event_types, tail_digests), start=1
                )
            ],
        },
    }
    if mutation is not None:
        path, value = mutation
        target = proof
        for key in path[:-1]:
            target = target[key]
        if value is None:
            del target[path[-1]]
        else:
            target[path[-1]] = value
    proof_path = task / "terminal-proof.json"
    proof_path.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if commit_proof:
        git(repo, "add", str(proof_path.relative_to(repo)))
        git(repo, "commit", "-m", "register terminal proof")
    return repo, task, proof_path


class TaskActivityTests(unittest.TestCase):
    def test_committed_taskrun_terminal_proof_is_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task, _ = taskrun_terminal_proof_fixture(Path(tmp))
            activity = classify_task_activity(task, repo)
            self.assertFalse(activity.active)
            self.assertEqual(activity.reason, "verified_taskrun_evidence_only")
            self.assertEqual(activity.state, "closed")
            self.assertNotIn(task.name, build_board(repo))

    def test_taskrun_terminal_proof_rejection_matrix(self) -> None:
        cases = (
            ("partial", (("run", "authority_digest"), None)),
            ("task mismatch", (("task", "task_id"), "foreign")),
            ("run mismatch", (("run", "task_run_id"), "foreign")),
            ("nonterminal", (("disposition", "closed"), False)),
            ("non-closed", (("classification",), "active")),
            (
                "broken chain",
                (("terminal_event_chain", "terminal_to_head"), []),
            ),
            (
                "projection mismatch",
                (("projection", "authority_projection_sha256"), "sha256:" + "0" * 64),
            ),
            ("stale", (("git", "source_worktree_head"), "f" * 40)),
            ("unreachable git", (("git", "work_commit"), "f" * 40)),
        )
        for name, mutation in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repo, task, _ = taskrun_terminal_proof_fixture(
                    Path(tmp), mutation=mutation
                )
                with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                    classify_task_activity(task, repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo, task, proof = taskrun_terminal_proof_fixture(Path(tmp))
            proof.unlink()
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo, task, _ = taskrun_terminal_proof_fixture(
                Path(tmp), commit_proof=False
            )
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo, task, proof = taskrun_terminal_proof_fixture(Path(tmp))
            proof.write_bytes(proof.read_bytes() + b"\n")
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo, task, _ = taskrun_terminal_proof_fixture(Path(tmp))
            task_json = json.loads((task / "task.json").read_text(encoding="utf-8"))
            authority = (
                repo
                / ".trellis/.runtime/taskrun/runs"
                / task_json["meta"]["task_run"]["id"]
                / "authority.sqlite3"
            )
            authority.parent.mkdir(parents=True)
            authority.write_bytes(b"not sqlite")
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

    def test_hsm_evidence_only_requires_no_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task = hsm_terminal_fixture(Path(tmp))
            idle = classify_task_activity(task, repo)
            self.assertFalse(idle.active)
            self.assertEqual(idle.reason, "verified_hsm_evidence_only")

            sessions = repo / ".trellis" / ".runtime" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "codex.json").write_text(
                json.dumps({"current_task": ".trellis/tasks/terminal-child"}),
                encoding="utf-8",
            )
            active = classify_task_activity(task, repo)
            self.assertTrue(active.active)
            self.assertEqual(active.reason, "terminal_task_has_active_session")

    def test_schema_v2_child_completion_is_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task = hsm_terminal_fixture(Path(tmp), schema_version=2)
            activity = classify_task_activity(task, repo)
            self.assertFalse(activity.active)
            self.assertEqual(activity.state, "child_completed")

    def test_hsm_conflict_is_task_state_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task = hsm_terminal_fixture(Path(tmp))
            value = json.loads((task / "task.json").read_text(encoding="utf-8"))
            value["status"] = "in_progress"
            (task / "task.json").write_text(
                json.dumps(value, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

    def test_active_hsm_with_terminal_status_is_task_state_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task = hsm_terminal_fixture(Path(tmp), schema_version=2)
            path = task / "task.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["meta"]["state_machine"]["current_state"] = (
                "child_waiting_completion_signal"
            )
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TaskStateInvalid, "terminal status conflicts with active HSM state"
            ):
                classify_task_activity(task, repo)

    def test_terminal_loop_ledger_is_evidence_only_before_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task, _ = terminal_fixture(Path(tmp))
            activity = classify_task_activity(task, repo)
            self.assertFalse(activity.active)
            self.assertEqual(activity.state, "cancelled")
            self.assertEqual(
                json.loads((task / "task.json").read_text(encoding="utf-8"))[
                    "status"
                ],
                "in_progress",
            )

    def test_malformed_loop_ledger_and_unverified_projection_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task, ledger = terminal_fixture(Path(tmp))
            ledger.path.write_bytes(b"not sqlite")
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task = repo / ".trellis" / "tasks" / "forged-loop"
            task.mkdir(parents=True)
            (task / "task.json").write_text(
                json.dumps(
                    {
                        "id": "forged-loop",
                        "meta": {
                            "loop_v1_terminal": {
                                "run_id": "forged-loop",
                                "terminal_kind": "cancelled",
                            },
                            "workflow_mode": "loop_v1",
                        },
                        "name": "forged-loop",
                        "status": "cancelled",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TaskStateInvalid, "TASK_STATE_INVALID"):
                classify_task_activity(task, repo)

    def test_default_task_uses_normal_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task = repo / ".trellis" / "tasks" / "legacy"
            task.mkdir(parents=True)
            path = task / "task.json"
            path.write_text('{"status":"planning"}\n', encoding="utf-8")
            self.assertTrue(classify_task_activity(task, repo).active)
            path.write_text('{"status":"completed"}\n', encoding="utf-8")
            self.assertFalse(classify_task_activity(task, repo).active)


if __name__ == "__main__":
    unittest.main()
