from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from loop_v1 import (
    FRESHNESS_BOUNDARIES,
    ContextError,
    FreshnessError,
    InterventionRequired,
    OperationConflict,
    ParentLedger,
    accept_child_result,
    approve_start_request,
    assert_fresh,
    create_start_request,
    freshness_token,
    intervention_reason,
    issue_child_packet,
    record_context_revision,
)
from loop_v1.context import _digest_json, _packet_execution_base


def start_envelope() -> dict[str, object]:
    return {
        "goal": "Deliver the accepted Loop v1 runtime contract.",
        "requirements": [
            {
                "requirement_id": "REQ-1",
                "required": True,
                "acceptance": ["context authority is deterministic"],
                "dependencies": [],
            },
            {
                "requirement_id": "REQ-2",
                "required": False,
                "acceptance": ["runtime contract is documented"],
                "dependencies": ["REQ-1"],
            },
        ],
        "acceptance": ["focused and regression tests pass"],
        "allowed_scope": ["Loop v1 bootstrap runtime"],
        "allowed_touches": [
            ".trellis/scripts/loop_v1/**",
            ".trellis/spec/project/loop-v1-runtime.md",
        ],
        "local_effects": ["write ignored SQLite state", "write local Git files"],
        "read_only_external_inputs": [],
        "resources": [
            {"resource_key": "canonical-repo", "mode": "exclusive", "capacity": 1}
        ],
        "risks": ["stale result acceptance"],
        "prohibited_actions": ["push", "release", "read secret contents"],
        "initial_child_graph": [
            {
                "child_id": "child-a",
                "requirements": ["REQ-1"],
                "touches": [".trellis/scripts/loop_v1/**"],
                "resources": ["canonical-repo"],
                "depends_on": [],
            },
            {
                "child_id": "child-b",
                "requirements": ["REQ-2"],
                "touches": [".trellis/spec/project/loop-v1-runtime.md"],
                "resources": ["canonical-repo"],
                "depends_on": ["child-a"],
            },
        ],
        "default_parallel": 2,
        "max_parallel": 3,
        "base_branch": "main",
        "base_head": "base-head-1",
        "verification_policy": {"focused": True, "regression": True},
        "review_policy": {"exact_tree": True},
        "retry_policy": {"same_problem_rounds": 3},
        "approved_agent_surfaces": ["codex"],
        "worker_capacity": 3,
        "reviewer_capacity": 1,
        "token_budget": 10000,
        "cost_budget": None,
        "dirty_path_fingerprint": "dirty-digest-1",
        "conformance_receipt": "receipt-loop-v1-fixture",
    }


def canonical_context() -> dict[str, object]:
    envelope = start_envelope()
    return {
        "requirements": [
            {
                **requirement,
                "revision": 1,
                "coverage_state": "uncovered",
            }
            for requirement in envelope["requirements"]
        ],
        "graph": copy.deepcopy(envelope["initial_child_graph"]),
        "decisions": [{"id": "DEC-1", "summary": "Use one parent ledger."}],
        "facts": [{"id": "FACT-1", "summary": "Admission is disabled."}],
        "integration": {
            "base_branch": "main",
            "base_head": "base-head-1",
            "base_tree_id": "base-tree-1",
            "integration_head": "base-head-1",
            "integration_tree_id": "base-tree-1",
        },
        "risks": ["stale result acceptance"],
        "prohibitions": ["push", "release", "read secret contents"],
        "context_slices": [
            {
                "slice_id": "slice-public",
                "kind": "spec",
                "source_ref": ".trellis/spec/project/loop-v1-runtime.md",
                "digest": "slice-digest-public",
                "visibility": "public",
                "excerpt": "Validate freshness before mutation.",
            },
            {
                "slice_id": "slice-internal",
                "kind": "decision",
                "source_ref": "DEC-1",
                "digest": "slice-digest-internal",
                "visibility": "internal",
                "excerpt": "The parent remains the sole writer.",
            },
            {
                "slice_id": "slice-secret-ref",
                "kind": "fingerprint",
                "source_ref": ".env",
                "digest": "secret-path-fingerprint-only",
                "visibility": "secret_ref",
                "excerpt": None,
            },
        ],
        "dependency_state": {"REQ-1": "ready", "REQ-2": "blocked"},
    }


def initialize(root: Path):
    return ParentLedger.initialize(
        root,
        "context-parent-run",
        selector="loop_v1",
        writer_id="parent-writer",
    )


def authorize(ledger: ParentLedger, lease) -> dict[str, object]:
    request = create_start_request(
        ledger,
        lease,
        request_id="start-request-1",
        envelope=start_envelope(),
    )
    approve_start_request(
        ledger,
        lease,
        request_id="start-request-1",
        request_digest=request["request_digest"],
        response_identity="user-response-1",
        response_at="2026-07-13T18:00:00Z",
        direct_user_action=True,
    )
    return request


def record_initial_context(ledger: ParentLedger, lease) -> dict[str, object]:
    return record_context_revision(
        ledger,
        lease,
        request_id="start-request-1",
        revision_id="context-1",
        reason="initial approved context",
        context=canonical_context(),
    )


def assignment() -> dict[str, object]:
    return {
        "packet_id": "packet-1",
        "child_id": "child-a",
        "requirements": ["REQ-1"],
        "forbidden_touches": [".trellis/scripts/loop_v1/forbidden.py"],
        "tests": ["python3 focused-test.py"],
        "context_slice_ids": ["slice-public", "slice-internal"],
        "parent_contact": "channel:parent-run",
        "result_deadline": "2026-07-13T19:00:00Z",
        "result_lease": "result-lease-1",
        "attempt": 1,
        "round": 1,
    }


def child_result(packet: dict[str, object]) -> dict[str, object]:
    identity = packet["identity"]
    return {
        "result_id": "result-1",
        "packet_id": packet["packet_id"],
        "child_id": packet["child_id"],
        "actual_touches": [".trellis/scripts/loop_v1/context.py"],
        "diff_identity": "diff-digest-1",
        "base_head": packet["base"]["head"],
        "base_tree_id": packet["base"]["tree_id"],
        "result_tree_id": "result-tree-1",
        "commands": [
            {
                "command": "python3 focused-test.py",
                "status": "passed",
                "output_digest": "test-output-digest-1",
            }
        ],
        "coverage": ["REQ-1"],
        "risks": [],
        "findings": [],
        "artifacts": [
            {
                "path": ".trellis/scripts/loop_v1/context.py",
                "digest": "artifact-digest-1",
            }
        ],
        **identity,
    }


def read_rows(ledger: ParentLedger, sql: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(ledger.reference()["sqlite_uri"], uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


class LoopV1ContextTests(unittest.TestCase):
    def test_start_request_is_immutable_and_direct_response_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            request = create_start_request(
                ledger,
                lease,
                request_id="start-request-1",
                envelope=start_envelope(),
            )
            replay = create_start_request(
                ledger,
                lease,
                request_id="start-request-1",
                envelope=start_envelope(),
            )
            self.assertEqual(request, replay)

            changed = start_envelope()
            changed["goal"] = "A different goal"
            with self.assertRaises(OperationConflict):
                create_start_request(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    envelope=changed,
                )
            digest_before = ledger.authority_digest()
            with self.assertRaises(FreshnessError):
                approve_start_request(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    request_digest="wrong-request-digest",
                    response_identity="user-response-1",
                    response_at="2026-07-13T18:00:00Z",
                    direct_user_action=True,
                )
            with self.assertRaises(ContextError):
                approve_start_request(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    request_digest=request["request_digest"],
                    response_identity="worker-message-1",
                    response_at="2026-07-13T18:00:00Z",
                    direct_user_action=False,
                )
            self.assertEqual(ledger.authority_digest(), digest_before)

            approval = approve_start_request(
                ledger,
                lease,
                request_id="start-request-1",
                request_digest=request["request_digest"],
                response_identity="user-response-1",
                response_at="2026-07-13T18:00:00Z",
                direct_user_action=True,
            )
            replayed = approve_start_request(
                ledger,
                lease,
                request_id="start-request-1",
                request_digest=request["request_digest"],
                response_identity="user-response-1",
                response_at="2026-07-13T18:00:00Z",
                direct_user_action=True,
            )
            self.assertEqual(approval, replayed)
            self.assertEqual(approval["status"], "approved")

    def test_context_and_graph_revisions_are_deterministic_and_in_envelope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            authorize(ledger, lease)
            first = record_initial_context(ledger, lease)
            replayed = record_initial_context(ledger, lease)
            self.assertEqual(first, replayed)
            self.assertEqual(first["sequence"], 1)

            revised = canonical_context()
            revised["graph"] = [
                {
                    "child_id": "child-a1",
                    "requirements": ["REQ-1"],
                    "touches": [".trellis/scripts/loop_v1/context.py"],
                    "resources": ["canonical-repo"],
                    "depends_on": [],
                },
                {
                    "child_id": "child-b",
                    "requirements": ["REQ-2"],
                    "touches": [".trellis/spec/project/loop-v1-runtime.md"],
                    "resources": ["canonical-repo"],
                    "depends_on": ["child-a1"],
                },
            ]
            second = record_context_revision(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="context-2",
                reason="replace child inside approved envelope",
                context=revised,
            )
            self.assertEqual(second["sequence"], 2)
            self.assertEqual(len(read_rows(ledger, "SELECT * FROM graph_revisions")), 2)

            expansion = copy.deepcopy(revised)
            expansion["graph"][0]["touches"] = ["outside-envelope/**"]
            digest_before = ledger.authority_digest()
            with self.assertRaises(InterventionRequired):
                record_context_revision(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    revision_id="context-3",
                    reason="scope expansion",
                    context=expansion,
                )
            self.assertEqual(ledger.authority_digest(), digest_before)

            projection = ledger.rebuild_projection(lease)
            payload = json.loads(Path(projection["path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["authority"]["tables"]["graph_revisions"]), 2)

    def test_context_revision_fences_a_changed_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            authorize(ledger, lease)
            source = record_initial_context(ledger, lease)
            advanced = canonical_context()
            advanced["decisions"].append(
                {"id": "DEC-2", "summary": "Advance canonical context first."}
            )
            record_context_revision(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="context-advanced",
                reason="advance before stale recovery commit",
                context=advanced,
            )
            stale_recovery = canonical_context()
            stale_recovery["graph"][0]["child_id"] = "child-a-repair"
            stale_recovery["graph"][1]["depends_on"] = ["child-a-repair"]
            digest_before = ledger.authority_digest()

            with self.assertRaises(FreshnessError):
                record_context_revision(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    revision_id="context-stale-recovery",
                    reason="stale recovery replacement",
                    context=stale_recovery,
                    expected_previous_digest=source["digest"],
                )

            self.assertEqual(ledger.authority_digest(), digest_before)

    def test_intervention_contract_is_exhaustive(self) -> None:
        no_intervention = {
            "scope_or_resource_expansion": False,
            "dependency_schema_security_or_secret_change": False,
            "unknown_effect_data_loss_or_user_dirt": False,
            "retry_budget_exhausted": False,
            "ambiguous_product_semantics": False,
            "explicit_user_control": None,
        }
        self.assertIsNone(intervention_reason(no_intervention))
        for condition in (
            "scope_or_resource_expansion",
            "dependency_schema_security_or_secret_change",
            "unknown_effect_data_loss_or_user_dirt",
            "retry_budget_exhausted",
            "ambiguous_product_semantics",
        ):
            signals = dict(no_intervention)
            signals[condition] = True
            self.assertEqual(intervention_reason(signals), condition)
        explicit = dict(no_intervention)
        explicit["explicit_user_control"] = "pause"
        self.assertEqual(intervention_reason(explicit), "explicit_user_pause")

    def test_child_packet_is_minimal_digest_bound_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            authorize(ledger, lease)
            record_initial_context(ledger, lease)
            packet = issue_child_packet(ledger, lease, assignment())
            replayed = issue_child_packet(ledger, lease, assignment())
            self.assertEqual(packet, replayed)
            self.assertEqual(packet["requirements"][0]["requirement_id"], "REQ-1")
            self.assertEqual(
                packet["execution_base"],
                {
                    "head": packet["base"]["head"],
                    "tree_id": packet["base"]["tree_id"],
                },
            )
            self.assertNotIn("base_head", packet["identity"])
            self.assertNotIn("base_tree_id", packet["identity"])
            self.assertEqual(
                [item["slice_id"] for item in packet["context_slices"]],
                ["slice-public", "slice-internal"],
            )
            serialized = json.dumps(packet, sort_keys=True)
            self.assertNotIn("slice-secret-ref", serialized)
            self.assertNotIn("FACT-1", serialized)
            self.assertNotIn("conversation", serialized)

            legacy = copy.deepcopy(packet)
            legacy.pop("execution_base")
            legacy.pop("packet_digest")
            legacy["identity"]["base_head"] = legacy["base"]["head"]
            legacy["identity"]["base_tree_id"] = legacy["base"]["tree_id"]
            legacy_bytes = json.dumps(legacy, sort_keys=True).encode()
            legacy_digest = _digest_json(legacy)
            self.assertEqual(
                _packet_execution_base(legacy),
                {
                    "head": legacy["base"]["head"],
                    "tree_id": legacy["base"]["tree_id"],
                },
            )
            self.assertEqual(json.dumps(legacy, sort_keys=True).encode(), legacy_bytes)
            self.assertEqual(_digest_json(legacy), legacy_digest)

            unknown = assignment()
            unknown["conversation"] = "unrelated transcript"
            with self.assertRaises(ContextError):
                issue_child_packet(ledger, lease, unknown)
            secret = assignment()
            secret["packet_id"] = "packet-secret"
            secret["context_slice_ids"] = ["slice-secret-ref"]
            with self.assertRaises(ContextError):
                issue_child_packet(ledger, lease, secret)
            secret_command = assignment()
            secret_command["packet_id"] = "packet-secret-command"
            secret_command["tests"] = ["API_TOKEN=not-allowed python3 test.py"]
            with self.assertRaises(ContextError):
                issue_child_packet(ledger, lease, secret_command)

    def test_every_boundary_rejects_stale_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            authorize(ledger, lease)
            record_initial_context(ledger, lease)
            token = freshness_token(ledger)
            for boundary in FRESHNESS_BOUNDARIES:
                self.assertEqual(assert_fresh(ledger, boundary, token), token)
            stale = dict(token)
            stale["epoch"] = token["epoch"] + 1
            for boundary in FRESHNESS_BOUNDARIES:
                with self.assertRaises(FreshnessError):
                    assert_fresh(ledger, boundary, stale)

            replacement = start_envelope()
            replacement["goal"] = "A newly approved replacement envelope."
            request = create_start_request(
                ledger,
                lease,
                request_id="start-request-2",
                envelope=replacement,
            )
            approve_start_request(
                ledger,
                lease,
                request_id="start-request-2",
                request_digest=request["request_digest"],
                response_identity="user-response-2",
                response_at="2026-07-13T18:30:00Z",
                direct_user_action=True,
            )
            with self.assertRaises(FreshnessError):
                assert_fresh(ledger, "dispatch", token)

    def test_stale_over_scope_or_incomplete_results_do_not_mutate_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = initialize(Path(tmp))
            authorize(ledger, lease)
            record_initial_context(ledger, lease)
            packet = issue_child_packet(ledger, lease, assignment())
            valid = child_result(packet)

            invalid_results = []
            stale = copy.deepcopy(valid)
            stale["context_digest"] = "stale-context"
            invalid_results.append((stale, FreshnessError))
            over_scope = copy.deepcopy(valid)
            over_scope["actual_touches"] = ["outside/scope.py"]
            invalid_results.append((over_scope, ContextError))
            unknown = copy.deepcopy(valid)
            unknown["conversation"] = "unrelated transcript"
            invalid_results.append((unknown, ContextError))

            for invalid, error in invalid_results:
                digest_before = ledger.authority_digest()
                with self.assertRaises(error):
                    accept_child_result(ledger, lease, invalid)
                self.assertEqual(ledger.authority_digest(), digest_before)

            failed_test = copy.deepcopy(valid)
            failed_test["result_id"] = "failed-result"
            failed_test["commands"][0]["status"] = "skipped"
            rejected = accept_child_result(ledger, lease, failed_test)
            self.assertFalse(rejected["accepted"])
            self.assertEqual(rejected["failed_tests"], ["python3 focused-test.py"])
            self.assertFalse(rejected["replayed"])
            rejected_replay = accept_child_result(ledger, lease, failed_test)
            self.assertFalse(rejected_replay["accepted"])
            self.assertTrue(rejected_replay["replayed"])
            changed_rejection = copy.deepcopy(failed_test)
            changed_rejection["commands"][0]["output_digest"] = "changed-output"
            with self.assertRaises(OperationConflict):
                accept_child_result(ledger, lease, changed_rejection)

            accepted = accept_child_result(ledger, lease, valid)
            self.assertTrue(accepted["accepted"])
            self.assertFalse(accepted["replayed"])
            replayed = accept_child_result(ledger, lease, valid)
            self.assertTrue(replayed["replayed"])
            self.assertEqual(
                read_rows(
                    ledger,
                    "SELECT disposition FROM message_receipts "
                    "WHERE receipt_id = 'result-1'",
                )[0][0],
                "accepted",
            )
            changed = copy.deepcopy(valid)
            changed["result_tree_id"] = "changed-result-tree"
            with self.assertRaises(OperationConflict):
                accept_child_result(ledger, lease, changed)

    def test_schema_v1_is_additively_migrated_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger, lease = initialize(root)
            connection = sqlite3.connect(ledger.path)
            try:
                connection.execute("DROP TABLE child_packets")
                connection.execute("DROP TABLE graph_revisions")
                connection.execute("DROP TABLE context_revisions")
                connection.execute(
                    "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
                )
                connection.commit()
            finally:
                connection.close()

            reopened, replayed_lease = ParentLedger.initialize(
                root,
                "context-parent-run",
                selector="loop_v1",
                writer_id="parent-writer",
                existing_lease=lease,
            )
            self.assertEqual(replayed_lease, lease)
            tables = {
                row[0]
                for row in read_rows(
                    reopened,
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
            self.assertTrue(
                {"context_revisions", "graph_revisions", "child_packets"}.issubset(
                    tables
                )
            )
            self.assertEqual(
                read_rows(reopened, "SELECT value FROM schema_meta")[0][0], "2"
            )


if __name__ == "__main__":
    unittest.main()
