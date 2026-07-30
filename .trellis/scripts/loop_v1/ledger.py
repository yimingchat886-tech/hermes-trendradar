"""Single-writer SQLite authority for one Loop v1 parent run."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from common.io import write_bytes_atomic


SCHEMA_VERSION = 2
OPERATION_PHASES = (
    "prepared",
    "effect_observed",
    "authority_committed",
    "projected",
)
_PHASE_INDEX = {phase: index for index, phase in enumerate(OPERATION_PHASES)}
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RUNTIME_ROOT = Path(".trellis/.runtime/loop-v1/parents")

_AUTHORITY_TABLE_ORDER = {
    "parent_runs": "run_id",
    "writer_fences": "epoch",
    "envelope_revisions": "revision_id",
    "context_revisions": "sequence",
    "graph_revisions": "graph_revision_id",
    "requirements": "requirement_id",
    "child_operations": "child_id",
    "child_packets": "packet_id",
    "resource_claims": "claim_id",
    "message_receipts": "receipt_id",
    "git_operations": "git_operation_id",
    "verifications": "verification_id",
    "effects": "effect_id",
    "gate_requests": "gate_id",
    "operations": "operation_id",
    "ledger_events": "position",
}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parent_runs (
    run_id TEXT PRIMARY KEY,
    selector TEXT NOT NULL,
    status TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 1),
    writer_id TEXT NOT NULL,
    fence_token TEXT NOT NULL UNIQUE,
    start_gate_ref TEXT,
    final_gate_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    kind TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN {OPERATION_PHASES!r}),
    epoch INTEGER NOT NULL CHECK (epoch >= 1),
    input_fingerprint TEXT NOT NULL,
    output_fingerprint TEXT,
    outcome_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_events (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    operation_id TEXT NOT NULL REFERENCES operations(operation_id),
    event_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS writer_fences (
    epoch INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
    writer_id TEXT NOT NULL,
    fence_token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS envelope_revisions (
    revision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    digest TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_revisions (
    revision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    previous_digest TEXT,
    digest TEXT NOT NULL UNIQUE,
    envelope_digest TEXT NOT NULL,
    requirement_digest TEXT NOT NULL,
    graph_digest TEXT NOT NULL,
    dependency_digest TEXT NOT NULL,
    reason TEXT NOT NULL,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS graph_revisions (
    graph_revision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    context_revision_id TEXT NOT NULL REFERENCES context_revisions(revision_id),
    digest TEXT NOT NULL,
    reason TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirements (
    requirement_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    requirement_class TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS child_operations (
    child_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    coverage_json TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    round INTEGER NOT NULL,
    state TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS child_packets (
    packet_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    child_id TEXT NOT NULL REFERENCES child_operations(child_id),
    epoch INTEGER NOT NULL CHECK (epoch >= 1),
    context_digest TEXT NOT NULL,
    requirement_digest TEXT NOT NULL,
    envelope_digest TEXT NOT NULL,
    graph_digest TEXT NOT NULL,
    dependency_digest TEXT NOT NULL,
    base_head TEXT NOT NULL,
    base_tree_id TEXT NOT NULL,
    packet_digest TEXT NOT NULL UNIQUE,
    packet_json TEXT NOT NULL,
    issued_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_claims (
    claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    child_id TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    mode TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_receipts (
    receipt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    sender TEXT NOT NULL,
    role TEXT NOT NULL,
    expected_epoch INTEGER NOT NULL,
    context_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    disposition TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_operations (
    git_operation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
    worktree TEXT,
    branch TEXT,
    ref_name TEXT,
    expected_old_ref TEXT,
    commit_id TEXT,
    tree_id TEXT,
    phase TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    verification_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    artifact_digest TEXT NOT NULL,
    actor TEXT NOT NULL,
    verdict TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    applicability_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_type TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    source_position INTEGER NOT NULL,
    digest TEXT,
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS effects (
    effect_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    classification TEXT NOT NULL,
    authorization_ref TEXT,
    outcome_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_requests (
    gate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES parent_runs(run_id),
    kind TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    response_identity TEXT,
    response_at TEXT,
    invalidation_reason TEXT,
    created_at TEXT NOT NULL
);
"""


class LedgerError(RuntimeError):
    """Base error for Loop ledger operations."""


class WriterFenceError(LedgerError):
    """Raised when a mutation does not hold the active writer fence."""


class OperationConflict(LedgerError):
    """Raised when operation identity, epoch, or phase CAS conflicts."""


class ProjectionError(LedgerError):
    """Raised when a projection cannot be written."""


@dataclass(frozen=True)
class WriterLease:
    """Capability required for every authoritative ledger mutation."""

    run_id: str
    writer_id: str
    epoch: int
    fence_token: str = field(repr=False)


def ledger_path(repo_root: Path, parent_run_id: str) -> Path:
    """Return the deterministic ignored database path for a parent run."""
    run_id = _required_text(parent_run_id, "parent_run_id")
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise LedgerError(
            "parent_run_id must use 1-128 ASCII letters, digits, '.', '_', or '-'"
        )
    return Path(repo_root).resolve() / _RUNTIME_ROOT / run_id / "ledger.sqlite3"


class ParentLedger:
    """One parent-run authority with fenced writes and rebuildable projections."""

    def __init__(self, repo_root: Path, parent_run_id: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.run_id = _required_text(parent_run_id, "parent_run_id")
        self.path = ledger_path(self.repo_root, self.run_id)

    @classmethod
    def initialize(
        cls,
        repo_root: Path,
        parent_run_id: str,
        *,
        selector: str,
        writer_id: str,
        start_gate_ref: str | None = None,
        existing_lease: WriterLease | None = None,
    ) -> tuple[ParentLedger, WriterLease]:
        """Create or idempotently reopen one parent ledger."""
        ledger = cls(repo_root, parent_run_id)
        selector = _required_text(selector, "selector")
        writer_id = _required_text(writer_id, "writer_id")
        gate_ref = start_gate_ref.strip() if start_gate_ref else None
        admission_checked = False
        if selector == "loop_v1" and not ledger.path.is_file():
            from .qualification import QualificationError, operation_qualification

            qualification = operation_qualification(ledger.repo_root)
            if qualification.enforced and not qualification.valid:
                raise QualificationError("; ".join(qualification.issues))
            admission_checked = True
        ledger.path.parent.mkdir(parents=True, exist_ok=True)

        connection = ledger._connect()
        try:
            ledger._initialize_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM parent_runs WHERE run_id = ?",
                (ledger.run_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["selector"] != selector
                    or row["writer_id"] != writer_id
                    or (gate_ref is not None and row["start_gate_ref"] != gate_ref)
                ):
                    raise OperationConflict(
                        "existing parent ledger identity does not match initialization request"
                    )
                if existing_lease is None:
                    raise WriterFenceError(
                        "parent ledger already exists; the active lease is required to reopen it"
                    )
                ledger._assert_lease(connection, existing_lease)
                lease = ledger._lease_from_row(row)
                connection.commit()
                return ledger, lease

            if selector == "loop_v1" and not admission_checked:
                from .qualification import QualificationError, operation_qualification

                qualification = operation_qualification(ledger.repo_root)
                if qualification.enforced and not qualification.valid:
                    raise QualificationError("; ".join(qualification.issues))

            now = _now()
            epoch = 1
            fence_token = secrets.token_hex(32)
            operation_id = f"initialize:{ledger.run_id}"
            input_fingerprint = _digest_json(
                {
                    "run_id": ledger.run_id,
                    "selector": selector,
                    "start_gate_ref": gate_ref,
                }
            )
            outcome = {"epoch": epoch, "status": "initialized", "writer_id": writer_id}
            connection.execute(
                """
                INSERT INTO parent_runs (
                    run_id, selector, status, epoch, writer_id, fence_token,
                    start_gate_ref, final_gate_ref, created_at, updated_at
                ) VALUES (?, ?, 'initialized', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    ledger.run_id,
                    selector,
                    epoch,
                    writer_id,
                    fence_token,
                    gate_ref,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, run_id, kind, phase, epoch, input_fingerprint,
                    output_fingerprint, outcome_json, created_at, updated_at
                ) VALUES (?, ?, 'parent_initialize', 'authority_committed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    ledger.run_id,
                    epoch,
                    input_fingerprint,
                    _digest_json(outcome),
                    _canonical_json(outcome),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO writer_fences (
                    epoch, run_id, operation_id, writer_id, fence_token,
                    acquired_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (epoch, ledger.run_id, operation_id, writer_id, fence_token, now),
            )
            ledger._insert_event(
                connection,
                operation_id=operation_id,
                event_type="parent_initialized",
                phase="authority_committed",
                epoch=epoch,
                payload=outcome,
                created_at=now,
            )
            connection.commit()
            return ledger, WriterLease(ledger.run_id, writer_id, epoch, fence_token)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reference(self) -> dict[str, object]:
        """Return the read-only identity shared with every child."""
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "read_only": True,
            "sqlite_uri": f"{self.path.resolve().as_uri()}?mode=ro",
        }

    def rotate_writer(
        self,
        lease: WriterLease,
        *,
        new_writer_id: str,
        operation_id: str,
    ) -> WriterLease:
        """CAS the active writer and return the new fenced lease."""
        new_writer_id = _required_text(new_writer_id, "new_writer_id")
        operation_id = _required_text(operation_id, "operation_id")
        input_fingerprint = _digest_json(
            {
                "expected_epoch": lease.epoch,
                "expected_fence_fingerprint": _digest_text(lease.fence_token),
                "expected_writer_id": lease.writer_id,
                "new_writer_id": new_writer_id,
                "run_id": self.run_id,
            }
        )

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["kind"] != "writer_rotation"
                    or existing["phase"] != "authority_committed"
                    or existing["input_fingerprint"] != input_fingerprint
                ):
                    raise OperationConflict(
                        "writer rotation operation ID was reused with new input"
                    )
                outcome = json.loads(existing["outcome_json"])
                current = self._parent_row(connection)
                if current["writer_id"] != outcome.get("writer_id") or current[
                    "epoch"
                ] != outcome.get("epoch"):
                    raise OperationConflict(
                        "writer rotation outcome is no longer the active lease"
                    )
                expected_output = _digest_json(
                    {
                        **outcome,
                        "fence_fingerprint": _digest_text(current["fence_token"]),
                    }
                )
                if existing["output_fingerprint"] != expected_output:
                    raise OperationConflict(
                        "writer rotation outcome fingerprint is inconsistent"
                    )
                replayed = self._lease_from_row(current)
                self._enforce_runtime_qualification(connection, replayed)
                connection.commit()
                return replayed

            self._assert_lease(connection, lease)
            self._enforce_runtime_qualification(connection, lease)
            now = _now()
            next_epoch = lease.epoch + 1
            next_token = secrets.token_hex(32)
            updated = connection.execute(
                """
                UPDATE parent_runs
                SET epoch = ?, writer_id = ?, fence_token = ?, updated_at = ?
                WHERE run_id = ? AND epoch = ? AND writer_id = ? AND fence_token = ?
                """,
                (
                    next_epoch,
                    new_writer_id,
                    next_token,
                    now,
                    self.run_id,
                    lease.epoch,
                    lease.writer_id,
                    lease.fence_token,
                ),
            )
            if updated.rowcount != 1:
                raise WriterFenceError("writer lease lost during rotation")

            outcome = {"epoch": next_epoch, "writer_id": new_writer_id}
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, run_id, kind, phase, epoch, input_fingerprint,
                    output_fingerprint, outcome_json, created_at, updated_at
                ) VALUES (?, ?, 'writer_rotation', 'authority_committed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    self.run_id,
                    next_epoch,
                    input_fingerprint,
                    _digest_json(
                        {
                            **outcome,
                            "fence_fingerprint": _digest_text(next_token),
                        }
                    ),
                    _canonical_json(outcome),
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE writer_fences SET released_at = ? WHERE epoch = ?",
                (now, lease.epoch),
            )
            connection.execute(
                """
                INSERT INTO writer_fences (
                    epoch, run_id, operation_id, writer_id, fence_token,
                    acquired_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (next_epoch, self.run_id, operation_id, new_writer_id, next_token, now),
            )
            self._insert_event(
                connection,
                operation_id=operation_id,
                event_type="writer_rotated",
                phase="authority_committed",
                epoch=next_epoch,
                payload=outcome,
                created_at=now,
            )
            connection.commit()
            return WriterLease(self.run_id, new_writer_id, next_epoch, next_token)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def prepare_operation(
        self,
        lease: WriterLease,
        *,
        operation_id: str,
        kind: str,
        input_fingerprint: str,
        intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Durably prepare an operation or return its exact replay state."""
        operation_id = _required_text(operation_id, "operation_id")
        kind = _required_text(kind, "kind")
        input_fingerprint = _required_text(input_fingerprint, "input_fingerprint")
        intent_value = intent or {}
        intent_json = _canonical_json(intent_value)
        with self._write_transaction(lease) as connection:
            existing = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["kind"] != kind
                    or existing["input_fingerprint"] != input_fingerprint
                    or existing["epoch"] != lease.epoch
                    or (
                        existing["phase"] == "prepared"
                        and existing["outcome_json"] != intent_json
                    )
                ):
                    raise OperationConflict(
                        "operation ID was reused with new input or epoch"
                    )
                return _operation_dict(existing)

            now = _now()
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, run_id, kind, phase, epoch, input_fingerprint,
                    output_fingerprint, outcome_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'prepared', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    operation_id,
                    self.run_id,
                    kind,
                    lease.epoch,
                    input_fingerprint,
                    intent_json,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                operation_id=operation_id,
                event_type="operation_prepared",
                phase="prepared",
                epoch=lease.epoch,
                payload={"intent": intent_value, "kind": kind},
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return _operation_dict(row)

    def advance_operation(
        self,
        lease: WriterLease,
        *,
        operation_id: str,
        expected_phase: str,
        phase: str,
        output_fingerprint: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance exactly one operation phase using epoch and phase CAS."""
        operation_id = _required_text(operation_id, "operation_id")
        output_fingerprint = _required_text(output_fingerprint, "output_fingerprint")
        if expected_phase not in _PHASE_INDEX or phase not in _PHASE_INDEX:
            raise OperationConflict("unknown operation phase")
        if _PHASE_INDEX[phase] != _PHASE_INDEX[expected_phase] + 1:
            raise OperationConflict("operation phases must advance exactly one step")
        outcome_json = _canonical_json(outcome or {})

        with self._write_transaction(lease) as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise OperationConflict("operation is not prepared")
            if row["epoch"] != lease.epoch:
                raise OperationConflict("operation belongs to a stale writer epoch")
            if row["phase"] == phase:
                if (
                    row["output_fingerprint"] == output_fingerprint
                    and row["outcome_json"] == outcome_json
                ):
                    return _operation_dict(row)
                raise OperationConflict(
                    "operation phase replay conflicts with durable outcome"
                )
            if row["phase"] != expected_phase:
                raise OperationConflict(
                    f"operation phase CAS failed: expected {expected_phase}, found {row['phase']}"
                )

            now = _now()
            updated = connection.execute(
                """
                UPDATE operations
                SET phase = ?, output_fingerprint = ?, outcome_json = ?, updated_at = ?
                WHERE operation_id = ? AND phase = ? AND epoch = ?
                """,
                (
                    phase,
                    output_fingerprint,
                    outcome_json,
                    now,
                    operation_id,
                    expected_phase,
                    lease.epoch,
                ),
            )
            if updated.rowcount != 1:
                raise OperationConflict(
                    "operation phase changed during compare-and-swap"
                )
            self._insert_event(
                connection,
                operation_id=operation_id,
                event_type="operation_phase_changed",
                phase=phase,
                epoch=lease.epoch,
                payload={
                    "from": expected_phase,
                    "outcome": outcome or {},
                    "output_fingerprint": output_fingerprint,
                    "to": phase,
                },
                created_at=now,
            )
            current = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return _operation_dict(current)

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Read one durable operation without acquiring writer authority."""
        connection = self._connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (_required_text(operation_id, "operation_id"),),
            ).fetchone()
            return _operation_dict(row) if row is not None else None
        finally:
            connection.close()

    def authority_snapshot(self) -> dict[str, object]:
        """Return a deterministic, fence-redacted snapshot of workflow authority."""
        snapshot, _ = self._authority_snapshot_with_position()
        return snapshot

    def authority_digest(self, connection: sqlite3.Connection | None = None) -> str:
        """Digest workflow authority, excluding derived projection checkpoints."""
        snapshot = (
            self._authority_snapshot_from_connection(connection)
            if connection is not None
            else self.authority_snapshot()
        )
        return _digest_json(snapshot)

    def rebuild_projection(
        self,
        lease: WriterLease,
        projection_type: str = "summary",
    ) -> dict[str, object]:
        """Atomically rebuild a deterministic file projection from authority."""
        if projection_type != "summary":
            raise ProjectionError(f"unsupported projection type: {projection_type}")
        self.assert_runtime_qualification(lease)
        snapshot, source_position = self._authority_snapshot_with_position()
        authority_digest = _digest_json(snapshot)
        content = {
            "authority": snapshot,
            "authority_digest": authority_digest,
            "run_id": self.run_id,
            "schema_version": SCHEMA_VERSION,
            "source_position": source_position,
        }
        payload = (
            json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        digest = sha256(payload).hexdigest()
        path = self._projection_path(projection_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_bytes_atomic(path, payload)
        except OSError as exc:
            self._record_projection(
                lease,
                projection_type=projection_type,
                source_position=source_position,
                digest=None,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise ProjectionError(f"projection write failed: {exc}") from exc

        status = self._record_projection(
            lease,
            projection_type=projection_type,
            source_position=source_position,
            digest=digest,
            status="current",
            error=None,
        )
        return {
            "authority_digest": authority_digest,
            "digest": digest,
            "path": str(path),
            "source_position": source_position,
            "status": status,
        }

    def _authority_snapshot_with_position(self) -> tuple[dict[str, object], int]:
        """Read authority and its ledger position from one SQLite snapshot."""
        connection = self._connect(read_only=True)
        try:
            connection.execute("BEGIN")
            snapshot = self._authority_snapshot_from_connection(connection)
            position = self._ledger_position(connection)
            connection.commit()
            return snapshot, position
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _authority_snapshot_from_connection(
        self, connection: sqlite3.Connection
    ) -> dict[str, object]:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table, order_by in _AUTHORITY_TABLE_ORDER.items():
            rows = connection.execute(
                f"SELECT * FROM {table} ORDER BY {order_by}"
            ).fetchall()
            values = [dict(row) for row in rows]
            if table in {"parent_runs", "writer_fences"}:
                for value in values:
                    token = value.pop("fence_token")
                    value["fence_fingerprint"] = _digest_text(token)
            tables[table] = values
        return {
            "run_id": self.run_id,
            "schema_version": SCHEMA_VERSION,
            "tables": tables,
        }

    def projection_status(self, projection_type: str = "summary") -> dict[str, object]:
        """Return effective projection freshness without mutating authority."""
        path = self._projection_path(projection_type)
        connection = self._connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT * FROM projection_checkpoints WHERE projection_type = ?",
                (projection_type,),
            ).fetchone()
            current_position = self._ledger_position(connection)
        finally:
            connection.close()

        if row is None:
            return {
                "file_exists": path.is_file(),
                "projection_type": projection_type,
                "recorded_status": None,
                "source_position": None,
                "status": "missing",
            }
        recorded = dict(row)
        if recorded["status"] == "failed":
            effective = "failed"
        elif not path.is_file():
            effective = "missing"
        elif recorded["source_position"] != current_position:
            effective = "stale"
        else:
            effective = recorded["status"]
        return {
            **recorded,
            "file_exists": path.is_file(),
            "recorded_status": recorded["status"],
            "status": effective,
        }

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif row["value"] == "1" and SCHEMA_VERSION == 2:
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
        elif row["value"] != str(SCHEMA_VERSION):
            raise LedgerError("unsupported Loop ledger schema version")
        connection.commit()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        else:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    def assert_runtime_qualification(self, lease: WriterLease) -> None:
        """Validate the persisted execution binding without a business write."""
        with self._write_transaction(lease):
            pass

    def revoke_execution_binding(
        self,
        lease: WriterLease,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        revoked_at: str,
        direct_user_action: bool,
        execution_receipt: str | None,
    ) -> dict[str, object]:
        """Record one direct-user terminal revocation without cleanup."""
        operation_id = _required_text(operation_id, "operation_id")
        actor = _required_text(actor, "actor")
        reason = _required_text(reason, "reason")
        revoked_at = _required_text(revoked_at, "revoked_at")
        if direct_user_action is not True:
            raise LedgerError("execution binding revocation requires a direct user action")
        supplied = {
            "actor": actor,
            "direct_user_action": True,
            "reason": reason,
            "revoked_at": revoked_at,
            "target": {
                "execution_receipt": execution_receipt,
                "run_id": self.run_id,
            },
        }
        with self._write_transaction(
            lease, enforce_execution_qualification=False
        ) as connection:
            existing = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["kind"] != "execution_binding_revocation"
                    or existing["phase"] != "authority_committed"
                    or existing["epoch"] != lease.epoch
                    or existing["input_fingerprint"] != _digest_json(supplied)
                ):
                    raise OperationConflict("execution binding revocation replay differs")
                return _operation_dict(existing)["outcome"]

            parent = self._parent_row(connection)
            if parent["status"] not in {"authorized", "paused", "recovery_waiting"}:
                raise LedgerError(
                    f"cannot revoke execution binding in status {parent['status']}"
                )
            binding = self._execution_binding(connection, parent)
            bound_receipt = binding.get("receipt_id") if isinstance(binding, dict) else None
            if execution_receipt != bound_receipt:
                raise LedgerError("revocation target does not match execution binding")
            now = _now()
            outcome = {**supplied, "preserved": True, "status": "revoked"}
            connection.execute(
                "UPDATE parent_runs SET status = 'revoked', updated_at = ? WHERE run_id = ?",
                (now, self.run_id),
            )
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, run_id, kind, phase, epoch, input_fingerprint,
                    output_fingerprint, outcome_json, created_at, updated_at
                ) VALUES (?, ?, 'execution_binding_revocation', 'authority_committed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    self.run_id,
                    lease.epoch,
                    _digest_json(supplied),
                    _digest_json(outcome),
                    _canonical_json(outcome),
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                operation_id=operation_id,
                event_type="execution_binding_revoked",
                phase="authority_committed",
                epoch=lease.epoch,
                payload=outcome,
                created_at=now,
            )
            return outcome

    def _execution_binding(
        self, connection: sqlite3.Connection, parent: sqlite3.Row
    ) -> dict[str, object] | None:
        start_gate_ref = parent["start_gate_ref"]
        if start_gate_ref is None:
            return None
        approval = connection.execute(
            "SELECT outcome_json FROM operations WHERE operation_id = ?",
            (f"start-approval:{start_gate_ref}",),
        ).fetchone()
        if approval is not None:
            try:
                outcome = json.loads(approval["outcome_json"])
            except (AttributeError, json.JSONDecodeError):
                return {"mode": "invalid"}
            if isinstance(outcome, dict) and "execution_binding" in outcome:
                binding = outcome["execution_binding"]
                return dict(binding) if isinstance(binding, dict) else {"mode": "invalid"}

        envelope = connection.execute(
            "SELECT contract_json FROM envelope_revisions WHERE revision_id = ?",
            (start_gate_ref,),
        ).fetchone()
        if envelope is None:
            return {"mode": "invalid"}
        try:
            receipt_id = json.loads(envelope["contract_json"]).get(
                "conformance_receipt"
            )
        except (AttributeError, json.JSONDecodeError):
            return {"mode": "invalid"}
        from .qualification import legacy_execution_binding

        return legacy_execution_binding(receipt_id)

    def _qualification_error(
        self,
        connection: sqlite3.Connection,
        lease: WriterLease,
    ) -> str | None:
        parent = self._parent_row(connection)
        if parent["selector"] != "loop_v1":
            return None
        if parent["status"] == "revoked":
            return "execution binding has been explicitly revoked"
        if parent["status"] == "initialized" or parent["start_gate_ref"] is None:
            return None
        if parent["status"] not in {
            "authorized",
            "paused",
            "recovery_waiting",
        }:
            return None

        from .qualification import QualificationError, execution_qualification

        try:
            status = execution_qualification(
                self.repo_root, self._execution_binding(connection, parent)
            )
            if not status.enforced or status.valid:
                return None
            reason = "; ".join(status.issues) or "qualification failed"
            receipt_id = status.receipt_id
        except QualificationError as exc:
            reason = str(exc)
            receipt_id = None

        if parent["status"] == "authorized":
            supplied = {
                "preserved": True,
                "reason": reason,
                "receipt_id": receipt_id,
                "requires_human_resume": True,
            }
            operation_id = f"qualification-pause:{_digest_json({'run_id': self.run_id, **supplied})[:32]}"
            existing = connection.execute(
                "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is None:
                now = _now()
                outcome = {**supplied, "epoch": lease.epoch, "status": "paused"}
                connection.execute(
                    "UPDATE parent_runs SET status = 'paused', updated_at = ? WHERE run_id = ?",
                    (now, self.run_id),
                )
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, run_id, kind, phase, epoch, input_fingerprint,
                        output_fingerprint, outcome_json, created_at, updated_at
                    ) VALUES (?, ?, 'qualification_pause', 'authority_committed', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        self.run_id,
                        lease.epoch,
                        _digest_json(supplied),
                        _digest_json(outcome),
                        _canonical_json(outcome),
                        now,
                        now,
                    ),
                )
                self._insert_event(
                    connection,
                    operation_id=operation_id,
                    event_type="qualification_paused",
                    phase="authority_committed",
                    epoch=lease.epoch,
                    payload=outcome,
                    created_at=now,
                )
        return reason

    def _enforce_runtime_qualification(
        self,
        connection: sqlite3.Connection,
        lease: WriterLease,
    ) -> None:
        error = self._qualification_error(connection, lease)
        if error is None:
            return
        connection.commit()
        from .qualification import QualificationError

        raise QualificationError(error)

    @contextmanager
    def _write_transaction(
        self,
        lease: WriterLease,
        *,
        enforce_execution_qualification: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_lease(connection, lease)
            if enforce_execution_qualification:
                self._enforce_runtime_qualification(connection, lease)
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _assert_lease(self, connection: sqlite3.Connection, lease: WriterLease) -> None:
        if lease.run_id != self.run_id:
            raise WriterFenceError("writer lease belongs to another parent run")
        row = self._parent_row(connection)
        if (
            row["epoch"] != lease.epoch
            or row["writer_id"] != lease.writer_id
            or not secrets.compare_digest(row["fence_token"], lease.fence_token)
        ):
            raise WriterFenceError(
                "writer lease is stale or does not own the active fence"
            )

    def _parent_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM parent_runs WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("parent ledger is not initialized")
        return row

    def _lease_from_row(self, row: sqlite3.Row) -> WriterLease:
        return WriterLease(
            run_id=self.run_id,
            writer_id=row["writer_id"],
            epoch=row["epoch"],
            fence_token=row["fence_token"],
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        event_type: str,
        phase: str,
        epoch: int,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ledger_events (
                run_id, operation_id, event_type, phase, epoch, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.run_id,
                operation_id,
                event_type,
                phase,
                epoch,
                _canonical_json(payload),
                created_at,
            ),
        )

    def _ledger_position(self, connection: sqlite3.Connection | None = None) -> int:
        owns_connection = connection is None
        active = connection or self._connect(read_only=True)
        try:
            row = active.execute(
                "SELECT COALESCE(MAX(position), 0) AS position FROM ledger_events"
            ).fetchone()
            return int(row["position"])
        finally:
            if owns_connection:
                active.close()

    def _record_projection(
        self,
        lease: WriterLease,
        *,
        projection_type: str,
        source_position: int,
        digest: str | None,
        status: str,
        error: str | None,
    ) -> str:
        with self._write_transaction(lease) as connection:
            current_position = self._ledger_position(connection)
            effective_status = status
            effective_error = error
            if status == "current" and current_position != source_position:
                effective_status = "stale"
                effective_error = "ledger advanced while projection was being written"
            connection.execute(
                """
                INSERT INTO projection_checkpoints (
                    projection_type, run_id, source_position, digest, status, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(projection_type) DO UPDATE SET
                    run_id = excluded.run_id,
                    source_position = excluded.source_position,
                    digest = excluded.digest,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    projection_type,
                    self.run_id,
                    source_position,
                    digest,
                    effective_status,
                    effective_error,
                    _now(),
                ),
            )
            return effective_status

    def _projection_path(self, projection_type: str) -> Path:
        if projection_type != "summary":
            raise ProjectionError(f"unsupported projection type: {projection_type}")
        return self.path.parent / "projections" / f"{projection_type}.json"


def _required_text(value: object, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise LedgerError(f"{field} is required")
    return text


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest_json(value: object) -> str:
    return _digest_text(_canonical_json(value))


def _digest_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _operation_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["outcome"] = json.loads(value.pop("outcome_json"))
    return value


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
