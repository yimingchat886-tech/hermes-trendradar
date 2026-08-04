"""Thin repository-owned operator for one non-default TaskRun."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from common.io import json_bytes
from prd import PrdError, resolve_requirement_ids

from .authority import OperationConflict, TaskRun, TaskRunError
from .close import _publish_task_projection, close_task_run
from .execution import (
    OPERATOR_LOOP_STRATEGY_REVISION,
    RESULT_FIELDS,
    _assert_unique_binding,
    _bootstrap_task_run_locked,
    _candidate_ref_is_independent,
    _repository_lock,
    _taskrun_commit_trailers_match,
    _terminal_decision,
    ingest_action_result,
    issue_final_request,
    issue_strategy_actions,
    record_action_review_and_reduce,
    record_final_acceptance,
    record_final_candidate_review,
)


STRATEGY_REVISION = "taskrun-operator-single-v1"
INDEPENDENT_GATES = (
    "external_effect",
    "final_candidate",
    "material_concurrency",
    "trust_boundary",
)
PROHIBITED_EFFECTS = (
    "archive",
    "deploy",
    "git_commit",
    "network",
    "push",
    "release",
)
_OPERATOR_START_FIELDS = {
    "actions",
    "allowed_effects",
    "approval_refs",
    "attempt_offset",
    "authorization_ref",
    "budgets",
    "context_digest",
    "identities",
    "prohibited_effects",
    "providers",
    "review_policy",
    "reviewers",
    "strategy_revision",
    "workers",
}
_OPERATOR_LOOP_START_FIELDS = _OPERATOR_START_FIELDS | {"candidate_commit"}
_GIT_OID = re.compile(r"[0-9a-f]{40,64}\Z")


class OperatorError(TaskRunError):
    """Raised when the operator cannot make one proven TaskRun step."""


class MaterialDriftError(OperatorError):
    """Raised before dispatch when an immutable execution boundary changed."""

    def __init__(
        self,
        changed: list[str],
        expected: dict[str, str],
        observed: dict[str, str],
    ) -> None:
        self.changed = tuple(changed)
        self.expected = copy.deepcopy(expected)
        self.observed = copy.deepcopy(observed)
        super().__init__(f"material TaskRun drift: {', '.join(changed)}")


class TaskRunOperator:
    """The only writer-facing composition surface for one bound TaskRun."""

    def __init__(self, repo_root: Path, run: TaskRun, *, actor: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.run = run
        self.actor = _required_text(actor, "actor")
        if run.repo_root != self.repo_root:
            raise OperatorError("TaskRun authority belongs to another repository")
        self._envelope()

    @classmethod
    def admit_single(
        cls,
        repo_root: Path,
        task_dir_name: str,
        *,
        actor: str,
        authorization_ref: str,
        worker_id: str,
        reviewer_id: str,
        provider_id: str = "local",
        action_risk: str = "low",
        low_risk_mode: str = "aggregate",
        attempts: int = 4,
    ) -> TaskRunOperator:
        """Bootstrap or exactly reopen one task-bound single authority."""
        root = Path(repo_root).resolve()
        with _repository_lock(root):
            task_dir, raw_task, task, prd = _read_task(root, task_dir_name)
            task_run_id = _run_id(task_dir.name, task)
            bound = (task.get("meta") or {}).get("task_run") or {}
            frozen_slots = None
            recorded_preflight = None
            if bound:
                if not isinstance(bound, dict) or bound.get("id") != task_run_id:
                    raise OperationConflict("task projection names another TaskRun")
                run = TaskRun.open(root, task_run_id)
                operator = cls(root, run, actor=actor)
                prior_envelope = operator._envelope()
                frozen_slots = prior_envelope.get("delivery_slots", [])
                recorded_preflight = _recorded_task_prd_preflight(prior_envelope)
            envelope = _start_envelope(
                root,
                task_dir.name,
                task,
                prd,
                authorization_ref=authorization_ref,
                worker_id=worker_id,
                reviewer_id=reviewer_id,
                provider_id=provider_id,
                action_risk=action_risk,
                low_risk_mode=low_risk_mode,
                attempts=attempts,
                attempt_offset=0,
                frozen_slots=frozen_slots,
                recorded_preflight=recorded_preflight,
            )
            if bound:
                if operator._envelope() != envelope:
                    raise OperationConflict("TaskRun admission replay changed")
            else:
                run = _bootstrap_task_run_locked(
                    root,
                    task_dir.name,
                    task,
                    actor=actor,
                    strategy="single",
                    start_envelope=envelope,
                )
                operator = cls(root, run, actor=actor)
            with _run_lock(operator.run):
                operator._ensure_running_projection(raw_task)
                operator._assert_current_context()
            return operator

    @classmethod
    def admit_loop(
        cls,
        repo_root: Path,
        task_dir_name: str,
        *,
        actor: str,
        authorization_ref: str,
        actions: Sequence[Mapping[str, object]],
        worker_ids: Sequence[str],
        reviewer_id: str,
        provider_id: str = "local",
        concurrency: int = 2,
        attempts: int = 4,
        candidate_commit_ref: str | None = None,
        candidate_commit_authorization_ref: str | None = None,
        action_risk: str = "low",
        low_risk_mode: str = "aggregate",
    ) -> TaskRunOperator:
        """Bootstrap or exactly reopen one task-bound loop authority."""
        root = Path(repo_root).resolve()
        with _repository_lock(root):
            task_dir, raw_task, task, prd = _read_task(root, task_dir_name)
            task_run_id = _run_id(task_dir.name, task)
            bound = (task.get("meta") or {}).get("task_run") or {}
            if bound and (
                not isinstance(bound, dict) or bound.get("id") != task_run_id
            ):
                raise OperationConflict("task projection names another TaskRun")
            frozen_slots = None
            recorded_preflight = None
            if bound:
                run = TaskRun.open(root, task_run_id)
                operator = cls(root, run, actor=actor)
                prior_envelope = operator._envelope()
                frozen_slots = prior_envelope.get("delivery_slots", [])
                recorded_preflight = _recorded_task_prd_preflight(prior_envelope)
            envelope = _loop_start_envelope(
                root,
                task_dir.name,
                task,
                prd,
                authorization_ref=authorization_ref,
                actions=actions,
                worker_ids=worker_ids,
                reviewer_id=reviewer_id,
                provider_id=provider_id,
                concurrency=concurrency,
                attempts=attempts,
                attempt_offset=0,
                candidate_commit_ref=candidate_commit_ref,
                candidate_commit_authorization_ref=(
                    candidate_commit_authorization_ref
                ),
                allow_committed_replay=bool(bound),
                action_risk=action_risk,
                low_risk_mode=low_risk_mode,
                frozen_slots=frozen_slots,
                recorded_preflight=recorded_preflight,
            )
            if bound:
                if operator._envelope() != envelope:
                    raise OperationConflict("TaskRun admission replay changed")
            else:
                run = _bootstrap_task_run_locked(
                    root,
                    task_dir.name,
                    task,
                    actor=actor,
                    strategy="loop",
                    start_envelope=envelope,
                )
                operator = cls(root, run, actor=actor)
            with _run_lock(operator.run):
                operator._ensure_running_projection(raw_task)
                operator._assert_current_context()
            return operator

    @classmethod
    def reopen(
        cls, repo_root: Path, task_run_id: str, *, actor: str
    ) -> TaskRunOperator:
        """Reopen one existing operator-bound authority without dispatching."""
        root = Path(repo_root).resolve()
        with _repository_lock(root):
            operator = cls(root, TaskRun.open(root, task_run_id), actor=actor)
            with _run_lock(operator.run):
                operator._recover_running_projection()
            return operator

    @property
    def task_run_id(self) -> str:
        return self.run.task_run_id

    def next_action(self, *, worker_id: str) -> dict[str, Any] | None:
        """Return the current crash-replay packet or plan one action just in time."""
        with _run_lock(self.run):
            self._require_running()
            self._assert_current_context()
            envelope = self._envelope()
            if envelope["strategy_revision"] != STRATEGY_REVISION:
                raise OperatorError("loop TaskRun dispatch requires next_actions")
            worker = _required_text(worker_id, "worker_id")
            if worker not in envelope["workers"]:
                raise OperatorError("worker is outside the immutable role boundary")

            state = self.run.snapshot()
            if state["execution"]["paused"] is not None:
                raise OperatorError("paused TaskRun cannot dispatch an action")
            pending = [
                item["intent"]
                for item in state["execution"]["actions"].values()
                if item["result"] is None
            ]
            if pending:
                intent = sorted(pending, key=lambda item: item["attempt"])[0]
            else:
                intents = issue_strategy_actions(self.run, actor=self.actor)
                if not intents:
                    return None
                intent = intents[0]
                state = self.run.snapshot()
            return self._action_packet(state, intent, worker)

    def next_actions(self) -> tuple[dict[str, Any], ...]:
        """Replay active loop packets and fill any conflict-free capacity."""
        with _repository_lock(self.repo_root):
            with _run_lock(self.run):
                self._require_running()
                self._assert_current_context()
                envelope = self._envelope()
                if envelope["strategy_revision"] != OPERATOR_LOOP_STRATEGY_REVISION:
                    raise OperatorError("single TaskRun dispatch requires next_action")
                state = self.run.snapshot()
                if state["execution"]["paused"] is not None:
                    raise OperatorError("paused TaskRun cannot dispatch an action")
                pending = sorted(
                    (
                        item["intent"]
                        for item in state["execution"]["actions"].values()
                        if item["result"] is None
                    ),
                    key=lambda intent: (intent["action_id"], intent["attempt"]),
                )
                claimed_elsewhere = _candidate_ref_is_active_elsewhere(
                    self.repo_root,
                    self.task_run_id,
                    envelope.get("candidate_commit"),
                )
                if claimed_elsewhere:
                    if pending:
                        raise OperationConflict(
                            "candidate commit ref has conflicting active claims"
                        )
                    return ()
                issue_strategy_actions(self.run, actor=self.actor)
                state = self.run.snapshot()
                pending = sorted(
                    (
                        item["intent"]
                        for item in state["execution"]["actions"].values()
                        if item["result"] is None
                    ),
                    key=lambda intent: (intent["action_id"], intent["attempt"]),
                )
                return tuple(
                    self._action_packet(
                        state,
                        intent,
                        intent["freshness"]["dispatch"]["worker_id"],
                    )
                    for intent in pending
                )

    def submit_result(self, payload: Mapping[str, object]) -> dict[str, Any]:
        """Validate and reduce one structured worker result through TaskRun."""
        with _run_lock(self.run):
            self._require_running()
            self._assert_current_context()
            return ingest_action_result(self.run, actor=self.actor, payload=payload)

    def submit_review(self, payload: Mapping[str, object]) -> dict[str, Any]:
        """Record one candidate-bound review through TaskRun."""
        with _run_lock(self.run):
            self._require_running()
            self._assert_current_context()
            return record_action_review_and_reduce(
                self.run, actor=self.actor, payload=payload
            )

    def submit_final_review(
        self, payload: Mapping[str, object]
    ) -> dict[str, Any]:
        """Bind one fresh independent review to the complete loop candidate."""
        with _run_lock(self.run):
            self._require_running()
            self._assert_current_context()
            if self._envelope()["strategy_revision"] != OPERATOR_LOOP_STRATEGY_REVISION:
                raise OperatorError("final aggregate review requires a loop TaskRun")
            return record_final_candidate_review(
                self.run, actor=self.actor, payload=payload
            )

    def reconcile_local_commit(
        self,
        receipt_producer: Callable[[dict[str, Any]], Mapping[str, object]] | None,
    ) -> dict[str, Any]:
        """Accept one exact separately authorized local commit boundary."""
        with _run_lock(self.run):
            state = self.run.snapshot()
            execution = state.get("execution")
            if not isinstance(execution, dict):
                raise OperatorError("commit reconciliation requires TaskRun execution")
            reconciliation = execution.get("commit_reconciliation")
            response = (
                reconciliation.get("response")
                if isinstance(reconciliation, dict)
                else None
            )
            if isinstance(response, dict):
                self._assert_current_context()
                return copy.deepcopy(reconciliation)
            if (
                state["status"] != "running"
                or state["terminal"] is not None
                or execution["config"]["strategy"] != "single"
            ):
                raise OperatorError(
                    "commit reconciliation requires one running single TaskRun"
                )
            if not callable(receipt_producer):
                raise OperatorError(
                    "commit reconciliation requires the host receipt producer"
                )

            stored_request = (
                reconciliation.get("request")
                if isinstance(reconciliation, dict)
                else None
            )
            if isinstance(stored_request, dict):
                request = {
                    key: copy.deepcopy(value)
                    for key, value in stored_request.items()
                    if key
                    not in {"authority_event_digest", "authority_event_position"}
                }
                self._assert_local_commit_reconciliation(request)
            else:
                request = self._local_commit_reconciliation_request()
                self.run.record_commit_reconciliation_request(
                    actor=self.actor,
                    request=request,
                )
                stored_request = self.run.snapshot()["execution"][
                    "commit_reconciliation"
                ]["request"]

            receipt = receipt_producer(copy.deepcopy(stored_request))
            if not isinstance(receipt, Mapping):
                raise OperatorError(
                    "commit reconciliation producer must return one receipt object"
                )
            self._assert_local_commit_reconciliation(request)
            self.run.record_commit_reconciliation_response(
                actor=self.actor,
                receipt=dict(receipt),
            )
            self._assert_current_context()
            return copy.deepcopy(
                self.run.snapshot()["execution"]["commit_reconciliation"]
            )

    def final_request(self) -> dict[str, Any]:
        """Record or replay the exact request consumed by the outer gate."""
        with _run_lock(self.run):
            self._require_running()
            self._assert_current_context()
            existing = self.run.snapshot()["execution"]["final_request"]
            if existing is not None:
                return copy.deepcopy(existing)
            if self._envelope().get("delivery_slots"):
                self.run.record_delivery_settlement(actor=self.actor)
            return issue_final_request(self.run, actor=self.actor)

    def complete(
        self,
        final_gate: Callable[[dict[str, Any]], Mapping[str, object]] | None = None,
    ) -> dict[str, Any]:
        """Consume one host-produced receipt, then bind terminal completion."""
        with _run_lock(self.run):
            return self._complete(final_gate)

    def _complete(
        self,
        final_gate: Callable[[dict[str, Any]], Mapping[str, object]] | None,
    ) -> dict[str, Any]:
        state = self.run.snapshot()
        terminal = state["terminal"]
        if terminal is not None:
            if terminal["disposition"] != "completed":
                raise OperatorError("cancelled TaskRun cannot complete")
            self._ensure_terminal_projection()
            return copy.deepcopy(terminal)

        self._assert_current_context()
        state = self.run.snapshot()
        if state["terminal"] is not None:
            return self._complete(final_gate)
        execution = state["execution"]
        binding = execution["terminal_authority"]
        if binding is None:
            if execution["final_request"] is None:
                raise OperatorError("completion requires one recorded final request")
            if not callable(final_gate):
                raise OperatorError("completion requires the host final-gate producer")
            receipt = final_gate(copy.deepcopy(execution["final_request"]))
            if not isinstance(receipt, Mapping):
                raise OperatorError(
                    "final-gate producer must return one receipt object"
                )
            self._assert_current_context()
            binding = record_final_acceptance(
                self.run,
                actor=self.actor,
                receipt=receipt,
            )

        self._assert_current_context()
        self.run.record_terminal(
            operation_id=f"terminal:{binding['final_response_event_digest']}",
            actor=self.actor,
            disposition="completed",
            authorization_ref=binding["final_response_event_digest"],
            evidence=binding,
        )
        self._ensure_terminal_projection()
        return copy.deepcopy(self.run.snapshot()["terminal"])

    def supersede(
        self,
        successor_task_dir_name: str,
        *,
        authorization_ref: str,
    ) -> TaskRunOperator | None:
        """Cancel one drifted run and admit its successor only if budget remains."""
        with _repository_lock(self.repo_root):
            with _run_lock(self.run):
                return self._supersede(successor_task_dir_name, authorization_ref)

    def _supersede(
        self, successor_task_dir_name: str, authorization_ref: str
    ) -> TaskRunOperator | None:
        state = self.run.snapshot()
        if state["terminal"] is not None:
            evidence = state["terminal"].get("evidence") or {}
            if (
                state["terminal"]["disposition"] != "cancelled"
                or evidence.get("kind") != "material_drift"
                or evidence.get("successor", {}).get("task_dir_name")
                != successor_task_dir_name
                or state["terminal"]["authorization_ref"] != authorization_ref
            ):
                raise OperationConflict(
                    "terminal TaskRun is not this supersession replay"
                )
            self._ensure_terminal_projection()
            return self._bootstrap_successor(evidence)

        drift = self._material_drift()
        if drift is None:
            raise OperatorError("supersession requires material drift")
        envelope = self._envelope()
        offset = envelope["attempt_offset"]
        local_actions = list(state["execution"]["actions"].values())
        if envelope["strategy_revision"] == OPERATOR_LOOP_STRATEGY_REVISION:
            repair_reservations = sum(
                1
                for item in local_actions
                for decision in item["decisions"]
                if decision["kind"] == "retry_exact_slice"
            )
            local_attempts = (
                1 + repair_reservations if local_actions else 0
            )
        else:
            local_attempts = len(local_actions)
        consumed = offset + local_attempts
        total = offset + envelope["budgets"]["attempts"]
        remaining = total - consumed

        task_dir, _, task, prd = _read_task(self.repo_root, successor_task_dir_name)
        if task_dir.name == state["task_dir_name"]:
            raise OperatorError("successor must use a new task identity")
        meta = task.get("meta") or {}
        if not isinstance(meta, dict):
            raise OperatorError("successor task meta must be an object")
        if meta.get("task_run") or meta.get("execution"):
            raise OperationConflict("successor task is already execution-bound")
        prior_contract = {
            "parent": state["task_json_seed"].get("parent"),
            "requirement_ids": sorted(
                {
                    requirement
                    for action in envelope["actions"]
                    for requirement in action["requirement_ids"]
                }
            ),
            "scope": state["task_json_seed"].get("scope"),
            "tier": state["task_json_seed"].get("tier"),
            "touches": sorted(state["task_json_seed"].get("touches") or []),
        }
        successor_contract = {
            "parent": task.get("parent"),
            "requirement_ids": task_prd_preflight(
                self.repo_root, task, prd.decode("utf-8")
            )["requirement_ids"],
            "scope": task.get("scope"),
            "tier": task.get("tier"),
            "touches": sorted(_text_list(task.get("touches"), "successor touches")),
        }
        if prior_contract != successor_contract:
            raise OperatorError("successor changes the frozen execution contract")
        if "delivery_slots" in envelope:
            successor_slots = _delivery_slots(
                self.repo_root,
                task_dir.name,
                task,
                prd.decode("utf-8"),
            )
            slot_fields = (
                "requirement_ids",
                "scope",
                "slot_digest",
                "slot_id",
                "touches",
            )
            if [
                {field: slot[field] for field in slot_fields}
                for slot in successor_slots
            ] != [
                {field: slot[field] for field in slot_fields}
                for slot in envelope["delivery_slots"]
            ]:
                raise OperatorError("successor changes the frozen delivery slots")
        successor_id = _run_id(task_dir.name, task)
        reservation = _assert_unique_binding(
            self.repo_root, successor_id, task_dir.name, task
        )
        if reservation is not None:
            raise OperationConflict("successor already has a supersession reservation")
        if TaskRun(self.repo_root, successor_id).path.is_file():
            raise OperationConflict("successor authority exists without its projection")
        successor = {
            "actor": self.actor,
            "task_dir_name": task_dir.name,
            "task_json": task,
        }
        if remaining:
            if envelope["strategy_revision"] == STRATEGY_REVISION:
                successor["start_envelope"] = _start_envelope(
                    self.repo_root,
                    task_dir.name,
                    task,
                    prd,
                    authorization_ref=authorization_ref,
                    worker_id=envelope["workers"][0],
                    reviewer_id=envelope["reviewers"][0],
                    provider_id=envelope["providers"][0],
                    action_risk=envelope["review_policy"]["action_risk"],
                    low_risk_mode=envelope["review_policy"]["low_risk_mode"],
                    attempts=remaining,
                    attempt_offset=consumed,
                )
            else:
                successor["start_envelope"] = _rebind_loop_envelope(
                    self.repo_root,
                    task_dir.name,
                    task,
                    prd,
                    prior=envelope,
                    authorization_ref=authorization_ref,
                    attempts=remaining,
                    attempt_offset=consumed,
                )
        evidence = {
            **drift,
            "kind": "material_drift",
            "repair_budget": {
                "consumed": consumed,
                "remaining": remaining,
                "total": total,
            },
            "successor": successor,
            "superseded_by": successor_id,
        }
        self.run.record_terminal(
            operation_id=f"terminal:material-drift:{_digest_json(evidence)}",
            actor=self.actor,
            disposition="cancelled",
            authorization_ref=_required_text(authorization_ref, "authorization_ref"),
            evidence=evidence,
        )
        self._ensure_terminal_projection()
        return self._bootstrap_successor(evidence)

    def close(self, *, fault_after: str | None = None) -> dict[str, Any]:
        """Run the existing status-only close with one stable operation ID."""
        with _run_lock(self.run):
            state = self.run.snapshot()
            if state["terminal"] is None:
                raise OperatorError("close requires a terminal TaskRun")
            self._ensure_terminal_projection()
        return close_task_run(
            self.run,
            operation_id=f"close:{self.task_run_id}:1",
            task_dir=self.repo_root / ".trellis/tasks" / state["task_dir_name"],
            actor=self.actor,
            fault_after=fault_after,
        )

    def _bootstrap_successor(
        self, evidence: Mapping[str, object]
    ) -> TaskRunOperator | None:
        successor = evidence.get("successor")
        if not isinstance(successor, Mapping):
            raise OperatorError("supersession evidence has no successor seed")
        budget = evidence.get("repair_budget")
        if not isinstance(budget, Mapping) or not isinstance(
            budget.get("remaining"), int
        ):
            raise OperatorError("supersession evidence has no repair budget")
        if budget["remaining"] == 0:
            return None
        start_envelope = successor["start_envelope"]
        strategy = (
            "loop"
            if start_envelope["strategy_revision"]
            == OPERATOR_LOOP_STRATEGY_REVISION
            else "single"
        )
        run = _bootstrap_task_run_locked(
            self.repo_root,
            successor["task_dir_name"],
            successor["task_json"],
            actor=successor["actor"],
            strategy=strategy,
            start_envelope=start_envelope,
        )
        operator = TaskRunOperator(self.repo_root, run, actor=str(successor["actor"]))
        operator._ensure_running_projection()
        return operator

    def _action_packet(
        self,
        state: dict[str, Any],
        intent: dict[str, Any],
        worker_id: str,
    ) -> dict[str, Any]:
        envelope = self._envelope()
        policy = envelope["review_policy"]
        action_mode = (
            "independent"
            if policy["action_risk"] == "high"
            else policy["low_risk_mode"]
        )
        required_by = ["final_candidate"]
        if policy["action_risk"] == "high":
            required_by.insert(0, "high_risk")
        offset = envelope["attempt_offset"]
        attempt_number = offset + intent["attempt"]
        if envelope["strategy_revision"] == OPERATOR_LOOP_STRATEGY_REVISION:
            attempt_number = (
                offset
                + envelope["budgets"]["attempts"]
                - intent["remaining_budgets"]["attempts"]
            )
        authority_digest = _digest_json(state)
        authority_position = state["position"]
        if envelope["strategy_revision"] == OPERATOR_LOOP_STRATEGY_REVISION:
            plan_event = next(
                (
                    event
                    for event in self.run.events()
                    if event["event_type"] == "action_planned"
                    and event["payload"]["intent"]["input_digest"]
                    == intent["input_digest"]
                ),
                None,
            )
            if plan_event is None:
                raise OperatorError("loop dispatch has no TaskRun plan authority")
            authority_digest = plan_event["event_digest"]
            authority_position = plan_event["position"]
        packet = {
            "authority_digest": authority_digest,
            "authority_position": authority_position,
            "identities": copy.deepcopy(envelope["identities"]),
            "intent": copy.deepcopy(intent),
            "provider_id": envelope["providers"][0],
            "repair_budget": {
                "attempt_number": attempt_number,
                "remaining": intent["remaining_budgets"]["attempts"],
                "total": offset + envelope["budgets"]["attempts"],
            },
            "review": {
                "action_mode": action_mode,
                "independent_gates": copy.deepcopy(policy["independent_gates"]),
                "required_by": required_by,
                "required_mode": "independent",
            },
            "worker_id": worker_id,
        }
        if envelope["strategy_revision"] == OPERATOR_LOOP_STRATEGY_REVISION:
            action = next(
                item
                for item in envelope["actions"]
                if item["action_id"] == intent["action_id"]
            )
            packet["claims"] = {
                "resources": copy.deepcopy(action.get("resources", [])),
                "touches": copy.deepcopy(action["touches"]),
            }
            packet["dispatch"] = copy.deepcopy(intent["freshness"]["dispatch"])
        return packet

    def _envelope(self) -> dict[str, Any]:
        state = self.run.snapshot()
        execution = state.get("execution")
        if not isinstance(execution, dict):
            raise OperatorError("TaskRun is not execution-bound")
        config = execution.get("config") or {}
        envelope = config.get("start_envelope")
        strategy = config.get("strategy")
        expected_revision = {
            "loop": OPERATOR_LOOP_STRATEGY_REVISION,
            "single": STRATEGY_REVISION,
        }.get(strategy)
        expected_fields = (
            _OPERATOR_LOOP_START_FIELDS
            if strategy == "loop"
            else _OPERATOR_START_FIELDS
        )
        if isinstance(envelope, dict) and "delivery_slots" in envelope:
            expected_fields = expected_fields | {"delivery_slots"}
        if (
            expected_revision is None
            or config.get("revision") != expected_revision
            or not isinstance(envelope, dict)
            or set(envelope) != expected_fields
        ):
            raise OperatorError("TaskRun is not operator-bound v1")
        return copy.deepcopy(envelope)

    def _require_running(self) -> None:
        state = self.run.snapshot()
        if state["status"] != "running" or state["terminal"] is not None:
            raise OperatorError("operator action requires one running TaskRun")

    def _local_commit_reconciliation_request(self) -> dict[str, Any]:
        state = self.run.snapshot()
        execution = state.get("execution")
        if not isinstance(execution, dict):
            raise OperatorError("commit reconciliation requires TaskRun execution")
        if (
            execution["config"]["strategy"] != "single"
            or execution["paused"] is not None
            or execution["commit_reconciliation"] is not None
            or execution["final_request"] is not None
            or execution["final_response"] is not None
            or execution.get("delivery_settlement") is not None
        ):
            raise OperatorError("commit reconciliation is not currently admissible")
        drift = self._material_drift()
        if drift is None or "base" not in drift["changed"]:
            raise OperatorError(
                "commit reconciliation requires one committed base advance"
            )
        envelope = self._envelope()
        prior = {
            **envelope["identities"],
            "context": envelope["context_digest"],
        }
        observed = self._observed_context_identities(state)
        return _build_local_commit_reconciliation_request(
            self.repo_root,
            task_run_id=self.task_run_id,
            prior_identities=prior,
            observed_identities=observed,
        )

    def _assert_local_commit_reconciliation(
        self, request: Mapping[str, object]
    ) -> None:
        state = self.run.snapshot()
        envelope = self._envelope()
        current = _build_local_commit_reconciliation_request(
            self.repo_root,
            task_run_id=self.task_run_id,
            prior_identities={
                **envelope["identities"],
                "context": envelope["context_digest"],
            },
            observed_identities=self._observed_context_identities(state),
        )
        if current != request:
            raise OperatorError("local commit reconciliation request drifted")

    def _observed_context_identities(
        self, state: Mapping[str, object]
    ) -> dict[str, str]:
        observed = _identities(self.repo_root)
        _, _, task, prd = _read_task(
            self.repo_root, str(state["task_dir_name"])
        )
        return {
            **observed,
            "context": _context_digest(task, prd, observed),
        }

    def _material_drift(self) -> dict[str, Any] | None:
        envelope = self._envelope()
        state = self.run.snapshot()
        observed_identities = _identities(self.repo_root)
        _, _, task, prd = _read_task(
            self.repo_root, state["task_dir_name"]
        )
        observed_context = _context_digest(task, prd, observed_identities)
        execution = state.get("execution")
        reconciliation = (
            execution.get("commit_reconciliation")
            if isinstance(execution, Mapping)
            else None
        )
        response = (
            reconciliation.get("response")
            if isinstance(reconciliation, Mapping)
            else None
        )
        request = (
            reconciliation.get("request")
            if isinstance(reconciliation, Mapping)
            else None
        )
        expected = (
            copy.deepcopy(request["observed_identities"])
            if isinstance(response, Mapping) and isinstance(request, Mapping)
            else {
                **envelope["identities"],
                "context": envelope["context_digest"],
            }
        )
        observed = {**observed_identities, "context": observed_context}
        candidate_identity = _candidate_commit_identity(
            self.repo_root, envelope, state
        )
        if candidate_identity is not None:
            expected["candidate_commit"] = candidate_identity[0]
            observed["candidate_commit"] = candidate_identity[1]
        changed = sorted(key for key in expected if expected[key] != observed[key])
        if not changed:
            return None
        return {"changed": changed, "expected": expected, "observed": observed}

    def _assert_current_context(self) -> None:
        drift = self._material_drift()
        if drift is not None:
            raise MaterialDriftError(
                drift["changed"], drift["expected"], drift["observed"]
            )

    def _ensure_running_projection(self, current: bytes | None = None) -> None:
        state = self.run.snapshot()
        if state["status"] != "running" or state["terminal"] is not None:
            return
        task_path = self._task_path(state)
        operation_id = f"operator-projection:{self.task_run_id}:running"
        claim = _projection_claim(task_path, operation_id)
        claim_present = claim.exists() or claim.is_symlink()
        if current is None:
            if task_path.exists():
                current = task_path.read_bytes()
            elif claim.is_file() and not claim.is_symlink():
                current = claim.read_bytes()
            else:
                raise OperatorError("running task projection is unavailable")
        final = self.run.task_projection_bytes()
        if current == final and not claim_present:
            return
        if claim_present and (claim.is_symlink() or not claim.is_file()):
            raise OperatorError("running projection claim is invalid")
        preimage = claim.read_bytes() if claim_present else current
        try:
            current_task = json.loads(preimage.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperatorError("task projection is not valid UTF-8 JSON") from exc
        if _unbound_task(current_task) != _unbound_task(state["task_json_seed"]):
            raise OperatorError("task projection changed before TaskRun admission")
        _publish_task_projection(
            task_path,
            preimage_digest=sha256(preimage).hexdigest(),
            final_projection=final,
            operation_id=operation_id,
        )

    def _recover_running_projection(self) -> None:
        state = self.run.snapshot()
        if state["status"] != "running" or state["terminal"] is not None:
            return
        task_path = self._task_path(state)
        operation_id = f"operator-projection:{self.task_run_id}:running"
        claim = _projection_claim(task_path, operation_id)
        if claim.exists() or claim.is_symlink() or not task_path.exists():
            self._ensure_running_projection()
            return
        current = task_path.read_bytes()
        if current == self.run.task_projection_bytes():
            return
        try:
            task = json.loads(current.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if _unbound_task(task) == _unbound_task(state["task_json_seed"]):
            self._ensure_running_projection(current)

    def _ensure_terminal_projection(self) -> None:
        state = self.run.snapshot()
        if state["terminal"] is None:
            raise OperatorError("terminal projection requires terminal authority")
        if state.get("close") is not None:
            return
        task_path = self._task_path(state)
        operation_id = f"operator-projection:{self.task_run_id}:terminal"
        claim = _projection_claim(task_path, operation_id)
        claim_present = claim.exists() or claim.is_symlink()
        current = task_path.read_bytes() if task_path.exists() else None
        final = self.run.task_projection_bytes()
        if current == final and not claim_present:
            return
        running = _running_projection_bytes(state)
        if claim_present:
            if (
                claim.is_symlink()
                or not claim.is_file()
                or claim.read_bytes() != running
            ):
                raise OperatorError("terminal projection claim is invalid")
        elif current != running:
            raise OperatorError("task projection drifted before terminal projection")
        _publish_task_projection(
            task_path,
            preimage_digest=sha256(running).hexdigest(),
            final_projection=final,
            operation_id=operation_id,
        )

    def _task_path(self, state: Mapping[str, object]) -> Path:
        return self.repo_root / ".trellis/tasks" / state["task_dir_name"] / "task.json"


def _delivery_slots(
    repo_root: Path,
    parent_dir_name: str,
    parent: Mapping[str, object],
    parent_prd: str,
) -> list[dict[str, Any]]:
    raw_children = parent.get("children") or []
    if not raw_children:
        return []
    if parent.get("tier") != "parent":
        raise OperatorError("only a parent task may bind delivery slots")
    children = _text_list(raw_children, "parent children")
    parent_requirements = set(
        task_prd_preflight(repo_root, parent, parent_prd)["requirement_ids"]
    )
    covered: set[str] = set()
    slots: list[dict[str, Any]] = []
    parent_mode = (parent.get("meta") or {}).get("workflow_mode")
    for index, child_name in enumerate(children, start=1):
        child_dir, _, child, child_prd = _read_task(repo_root, child_name)
        meta = child.get("meta") or {}
        if (
            child.get("tier") != "child"
            or child.get("parent") != parent_dir_name
            or child.get("status") != "planning"
            or not isinstance(meta, dict)
            or meta.get("workflow_mode") != parent_mode
            or meta.get("task_run") is not None
            or meta.get("execution") is not None
        ):
            raise OperatorError("parent delivery child is not an unbound TaskRun plan")
        requirement_ids = task_prd_preflight(
            repo_root, child, child_prd.decode("utf-8")
        )["requirement_ids"]
        overlap = covered.intersection(requirement_ids)
        if overlap:
            raise OperatorError("parent delivery slots have overlapping REQ IDs")
        covered.update(requirement_ids)
        touches = sorted(_text_list(child.get("touches"), "child touches"))
        scope = child.get("scope")
        if scope is not None:
            scope = _required_text(scope, "child scope")
        slot = {
            "initial_task_dir_name": child_dir.name,
            "initial_task_run_id": _run_id(child_dir.name, child),
            "requirement_ids": requirement_ids,
            "scope": scope,
            "slot_id": f"slot-{index:03d}",
            "touches": touches,
        }
        slot["slot_digest"] = _digest_json(
            {key: slot[key] for key in ("requirement_ids", "scope", "slot_id", "touches")}
        )
        slots.append(slot)
    if covered != parent_requirements:
        raise OperatorError("parent delivery slots do not exactly cover parent REQ IDs")
    return slots


def _start_envelope(
    repo_root: Path,
    task_dir_name: str,
    task: dict[str, Any],
    prd: bytes,
    *,
    authorization_ref: str,
    worker_id: str,
    reviewer_id: str,
    provider_id: str,
    action_risk: str,
    low_risk_mode: str,
    attempts: int,
    attempt_offset: int,
    frozen_slots: Sequence[Mapping[str, object]] | None = None,
    recorded_preflight: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    worker = _required_text(worker_id, "worker_id")
    reviewer = _required_text(reviewer_id, "reviewer_id")
    if worker == reviewer:
        raise OperatorError("worker and reviewer must be independent identities")
    identities = _identities(repo_root)
    task_id = _required_text(task.get("id") or task.get("name"), "task id")
    authorization = _required_text(authorization_ref, "authorization_ref")
    text = prd.decode("utf-8")
    preflight = (
        task_prd_preflight(repo_root, task, text)
        if recorded_preflight is None
        else recorded_preflight
    )
    envelope = {
        "actions": [
            {
                "action_id": "single-action",
                "checks": preflight["verification_commands"],
                "dependencies": [],
                "problem_id": task_id,
                "requirement_ids": preflight["requirement_ids"],
                "result_schema": sorted(RESULT_FIELDS),
                "touches": _text_list(task.get("touches"), "task touches"),
            }
        ],
        "allowed_effects": ["workspace_write"],
        "approval_refs": [authorization],
        "attempt_offset": attempt_offset,
        "authorization_ref": authorization,
        "budgets": {
            "attempts": attempts,
            "concurrency": 1,
            "cost": 0,
            "providers": 1,
            "reviewers": 1,
            "workers": 1,
        },
        "context_digest": _context_digest(task, prd, identities),
        "identities": identities,
        "prohibited_effects": list(PROHIBITED_EFFECTS),
        "providers": [_required_text(provider_id, "provider_id")],
        "review_policy": {
            "action_risk": _required_text(action_risk, "action_risk"),
            "independent_gates": list(INDEPENDENT_GATES),
            "low_risk_mode": _required_text(low_risk_mode, "low_risk_mode"),
        },
        "reviewers": [reviewer],
        "strategy_revision": STRATEGY_REVISION,
        "workers": [worker],
    }
    slots = (
        _delivery_slots(repo_root, task_dir_name, task, text)
        if frozen_slots is None
        else copy.deepcopy(list(frozen_slots))
    )
    if slots:
        envelope["delivery_slots"] = slots
    return envelope


def _loop_start_envelope(
    repo_root: Path,
    task_dir_name: str,
    task: dict[str, Any],
    prd: bytes,
    *,
    authorization_ref: str,
    actions: Sequence[Mapping[str, object]],
    worker_ids: Sequence[str],
    reviewer_id: str,
    provider_id: str,
    concurrency: int,
    attempts: int,
    attempt_offset: int,
    candidate_commit_ref: str | None,
    candidate_commit_authorization_ref: str | None,
    allow_committed_replay: bool,
    action_risk: str,
    low_risk_mode: str,
    frozen_slots: Sequence[Mapping[str, object]] | None = None,
    recorded_preflight: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    if isinstance(worker_ids, (str, bytes)):
        raise OperatorError("worker_ids must be a sequence of identities")
    workers = _text_list(list(worker_ids), "worker_ids")
    reviewer = _required_text(reviewer_id, "reviewer_id")
    if reviewer in workers:
        raise OperatorError("workers and reviewer must be independent identities")
    if isinstance(actions, (str, bytes)) or not actions:
        raise OperatorError("loop actions must be a non-empty sequence")
    try:
        normalized_actions = [copy.deepcopy(dict(action)) for action in actions]
    except (TypeError, ValueError) as exc:
        raise OperatorError("loop actions must be objects") from exc
    if (candidate_commit_ref is None) != (
        candidate_commit_authorization_ref is None
    ):
        raise OperatorError(
            "candidate commit ref and authorization must be supplied together"
        )
    candidate_commit = None
    if candidate_commit_ref is not None:
        run_authorization = _required_text(authorization_ref, "authorization_ref")
        commit_authorization = _required_text(
            candidate_commit_authorization_ref,
            "candidate_commit_authorization_ref",
        )
        if commit_authorization == run_authorization:
            raise OperatorError("candidate commit requires separate authorization")
        candidate_commit = _candidate_commit_binding(
            repo_root,
            candidate_commit_ref,
            commit_authorization,
            allow_committed_replay=allow_committed_replay,
        )
        git_claim = f"git-ref:{candidate_commit['ref']}"
        for action in normalized_actions:
            resources = action.get("resources", [])
            if not isinstance(resources, list):
                raise OperatorError("action resources must be a list")
            resources = list(resources)
            if git_claim not in resources:
                resources.append(git_claim)
            action["resources"] = resources

    envelope = _start_envelope(
        repo_root,
        task_dir_name,
        task,
        prd,
        authorization_ref=authorization_ref,
        worker_id=workers[0],
        reviewer_id=reviewer,
        provider_id=provider_id,
        action_risk=action_risk,
        low_risk_mode=low_risk_mode,
        attempts=attempts,
        attempt_offset=attempt_offset,
        frozen_slots=frozen_slots,
        recorded_preflight=recorded_preflight,
    )
    envelope["actions"] = normalized_actions
    envelope["budgets"]["concurrency"] = concurrency
    envelope["budgets"]["workers"] = len(workers)
    envelope["candidate_commit"] = candidate_commit
    envelope["strategy_revision"] = OPERATOR_LOOP_STRATEGY_REVISION
    envelope["workers"] = workers
    if candidate_commit is not None:
        commit_authorization = candidate_commit["authorization_ref"]
        if commit_authorization not in envelope["approval_refs"]:
            envelope["approval_refs"].append(commit_authorization)
        envelope["allowed_effects"].append("git_commit")
        envelope["prohibited_effects"].remove("git_commit")
    return envelope


def _rebind_loop_envelope(
    repo_root: Path,
    task_dir_name: str,
    task: dict[str, Any],
    prd: bytes,
    *,
    prior: Mapping[str, object],
    authorization_ref: str,
    attempts: int,
    attempt_offset: int,
) -> dict[str, Any]:
    envelope = copy.deepcopy(dict(prior))
    authorization = _required_text(authorization_ref, "authorization_ref")
    prior_authorization = envelope["authorization_ref"]
    candidate_commit = envelope.get("candidate_commit")
    removed_authorizations = {prior_authorization, authorization}
    if isinstance(candidate_commit, Mapping):
        removed_authorizations.add(candidate_commit["authorization_ref"])
        git_claim = f"git-ref:{candidate_commit['ref']}"
        envelope["candidate_commit"] = None
        envelope["allowed_effects"] = [
            effect for effect in envelope["allowed_effects"] if effect != "git_commit"
        ]
        if "git_commit" not in envelope["prohibited_effects"]:
            envelope["prohibited_effects"].append("git_commit")
        for action in envelope["actions"]:
            if "resources" in action:
                action["resources"] = [
                    resource
                    for resource in action["resources"]
                    if resource != git_claim
                ]
    envelope["approval_refs"] = [
        authorization,
        *(
            ref
            for ref in envelope["approval_refs"]
            if ref not in removed_authorizations
        ),
    ]
    envelope["authorization_ref"] = authorization
    envelope["attempt_offset"] = attempt_offset
    envelope["budgets"]["attempts"] = attempts
    identities = _identities(repo_root)
    envelope["identities"] = identities
    envelope["context_digest"] = _context_digest(task, prd, identities)
    if "delivery_slots" in envelope:
        envelope["delivery_slots"] = _delivery_slots(
            repo_root,
            task_dir_name,
            task,
            prd.decode("utf-8"),
        )
    return envelope


def _candidate_commit_binding(
    repo_root: Path,
    ref: object,
    authorization_ref: object,
    *,
    allow_committed_replay: bool,
) -> dict[str, str]:
    branch_ref = _required_text(ref, "candidate_commit_ref")
    authorization = _required_text(
        authorization_ref, "candidate_commit_authorization_ref"
    )
    if not branch_ref.startswith("refs/heads/"):
        raise OperatorError("candidate_commit_ref must be one full local branch ref")
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "check-ref-format", branch_ref],
            check=True,
            capture_output=True,
        )
        if not _candidate_ref_is_independent(repo_root, branch_ref):
            raise OperatorError(
                "candidate commit ref must be one independent direct branch"
            )
        current = _git_text(
            repo_root, "rev-parse", "--verify", f"{branch_ref}^{{commit}}"
        )
        expected_old = _git_text(
            repo_root, "rev-parse", "--verify", "HEAD^{commit}"
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperatorError("candidate commit branch is unavailable") from exc
    if not _GIT_OID.fullmatch(current) or not _GIT_OID.fullmatch(expected_old):
        raise OperatorError("candidate commit branch has no full Git object ID")
    if current != expected_old:
        if not allow_committed_replay:
            raise OperatorError(
                "candidate commit branch must equal repository base at admission"
            )
        try:
            parents = _git_text(
                repo_root, "rev-list", "--parents", "-n", "1", current
            ).split()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OperatorError("candidate commit branch is unavailable") from exc
        if parents != [current, expected_old]:
            raise OperatorError("candidate commit branch advanced outside its binding")
    return {
        "authorization_ref": authorization,
        "expected_old_oid": expected_old,
        "ref": branch_ref,
    }


def _candidate_ref_is_active_elsewhere(
    repo_root: Path,
    task_run_id: str,
    binding: object,
) -> bool:
    if not isinstance(binding, Mapping):
        return False
    runs_root = repo_root / ".trellis/.runtime/taskrun/runs"
    if not runs_root.exists():
        return False
    if runs_root.is_symlink() or not runs_root.is_dir():
        raise OperatorError("TaskRun authority directory is unavailable")
    for authority in sorted(runs_root.glob("*/authority.sqlite3")):
        other_id = authority.parent.name
        if other_id == task_run_id:
            continue
        if authority.is_symlink() or authority.parent.is_symlink():
            raise OperatorError("TaskRun authority path is unsafe")
        try:
            state = TaskRun.open(repo_root, other_id).snapshot()
        except TaskRunError as exc:
            raise OperatorError("TaskRun authority scan failed") from exc
        if state["status"] != "running" or state["terminal"] is not None:
            continue
        execution = state.get("execution")
        if not isinstance(execution, Mapping):
            continue
        config = execution.get("config")
        envelope = config.get("start_envelope") if isinstance(config, Mapping) else None
        other_binding = (
            envelope.get("candidate_commit")
            if isinstance(envelope, Mapping)
            else None
        )
        if (
            not isinstance(other_binding, Mapping)
            or other_binding.get("ref") != binding.get("ref")
        ):
            continue
        actions = execution.get("actions")
        if execution.get("paused") is not None or (
            isinstance(actions, Mapping)
            and any(
                isinstance(action, dict) and _terminal_decision(action) is None
                for action in actions.values()
            )
        ):
            return True
    return False


def _candidate_commit_identity(
    repo_root: Path,
    envelope: Mapping[str, object],
    state: Mapping[str, object],
) -> tuple[str, str] | None:
    binding = envelope.get("candidate_commit")
    if not isinstance(binding, Mapping):
        return None
    expected_old = str(binding["expected_old_oid"])
    try:
        if not _candidate_ref_is_independent(repo_root, str(binding["ref"])):
            return expected_old, "unsafe-ref"
        current = _git_text(
            repo_root,
            "rev-parse",
            "--verify",
            f"{binding['ref']}^{{commit}}",
        )
    except (OSError, subprocess.CalledProcessError):
        return expected_old, "unavailable"

    execution = state.get("execution")
    actions = execution.get("actions", {}) if isinstance(execution, Mapping) else {}
    committed = []
    for action in actions.values():
        result = action.get("result") if isinstance(action, Mapping) else None
        if (
            not isinstance(result, Mapping)
            or result.get("unknowns")
            or "git_commit" not in result.get("actual_effects", [])
        ):
            continue
        try:
            receipt = json.loads(result["effect_receipts"]["git_commit"])
            committed.append(_required_text(receipt["commit_oid"], "commit_oid"))
        except (KeyError, TypeError, json.JSONDecodeError, OperatorError):
            return "verified-receipt", current
    if committed:
        expected = committed[0] if len(set(committed)) == 1 else "multiple-commits"
        return expected, current
    if current == expected_old:
        return expected_old, current

    try:
        parents = _git_text(
            repo_root, "rev-list", "--parents", "-n", "1", current
        ).split()
        message = _git_text(repo_root, "show", "-s", "--format=%B", current)
    except (OSError, subprocess.CalledProcessError):
        return expected_old, current
    if parents != [current, expected_old]:
        return expected_old, current
    authorization = binding["authorization_ref"]
    for action in actions.values():
        if not isinstance(action, Mapping) or action.get("result") is not None:
            continue
        intent = action.get("intent")
        if not isinstance(intent, Mapping):
            continue
        dispatch = intent.get("freshness", {}).get("dispatch", {})
        trailers = (
            f"TaskRun-Operation: {dispatch.get('operation_id')}",
            f"TaskRun-Input: {intent.get('input_digest')}",
            f"TaskRun-Fence: {dispatch.get('fence_digest')}",
            f"TaskRun-Authorization: {authorization}",
        )
        if _taskrun_commit_trailers_match(message, trailers):
            return current, current
    return expected_old, current


def _git_text(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
    ).stdout


def _build_local_commit_reconciliation_request(
    repo_root: Path,
    *,
    task_run_id: str,
    prior_identities: Mapping[str, object],
    observed_identities: Mapping[str, object],
) -> dict[str, Any]:
    try:
        if _git_bytes(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            raise OperatorError(
                "commit reconciliation requires one clean repository worktree"
            )
        base = _required_git_oid(
            prior_identities.get("base"), "commit reconciliation base"
        )
        head = _required_git_oid(
            observed_identities.get("base"), "commit reconciliation head"
        )
        if base == head:
            raise OperatorError(
                "commit reconciliation requires one committed base advance"
            )
        chain = _linear_commit_chain(repo_root, base, head)
        head_tree = _required_git_oid(
            _git_text(repo_root, "rev-parse", "--verify", f"{head}^{{tree}}"),
            "commit reconciliation tree",
        )
        raw_paths = _git_bytes(
            repo_root,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base,
            head,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperatorError("local commit reconciliation evidence is unavailable") from exc
    try:
        changed_paths = sorted(
            path for path in raw_paths.decode("utf-8").split("\0") if path
        )
    except UnicodeDecodeError as exc:
        raise OperatorError(
            "commit reconciliation changed paths are not UTF-8"
        ) from exc
    if not changed_paths or len(changed_paths) != len(set(changed_paths)):
        raise OperatorError(
            "commit reconciliation requires unique committed changed paths"
        )
    request = {
        "base_commit": base,
        "changed_paths": changed_paths,
        "commit_chain": chain,
        "head_commit": head,
        "head_parent_commit": chain[-2] if len(chain) > 1 else base,
        "head_tree": head_tree,
        "observed_identities": copy.deepcopy(dict(observed_identities)),
        "prior_identities": copy.deepcopy(dict(prior_identities)),
        "task_run_id": _required_text(task_run_id, "task_run_id"),
    }
    request["request_digest"] = _digest_json(request)
    return request


def _linear_commit_chain(repo_root: Path, base: str, head: str) -> list[str]:
    reverse_chain: list[str] = []
    current = head
    while current != base:
        if len(reverse_chain) >= 4096:
            raise OperatorError("commit reconciliation chain is unbounded")
        try:
            parents = _git_text(
                repo_root, "rev-list", "--parents", "-n", "1", current
            ).split()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OperatorError(
                "commit reconciliation chain is unavailable"
            ) from exc
        if len(parents) != 2 or parents[0] != current:
            raise OperatorError(
                "commit reconciliation requires one linear non-merge commit chain"
            )
        reverse_chain.append(current)
        current = _required_git_oid(
            parents[1], "commit reconciliation parent"
        )
    if not reverse_chain:
        raise OperatorError("commit reconciliation chain is empty")
    return list(reversed(reverse_chain))


def _projection_claim(path: Path, operation_id: str) -> Path:
    digest = sha256(operation_id.encode("utf-8")).hexdigest()
    return path.with_name(f".{path.name}.{digest}.close-claim")


@contextmanager
def _run_lock(run: TaskRun) -> Iterator[None]:
    try:
        descriptor = os.open(run.path.parent, os.O_RDONLY)
    except OSError as exc:
        raise OperatorError("TaskRun authority is unreadable") from exc
    locked = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_task(
    repo_root: Path, task_dir_name: str
) -> tuple[Path, bytes, dict[str, Any], bytes]:
    name = _required_text(task_dir_name, "task_dir_name")
    if Path(name).name != name or name in {".", ".."}:
        raise OperatorError("task_dir_name must be one safe directory name")
    task_dir = repo_root / ".trellis/tasks" / name
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise OperatorError("task directory must be one direct active directory")
    task_path = task_dir / "task.json"
    prd_path = task_dir / "prd.md"
    if any(path.is_symlink() or not path.is_file() for path in (task_path, prd_path)):
        raise OperatorError("task.json and prd.md must be regular files")
    raw_task = task_path.read_bytes()
    try:
        task = json.loads(raw_task.decode("utf-8"))
        prd = prd_path.read_bytes()
        prd.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError("task artifacts must be valid UTF-8 JSON/Markdown") from exc
    if not isinstance(task, dict):
        raise OperatorError("task.json must contain one object")
    return task_dir, raw_task, task, prd


def _identities(repo_root: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD^{commit}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperatorError("repository base commit is unavailable") from exc
    base = result.stdout.strip()
    if not _GIT_OID.fullmatch(base):
        raise OperatorError("repository base is not one full lowercase Git object ID")
    runtime_root = repo_root / ".trellis/scripts/taskrun"
    contract = repo_root / ".trellis/spec/project/taskrun-runtime.md"
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise OperatorError("TaskRun runtime directory is unavailable")
    if contract.is_symlink() or not contract.is_file():
        raise OperatorError("TaskRun contract is unavailable")
    runtime_files = sorted(runtime_root.rglob("*.py"))
    if not runtime_files:
        raise OperatorError("TaskRun runtime identity is incomplete")
    files = runtime_files + [repo_root / ".trellis/scripts/common/io.py"]
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise OperatorError("TaskRun runtime identity is incomplete")
    runtime = [
        {
            "digest": sha256(path.read_bytes()).hexdigest(),
            "path": path.relative_to(repo_root).as_posix(),
        }
        for path in files
    ]
    return {
        "base": base,
        "contract": sha256(contract.read_bytes()).hexdigest(),
        "runtime": _digest_json(runtime),
    }


def _context_digest(
    task: dict[str, Any], prd: bytes, identities: Mapping[str, str]
) -> str:
    return _digest_json(
        {
            "identities": identities,
            "prd_digest": sha256(prd).hexdigest(),
            "task": _task_contract(task),
        }
    )


def _task_contract(task: Mapping[str, object]) -> dict[str, Any]:
    value = copy.deepcopy(dict(task))
    for field in ("commit", "completedAt", "status"):
        value.pop(field, None)
    meta = value.get("meta") or {}
    if not isinstance(meta, dict):
        raise OperatorError("task.json.meta must be an object")
    meta.pop("execution", None)
    meta.pop("task_run", None)
    value["meta"] = meta
    return value


def _unbound_task(task: object) -> dict[str, Any]:
    if not isinstance(task, Mapping):
        raise OperatorError("task projection must contain one object")
    value = copy.deepcopy(dict(task))
    meta = value.get("meta") or {}
    if not isinstance(meta, dict):
        raise OperatorError("task projection meta must be an object")
    meta.pop("execution", None)
    meta.pop("task_run", None)
    value["meta"] = meta
    return value


def _running_projection_bytes(state: Mapping[str, object]) -> bytes:
    projection = copy.deepcopy(state["task_json_seed"])
    meta = copy.deepcopy(projection.get("meta") or {})
    meta["task_run"] = {
        "authority": "sqlite",
        "id": state["task_run_id"],
        "projection": True,
        "state": "running",
    }
    projection["meta"] = meta
    projection["status"] = "running"
    return json_bytes(projection)


def task_prd_preflight(
    repo_root: Path,
    task: Mapping[str, object],
    prd: str,
) -> dict[str, list[str]]:
    """Parse one unadmitted task PRD exactly as admission will consume it."""

    try:
        requirement_ids = resolve_requirement_ids(
            repo_root,
            prd,
            default_owner=task.get("owner"),
        )
    except PrdError as exc:
        raise OperatorError(str(exc)) from exc
    return {
        "requirement_ids": requirement_ids,
        "verification_commands": _verification_commands(prd),
    }


def _recorded_task_prd_preflight(
    envelope: Mapping[str, object],
) -> dict[str, list[str]]:
    """Reuse the immutable PRD contract when reopening admitted authority."""

    actions = envelope.get("actions")
    if not isinstance(actions, list) or not actions or not isinstance(actions[0], dict):
        raise OperatorError("recorded TaskRun envelope actions are invalid")
    return {
        "requirement_ids": _text_list(
            actions[0].get("requirement_ids"), "recorded requirement IDs"
        ),
        "verification_commands": _text_list(
            actions[0].get("checks"), "recorded verification commands"
        ),
    }


def _verification_commands(prd: str) -> list[str]:
    values = []
    for line in _section(prd, "Verification Commands"):
        match = re.match(r"\s*-\s+`(.+)`\s*$", line)
        if match:
            values.append(match.group(1))
    if (
        not values
        or len(values) != len(set(values))
        or any(value.strip().upper() in {"TBD", "TODO"} for value in values)
    ):
        raise OperatorError(
            "PRD must contain unique non-placeholder verification commands"
        )
    return values


def _section(document: str, heading: str) -> list[str]:
    lines = document.splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise OperatorError(f"PRD is missing {marker}") from exc
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def _text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise OperatorError(f"{field} must be a list")
    result = [_required_text(item, field) for item in value]
    if not result or len(result) != len(set(result)):
        raise OperatorError(f"{field} must contain unique values")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperatorError(f"{field} must be a non-empty string")
    return value.strip()


def _required_git_oid(value: object, field: str) -> str:
    value = _required_text(value, field)
    if not _GIT_OID.fullmatch(value):
        raise OperatorError(f"{field} must be one full lowercase Git object ID")
    return value


def _run_id(task_dir_name: str, task: Mapping[str, object]) -> str:
    task_id = _required_text(task.get("id") or task.get("name"), "task id")
    return f"task-{_digest_json({'task_dir_name': task_dir_name, 'task_id': task_id})}"


def taskrun_id_for_task(
    task_dir_name: str,
    task: Mapping[str, object],
) -> str:
    """Return the deterministic pre-admission TaskRun identity."""
    return _run_id(task_dir_name, task)


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
