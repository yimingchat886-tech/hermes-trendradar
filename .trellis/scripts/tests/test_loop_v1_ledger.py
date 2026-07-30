from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common.config import get_loop_v1_admission
from common.io import write_bytes_atomic as real_write_bytes_atomic
from loop_v1 import (
    OperationConflict,
    ParentLedger,
    ProjectionError,
    WriterFenceError,
    WriterLease,
    ledger_path,
)
from loop_v1.qualification import QualificationError
EXPECTED_TABLES = {
    "child_operations",
    "effects",
    "envelope_revisions",
    "gate_requests",
    "git_operations",
    "ledger_events",
    "message_receipts",
    "operations",
    "parent_runs",
    "projection_checkpoints",
    "requirements",
    "resource_claims",
    "schema_meta",
    "verifications",
    "writer_fences",
}


def initialize(root: Path) -> tuple[ParentLedger, WriterLease]:
    return ParentLedger.initialize(
        root,
        "fixture-parent-run",
        selector="loop_v1",
        writer_id="parent-writer-a",
        start_gate_ref="gate-start-1",
    )


def read_rows(
    ledger: ParentLedger, sql: str, params: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(ledger.reference()["sqlite_uri"], uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


class LoopV1LedgerTests(unittest.TestCase):
    def test_invalid_repository_role_rejects_before_ledger_mutation(self) -> None:
        for runtime_mode in ("", "unknown", "downstream_project"):
            with self.subTest(runtime_mode=runtime_mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = root / ".trellis/config.yaml"
                config.parent.mkdir(parents=True)
                config.write_text(
                    "loop_v1:\n"
                    "  admission_enabled: true\n"
                    "  parent_default: loop_v1\n"
                    f"  runtime_mode: {runtime_mode}\n",
                    encoding="utf-8",
                )

                with self.assertRaises(QualificationError):
                    initialize(root)

                self.assertFalse((root / ".trellis/.runtime").exists())

    def test_one_deterministic_parent_database_and_complete_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger, lease = initialize(root)

            self.assertEqual(
                ledger.path,
                ledger_path(root, "fixture-parent-run"),
            )
            self.assertTrue(ledger.path.is_file())
            self.assertEqual(ledger.reference()["read_only"], True)
            self.assertEqual(ledger.reference()["run_id"], "fixture-parent-run")

            tables = {
                row["name"]
                for row in read_rows(
                    ledger,
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))
            self.assertEqual(read_rows(ledger, "PRAGMA integrity_check")[0][0], "ok")
            self.assertEqual(read_rows(ledger, "PRAGMA journal_mode")[0][0], "wal")
            self.assertEqual(
                len(list((root / ".trellis" / ".runtime").rglob("ledger.sqlite3"))),
                1,
            )

            reopened, replayed_lease = ParentLedger.initialize(
                root,
                "fixture-parent-run",
                selector="loop_v1",
                writer_id="parent-writer-a",
                start_gate_ref="gate-start-1",
                existing_lease=lease,
            )
            self.assertEqual(reopened.path, ledger.path)
            self.assertEqual(replayed_lease, lease)
            self.assertEqual(len(read_rows(ledger, "SELECT * FROM parent_runs")), 1)

            with self.assertRaises(WriterFenceError):
                ParentLedger.initialize(
                    root,
                    "fixture-parent-run",
                    selector="loop_v1",
                    writer_id="parent-writer-a",
                    start_gate_ref="gate-start-1",
                )

    def test_runtime_database_path_is_ignored_by_repository(self) -> None:
        result = subprocess.run(
            [
                "git",
                "check-ignore",
                "-q",
                ".trellis/.runtime/loop-v1/parents/example/ledger.sqlite3",
            ],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_only_active_fenced_writer_can_mutate_and_rotation_replays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, first = initialize(Path(tmp))
            forged = WriterLease(first.run_id, first.writer_id, first.epoch, "0" * 64)
            with self.assertRaises(WriterFenceError):
                ledger.prepare_operation(
                    forged,
                    operation_id="forged-operation",
                    kind="test",
                    input_fingerprint="input-forged",
                )

            second = ledger.rotate_writer(
                first,
                new_writer_id="parent-writer-b",
                operation_id="rotate-writer-1",
            )
            replayed = ledger.rotate_writer(
                first,
                new_writer_id="parent-writer-b",
                operation_id="rotate-writer-1",
            )
            self.assertEqual(replayed, second)
            self.assertEqual(second.epoch, first.epoch + 1)
            self.assertNotEqual(second.fence_token, first.fence_token)

            with self.assertRaises(WriterFenceError):
                ledger.prepare_operation(
                    first,
                    operation_id="stale-writer-operation",
                    kind="test",
                    input_fingerprint="input-stale",
                )
            prepared = ledger.prepare_operation(
                second,
                operation_id="active-writer-operation",
                kind="test",
                input_fingerprint="input-active",
            )
            self.assertEqual(prepared["epoch"], second.epoch)

            with self.assertRaises(OperationConflict):
                ledger.rotate_writer(
                    first,
                    new_writer_id="different-writer",
                    operation_id="rotate-writer-1",
                )

    def test_operation_phase_replay_is_idempotent_and_compare_and_swap_guarded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            prepared = ledger.prepare_operation(
                lease,
                operation_id="operation-1",
                kind="local-effect",
                input_fingerprint="input-1",
            )
            replayed = ledger.prepare_operation(
                lease,
                operation_id="operation-1",
                kind="local-effect",
                input_fingerprint="input-1",
            )
            self.assertEqual(prepared, replayed)

            with self.assertRaises(OperationConflict):
                ledger.prepare_operation(
                    lease,
                    operation_id="operation-1",
                    kind="local-effect",
                    input_fingerprint="different-input",
                )
            with self.assertRaises(OperationConflict):
                ledger.advance_operation(
                    lease,
                    operation_id="operation-1",
                    expected_phase="prepared",
                    phase="authority_committed",
                    output_fingerprint="output-skip",
                )

            observed = ledger.advance_operation(
                lease,
                operation_id="operation-1",
                expected_phase="prepared",
                phase="effect_observed",
                output_fingerprint="output-1",
                outcome={"effect": "observed"},
            )
            replayed_observed = ledger.advance_operation(
                lease,
                operation_id="operation-1",
                expected_phase="prepared",
                phase="effect_observed",
                output_fingerprint="output-1",
                outcome={"effect": "observed"},
            )
            self.assertEqual(observed, replayed_observed)

            with self.assertRaises(OperationConflict):
                ledger.advance_operation(
                    lease,
                    operation_id="operation-1",
                    expected_phase="prepared",
                    phase="effect_observed",
                    output_fingerprint="conflicting-output",
                    outcome={"effect": "observed"},
                )

            committed = ledger.advance_operation(
                lease,
                operation_id="operation-1",
                expected_phase="effect_observed",
                phase="authority_committed",
                output_fingerprint="output-2",
                outcome={"authority": "committed"},
            )
            projected = ledger.advance_operation(
                lease,
                operation_id="operation-1",
                expected_phase="authority_committed",
                phase="projected",
                output_fingerprint="output-3",
                outcome={"projection": "current"},
            )
            self.assertEqual(committed["phase"], "authority_committed")
            self.assertEqual(projected["phase"], "projected")
            self.assertEqual(ledger.get_operation("operation-1"), projected)

            phases = [
                row["phase"]
                for row in read_rows(
                    ledger,
                    "SELECT phase FROM ledger_events WHERE operation_id = ? ORDER BY position",
                    ("operation-1",),
                )
            ]
            self.assertEqual(
                phases,
                ["prepared", "effect_observed", "authority_committed", "projected"],
            )
            observed_event = read_rows(
                ledger,
                """
                SELECT payload_json FROM ledger_events
                WHERE operation_id = ? AND phase = 'effect_observed'
                """,
                ("operation-1",),
            )[0]
            self.assertIn(
                '"outcome":{"effect":"observed"}', observed_event["payload_json"]
            )

    def test_old_epoch_operation_cannot_advance_after_writer_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, first = initialize(Path(tmp))
            ledger.prepare_operation(
                first,
                operation_id="old-epoch-operation",
                kind="worker-result",
                input_fingerprint="input-old-epoch",
            )
            second = ledger.rotate_writer(
                first,
                new_writer_id="parent-writer-b",
                operation_id="rotate-writer-1",
            )

            with self.assertRaises(WriterFenceError):
                ledger.advance_operation(
                    first,
                    operation_id="old-epoch-operation",
                    expected_phase="prepared",
                    phase="effect_observed",
                    output_fingerprint="output-old",
                )
            with self.assertRaises(OperationConflict):
                ledger.advance_operation(
                    second,
                    operation_id="old-epoch-operation",
                    expected_phase="prepared",
                    phase="effect_observed",
                    output_fingerprint="output-old",
                )

    def test_projection_delete_and_rebuild_is_deterministic_and_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            ledger.prepare_operation(
                lease,
                operation_id="projection-source-operation",
                kind="test",
                input_fingerprint="input-projection",
            )
            authority_before = ledger.authority_digest()

            first = ledger.rebuild_projection(lease)
            first_bytes = Path(first["path"]).read_bytes()
            self.assertEqual(first["status"], "current")
            self.assertEqual(ledger.projection_status()["status"], "current")
            self.assertEqual(ledger.authority_digest(), authority_before)

            Path(first["path"]).unlink()
            self.assertEqual(ledger.projection_status()["status"], "missing")
            second = ledger.rebuild_projection(lease)
            self.assertEqual(Path(second["path"]).read_bytes(), first_bytes)
            self.assertEqual(second["digest"], first["digest"])
            self.assertEqual(ledger.authority_digest(), authority_before)

            ledger.prepare_operation(
                lease,
                operation_id="new-authority-operation",
                kind="test",
                input_fingerprint="input-new-authority",
            )
            self.assertEqual(ledger.projection_status()["status"], "stale")
            with self.assertRaises(ProjectionError):
                ledger.projection_status("../outside")

    def test_projection_failure_does_not_roll_back_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            ledger.prepare_operation(
                lease,
                operation_id="committed-operation",
                kind="test",
                input_fingerprint="input-committed",
            )
            ledger.advance_operation(
                lease,
                operation_id="committed-operation",
                expected_phase="prepared",
                phase="effect_observed",
                output_fingerprint="output-observed",
            )
            ledger.advance_operation(
                lease,
                operation_id="committed-operation",
                expected_phase="effect_observed",
                phase="authority_committed",
                output_fingerprint="output-committed",
            )
            authority_before = ledger.authority_digest()

            with mock.patch(
                "loop_v1.ledger.write_bytes_atomic",
                side_effect=OSError("injected projection failure"),
            ):
                with self.assertRaises(ProjectionError):
                    ledger.rebuild_projection(lease)

            self.assertEqual(ledger.authority_digest(), authority_before)
            self.assertEqual(
                ledger.get_operation("committed-operation")["phase"],
                "authority_committed",
            )
            self.assertEqual(ledger.projection_status()["status"], "failed")
            self.assertEqual(ledger.rebuild_projection(lease)["status"], "current")

    def test_projection_cannot_claim_current_if_authority_advances_during_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))

            def write_then_advance(path: Path, payload: bytes) -> None:
                real_write_bytes_atomic(path, payload)
                ledger.prepare_operation(
                    lease,
                    operation_id="concurrent-authority-operation",
                    kind="test",
                    input_fingerprint="input-concurrent-authority",
                )

            with mock.patch(
                "loop_v1.ledger.write_bytes_atomic",
                side_effect=write_then_advance,
            ):
                result = ledger.rebuild_projection(lease)

            self.assertEqual(result["status"], "stale")
            self.assertEqual(ledger.projection_status()["status"], "stale")

    def test_repository_bootstrap_admission_remains_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enabled, parent_default = get_loop_v1_admission(Path(tmp))
            self.assertFalse(enabled)
            self.assertEqual(parent_default, "current_trellis")


if __name__ == "__main__":
    unittest.main()
