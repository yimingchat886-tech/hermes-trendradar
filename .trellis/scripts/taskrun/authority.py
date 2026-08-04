"""Per-TaskRun SQLite authority with deterministic replay and projections."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from common.io import json_bytes


SCHEMA_VERSION = 1
_RUNTIME_ROOT = Path(".trellis/.runtime/taskrun/runs")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OID = re.compile(r"[0-9a-f]{40,64}\Z")
_MONTH = re.compile(r"\d{4}-\d{2}\Z")
_RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_TERMINAL = {"completed", "cancelled"}
_ACTIVE_HSM_STATES = {
    "parent": {
        "parent_prd_draft",
        "parent_waiting_completion_signal",
        "parent_commit_ready",
        "parent_archive_ready",
        "parent_blocked",
    },
    "child": {
        "child_plan_draft",
        "child_waiting_completion_signal",
        "child_commit_ready",
        "child_archive_ready",
        "child_blocked",
    },
}
_CLOSE_EFFECT_ORDER = (
    "pointer_cleared",
    "directory_archived",
    "task_projection_rebuilt",
    "board_rebuilt",
)
_CLOSE_EFFECTS = set(_CLOSE_EFFECT_ORDER)
_OPERATION_PHASES = {
    "prepared",
    "effect_observed",
    "authority_committed",
    "projected",
    "unknown_outcome",
}
_EVENT_KIND_TYPES = {
    "initialize": "taskrun_created",
    "start": "run_started",
    "terminal": "terminal_recorded",
    "action_plan": "action_planned",
    "action_result": "action_result_validated",
    "action_review": "action_reviewed",
    "strategy_decision": "strategy_decided",
    "execution_intervention": "execution_intervened",
    "execution_resume": "execution_resumed",
    "commit_reconciliation_request": "commit_reconciliation_requested",
    "commit_reconciliation_response": "commit_reconciliation_recorded",
    "delivery_settlement": "delivery_slots_reconciled",
    "final_candidate_review": "final_candidate_reviewed",
    "final_request": "final_request_recorded",
    "final_response": "final_response_recorded",
}
_STRATEGY_DECISIONS = {
    "continue",
    "review_required",
    "candidate_ready",
    "retry_exact_slice",
    "pause",
    "intervention",
}
_EXECUTION_INTENT_KEYS = {
    "action_id",
    "allowed_effects",
    "approval_refs",
    "attempt",
    "checks",
    "freshness",
    "input_digest",
    "problem_id",
    "remaining_budgets",
    "requirement_ids",
    "result_schema",
    "strategy_revision",
    "task_run_id",
    "touches",
}
_EXECUTION_RESULT_KEYS = {
    "action_id",
    "actual_effects",
    "actual_touches",
    "artifact_digests",
    "attempt",
    "candidate_digest",
    "checks",
    "diff_digest",
    "effect_receipts",
    "findings",
    "freshness",
    "input_digest",
    "provider_id",
    "requirement_ids",
    "result_digest",
    "risks",
    "task_run_id",
    "transport_id",
    "tree_digest",
    "unknowns",
    "worker_id",
}
_EXECUTION_REVIEW_KEYS = {
    "action_id",
    "attempt",
    "candidate_digest",
    "findings",
    "input_digest",
    "reviewer_id",
    "task_run_id",
    "verdict",
}
_EXECUTION_DECISION_KEYS = {
    "action_id",
    "attempt",
    "input_digest",
    "kind",
    "next_input_digest",
    "reason",
    "task_run_id",
}
_DELIVERY_SETTLEMENT_KEYS = {
    "candidate_ready_event_digest",
    "settlement_digest",
    "slots",
    "task_run_id",
}
_SETTLED_SLOT_KEYS = {
    "attempts",
    "fulfilled_by",
    "requirement_ids",
    "slot_digest",
    "slot_id",
}
_SETTLED_ATTEMPT_KEYS = {
    "disposition",
    "strategy",
    "successor_task_run_id",
    "task_dir_name",
    "task_run_id",
    "terminal_event_digest",
    "terminal_event_position",
}
_EXECUTION_INTERVENTION_KEYS = {
    "action_id",
    "attempt",
    "generation",
    "input_digest",
    "intervention_digest",
    "phase",
    "reason",
    "task_run_id",
}
_EXECUTION_RESUME_KEYS = {
    "authorization_ref",
    "intervention_digest",
    "task_run_id",
}
_CONTEXT_IDENTITY_KEYS = {"base", "context", "contract", "runtime"}
_COMMIT_RECONCILIATION_REQUEST_KEYS = {
    "base_commit",
    "changed_paths",
    "commit_chain",
    "head_commit",
    "head_parent_commit",
    "head_tree",
    "observed_identities",
    "prior_identities",
    "request_digest",
    "task_run_id",
}
_COMMIT_RECONCILIATION_RECEIPT_KEYS = {
    "authorization_ref",
    "decision",
    "receipt_audit_at",
    "receipt_audit_id",
    "receipt_digest",
    "receipt_id",
    "request_digest",
    "request_event_digest",
    "task_run_id",
}
_FINAL_CANDIDATE_KEYS = {
    "candidate_digest",
    "component_candidates",
    "findings",
    "review_id",
    "reviewer_id",
    "task_run_id",
    "verdict",
}
_COMPONENT_CANDIDATE_KEYS = {"action_id", "attempt", "candidate_digest"}
_FINAL_GATE_RECEIPT_KEYS = {
    "decision",
    "final_request_digest",
    "final_request_event_digest",
    "receipt_audit_at",
    "receipt_audit_id",
    "receipt_digest",
    "receipt_id",
    "task_run_id",
}
_INTERVENTION_REASONS = {
    "artifact_scope_expansion",
    "effect_expansion",
    "provider_surface_expansion",
    "scope_expansion",
    "secret_like_content",
    "worker_surface_expansion",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    task_run_id TEXT PRIMARY KEY,
    task_dir_name TEXT NOT NULL,
    task_json_seed TEXT NOT NULL,
    status TEXT NOT NULL,
    terminal_json TEXT,
    close_json TEXT,
    closed INTEGER NOT NULL CHECK (closed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
    kind TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    phase TEXT NOT NULL,
    intent_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
    operation_id TEXT NOT NULL REFERENCES operations(operation_id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_digest TEXT,
    event_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_type TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL REFERENCES task_runs(task_run_id),
    source_position INTEGER NOT NULL,
    digest TEXT,
    status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
"""


class TaskRunError(RuntimeError):
    """Base error for TaskRun authority failures."""


class EventConflict(TaskRunError):
    """Raised when an event identity or hash chain conflicts."""


class OperationConflict(TaskRunError):
    """Raised when a stable operation ID is reused with different input."""


class InvalidTransition(TaskRunError):
    """Raised when an event cannot follow the current replay state."""


def taskrun_path(repo_root: Path, task_run_id: str) -> Path:
    run_id = _required_text(task_run_id, "task_run_id")
    if not _SAFE_ID.fullmatch(run_id):
        raise TaskRunError(
            "task_run_id must use 1-128 ASCII letters, digits, '.', '_', or '-'"
        )
    return Path(repo_root).resolve() / _RUNTIME_ROOT / run_id / "authority.sqlite3"


class TaskRun:
    """One local TaskRun authority; filesystem surfaces are projections."""

    def __init__(self, repo_root: Path, task_run_id: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.task_run_id = _required_text(task_run_id, "task_run_id")
        self.path = taskrun_path(self.repo_root, self.task_run_id)

    @classmethod
    def initialize(
        cls,
        repo_root: Path,
        task_run_id: str,
        *,
        actor: str,
        task_dir_name: str,
        task_json: dict[str, Any],
    ) -> TaskRun:
        """Create or idempotently reopen one TaskRun authority."""
        run = cls(repo_root, task_run_id)
        actor = _required_text(actor, "actor")
        task_dir_name = _required_task_dir_name(task_dir_name)
        task_json = _required_object(task_json, "task_json")
        request = {
            "actor": actor,
            "task_dir_name": task_dir_name,
            "task_json": task_json,
            "task_run_id": run.task_run_id,
        }
        input_digest = _digest_json(request)
        run.path.parent.mkdir(parents=True, exist_ok=True)
        connection = run._connect()
        try:
            run._initialize_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM task_runs WHERE task_run_id = ?",
                (run.task_run_id,),
            ).fetchone()
            if existing is not None:
                operation = connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (f"initialize:{run.task_run_id}",),
                ).fetchone()
                if operation is None or operation["input_digest"] != input_digest:
                    raise OperationConflict(
                        "existing TaskRun identity does not match initialization request"
                    )
                connection.commit()
            else:
                now = _now()
                connection.execute(
                    """
                    INSERT INTO task_runs (
                        task_run_id, task_dir_name, task_json_seed, status,
                        terminal_json, close_json, closed, created_at, updated_at
                    ) VALUES (?, ?, ?, 'created', NULL, NULL, 0, ?, ?)
                    """,
                    (
                        run.task_run_id,
                        task_dir_name,
                        _canonical_json(task_json),
                        now,
                        now,
                    ),
                )
                operation_id = f"initialize:{run.task_run_id}"
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, task_run_id, kind, input_digest, phase,
                        intent_json, outcome_json, created_at, updated_at
                    ) VALUES (?, ?, 'initialize', ?, 'authority_committed', ?, '{}', ?, ?)
                    """,
                    (
                        operation_id,
                        run.task_run_id,
                        input_digest,
                        _canonical_json(request),
                        now,
                        now,
                    ),
                )
                run._append_event(
                    connection,
                    event_id=operation_id,
                    operation_id=operation_id,
                    event_type="taskrun_created",
                    payload=request,
                    created_at=now,
                )
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        run._validate_reopen()
        return run

    @classmethod
    def open(cls, repo_root: Path, task_run_id: str) -> TaskRun:
        run = cls(repo_root, task_run_id)
        if not run.path.is_file():
            raise TaskRunError("TaskRun authority does not exist")
        run._validate_reopen()
        return run

    def reference(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "read_only": True,
            "sqlite_uri": f"{self.path.resolve().as_uri()}?mode=ro",
            "task_run_id": self.task_run_id,
        }

    def record_started(self, *, operation_id: str, actor: str) -> dict[str, Any]:
        payload = {"actor": _required_text(actor, "actor")}
        return self._record_single_event(
            operation_id=operation_id,
            kind="start",
            event_type="run_started",
            payload=payload,
        )

    def record_action_planned(
        self,
        *,
        operation_id: str,
        actor: str,
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "intent": _required_object(intent, "intent"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="action_plan",
            event_type="action_planned",
            payload=payload,
        )

    def record_action_result(
        self,
        *,
        operation_id: str,
        actor: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "result": _required_object(result, "result"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="action_result",
            event_type="action_result_validated",
            payload=payload,
        )

    def record_action_review(
        self,
        *,
        operation_id: str,
        actor: str,
        review: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "review": _required_object(review, "review"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="action_review",
            event_type="action_reviewed",
            payload=payload,
        )

    def record_strategy_decision(
        self,
        *,
        operation_id: str,
        actor: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "decision": _required_object(decision, "decision"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="strategy_decision",
            event_type="strategy_decided",
            payload=payload,
        )

    def record_delivery_settlement(
        self,
        *,
        actor: str,
    ) -> dict[str, Any]:
        settlement = _delivery_settlement(self)
        payload = {
            "actor": _required_text(actor, "actor"),
            "settlement": settlement,
        }
        return self._record_single_event(
            operation_id=f"delivery-settlement:{settlement['settlement_digest']}",
            kind="delivery_settlement",
            event_type="delivery_slots_reconciled",
            payload=payload,
        )

    def record_execution_intervention(
        self,
        *,
        operation_id: str,
        actor: str,
        intervention: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "intervention": _required_object(intervention, "intervention"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="execution_intervention",
            event_type="execution_intervened",
            payload=payload,
        )

    def record_execution_resumed(
        self,
        *,
        operation_id: str,
        actor: str,
        resume: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "resume": _required_object(resume, "resume"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="execution_resume",
            event_type="execution_resumed",
            payload=payload,
        )

    def record_commit_reconciliation_request(
        self,
        *,
        actor: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        request = _validated_commit_reconciliation_request(request)
        payload = {
            "actor": _required_text(actor, "actor"),
            "request": request,
        }
        return self._record_single_event(
            operation_id=f"commit-reconciliation-request:{request['request_digest']}",
            kind="commit_reconciliation_request",
            event_type="commit_reconciliation_requested",
            payload=payload,
        )

    def record_commit_reconciliation_response(
        self,
        *,
        actor: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        receipt = _validated_commit_reconciliation_receipt(receipt)
        payload = {
            "actor": _required_text(actor, "actor"),
            "receipt": receipt,
        }
        return self._record_single_event(
            operation_id=f"commit-reconciliation-response:{receipt['receipt_digest']}",
            kind="commit_reconciliation_response",
            event_type="commit_reconciliation_recorded",
            payload=payload,
        )

    def record_final_candidate_review(
        self,
        *,
        operation_id: str,
        actor: str,
        final_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "final_candidate": _required_object(final_candidate, "final_candidate"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="final_candidate_review",
            event_type="final_candidate_reviewed",
            payload=payload,
        )

    def record_final_request(
        self,
        *,
        operation_id: str,
        actor: str,
        final_request: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "actor": _required_text(actor, "actor"),
            "final_request": _required_object(final_request, "final_request"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="final_request",
            event_type="final_request_recorded",
            payload=payload,
        )

    def record_final_response(
        self,
        *,
        operation_id: str,
        actor: str,
        final_gate_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        receipt = _validated_final_gate_receipt(final_gate_receipt)
        operation_id = _required_text(operation_id, "operation_id")
        if operation_id != f"final-response:{receipt['receipt_digest']}":
            raise OperationConflict(
                "final response operation ID must bind the receipt digest"
            )
        payload = {
            "actor": _required_text(actor, "actor"),
            "final_gate_receipt": receipt,
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="final_response",
            event_type="final_response_recorded",
            payload=payload,
        )

    def record_terminal(
        self,
        *,
        operation_id: str,
        actor: str,
        disposition: str,
        authorization_ref: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if disposition not in _TERMINAL:
            raise InvalidTransition("terminal disposition must be completed or cancelled")
        payload = {
            "actor": _required_text(actor, "actor"),
            "authorization_ref": _required_text(
                authorization_ref, "authorization_ref"
            ),
            "disposition": disposition,
            "evidence": _required_object(evidence, "evidence"),
        }
        return self._record_single_event(
            operation_id=operation_id,
            kind="terminal",
            event_type="terminal_recorded",
            payload=payload,
        )

    def prepare_close(
        self,
        *,
        operation_id: str,
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        operation_id = self._required_close_operation_id(operation_id)
        intent = _required_object(intent, "intent")
        input_digest = _digest_json(intent)
        with self._write_transaction() as connection:
            state = self._replay_connection(connection)
            _validate_close_intent(intent, state)
            existing = self._operation_row(connection, operation_id)
            if existing is not None:
                self._assert_operation(existing, "close", input_digest)
                return _operation_dict(existing)

            if state["terminal"] is None or state["closed"]:
                raise InvalidTransition("close requires one open terminal TaskRun")
            expected_projection_digest = sha256(self.task_projection_bytes()).hexdigest()
            digest_key = (
                "task_projection_digest"
                if _is_legacy_close_intent(intent)
                else "task_projection_preimage_digest"
            )
            if intent[digest_key] != expected_projection_digest:
                raise InvalidTransition("close intent task projection digest is stale")
            now = _now()
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, task_run_id, kind, input_digest, phase,
                    intent_json, outcome_json, created_at, updated_at
                ) VALUES (?, ?, 'close', ?, 'prepared', ?, '{}', ?, ?)
                """,
                (
                    operation_id,
                    self.task_run_id,
                    input_digest,
                    _canonical_json(intent),
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                event_id=f"{operation_id}:prepared",
                operation_id=operation_id,
                event_type="close_prepared",
                payload={"intent": intent},
                created_at=now,
            )
            return _operation_dict(self._required_operation(connection, operation_id, "close"))

    def observe_close_effect(
        self,
        *,
        operation_id: str,
        effect: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        if effect not in _CLOSE_EFFECTS:
            raise InvalidTransition(f"unsupported close effect: {effect}")
        observation = _required_object(observation, "observation")
        with self._write_transaction() as connection:
            operation = self._required_operation(connection, operation_id, "close")
            if not _is_legacy_close_intent(json.loads(operation["intent_json"])):
                raise InvalidTransition("status-only close has no physical effects")
            if operation["phase"] in {"authority_committed", "projected"}:
                state = self._replay_connection(connection)
                durable = (state.get("close") or {}).get("effects", {}).get(effect)
                if durable != observation:
                    raise OperationConflict("close effect replay conflicts with outcome")
                return durable
            if operation["phase"] == "unknown_outcome":
                raise InvalidTransition("close has unknown outcome")
            state = self._replay_connection(connection)
            durable = (state.get("close") or {}).get("effects", {}).get(effect)
            if durable is not None:
                if durable != observation:
                    raise OperationConflict("close effect replay conflicts with outcome")
                return durable
            _validate_close_observation(state, operation_id, effect, observation)
            now = _now()
            self._append_event(
                connection,
                event_id=f"{operation_id}:effect:{effect}",
                operation_id=operation_id,
                event_type="close_effect_observed",
                payload={"effect": effect, "observation": observation},
                created_at=now,
            )
            state = self._replay_connection(connection)
            effects = (state.get("close") or {}).get("effects", {})
            connection.execute(
                """
                UPDATE operations
                SET phase = 'effect_observed', outcome_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (_canonical_json({"effects": effects}), now, operation_id),
            )
            if effect in {"task_projection_rebuilt", "board_rebuilt"}:
                digest = _required_text(observation.get("digest"), "observation.digest")
                self._upsert_projection(
                    connection,
                    projection_type="task_json" if effect.startswith("task_") else "board",
                    digest=digest,
                    status="current",
                    error=None,
                    now=now,
                )
            return observation

    def record_projection_failure(self, projection_type: str, error: str) -> None:
        projection_type = _required_text(projection_type, "projection_type")
        error = _required_text(error, "error")
        with self._write_transaction() as connection:
            self._upsert_projection(
                connection,
                projection_type=projection_type,
                digest=None,
                status="failed",
                error=error,
                now=_now(),
            )

    def record_projection_current(self, projection_type: str, digest: str) -> None:
        projection_type = _required_text(projection_type, "projection_type")
        digest = _required_digest(digest, "digest")
        with self._write_transaction() as connection:
            self._upsert_projection(
                connection,
                projection_type=projection_type,
                digest=digest,
                status="current",
                error=None,
                now=_now(),
            )

    def mark_close_unknown(
        self,
        *,
        operation_id: str,
        reason: str,
        detail_digest: str,
    ) -> dict[str, Any]:
        payload = {
            "detail_digest": _required_digest(detail_digest, "detail_digest"),
            "reason": _required_text(reason, "reason"),
        }
        with self._write_transaction() as connection:
            operation = self._required_operation(connection, operation_id, "close")
            if not _is_legacy_close_intent(json.loads(operation["intent_json"])):
                raise InvalidTransition("status-only close cannot have an unknown effect")
            if operation["phase"] == "unknown_outcome":
                existing = json.loads(operation["outcome_json"])
                if existing != payload:
                    raise OperationConflict("unknown outcome replay conflicts")
                return existing
            if operation["phase"] in {"authority_committed", "projected"}:
                raise InvalidTransition("closed operation cannot become unknown")
            now = _now()
            self._append_event(
                connection,
                event_id=f"{operation_id}:unknown",
                operation_id=operation_id,
                event_type="close_unknown_outcome",
                payload=payload,
                created_at=now,
            )
            connection.execute(
                """
                UPDATE operations
                SET phase = 'unknown_outcome', outcome_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (_canonical_json(payload), now, operation_id),
            )
            return payload

    def commit_close(self, *, operation_id: str) -> dict[str, Any]:
        with self._write_transaction() as connection:
            operation = self._required_operation(connection, operation_id, "close")
            if operation["phase"] in {"authority_committed", "projected"}:
                return _operation_dict(operation)
            if operation["phase"] == "unknown_outcome":
                raise InvalidTransition("close has unknown outcome")
            legacy = _is_legacy_close_intent(json.loads(operation["intent_json"]))
            state = self._replay_connection(connection)
            effects = set((state.get("close") or {}).get("effects", {}))
            missing = sorted(_CLOSE_EFFECTS - effects) if legacy else []
            if legacy and missing:
                raise InvalidTransition(f"close effects are incomplete: {', '.join(missing)}")
            if not legacy and effects:
                raise InvalidTransition("status-only close cannot commit physical effects")
            now = _now()
            event_payload = {"effects": sorted(effects)} if legacy else {}
            self._append_event(
                connection,
                event_id=f"{operation_id}:closed",
                operation_id=operation_id,
                event_type="closed",
                payload=event_payload,
                created_at=now,
            )
            outcome = (
                {"closed": True, "effects": sorted(effects)}
                if legacy
                else {"closed": True}
            )
            connection.execute(
                """
                UPDATE operations
                SET phase = 'authority_committed', outcome_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (_canonical_json(outcome), now, operation_id),
            )
            return _operation_dict(self._required_operation(connection, operation_id, "close"))

    def mark_projected(
        self,
        *,
        operation_id: str,
        projection_digest: str,
    ) -> dict[str, Any]:
        projection_digest = _required_digest(projection_digest, "projection_digest")
        with self._write_transaction() as connection:
            operation = self._required_operation(connection, operation_id, "close")
            legacy = _is_legacy_close_intent(json.loads(operation["intent_json"]))
            state = self._replay_connection(connection)
            effects = (state.get("close") or {}).get("effects", {})
            if legacy:
                if {"task_projection_rebuilt", "board_rebuilt"} - set(effects):
                    raise InvalidTransition(
                        "projection checkpoint requires task and Board effects"
                    )
                expected_digest = _digest_json(
                    {
                        "board": effects["board_rebuilt"]["digest"],
                        "task_json": effects["task_projection_rebuilt"]["digest"],
                    }
                )
            else:
                expected_digest = sha256(self.task_projection_bytes()).hexdigest()
            if projection_digest != expected_digest:
                raise InvalidTransition(
                    "projection checkpoint digest does not match authority"
                )
            expected = {"projection_digest": projection_digest}
            if operation["phase"] == "projected":
                existing = json.loads(operation["outcome_json"])
                if existing != expected:
                    raise OperationConflict("projection replay conflicts")
                return _operation_dict(operation)
            if operation["phase"] != "authority_committed":
                raise InvalidTransition("projection checkpoint requires committed close")
            now = _now()
            self._append_event(
                connection,
                event_id=f"{operation_id}:projected",
                operation_id=operation_id,
                event_type="projection_checkpointed",
                payload=expected,
                created_at=now,
            )
            self._upsert_projection(
                connection,
                projection_type="close",
                digest=projection_digest,
                status="current",
                error=None,
                now=now,
            )
            if legacy:
                state = self._replay_connection(connection)
                effects = state["close"]["effects"]
                projection_digests = (
                    ("task_json", effects["task_projection_rebuilt"]["digest"]),
                    ("board", effects["board_rebuilt"]["digest"]),
                )
            else:
                projection_digests = (("task_json", projection_digest),)
            for projection_type, digest in projection_digests:
                self._upsert_projection(
                    connection,
                    projection_type=projection_type,
                    digest=digest,
                    status="current",
                    error=None,
                    now=now,
                )
            connection.execute(
                """
                UPDATE operations
                SET phase = 'projected', outcome_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (_canonical_json(expected), now, operation_id),
            )
            return _operation_dict(self._required_operation(connection, operation_id, "close"))

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        connection = self._connect(read_only=True)
        try:
            row = self._operation_row(connection, _required_text(operation_id, "operation_id"))
            return _operation_dict(row) if row is not None else None
        finally:
            connection.close()

    def events(self) -> list[dict[str, Any]]:
        connection = self._connect(read_only=True)
        try:
            return [
                _event_dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE task_run_id = ? ORDER BY position",
                    (self.task_run_id,),
                ).fetchall()
            ]
        finally:
            connection.close()

    def snapshot(self) -> dict[str, Any]:
        connection = self._connect(read_only=True)
        try:
            return self._replay_connection(connection)
        finally:
            connection.close()

    def authority_digest(self) -> str:
        return _digest_json(self.snapshot())

    def task_projection(
        self,
        *,
        close_operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Build deterministic task JSON; any close preview is non-authoritative."""
        state = self.snapshot()
        projected_closed = state["closed"]
        if close_operation_id:
            close = state.get("close") or {}
            if close.get("operation_id") != close_operation_id:
                raise InvalidTransition("close projection operation does not match")
            if close.get("unknown_outcome"):
                raise InvalidTransition("cannot project an unknown close outcome")
            if _is_legacy_close_intent(close["intent"]):
                required = {"pointer_cleared", "directory_archived"}
                if not required.issubset(close.get("effects", {})):
                    raise InvalidTransition(
                        "legacy close projection requires pointer and directory effects"
                    )
            projected_closed = True

        projection = copy.deepcopy(state["task_json_seed"])
        meta = copy.deepcopy(projection.get("meta") or {})
        terminal = state.get("terminal")
        close = state.get("close") or {}
        task_run = {
            "authority": "sqlite",
            "id": self.task_run_id,
            "projection": True,
            "state": "closed" if projected_closed else state["status"],
        }
        if projected_closed and _is_legacy_close_intent(close.get("intent") or {}):
            task_run["archive_path"] = close["intent"]["archive_path"]
        meta["task_run"] = task_run
        projection["meta"] = meta
        machine = meta.get("state_machine")
        status = projection.get("status")
        tier = projection.get("tier")
        schema_version = (
            machine.get("schema_version") if isinstance(machine, dict) else None
        )
        current_state = (
            machine.get("current_state") if isinstance(machine, dict) else None
        )
        hsm_terminal_projection = terminal is not None and (
            isinstance(status, str)
            and status in {"blocked", "in_progress", "planning", "review"}
            and meta.get("workflow_mode") == "harness_state_machine"
            and isinstance(tier, str)
            and tier in _ACTIVE_HSM_STATES
            and isinstance(machine, dict)
            and machine.get("kind") == tier
            and (
                schema_version is None
                or (type(schema_version) is int and schema_version in {1, 2})
            )
            and isinstance(current_state, str)
            and current_state in _ACTIVE_HSM_STATES[tier]
        )
        if hsm_terminal_projection:
            return projection
        projection["status"] = terminal["disposition"] if terminal else state["status"]
        if terminal and terminal["disposition"] == "completed":
            projection["completedAt"] = terminal["created_at"][:10]
        git_evidence = (close.get("intent") or {}).get("git_evidence") or {}
        reconciled_commit = (
            (terminal.get("evidence") or {})
            .get("commit_reconciliation", {})
            .get("head_commit")
            if terminal
            else None
        )
        if terminal and terminal["disposition"] == "completed":
            if reconciled_commit:
                projection["commit"] = reconciled_commit
            elif git_evidence.get("commit"):
                projection["commit"] = git_evidence["commit"]
        return projection

    def task_projection_bytes(self, *, close_operation_id: str | None = None) -> bytes:
        return json_bytes(self.task_projection(close_operation_id=close_operation_id))

    def projection_status(self, projection_type: str) -> dict[str, Any]:
        connection = self._connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT * FROM projection_checkpoints WHERE projection_type = ?",
                (_required_text(projection_type, "projection_type"),),
            ).fetchone()
            return dict(row) if row is not None else {"status": "missing"}
        finally:
            connection.close()

    def _record_single_event(
        self,
        *,
        operation_id: str,
        kind: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        operation_id = _required_text(operation_id, "operation_id")
        input_digest = _digest_json(payload)
        with self._write_transaction() as connection:
            existing = self._operation_row(connection, operation_id)
            if existing is not None:
                self._assert_operation(existing, kind, input_digest)
                return _operation_dict(existing)
            now = _now()
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, task_run_id, kind, input_digest, phase,
                    intent_json, outcome_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'authority_committed', ?, '{}', ?, ?)
                """,
                (
                    operation_id,
                    self.task_run_id,
                    kind,
                    input_digest,
                    _canonical_json(payload),
                    now,
                    now,
                ),
            )
            event = self._append_event(
                connection,
                event_id=operation_id,
                operation_id=operation_id,
                event_type=event_type,
                payload=payload,
                created_at=now,
            )
            outcome = {"event_digest": event["event_digest"], "position": event["position"]}
            connection.execute(
                "UPDATE operations SET outcome_json = ? WHERE operation_id = ?",
                (_canonical_json(outcome), operation_id),
            )
            return _operation_dict(self._required_operation(connection, operation_id, kind))

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        payload_json = _canonical_json(payload)
        existing = connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["operation_id"] != operation_id
                or existing["event_type"] != event_type
                or existing["payload_json"] != payload_json
            ):
                raise EventConflict("event ID was reused with different input")
            return _event_dict(existing)

        state = self._replay_connection(connection, allow_empty=True)
        previous = connection.execute(
            "SELECT event_digest FROM events ORDER BY position DESC LIMIT 1"
        ).fetchone()
        previous_digest = previous["event_digest"] if previous else None
        event_digest = _digest_json(
            {
                "created_at": created_at,
                "event_id": event_id,
                "event_type": event_type,
                "operation_id": operation_id,
                "payload": payload,
                "previous_digest": previous_digest,
                "task_run_id": self.task_run_id,
            }
        )
        connection.execute(
            """
            INSERT INTO events (
                event_id, task_run_id, operation_id, event_type, payload_json,
                previous_digest, event_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                self.task_run_id,
                operation_id,
                event_type,
                payload_json,
                previous_digest,
                event_digest,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        event = _event_dict(row)
        event["payload"] = payload
        event["created_at"] = created_at
        self._reduce_event(state, event)
        self._write_materialization(connection, state, created_at)
        return _event_dict(row)

    def _replay_connection(
        self,
        connection: sqlite3.Connection,
        *,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT * FROM events WHERE task_run_id = ? ORDER BY position",
            (self.task_run_id,),
        ).fetchall()
        if not rows and not allow_empty:
            raise EventConflict("TaskRun event stream is empty")
        state: dict[str, Any] = {}
        previous_digest: str | None = None
        expected_position = 1
        for row in rows:
            event = _event_dict(row)
            if event["position"] != expected_position:
                raise EventConflict("TaskRun event positions are not contiguous")
            if event["previous_digest"] != previous_digest:
                raise EventConflict("TaskRun event hash chain is broken")
            expected_digest = _digest_json(
                {
                    "created_at": event["created_at"],
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "operation_id": event["operation_id"],
                    "payload": event["payload"],
                    "previous_digest": previous_digest,
                    "task_run_id": self.task_run_id,
                }
            )
            if event["event_digest"] != expected_digest:
                raise EventConflict("TaskRun event digest is invalid")
            self._reduce_event(state, event)
            previous_digest = event["event_digest"]
            expected_position += 1
        if rows:
            state["position"] = rows[-1]["position"]
        return state

    def _reduce_event(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == "taskrun_created":
            if state:
                raise InvalidTransition("taskrun_created must be the first event")
            execution_config = _execution_seed(payload["task_json"])
            state.update(
                {
                    "close": None,
                    "closed": False,
                    "execution": (
                        {
                            "actions": {},
                            "candidate_ready": None,
                            "commit_reconciliation": None,
                            "config": execution_config,
                            "decisions": [],
                            "final_candidate": None,
                            "final_request": None,
                            "final_response": None,
                            "paused": None,
                            "resumes": [],
                            "terminal_authority": None,
                            **(
                                {"delivery_settlement": None}
                                if execution_config["start_envelope"].get(
                                    "delivery_slots"
                                )
                                else {}
                            ),
                        }
                        if execution_config
                        else None
                    ),
                    "status": "created",
                    "task_dir_name": payload["task_dir_name"],
                    "task_json_seed": copy.deepcopy(payload["task_json"]),
                    "task_run_id": payload["task_run_id"],
                    "terminal": None,
                }
            )
            return
        if not state:
            raise InvalidTransition("TaskRun event stream has no genesis")
        if event_type == "run_started":
            if state["status"] != "created" or state["closed"]:
                raise InvalidTransition("run_started requires created state")
            state["status"] = "running"
            return
        if event_type == "terminal_recorded":
            if state["terminal"] is not None or state["closed"]:
                raise InvalidTransition("terminal disposition is already recorded")
            if state["status"] not in {"created", "running"}:
                raise InvalidTransition("terminal disposition requires an open TaskRun")
            disposition = payload.get("disposition")
            if disposition not in _TERMINAL:
                raise InvalidTransition("invalid terminal disposition")
            if disposition == "completed" and state["status"] != "running":
                raise InvalidTransition("completed disposition requires a started TaskRun")
            execution = state.get("execution")
            if disposition == "completed" and execution is not None:
                terminal_authority = execution.get("terminal_authority")
                if (
                    execution.get("paused")
                    or terminal_authority is None
                    or payload.get("authorization_ref")
                    != terminal_authority.get("final_response_event_digest")
                    or payload.get("evidence") != terminal_authority
                ):
                    raise InvalidTransition(
                        "completed execution requires the exact direct final response authority"
                    )
            state["status"] = disposition
            state["terminal"] = {
                **copy.deepcopy(payload),
                "created_at": event["created_at"],
                "event_id": event["event_id"],
                "position": event.get("position"),
            }
            return
        execution = state.get("execution")
        if event_type in {
            "action_planned",
            "action_result_validated",
            "action_reviewed",
            "commit_reconciliation_recorded",
            "commit_reconciliation_requested",
            "delivery_slots_reconciled",
            "execution_intervened",
            "execution_resumed",
            "final_candidate_reviewed",
            "final_request_recorded",
            "final_response_recorded",
            "strategy_decided",
        }:
            if execution is None or state["status"] != "running" or state["closed"]:
                raise InvalidTransition(
                    "execution facts require one running bootstrapped TaskRun"
                )
        if event_type == "action_planned":
            intent = _required_object(payload.get("intent"), "intent")
            _validate_execution_fact(intent, _EXECUTION_INTENT_KEYS, "intent")
            supplied_input_digest = intent["input_digest"]
            if supplied_input_digest != _digest_json(
                {key: value for key, value in intent.items() if key != "input_digest"}
            ):
                raise InvalidTransition("action intent input digest is invalid")
            key = _execution_action_key(intent, state["task_run_id"])
            if execution["paused"] or key in execution["actions"]:
                raise InvalidTransition("action attempt is already planned or paused")
            prior = sorted(
                (
                    item
                    for item in execution["actions"].values()
                    if item["intent"]["action_id"] == intent["action_id"]
                ),
                key=lambda item: item["intent"]["attempt"],
            )
            if not prior and intent["attempt"] != 1:
                raise InvalidTransition("first action attempt must be one")
            if prior:
                previous = prior[-1]
                terminal = _terminal_strategy_decision(previous["decisions"])
                if (
                    intent["attempt"] != previous["intent"]["attempt"] + 1
                    or terminal is None
                    or terminal["kind"] != "retry_exact_slice"
                    or intent["problem_id"] != previous["intent"]["problem_id"]
                    or intent["requirement_ids"]
                    != previous["intent"]["requirement_ids"]
                ):
                    raise InvalidTransition(
                        "replacement must preserve the exact problem slice"
                    )
            execution["actions"][key] = {
                "decisions": [],
                "intent": copy.deepcopy(intent),
                "result": None,
                "review": None,
            }
            return
        if event_type == "action_result_validated":
            result = _required_object(payload.get("result"), "result")
            _validate_execution_fact(result, _EXECUTION_RESULT_KEYS, "result")
            if result["result_digest"] != _digest_json(
                {key: value for key, value in result.items() if key != "result_digest"}
            ):
                raise InvalidTransition("action result digest is invalid")
            action = _execution_action(execution, result, state["task_run_id"])
            if action["result"] is not None:
                raise InvalidTransition("action result is already recorded")
            if (
                result.get("input_digest") != action["intent"]["input_digest"]
                or result.get("freshness") != action["intent"]["freshness"]
            ):
                raise InvalidTransition("action result identity is stale")
            action["result"] = copy.deepcopy(result)
            return
        if event_type == "action_reviewed":
            review = _required_object(payload.get("review"), "review")
            _validate_execution_fact(review, _EXECUTION_REVIEW_KEYS, "review")
            action = _execution_action(execution, review, state["task_run_id"])
            if action["result"] is None or action["review"] is not None:
                raise InvalidTransition("action review requires one unreviewed result")
            if _terminal_strategy_decision(action["decisions"]) is not None:
                raise InvalidTransition("action review cannot follow a terminal decision")
            if (
                review.get("input_digest") != action["intent"]["input_digest"]
                or review.get("candidate_digest")
                != action["result"].get("candidate_digest")
                or review.get("reviewer_id") == action["result"].get("worker_id")
            ):
                raise InvalidTransition("action review identity is invalid")
            findings = review.get("findings")
            verdict = review.get("verdict")
            if not isinstance(findings, list) or verdict not in {
                "accepted",
                "changes_required",
            }:
                raise InvalidTransition("action review verdict is invalid")
            if (verdict == "accepted" and findings) or (
                verdict == "changes_required" and not findings
            ):
                raise InvalidTransition("action review findings do not match verdict")
            action["review"] = copy.deepcopy(review)
            return
        if event_type == "strategy_decided":
            decision = _required_object(payload.get("decision"), "decision")
            _validate_execution_fact(decision, _EXECUTION_DECISION_KEYS, "decision")
            if decision["next_input_digest"] != _digest_json(
                {
                    key: value
                    for key, value in decision.items()
                    if key != "next_input_digest"
                }
            ):
                raise InvalidTransition("strategy decision digest is invalid")
            action = _execution_action(execution, decision, state["task_run_id"])
            kind = decision.get("kind")
            if kind not in _STRATEGY_DECISIONS or action["result"] is None:
                raise InvalidTransition("strategy decision is invalid")
            if decision.get("input_digest") != action["intent"]["input_digest"]:
                raise InvalidTransition("strategy decision input is stale")
            terminal = _terminal_strategy_decision(action["decisions"])
            if terminal is not None or any(
                item["kind"] == kind for item in action["decisions"]
            ):
                raise InvalidTransition("strategy decision is duplicated or terminal")
            if kind == "review_required" and action["review"] is not None:
                raise InvalidTransition("review_required cannot follow a review")
            if kind in {"continue", "candidate_ready"} and (
                action["review"] is None
                or action["review"].get("verdict") != "accepted"
            ):
                raise InvalidTransition("accepted strategy decision requires review")
            if kind == "candidate_ready" and execution["config"]["strategy"] != "single":
                raise InvalidTransition("loop readiness requires a final candidate review")
            action["decisions"].append(copy.deepcopy(decision))
            execution["decisions"].append(copy.deepcopy(decision))
            if kind == "candidate_ready":
                if execution["candidate_ready"] is not None:
                    raise InvalidTransition("candidate_ready authority already exists")
                execution["candidate_ready"] = {
                    "authority_event_digest": event["event_digest"],
                    "authority_position": event["position"],
                    "candidate_digest": action["result"]["candidate_digest"],
                }
            if kind in {"pause", "intervention"}:
                execution["paused"] = copy.deepcopy(decision)
            return
        if event_type == "execution_intervened":
            intervention = _required_object(
                payload.get("intervention"), "intervention"
            )
            _validate_execution_fact(
                intervention, _EXECUTION_INTERVENTION_KEYS, "intervention"
            )
            expected_digest = _digest_json(
                {
                    key: value
                    for key, value in intervention.items()
                    if key != "intervention_digest"
                }
            )
            if intervention["intervention_digest"] != expected_digest:
                raise InvalidTransition("execution intervention digest is invalid")
            if intervention.get("phase") != "result":
                raise InvalidTransition("execution intervention phase is invalid")
            if intervention.get("reason") not in _INTERVENTION_REASONS:
                raise InvalidTransition("execution intervention reason is invalid")
            generation = intervention.get("generation")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation != len(execution["resumes"]) + 1
            ):
                raise InvalidTransition("execution intervention generation is stale")
            action = _execution_action(execution, intervention, state["task_run_id"])
            if (
                execution["paused"] is not None
                or action["result"] is not None
                or _terminal_strategy_decision(action["decisions"]) is not None
                or intervention["input_digest"] != action["intent"]["input_digest"]
            ):
                raise InvalidTransition("execution intervention identity is stale")
            execution["paused"] = {
                **copy.deepcopy(intervention),
                "authority_event_digest": event["event_digest"],
            }
            return
        if event_type == "execution_resumed":
            resume = _required_object(payload.get("resume"), "resume")
            if set(resume) != _EXECUTION_RESUME_KEYS:
                raise InvalidTransition("execution resume keys do not match the schema")
            if resume.get("task_run_id") != state["task_run_id"]:
                raise InvalidTransition("execution resume TaskRun identity is stale")
            authorization_ref = _required_text(
                resume.get("authorization_ref"), "resume.authorization_ref"
            )
            intervention_digest = _required_digest(
                resume.get("intervention_digest"), "resume.intervention_digest"
            )
            paused = execution.get("paused")
            if (
                not isinstance(paused, dict)
                or paused.get("intervention_digest") != intervention_digest
            ):
                raise InvalidTransition("execution resume does not match the intervention")
            envelope = execution["config"]["start_envelope"]
            prior_resume_refs = {
                item["authorization_ref"] for item in execution["resumes"]
            }
            if (
                authorization_ref == envelope.get("authorization_ref")
                or authorization_ref in set(envelope.get("approval_refs", []))
                or authorization_ref in prior_resume_refs
            ):
                raise InvalidTransition("execution resume requires separate authority")
            execution["resumes"].append(
                {
                    **copy.deepcopy(resume),
                    "authority_event_digest": event["event_digest"],
                }
            )
            execution["paused"] = None
            return
        if event_type == "commit_reconciliation_requested":
            request = _validated_commit_reconciliation_request(
                payload.get("request")
            )
            envelope = execution["config"]["start_envelope"]
            expected_prior = {
                **copy.deepcopy(envelope["identities"]),
                "context": envelope["context_digest"],
            }
            if (
                execution["config"]["strategy"] != "single"
                or execution["paused"] is not None
                or execution["commit_reconciliation"] is not None
                or execution["final_request"] is not None
                or execution["final_response"] is not None
                or execution.get("delivery_settlement") is not None
                or request["task_run_id"] != state["task_run_id"]
                or request["prior_identities"] != expected_prior
            ):
                raise InvalidTransition(
                    "commit reconciliation request is not currently admissible"
                )
            execution["commit_reconciliation"] = {
                "request": {
                    **request,
                    "authority_event_digest": event["event_digest"],
                    "authority_event_position": event["position"],
                },
                "response": None,
            }
            return
        if event_type == "commit_reconciliation_recorded":
            receipt = _validated_commit_reconciliation_receipt(
                payload.get("receipt")
            )
            reconciliation = execution.get("commit_reconciliation")
            request = (
                reconciliation.get("request")
                if isinstance(reconciliation, dict)
                else None
            )
            if (
                not isinstance(request, dict)
                or reconciliation.get("response") is not None
                or receipt["task_run_id"] != state["task_run_id"]
                or receipt["request_digest"] != request["request_digest"]
                or receipt["request_event_digest"]
                != request["authority_event_digest"]
            ):
                raise InvalidTransition(
                    "commit reconciliation receipt does not bind the exact request"
                )
            envelope = execution["config"]["start_envelope"]
            prior_authorizations = {
                envelope["authorization_ref"],
                *envelope.get("approval_refs", []),
                *(item["authorization_ref"] for item in execution["resumes"]),
            }
            if receipt["authorization_ref"] in prior_authorizations:
                raise InvalidTransition(
                    "commit reconciliation requires separate authority"
                )
            reconciliation["response"] = {
                "receipt": receipt,
                "authority_event_digest": event["event_digest"],
                "authority_event_position": event["position"],
            }
            return
        if event_type == "final_candidate_reviewed":
            final = _required_object(
                payload.get("final_candidate"), "final_candidate"
            )
            if set(final) != _FINAL_CANDIDATE_KEYS:
                raise InvalidTransition("final candidate keys do not match the schema")
            if (
                execution["config"]["strategy"] != "loop"
                or execution["paused"] is not None
                or execution["candidate_ready"] is not None
                or execution["final_candidate"] is not None
                or final.get("task_run_id") != state["task_run_id"]
            ):
                raise InvalidTransition("final candidate review is not currently admissible")
            candidate_digest = _required_digest(
                final.get("candidate_digest"), "final_candidate.candidate_digest"
            )
            reviewer_id = _required_text(
                final.get("reviewer_id"), "final_candidate.reviewer_id"
            )
            _required_text(final.get("review_id"), "final_candidate.review_id")
            if (
                final.get("verdict") != "accepted"
                or final.get("findings") != []
                or reviewer_id
                not in execution["config"]["start_envelope"]["reviewers"]
            ):
                raise InvalidTransition("final candidate requires an accepted approved review")
            components = _execution_components(final.get("component_candidates"))
            expected = _accepted_execution_components(execution)
            if components != expected or reviewer_id in {
                item["result"]["worker_id"]
                for item in execution["actions"].values()
                if item["result"] is not None
            }:
                raise InvalidTransition("final candidate components or reviewer are invalid")
            execution["final_candidate"] = {
                **copy.deepcopy(final),
                "authority_event_digest": event["event_digest"],
            }
            execution["candidate_ready"] = {
                "authority_event_digest": event["event_digest"],
                "authority_position": event["position"],
                "candidate_digest": candidate_digest,
            }
            return
        if event_type == "delivery_slots_reconciled":
            if (
                execution.get("delivery_settlement") is not None
                or execution.get("final_request") is not None
                or not isinstance(execution.get("candidate_ready"), dict)
            ):
                raise InvalidTransition(
                    "delivery settlement requires one unreconciled ready candidate"
                )
            settlement = _validated_delivery_settlement(
                payload.get("settlement"),
                task_run_id=state["task_run_id"],
                candidate_ready=execution["candidate_ready"],
                configured_slots=execution["config"]["start_envelope"].get(
                    "delivery_slots"
                ),
            )
            execution["delivery_settlement"] = {
                **settlement,
                "authority_event_digest": event["event_digest"],
                "authority_event_position": event["position"],
            }
            return
        if event_type == "final_request_recorded":
            final_request = _required_object(
                payload.get("final_request"), "final_request"
            )
            if execution["final_request"] is not None:
                raise InvalidTransition("final request authority already exists")
            request_state = copy.deepcopy(state)
            request_state["position"] = event["position"] - 1
            if final_request != build_final_request(request_state):
                raise InvalidTransition(
                    "final request does not match current candidate-ready authority"
                )
            execution["final_request"] = {
                **copy.deepcopy(final_request),
                "authority_event_digest": event["event_digest"],
                "authority_event_position": event["position"],
            }
            return
        if event_type == "final_response_recorded":
            receipt = _validated_final_gate_receipt(
                payload.get("final_gate_receipt")
            )
            final_request = execution.get("final_request")
            if (
                not isinstance(final_request, dict)
                or execution["final_response"] is not None
                or receipt["task_run_id"] != state["task_run_id"]
                or receipt["final_request_digest"]
                != final_request["request_digest"]
                or receipt["final_request_event_digest"]
                != final_request["authority_event_digest"]
            ):
                raise InvalidTransition(
                    "final gate receipt does not bind the exact current final request"
                )
            execution["final_response"] = {
                "final_gate_receipt": copy.deepcopy(receipt),
                "authority_event_digest": event["event_digest"],
                "authority_event_position": event["position"],
            }
            execution["terminal_authority"] = {
                "candidate_ready": copy.deepcopy(execution["candidate_ready"]),
                "final_gate_receipt": copy.deepcopy(receipt),
                "final_request_authority_position": final_request[
                    "authority_event_position"
                ],
                "final_request_digest": final_request["request_digest"],
                "final_request_event_digest": final_request[
                    "authority_event_digest"
                ],
                "final_response_authority_position": event["position"],
                "final_response_event_digest": event["event_digest"],
            }
            commit_reconciliation = _commit_reconciliation_binding(execution)
            if commit_reconciliation is not None:
                execution["terminal_authority"]["commit_reconciliation"] = (
                    commit_reconciliation
                )
            return
        if event_type == "close_prepared":
            if state["terminal"] is None or state["closed"] or state["close"] is not None:
                raise InvalidTransition("close_prepared requires one open terminal TaskRun")
            state["close"] = {
                "effects": {},
                "intent": copy.deepcopy(payload["intent"]),
                "operation_id": event["operation_id"],
                "unknown_outcome": None,
            }
            return
        close = state.get("close")
        if event_type == "close_effect_observed":
            if not close or close["operation_id"] != event["operation_id"]:
                raise InvalidTransition("close effect has no matching operation")
            if close["unknown_outcome"] or state["closed"]:
                raise InvalidTransition("close effect cannot follow terminal close state")
            if not _is_legacy_close_intent(close["intent"]):
                raise InvalidTransition("status-only close cannot observe physical effects")
            effect = payload.get("effect")
            if effect not in _CLOSE_EFFECTS:
                raise InvalidTransition("unknown close effect")
            if effect in close["effects"]:
                raise InvalidTransition("close effect is duplicated")
            close["effects"][effect] = copy.deepcopy(payload["observation"])
            return
        if event_type == "close_unknown_outcome":
            if not close or close["operation_id"] != event["operation_id"] or state["closed"]:
                raise InvalidTransition("unknown close outcome has no open operation")
            if not _is_legacy_close_intent(close["intent"]):
                raise InvalidTransition("status-only close cannot have an unknown effect")
            close["unknown_outcome"] = copy.deepcopy(payload)
            return
        if event_type == "closed":
            if not close or close["operation_id"] != event["operation_id"]:
                raise InvalidTransition("closed has no matching operation")
            if close["unknown_outcome"]:
                raise InvalidTransition("unknown close outcome cannot become closed")
            legacy = _is_legacy_close_intent(close["intent"])
            missing = _CLOSE_EFFECTS - set(close["effects"])
            if legacy and missing:
                raise InvalidTransition("closed requires every close effect")
            if not legacy and (close["effects"] or payload):
                raise InvalidTransition("status-only closed event cannot contain effects")
            state["closed"] = True
            return
        if event_type == "projection_checkpointed":
            if not state["closed"]:
                raise InvalidTransition("projection checkpoint requires closed state")
            close["projection_digest"] = payload["projection_digest"]
            return
        raise InvalidTransition(f"unsupported TaskRun event type: {event_type}")

    def _write_materialization(
        self,
        connection: sqlite3.Connection,
        state: dict[str, Any],
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            UPDATE task_runs
            SET status = ?, terminal_json = ?, close_json = ?, closed = ?, updated_at = ?
            WHERE task_run_id = ?
            """,
            (
                state["status"],
                _canonical_json(state["terminal"]) if state["terminal"] else None,
                _canonical_json(state["close"]) if state["close"] else None,
                1 if state["closed"] else 0,
                updated_at,
                self.task_run_id,
            ),
        )

    def _validate_reopen(self) -> None:
        connection = self._connect(read_only=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise TaskRunError("TaskRun SQLite integrity check failed")
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if version is None or version["value"] != str(SCHEMA_VERSION):
                raise TaskRunError("unsupported TaskRun schema version")
            state = self._replay_connection(connection)
            self._validate_operations(connection, state)
            if connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] != 1:
                raise TaskRunError("TaskRun database must contain exactly one run")
            row = connection.execute(
                "SELECT * FROM task_runs WHERE task_run_id = ?",
                (self.task_run_id,),
            ).fetchone()
            if row is None:
                raise TaskRunError("TaskRun materialization is missing")
            expected = {
                "close": json.loads(row["close_json"]) if row["close_json"] else None,
                "closed": bool(row["closed"]),
                "status": row["status"],
                "task_dir_name": row["task_dir_name"],
                "task_json_seed": json.loads(row["task_json_seed"]),
                "terminal": json.loads(row["terminal_json"]) if row["terminal_json"] else None,
            }
            actual = {key: state[key] for key in expected}
            if actual != expected:
                raise EventConflict("TaskRun materialization does not match replay")
        finally:
            connection.close()

    def _validate_operations(
        self,
        connection: sqlite3.Connection,
        state: dict[str, Any],
    ) -> None:
        events = {
            row["event_id"]: row
            for row in connection.execute(
                "SELECT * FROM events WHERE task_run_id = ?",
                (self.task_run_id,),
            ).fetchall()
        }
        for operation in connection.execute(
            "SELECT * FROM operations WHERE task_run_id = ? ORDER BY operation_id",
            (self.task_run_id,),
        ).fetchall():
            try:
                intent = json.loads(operation["intent_json"])
                outcome = json.loads(operation["outcome_json"])
            except json.JSONDecodeError as exc:
                raise OperationConflict("operation JSON is invalid") from exc
            if operation["phase"] not in _OPERATION_PHASES:
                raise OperationConflict("operation has an invalid phase")
            if operation["input_digest"] != _digest_json(intent):
                raise OperationConflict("operation input digest is invalid")

            operation_id = operation["operation_id"]
            kind = operation["kind"]
            if kind in _EVENT_KIND_TYPES:
                event = events.get(operation_id)
                expected_type = _EVENT_KIND_TYPES[kind]
                expected_outcome = (
                    {}
                    if kind == "initialize"
                    else {
                        "event_digest": event["event_digest"] if event else None,
                        "position": event["position"] if event else None,
                    }
                )
                if (
                    event is None
                    or event["event_type"] != expected_type
                    or operation["phase"] != "authority_committed"
                    or outcome != expected_outcome
                ):
                    raise OperationConflict("operation outcome does not match its event")
                continue
            if kind != "close":
                raise OperationConflict("operation kind is unsupported")

            close = state.get("close") or {}
            if close.get("operation_id") != operation_id:
                raise OperationConflict("close operation does not match replay")
            legacy = _is_legacy_close_intent(close.get("intent") or {})
            if close.get("unknown_outcome"):
                if not legacy:
                    raise OperationConflict("status-only close has an unknown effect")
                expected_phase = "unknown_outcome"
                expected_outcome = close["unknown_outcome"]
            elif close.get("projection_digest"):
                expected_phase = "projected"
                expected_outcome = {
                    "projection_digest": close["projection_digest"]
                }
            elif state["closed"]:
                expected_phase = "authority_committed"
                expected_outcome = (
                    {
                        "closed": True,
                        "effects": sorted(close["effects"]),
                    }
                    if legacy
                    else {"closed": True}
                )
            elif close.get("effects"):
                if not legacy:
                    raise OperationConflict("status-only close contains physical effects")
                expected_phase = "effect_observed"
                expected_outcome = {"effects": close["effects"]}
            else:
                expected_phase = "prepared"
                expected_outcome = {}
            if operation["phase"] != expected_phase or outcome != expected_outcome:
                raise OperationConflict("close operation outcome does not match replay")

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
        elif row["value"] != str(SCHEMA_VERSION):
            raise TaskRunError("unsupported TaskRun schema version")
        connection.commit()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
                timeout=5,
            )
        else:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _operation_row(
        self, connection: sqlite3.Connection, operation_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND task_run_id = ?",
            (operation_id, self.task_run_id),
        ).fetchone()

    def _required_operation(
        self, connection: sqlite3.Connection, operation_id: str, kind: str
    ) -> sqlite3.Row:
        row = self._operation_row(connection, _required_text(operation_id, "operation_id"))
        if row is None or row["kind"] != kind:
            raise OperationConflict("operation does not exist with the required kind")
        if row["phase"] not in _OPERATION_PHASES:
            raise OperationConflict("operation has an invalid phase")
        return row

    def _assert_operation(
        self, row: sqlite3.Row, kind: str, input_digest: str
    ) -> None:
        if row["kind"] != kind or row["input_digest"] != input_digest:
            raise OperationConflict("operation ID was reused with different input")

    def _required_close_operation_id(self, operation_id: str) -> str:
        value = _required_text(operation_id, "operation_id")
        prefix = f"close:{self.task_run_id}:"
        suffix = value[len(prefix) :] if value.startswith(prefix) else ""
        if not suffix or not _SAFE_ID.fullmatch(suffix):
            raise OperationConflict(
                f"close operation_id must match close:{self.task_run_id}:<attempt>"
            )
        return value

    def _upsert_projection(
        self,
        connection: sqlite3.Connection,
        *,
        projection_type: str,
        digest: str | None,
        status: str,
        error: str | None,
        now: str,
    ) -> None:
        position = connection.execute(
            "SELECT COALESCE(MAX(position), 0) FROM events WHERE task_run_id = ?",
            (self.task_run_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO projection_checkpoints (
                projection_type, task_run_id, source_position, digest, status,
                error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(projection_type) DO UPDATE SET
                source_position = excluded.source_position,
                digest = excluded.digest,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                projection_type,
                self.task_run_id,
                position,
                digest,
                status,
                error,
                now,
            ),
        )


def _delivery_settlement(parent: TaskRun) -> dict[str, Any]:
    state = parent.snapshot()
    execution = state.get("execution")
    if not isinstance(execution, dict):
        raise InvalidTransition("delivery settlement requires an execution parent")
    existing = execution.get("delivery_settlement")
    if isinstance(existing, dict):
        return {
            key: copy.deepcopy(existing[key])
            for key in _DELIVERY_SETTLEMENT_KEYS
        }
    ready = execution.get("candidate_ready")
    slots = execution.get("config", {}).get("start_envelope", {}).get(
        "delivery_slots"
    )
    if not isinstance(ready, dict) or not isinstance(slots, list) or not slots:
        raise InvalidTransition(
            "delivery settlement requires one ready parent with frozen slots"
        )
    seen_runs: set[str] = set()
    settled_slots = [
        _settled_delivery_slot(parent.repo_root, slot, seen_runs) for slot in slots
    ]
    settlement = {
        "candidate_ready_event_digest": ready["authority_event_digest"],
        "slots": settled_slots,
        "task_run_id": parent.task_run_id,
    }
    return {**settlement, "settlement_digest": _digest_json(settlement)}


def _settled_delivery_slot(
    repo_root: Path,
    configured: object,
    seen_runs: set[str],
) -> dict[str, Any]:
    slot = _required_object(configured, "delivery slot")
    task_dir_name = _required_task_dir_name(
        _required_text(slot.get("initial_task_dir_name"), "initial child task")
    )
    run_id = _required_text(slot.get("initial_task_run_id"), "initial child TaskRun")
    expected_seed = None
    expected_envelope = None
    attempts = []
    while True:
        if run_id in seen_runs:
            raise InvalidTransition("delivery slot successor chain is cyclic or shared")
        seen_runs.add(run_id)
        try:
            run = TaskRun.open(repo_root, run_id)
            child = run.snapshot()
        except TaskRunError as exc:
            raise InvalidTransition("delivery slot TaskRun authority is unavailable") from exc
        execution = child.get("execution")
        config = execution.get("config") if isinstance(execution, dict) else None
        envelope = config.get("start_envelope") if isinstance(config, dict) else None
        strategy = config.get("strategy") if isinstance(config, dict) else None
        if (
            child.get("task_run_id") != run_id
            or child.get("task_dir_name") != task_dir_name
            or strategy not in {"single", "loop"}
            or not isinstance(envelope, dict)
            or expected_seed is not None
            and _without_execution_binding(child.get("task_json_seed"))
            != _without_execution_binding(expected_seed)
            or expected_envelope is not None
            and envelope != expected_envelope
        ):
            raise InvalidTransition("delivery slot TaskRun binding changed")
        seed = child.get("task_json_seed")
        actions = envelope.get("actions")
        if not isinstance(seed, dict) or not isinstance(actions, list):
            raise InvalidTransition("delivery slot execution contract is invalid")
        try:
            requirement_ids = sorted(
                {
                    requirement
                    for action in actions
                    for requirement in action["requirement_ids"]
                }
            )
            touches = sorted(seed["touches"])
        except (KeyError, TypeError):
            raise InvalidTransition("delivery slot execution contract is invalid") from None
        if (
            requirement_ids != slot.get("requirement_ids")
            or seed.get("scope") != slot.get("scope")
            or touches != slot.get("touches")
        ):
            raise InvalidTransition("delivery slot attempt changed its frozen contract")
        terminal = child.get("terminal")
        if not isinstance(terminal, dict):
            raise InvalidTransition("delivery slot attempt is not terminal")
        position = terminal.get("position")
        terminal_event = next(
            (event for event in run.events() if event["position"] == position),
            None,
        )
        disposition = terminal.get("disposition")
        if (
            not isinstance(terminal_event, dict)
            or terminal_event.get("event_type") != "terminal_recorded"
            or terminal_event.get("payload", {}).get("disposition") != disposition
            or disposition not in {"completed", "cancelled"}
        ):
            raise InvalidTransition("delivery slot terminal event is invalid")
        attempt = {
            "disposition": disposition,
            "strategy": strategy,
            "successor_task_run_id": None,
            "task_dir_name": task_dir_name,
            "task_run_id": run_id,
            "terminal_event_digest": terminal_event["event_digest"],
            "terminal_event_position": terminal_event["position"],
        }
        attempts.append(attempt)
        if disposition == "completed":
            final_request = execution.get("final_request")
            if (
                not isinstance(final_request, dict)
                or final_request.get("requirement_ids") != slot.get("requirement_ids")
                or not isinstance(execution.get("terminal_authority"), dict)
            ):
                raise InvalidTransition(
                    "delivery slot completion does not bind its requirements"
                )
            break
        evidence = terminal.get("evidence")
        successor = evidence.get("successor") if isinstance(evidence, dict) else None
        next_run_id = evidence.get("superseded_by") if isinstance(evidence, dict) else None
        if (
            not isinstance(evidence, dict)
            or evidence.get("kind") != "material_drift"
            or not isinstance(successor, dict)
            or not isinstance(next_run_id, str)
            or not re.fullmatch(r"task-[0-9a-f]{64}", next_run_id)
            or not isinstance(successor.get("task_json"), dict)
            or not isinstance(successor.get("start_envelope"), dict)
        ):
            raise InvalidTransition("delivery slot cancellation has no exact successor")
        task_dir_name = _required_task_dir_name(
            _required_text(successor.get("task_dir_name"), "successor task")
        )
        attempt["successor_task_run_id"] = next_run_id
        run_id = next_run_id
        expected_seed = successor["task_json"]
        expected_envelope = successor["start_envelope"]
    return {
        "attempts": attempts,
        "fulfilled_by": attempts[-1]["task_run_id"],
        "requirement_ids": copy.deepcopy(slot["requirement_ids"]),
        "slot_digest": slot["slot_digest"],
        "slot_id": slot["slot_id"],
    }


def _without_execution_binding(value: object) -> dict[str, Any]:
    task = _required_object(value, "successor task seed")
    meta = task.get("meta") or {}
    if not isinstance(meta, dict):
        raise InvalidTransition("successor task seed metadata is invalid")
    meta.pop("execution", None)
    meta.pop("task_run", None)
    task["meta"] = meta
    return task


def _validated_delivery_settlement(
    value: object,
    *,
    task_run_id: str,
    candidate_ready: dict[str, Any],
    configured_slots: object,
) -> dict[str, Any]:
    settlement = _required_object(value, "delivery settlement")
    if set(settlement) != _DELIVERY_SETTLEMENT_KEYS:
        raise InvalidTransition("delivery settlement keys do not match the schema")
    if (
        settlement.get("task_run_id") != task_run_id
        or settlement.get("candidate_ready_event_digest")
        != candidate_ready.get("authority_event_digest")
        or not isinstance(configured_slots, list)
        or not configured_slots
    ):
        raise InvalidTransition("delivery settlement identity is stale")
    raw_slots = settlement.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) != len(configured_slots):
        raise InvalidTransition("delivery settlement slot roster changed")
    slots: list[dict[str, Any]] = []
    for raw_slot, configured in zip(raw_slots, configured_slots, strict=True):
        slot = _required_object(raw_slot, "settled slot")
        if set(slot) != _SETTLED_SLOT_KEYS or not isinstance(configured, dict):
            raise InvalidTransition("settled slot keys do not match the schema")
        if any(
            slot.get(field) != configured.get(field)
            for field in ("requirement_ids", "slot_digest", "slot_id")
        ):
            raise InvalidTransition("settled slot changed its frozen contract")
        raw_attempts = slot.get("attempts")
        if not isinstance(raw_attempts, list) or not raw_attempts:
            raise InvalidTransition("settled slot has no terminal attempt chain")
        attempts: list[dict[str, Any]] = []
        for index, raw_attempt in enumerate(raw_attempts):
            attempt = _required_object(raw_attempt, "settled attempt")
            if set(attempt) != _SETTLED_ATTEMPT_KEYS:
                raise InvalidTransition("settled attempt keys do not match the schema")
            _required_task_dir_name(str(attempt.get("task_dir_name") or ""))
            run_id = _required_text(attempt.get("task_run_id"), "settled TaskRun ID")
            if not re.fullmatch(r"task-[0-9a-f]{64}", run_id):
                raise InvalidTransition("settled TaskRun ID is invalid")
            if attempt.get("strategy") not in {"single", "loop"}:
                raise InvalidTransition("settled attempt strategy is invalid")
            _required_digest(
                attempt.get("terminal_event_digest"),
                "settled terminal event digest",
            )
            position = attempt.get("terminal_event_position")
            if isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise InvalidTransition("settled terminal event position is invalid")
            expected_disposition = (
                "completed" if index == len(raw_attempts) - 1 else "cancelled"
            )
            successor = attempt.get("successor_task_run_id")
            if attempt.get("disposition") != expected_disposition or (
                expected_disposition == "cancelled"
                and successor != raw_attempts[index + 1].get("task_run_id")
            ) or (expected_disposition == "completed" and successor is not None):
                raise InvalidTransition("settled attempt chain is unresolved")
            attempts.append(copy.deepcopy(attempt))
        if (
            attempts[0]["task_dir_name"] != configured.get("initial_task_dir_name")
            or attempts[0]["task_run_id"] != configured.get("initial_task_run_id")
            or slot.get("fulfilled_by") != attempts[-1]["task_run_id"]
        ):
            raise InvalidTransition("settled slot does not start and finish exactly")
        slots.append({**copy.deepcopy(slot), "attempts": attempts})
    normalized = {
        "candidate_ready_event_digest": settlement["candidate_ready_event_digest"],
        "slots": slots,
        "task_run_id": task_run_id,
    }
    if settlement.get("settlement_digest") != _digest_json(normalized):
        raise InvalidTransition("delivery settlement digest is invalid")
    return {**normalized, "settlement_digest": settlement["settlement_digest"]}


def _commit_reconciliation_binding(
    execution: dict[str, Any],
) -> dict[str, Any] | None:
    reconciliation = execution.get("commit_reconciliation")
    if reconciliation is None:
        return None
    if not isinstance(reconciliation, dict):
        raise InvalidTransition("commit reconciliation authority is invalid")
    request = reconciliation.get("request")
    response = reconciliation.get("response")
    receipt = response.get("receipt") if isinstance(response, dict) else None
    if not isinstance(request, dict) or not isinstance(receipt, dict):
        raise InvalidTransition("commit reconciliation is not accepted")
    return {
        "authorization_ref": receipt["authorization_ref"],
        "base_commit": request["base_commit"],
        "changed_paths": copy.deepcopy(request["changed_paths"]),
        "commit_chain": copy.deepcopy(request["commit_chain"]),
        "head_commit": request["head_commit"],
        "head_parent_commit": request["head_parent_commit"],
        "head_tree": request["head_tree"],
        "receipt_digest": receipt["receipt_digest"],
        "request_digest": request["request_digest"],
        "request_event_digest": request["authority_event_digest"],
        "response_event_digest": response["authority_event_digest"],
    }


def build_final_request(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the immutable final request from current accepted authority facts."""
    state = _required_object(snapshot, "snapshot")
    execution = state.get("execution")
    if (
        not isinstance(execution, dict)
        or state.get("status") != "running"
        or state.get("terminal") is not None
        or execution.get("paused") is not None
        or not isinstance(execution.get("candidate_ready"), dict)
    ):
        raise InvalidTransition("final request requires one ready running execution")
    stored = execution.get("final_request")
    if stored is not None:
        if not isinstance(stored, dict):
            raise InvalidTransition("stored final request authority is invalid")
        return {
            key: copy.deepcopy(value)
            for key, value in stored.items()
            if key not in {"authority_event_digest", "authority_event_position"}
        }
    ready = execution["candidate_ready"]
    commit_reconciliation = _commit_reconciliation_binding(execution)
    configured_slots = execution["config"]["start_envelope"].get("delivery_slots")
    delivery_settlement = execution.get("delivery_settlement")
    if configured_slots:
        if (
            not isinstance(delivery_settlement, dict)
            or delivery_settlement.get("candidate_ready_event_digest")
            != ready.get("authority_event_digest")
            or delivery_settlement.get("authority_event_position")
            != state.get("position")
        ):
            raise InvalidTransition("parent delivery slots are not terminally reconciled")
    else:
        latest_position = ready.get("authority_position")
        reconciliation = execution.get("commit_reconciliation")
        if commit_reconciliation is not None and isinstance(reconciliation, dict):
            response = reconciliation.get("response")
            if isinstance(response, dict):
                latest_position = max(
                    latest_position,
                    response.get("authority_event_position", -1),
                )
        if delivery_settlement is not None or latest_position != state.get("position"):
            raise InvalidTransition("candidate-ready authority position is stale")

    requirement_ids: set[str] = set()
    actual_effects: set[str] = set()
    unresolved_risks: set[str] = set()
    checks = []
    reviews = []
    for component in _accepted_execution_components(execution):
        action = execution["actions"][
            f"{component['action_id']}:{component['attempt']}"
        ]
        result = action["result"]
        review = action["review"]
        if (
            result is None
            or review is None
            or review.get("verdict") != "accepted"
            or review.get("findings") != []
            or any(status != "passed" for status in result["checks"].values())
        ):
            raise InvalidTransition("final request requires accepted green action facts")
        requirement_ids.update(result["requirement_ids"])
        actual_effects.update(result["actual_effects"])
        unresolved_risks.update(result["risks"])
        unresolved_risks.update(result["unknowns"])
        checks.append(
            {
                "action_id": component["action_id"],
                "attempt": component["attempt"],
                "candidate_digest": component["candidate_digest"],
                "statuses": copy.deepcopy(result["checks"]),
            }
        )
        reviews.append(
            {
                "action_id": component["action_id"],
                "attempt": component["attempt"],
                "candidate_digest": component["candidate_digest"],
                "findings": copy.deepcopy(review["findings"]),
                "reviewer_id": review["reviewer_id"],
                "verdict": review["verdict"],
            }
        )

    final_review = execution.get("final_candidate")
    request = {
        "actual_effects": sorted(actual_effects),
        "authority_position": state["position"],
        "candidate_ready": copy.deepcopy(ready),
        "checks": checks,
        "final_review": copy.deepcopy(final_review),
        "requirement_ids": sorted(requirement_ids),
        "reviews": reviews,
        "task_run_id": state["task_run_id"],
        "unresolved_risks": sorted(unresolved_risks),
    }
    if delivery_settlement is not None:
        request["delivery_settlement"] = copy.deepcopy(delivery_settlement)
    if commit_reconciliation is not None:
        request["commit_reconciliation"] = commit_reconciliation
    request["request_digest"] = _digest_json(request)
    return request


def read_legacy_task_evidence(task_dir: Path) -> dict[str, Any]:
    """Return digest-only evidence without changing a legacy task directory."""
    task_dir = Path(task_dir)
    task_json_path = task_dir / "task.json"
    try:
        task_bytes = task_json_path.read_bytes()
        task = json.loads(task_bytes.decode("utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise TaskRunError("legacy task.json is unreadable") from exc
    if not isinstance(task, dict):
        raise TaskRunError("legacy task.json must contain an object")
    if ((task.get("meta") or {}).get("task_run") or {}).get("authority") == "sqlite":
        raise TaskRunError("task is already a TaskRun projection")
    state_path = task_dir / "state-events.jsonl"
    try:
        state_bytes = state_path.read_bytes() if state_path.is_file() else None
    except OSError as exc:
        raise TaskRunError("legacy state evidence is unreadable") from exc
    return {
        "kind": "legacy_task",
        "read_only": True,
        "state_events_digest": sha256(state_bytes).hexdigest() if state_bytes is not None else None,
        "status": task.get("status"),
        "task_id": task.get("id") or task.get("name"),
        "task_json_digest": sha256(task_bytes).hexdigest(),
        "workflow_mode": (task.get("meta") or {}).get("workflow_mode"),
    }


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskRunError(f"{field} must be a non-empty string")
    return value.strip()


def _required_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskRunError(f"{field} must be an object")
    return copy.deepcopy(value)


def _execution_seed(task_json: dict[str, Any]) -> dict[str, Any] | None:
    meta = task_json.get("meta") or {}
    if not isinstance(meta, dict):
        raise InvalidTransition("task_json.meta must be an object")
    execution = meta.get("execution")
    if execution is None:
        return None
    if not isinstance(execution, dict) or set(execution) != {
        "context_digest",
        "revision",
        "start_envelope",
        "strategy",
    }:
        raise InvalidTransition("task_json.meta.execution does not match the schema")
    if execution.get("strategy") not in {"single", "loop"}:
        raise InvalidTransition("execution strategy must be single or loop")
    _required_text(execution.get("revision"), "execution.revision")
    _required_digest(execution.get("context_digest"), "execution.context_digest")
    _required_object(execution.get("start_envelope"), "execution.start_envelope")
    return copy.deepcopy(execution)


def _execution_action_key(value: dict[str, Any], task_run_id: str) -> str:
    action_id = _required_text(value.get("action_id"), "action.action_id")
    attempt = value.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise InvalidTransition("action.attempt must be a positive integer")
    if value.get("task_run_id") != task_run_id:
        raise InvalidTransition("action.task_run_id does not match TaskRun")
    _required_digest(value.get("input_digest"), "action.input_digest")
    return f"{action_id}:{attempt}"


def _execution_action(
    execution: dict[str, Any], value: dict[str, Any], task_run_id: str
) -> dict[str, Any]:
    key = _execution_action_key(value, task_run_id)
    action = execution["actions"].get(key)
    if action is None:
        raise InvalidTransition("execution action attempt is not planned")
    return action


def _terminal_strategy_decision(
    decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    terminal = [
        decision
        for decision in decisions
        if decision.get("kind") != "review_required"
    ]
    return terminal[-1] if terminal else None


def _execution_components(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise InvalidTransition("final candidate components must be a list")
    components = []
    for item in value:
        if not isinstance(item, dict) or set(item) != _COMPONENT_CANDIDATE_KEYS:
            raise InvalidTransition("final candidate component keys are invalid")
        action_id = _required_text(item.get("action_id"), "component.action_id")
        attempt = item.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise InvalidTransition("component.attempt must be a positive integer")
        components.append(
            {
                "action_id": action_id,
                "attempt": attempt,
                "candidate_digest": _required_digest(
                    item.get("candidate_digest"), "component.candidate_digest"
                ),
            }
        )
    if components != sorted(components, key=lambda item: item["action_id"]):
        raise InvalidTransition("final candidate components must be action-sorted")
    if len({item["action_id"] for item in components}) != len(components):
        raise InvalidTransition("final candidate components must be unique")
    return components


def _accepted_execution_components(execution: dict[str, Any]) -> list[dict[str, Any]]:
    components = []
    for action in execution["actions"].values():
        decision = _terminal_strategy_decision(action["decisions"])
        if decision is None or decision.get("kind") not in {"continue", "candidate_ready"}:
            continue
        result = action["result"]
        components.append(
            {
                "action_id": action["intent"]["action_id"],
                "attempt": action["intent"]["attempt"],
                "candidate_digest": result["candidate_digest"],
            }
        )
    required = execution["config"]["start_envelope"]["actions"]
    if {item["action_id"] for item in components} != {
        item["action_id"] for item in required
    }:
        raise InvalidTransition("final candidate requires every accepted action")
    return sorted(components, key=lambda item: item["action_id"])


def _validate_execution_fact(
    value: dict[str, Any], expected_keys: set[str], label: str
) -> None:
    if set(value) != expected_keys:
        raise InvalidTransition(f"execution {label} keys do not match the schema")
    for field in ("action_id", "task_run_id"):
        _required_text(value.get(field), f"{label}.{field}")
    _required_digest(value.get("input_digest"), f"{label}.input_digest")
    if "candidate_digest" in value:
        _required_digest(value.get("candidate_digest"), f"{label}.candidate_digest")
    if "result_digest" in value:
        _required_digest(value.get("result_digest"), f"{label}.result_digest")
    if "next_input_digest" in value:
        _required_digest(
            value.get("next_input_digest"), f"{label}.next_input_digest"
        )


def _required_digest(value: object, field: str) -> str:
    value = _required_text(value, field)
    if not _DIGEST.fullmatch(value):
        raise TaskRunError(f"{field} must be one lowercase SHA-256 digest")
    return value


def _required_rfc3339_utc(value: object, field: str) -> str:
    if not isinstance(value, str) or not _RFC3339_UTC.fullmatch(value):
        raise TaskRunError(f"{field} must be one UTC RFC 3339 timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise TaskRunError(f"{field} must be one valid UTC RFC 3339 timestamp") from exc
    return value


def _required_git_oid(value: object, field: str) -> str:
    value = _required_text(value, field)
    if not _GIT_OID.fullmatch(value):
        raise TaskRunError(f"{field} must be one full lowercase Git object ID")
    return value


def _validated_context_identities(value: object, field: str) -> dict[str, str]:
    identities = _required_object(value, field)
    if set(identities) != _CONTEXT_IDENTITY_KEYS:
        raise InvalidTransition(f"{field} keys do not match the schema")
    return {
        "base": _required_git_oid(identities["base"], f"{field}.base"),
        "context": _required_digest(identities["context"], f"{field}.context"),
        "contract": _required_digest(identities["contract"], f"{field}.contract"),
        "runtime": _required_digest(identities["runtime"], f"{field}.runtime"),
    }


def _validated_changed_paths(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise InvalidTransition(
            "commit reconciliation changed_paths must be a non-empty list"
        )
    paths: list[str] = []
    for raw in value:
        path = _required_text(raw, "commit_reconciliation.changed_paths")
        parsed = Path(path)
        if (
            parsed.is_absolute()
            or ".." in parsed.parts
            or path.startswith("./")
            or parsed.as_posix() != path
        ):
            raise InvalidTransition(
                "commit reconciliation changed path must be repository-relative"
            )
        paths.append(path)
    if paths != sorted(set(paths)):
        raise InvalidTransition(
            "commit reconciliation changed_paths must be sorted and unique"
        )
    return paths


def _validated_commit_reconciliation_request(value: object) -> dict[str, Any]:
    request = _required_object(value, "commit_reconciliation.request")
    if set(request) != _COMMIT_RECONCILIATION_REQUEST_KEYS:
        raise InvalidTransition(
            "commit reconciliation request keys do not match the schema"
        )
    _required_text(request["task_run_id"], "commit_reconciliation.task_run_id")
    base_commit = _required_git_oid(
        request["base_commit"], "commit_reconciliation.base_commit"
    )
    head_commit = _required_git_oid(
        request["head_commit"], "commit_reconciliation.head_commit"
    )
    head_parent = _required_git_oid(
        request["head_parent_commit"],
        "commit_reconciliation.head_parent_commit",
    )
    _required_git_oid(request["head_tree"], "commit_reconciliation.head_tree")
    chain = request["commit_chain"]
    if not isinstance(chain, list) or not chain:
        raise InvalidTransition(
            "commit reconciliation commit_chain must be a non-empty list"
        )
    commits = [
        _required_git_oid(item, "commit_reconciliation.commit_chain")
        for item in chain
    ]
    if len(commits) != len(set(commits)) or commits[-1] != head_commit:
        raise InvalidTransition("commit reconciliation commit_chain is invalid")
    expected_parent = commits[-2] if len(commits) > 1 else base_commit
    if head_parent != expected_parent or base_commit in commits:
        raise InvalidTransition("commit reconciliation parent binding is invalid")
    prior = _validated_context_identities(
        request["prior_identities"], "commit_reconciliation.prior_identities"
    )
    observed = _validated_context_identities(
        request["observed_identities"],
        "commit_reconciliation.observed_identities",
    )
    if prior["base"] != base_commit or observed["base"] != head_commit:
        raise InvalidTransition("commit reconciliation identity binding is invalid")
    request["changed_paths"] = _validated_changed_paths(request["changed_paths"])
    supplied_digest = _required_digest(
        request["request_digest"], "commit_reconciliation.request_digest"
    )
    expected_digest = _digest_json(
        {key: request[key] for key in request if key != "request_digest"}
    )
    if supplied_digest != expected_digest:
        raise InvalidTransition("commit reconciliation request digest is invalid")
    return request


def _validated_commit_reconciliation_receipt(value: object) -> dict[str, Any]:
    receipt = _required_object(value, "commit_reconciliation.receipt")
    if set(receipt) != _COMMIT_RECONCILIATION_RECEIPT_KEYS:
        raise InvalidTransition(
            "commit reconciliation receipt keys do not match the schema"
        )
    _required_text(receipt["task_run_id"], "commit_reconciliation.task_run_id")
    _required_text(
        receipt["authorization_ref"],
        "commit_reconciliation.authorization_ref",
    )
    _required_digest(
        receipt["request_digest"], "commit_reconciliation.request_digest"
    )
    _required_digest(
        receipt["request_event_digest"],
        "commit_reconciliation.request_event_digest",
    )
    if receipt["decision"] != "accepted":
        raise InvalidTransition(
            "commit reconciliation receipt decision must be accepted"
        )
    _required_text(receipt["receipt_id"], "commit_reconciliation.receipt_id")
    _required_text(
        receipt["receipt_audit_id"],
        "commit_reconciliation.receipt_audit_id",
    )
    _required_rfc3339_utc(
        receipt["receipt_audit_at"],
        "commit_reconciliation.receipt_audit_at",
    )
    supplied_digest = _required_digest(
        receipt["receipt_digest"], "commit_reconciliation.receipt_digest"
    )
    expected_digest = _digest_json(
        {key: receipt[key] for key in receipt if key != "receipt_digest"}
    )
    if supplied_digest != expected_digest:
        raise InvalidTransition("commit reconciliation receipt digest is invalid")
    return receipt


def _validated_final_gate_receipt(value: object) -> dict[str, Any]:
    receipt = _required_object(value, "final_gate_receipt")
    if set(receipt) != _FINAL_GATE_RECEIPT_KEYS:
        raise InvalidTransition("final gate receipt keys do not match the schema")
    _required_text(receipt["task_run_id"], "final_gate_receipt.task_run_id")
    _required_digest(
        receipt["final_request_digest"], "final_gate_receipt.final_request_digest"
    )
    _required_digest(
        receipt["final_request_event_digest"],
        "final_gate_receipt.final_request_event_digest",
    )
    if receipt["decision"] != "accepted":
        raise InvalidTransition("final gate receipt decision must be accepted")
    _required_text(receipt["receipt_id"], "final_gate_receipt.receipt_id")
    _required_text(receipt["receipt_audit_id"], "final_gate_receipt.receipt_audit_id")
    _required_rfc3339_utc(
        receipt["receipt_audit_at"], "final_gate_receipt.receipt_audit_at"
    )
    supplied_digest = _required_digest(
        receipt["receipt_digest"], "final_gate_receipt.receipt_digest"
    )
    expected_digest = _digest_json(
        {key: receipt[key] for key in _FINAL_GATE_RECEIPT_KEYS - {"receipt_digest"}}
    )
    if supplied_digest != expected_digest:
        raise InvalidTransition("final gate receipt digest does not match its binding")
    return receipt


def _required_task_dir_name(value: str) -> str:
    value = _required_text(value, "task_dir_name")
    if Path(value).name != value or value in {".", "..", "archive"}:
        raise TaskRunError("task_dir_name must be one safe directory name")
    return value


def _is_legacy_close_intent(intent: dict[str, Any]) -> bool:
    return "archive_path" in intent


def _validate_close_intent(intent: dict[str, Any], state: dict[str, Any]) -> None:
    legacy_required = {
        "actor",
        "archive_path",
        "git_evidence",
        "source_path",
        "task_projection_digest",
        "terminal_disposition",
    }
    status_only_required = {
        "actor",
        "task_path",
        "task_projection_preimage_digest",
        "terminal_disposition",
    }
    keys = set(intent)
    if frozenset(keys) not in {
        frozenset(legacy_required),
        frozenset(status_only_required),
    }:
        raise InvalidTransition("close intent keys do not match the schema")
    _required_text(intent["actor"], "intent.actor")
    path_keys = (
        ("source_path", "archive_path")
        if keys == legacy_required
        else ("task_path",)
    )
    for key in path_keys:
        value = _required_text(intent[key], f"intent.{key}")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or value.startswith("./"):
            raise InvalidTransition(f"intent.{key} must be a repository-relative path")
    task_name = state["task_dir_name"]
    expected_task_path = f".trellis/tasks/{task_name}"
    if keys == status_only_required:
        if intent["task_path"] != expected_task_path:
            raise InvalidTransition("close intent task path does not match TaskRun")
        _required_digest(
            intent["task_projection_preimage_digest"],
            "intent.task_projection_preimage_digest",
        )
        terminal = state.get("terminal") or {}
        if intent["terminal_disposition"] != terminal.get("disposition"):
            raise InvalidTransition(
                "close intent terminal disposition does not match authority"
            )
        return

    if intent["source_path"] != expected_task_path:
        raise InvalidTransition("close intent source path does not match TaskRun")
    archive_parts = Path(intent["archive_path"]).parts
    if (
        len(archive_parts) != 5
        or archive_parts[:3] != (".trellis", "tasks", "archive")
        or not _MONTH.fullmatch(archive_parts[3])
        or archive_parts[4] != task_name
    ):
        raise InvalidTransition("close intent archive path does not match TaskRun")
    _required_digest(intent["task_projection_digest"], "intent.task_projection_digest")
    terminal = state.get("terminal") or {}
    if intent["terminal_disposition"] != terminal.get("disposition"):
        raise InvalidTransition("close intent terminal disposition does not match authority")
    evidence = _required_object(intent["git_evidence"], "intent.git_evidence")
    _required_text(evidence.get("authorization_ref"), "intent.git_evidence.authorization_ref")
    if terminal.get("disposition") == "completed":
        if set(evidence) != {"authorization_ref", "commit", "tree"}:
            raise InvalidTransition("completed Git evidence keys do not match the schema")
        for key in ("commit", "tree"):
            value = _required_text(evidence.get(key), f"intent.git_evidence.{key}")
            if not _GIT_OID.fullmatch(value):
                raise InvalidTransition(f"intent.git_evidence.{key} is not a full Git object ID")
    elif set(evidence) != {"authorization_ref"}:
        raise InvalidTransition("cancelled close must not bind delivery Git evidence")


def _validate_close_observation(
    state: dict[str, Any],
    operation_id: str,
    effect: str,
    observation: dict[str, Any],
) -> None:
    close = state.get("close") or {}
    if close.get("operation_id") != operation_id:
        raise InvalidTransition("close effect has no matching operation")
    intent = close["intent"]
    if not _is_legacy_close_intent(intent):
        raise InvalidTransition("status-only close has no physical effects")
    effects = close.get("effects", {})
    expected_effect = _CLOSE_EFFECT_ORDER[len(effects)] if len(effects) < len(_CLOSE_EFFECT_ORDER) else None
    if effect != expected_effect:
        raise InvalidTransition("close effects must be observed in canonical order")
    if effect == "pointer_cleared":
        if set(observation) != {"cleared", "remaining"}:
            raise InvalidTransition("pointer observation keys do not match the schema")
        if not isinstance(observation["cleared"], int) or observation["cleared"] < 0:
            raise InvalidTransition("pointer cleared count must be non-negative")
        if observation["remaining"] != 0:
            raise InvalidTransition("pointer observation must prove zero remaining")
        return
    if effect == "directory_archived":
        expected = {
            "archive_path": intent["archive_path"],
            "source_absent": True,
            "task_projection_digest": intent["task_projection_digest"],
        }
        if observation != expected:
            raise InvalidTransition("directory observation does not match close intent")
        return
    if set(observation) != {"digest"}:
        raise InvalidTransition("projection observation keys do not match the schema")
    _required_digest(observation["digest"], "observation.digest")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _operation_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["intent"] = json.loads(value.pop("intent_json"))
    value["outcome"] = json.loads(value.pop("outcome_json"))
    return value


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value
