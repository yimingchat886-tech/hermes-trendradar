from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common.task_activity import classify_task_activity
from loop_v1.context import _insert_committed_operation
from loop_v1.ledger import ParentLedger, _digest_json, _now
from loop_v1.task_projection import (
    TaskProjectionError,
    project_terminal,
    projection_status,
)
from loop_v1.task_archive import TaskArchiveError, retire_task_evidence


RUN_ID = "terminal-projection-run"


def terminal_fixture(
    root: Path,
    status: str = "cancelled",
    children: dict[str, str] | None = None,
):
    repo = root / "repo"
    task_dir = repo / ".trellis" / "tasks" / f"07-29-{RUN_ID}"
    task_dir.mkdir(parents=True)
    child_states = children or {}
    parent_ref = task_dir.name
    task = {
        "children": [f"07-29-{child_id}" for child_id in child_states],
        "id": RUN_ID,
        "meta": {"workflow_mode": "loop_v1"},
        "name": RUN_ID,
        "status": "in_progress",
        "tier": "parent",
    }
    (task_dir / "task.json").write_text(
        json.dumps(task, indent=2) + "\n",
        encoding="utf-8",
    )
    (task_dir / "prd.md").write_text("preserved PRD\n", encoding="utf-8")
    for child_id in child_states:
        child_dir = repo / ".trellis" / "tasks" / f"07-29-{child_id}"
        child_dir.mkdir()
        (child_dir / "task.json").write_text(
            json.dumps(
                {
                    "id": child_id,
                    "meta": {"workflow_mode": "loop_v1"},
                    "name": child_id,
                    "parent": parent_ref,
                    "status": "planning",
                    "tier": "child",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (child_dir / "stage-report.md").write_text(
            f"preserved {child_id}\n",
            encoding="utf-8",
        )
    scripts = repo / ".trellis" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "board.py").write_text(
        "from pathlib import Path\n"
        "Path('BOARD.md').write_text('generated board\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Projection Test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "projection@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    ledger, lease = ParentLedger.initialize(
        repo,
        RUN_ID,
        selector="test",
        writer_id="projection-writer",
    )
    operation_kind = {
        "archived": "parent_archive",
        "cancelled": "parent_cancel",
        "revoked": "execution_binding_revocation",
    }[status]
    supplied = {"status": status}
    if status == "cancelled":
        supplied["requested_at"] = "2026-07-29T18:30:00Z"
    elif status == "revoked":
        supplied["revoked_at"] = "2026-07-29T18:31:00Z"
    with ledger._write_transaction(
        lease,
        enforce_execution_qualification=False,
    ) as connection:
        now = _now()
        for child_id, child_state in child_states.items():
            connection.execute(
                """
                INSERT INTO child_operations (
                    child_id, run_id, coverage_json, context_digest,
                    attempt, round, state, epoch, updated_at
                ) VALUES (?, ?, '{}', ?, 1, 1, ?, ?, ?)
                """,
                (child_id, RUN_ID, "a" * 64, child_state, lease.epoch, now),
            )
        connection.execute(
            "UPDATE parent_runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (status, now, RUN_ID),
        )
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=f"terminal:{status}",
            kind=operation_kind,
            epoch=lease.epoch,
            input_fingerprint=_digest_json(supplied),
            outcome=supplied,
            event_type=f"parent_{status}",
            created_at=now,
        )
    return repo, task_dir, ledger


class LoopV1TaskProjectionTests(unittest.TestCase):
    def test_terminal_projection_is_cas_bound_preserving_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task_dir, _ = terminal_fixture(Path(tmp))
            prd_before = (task_dir / "prd.md").read_bytes()
            stale = projection_status(repo, RUN_ID)
            self.assertEqual(stale["status"], "stale")

            with self.assertRaisesRegex(TaskProjectionError, "task digest changed"):
                project_terminal(
                    repo,
                    RUN_ID,
                    expected_authority_digest=str(stale["authority_digest"]),
                    expected_task_digest="sha256:" + "0" * 64,
                )

            projected = project_terminal(
                repo,
                RUN_ID,
                expected_authority_digest=str(stale["authority_digest"]),
                expected_task_digest=str(stale["task_digest"]),
            )
            replay = project_terminal(
                repo,
                RUN_ID,
                expected_authority_digest=str(projected["authority_digest"]),
                expected_task_digest=str(projected["task_digest"]),
            )

            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(projected["status"], "current")
            self.assertEqual(replay, projected)
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(
                task["meta"]["loop_v1_terminal"]["terminal_kind"],
                "cancelled",
            )
            self.assertEqual((task_dir / "prd.md").read_bytes(), prd_before)
            self.assertEqual(
                (repo / "BOARD.md").read_text(encoding="utf-8"),
                "generated board\n",
            )

    def test_projection_maps_archived_and_revoked_terminal_kinds(self) -> None:
        for terminal, task_status in (
            ("archived", "completed"),
            ("revoked", "cancelled"),
        ):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as tmp:
                repo, task_dir, _ = terminal_fixture(Path(tmp), terminal)
                status = projection_status(repo, RUN_ID)
                project_terminal(
                    repo,
                    RUN_ID,
                    expected_authority_digest=str(status["authority_digest"]),
                    expected_task_digest=str(status["task_digest"]),
                )
                task = json.loads(
                    (task_dir / "task.json").read_text(encoding="utf-8")
                )
                self.assertEqual(task["status"], task_status)
                self.assertEqual(
                    task["meta"]["loop_v1_terminal"]["terminal_kind"],
                    terminal,
                )

    def test_projection_accepts_one_archive_location_and_rejects_ambiguity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task_dir, _ = terminal_fixture(Path(tmp))
            month = repo / ".trellis" / "tasks" / "archive" / "2026-07"
            month.mkdir(parents=True)
            archived = month / task_dir.name
            shutil.move(str(task_dir), archived)

            unique = projection_status(repo, RUN_ID)
            self.assertEqual(unique["status"], "stale")
            shutil.copytree(archived, task_dir)
            ambiguous = projection_status(repo, RUN_ID)
            self.assertEqual(ambiguous["status"], "conflict")
            self.assertEqual(ambiguous["reason"], "task_location_ambiguous")

    def test_current_terminal_projection_retires_and_replays_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task_dir, _ = terminal_fixture(Path(tmp))
            stale = projection_status(repo, RUN_ID)
            current = project_terminal(
                repo,
                RUN_ID,
                expected_authority_digest=str(stale["authority_digest"]),
                expected_task_digest=str(stale["task_digest"]),
            )
            request = {
                "expected_authority_digest": str(current["authority_digest"]),
                "expected_task_digest": str(current["task_digest"]),
                "operator_state": {"pending_action": None},
                "commit_enabled": False,
            }

            with self.assertRaisesRegex(TaskArchiveError, "no pending action"):
                retire_task_evidence(
                    repo,
                    RUN_ID,
                    **{**request, "operator_state": {"pending_action": {"id": "pending"}}},
                )
            self.assertTrue(task_dir.is_dir())

            retired = retire_task_evidence(repo, RUN_ID, **request)
            replay = retire_task_evidence(repo, RUN_ID, **request)

            archived = repo / retired["location"]
            self.assertFalse(task_dir.exists())
            self.assertTrue(archived.is_dir())
            self.assertFalse(retired["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(projection_status(repo, RUN_ID)["status"], "current")

    def test_terminal_projection_and_retirement_include_exact_loop_children(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, task_dir, _ = terminal_fixture(
                Path(tmp),
                children={
                    "integrated-child": "integrated",
                    "cancelled-child": "cancelled",
                },
            )
            stale = projection_status(repo, RUN_ID)
            self.assertEqual(
                [item["status"] for item in stale["child_projections"]],
                ["stale", "stale"],
            )
            current = project_terminal(
                repo,
                RUN_ID,
                expected_authority_digest=str(stale["authority_digest"]),
                expected_task_digest=str(stale["task_digest"]),
            )
            self.assertEqual(current["status"], "current")
            for child_id, expected in (
                ("integrated-child", "completed"),
                ("cancelled-child", "cancelled"),
            ):
                child_dir = repo / ".trellis" / "tasks" / f"07-29-{child_id}"
                child = json.loads(
                    (child_dir / "task.json").read_text(encoding="utf-8")
                )
                self.assertEqual(child["status"], expected)
                self.assertFalse(classify_task_activity(child_dir, repo).active)

            request = {
                "expected_authority_digest": str(current["authority_digest"]),
                "expected_task_digest": str(current["task_digest"]),
                "operator_state": {"pending_action": None},
                "commit_enabled": False,
            }
            retired = retire_task_evidence(repo, RUN_ID, **request)
            replay = retire_task_evidence(repo, RUN_ID, **request)
            archived_parent = repo / retired["location"]
            self.assertFalse(task_dir.exists())
            self.assertTrue(archived_parent.is_dir())
            for child_id in ("integrated-child", "cancelled-child"):
                self.assertFalse(
                    (repo / ".trellis" / "tasks" / f"07-29-{child_id}").exists()
                )
                self.assertTrue(
                    (archived_parent.parent / f"07-29-{child_id}").is_dir()
                )
            self.assertFalse(retired["replayed"])
            self.assertTrue(replay["replayed"])


if __name__ == "__main__":
    unittest.main()
