from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from loop_v1 import (
    InterventionRequired,
    SchedulerError,
    accept_child_result,
    approve_start_request,
    create_start_request,
    deterministic_integration_selection,
    freshness_token,
    issue_child_packet,
    record_context_revision,
    record_optional_omission,
    record_problem_attempt,
    release_child_resources,
    repair_or_pause_graph,
    requirement_progress,
    schedule_ready,
)
from loop_v1.integration import prepare_replacement_revision
from test_loop_v1_context import canonical_context, initialize, start_envelope


def scheduler_envelope() -> dict[str, object]:
    value = start_envelope()
    value["resources"] = [
        {
            "resource_key": "canonical-repo",
            "aliases": ["repo"],
            "mode": "exclusive",
            "capacity": 1,
        },
        {
            "resource_key": "shared-cache",
            "aliases": ["cache", "package-cache"],
            "mode": "shared",
            "capacity": 2,
        },
        {
            "resource_key": "wide-cache",
            "aliases": ["wide"],
            "mode": "shared",
            "capacity": 5,
        },
    ]
    value["worker_capacity"] = 5
    return value


def graph_node(
    child_id: str,
    requirements: list[str],
    touch: str | None,
    resource: str | None,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "child_id": child_id,
        "requirements": requirements,
        "touches": [] if touch is None else [touch],
        "resources": [] if resource is None else [resource],
        "depends_on": depends_on or [],
    }


def setup_runtime(
    root: Path,
    *,
    graph: list[dict[str, object]] | None = None,
    states: dict[str, str] | None = None,
):
    ledger, lease = initialize(root)
    request = create_start_request(
        ledger,
        lease,
        request_id="start-request-1",
        envelope=scheduler_envelope(),
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
    context = canonical_context()
    if graph is not None:
        context["graph"] = graph
    for requirement in context["requirements"]:
        if states and requirement["requirement_id"] in states:
            requirement["coverage_state"] = states[requirement["requirement_id"]]
    record_context_revision(
        ledger,
        lease,
        request_id="start-request-1",
        revision_id="context-1",
        reason="scheduler fixture",
        context=context,
    )
    return ledger, lease


def packet_assignment(child_id: str, packet_id: str, requirement_id: str):
    return {
        "packet_id": packet_id,
        "child_id": child_id,
        "requirements": [requirement_id],
        "forbidden_touches": [],
        "tests": [f"python3 test-{child_id}.py"],
        "context_slice_ids": ["slice-public"],
        "parent_contact": "channel:parent-run",
        "result_deadline": "2026-07-13T19:00:00Z",
        "result_lease": f"lease-{child_id}",
        "attempt": 1,
        "round": 1,
    }


def structured_result(
    packet: dict[str, object],
    *,
    result_id: str,
    requirement_id: str,
    path: str,
) -> dict[str, object]:
    child_id = packet["child_id"]
    return {
        "result_id": result_id,
        "packet_id": packet["packet_id"],
        "child_id": child_id,
        "actual_touches": [path],
        "diff_identity": f"diff-{child_id}",
        "base_head": packet["base"]["head"],
        "base_tree_id": packet["base"]["tree_id"],
        "result_tree_id": f"tree-{child_id}",
        "commands": [
            {
                "command": f"python3 test-{child_id}.py",
                "status": "passed",
                "output_digest": f"output-{child_id}",
            }
        ],
        "coverage": [requirement_id],
        "risks": [],
        "findings": [],
        "artifacts": [{"path": path, "digest": f"artifact-{child_id}"}],
        **packet["identity"],
    }


class LoopV1SchedulerTests(unittest.TestCase):
    def test_requirement_truth_and_optional_omission_are_coverage_owned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(
                Path(tmp), states={"REQ-1": "covered", "REQ-2": "omitted"}
            )
            before = requirement_progress(ledger)
            self.assertFalse(before["final_ready"])
            self.assertEqual(before["omission_missing_rationale"], ["REQ-2"])

            omission = record_optional_omission(
                ledger,
                lease,
                operation_id="omit:req-2",
                requirement_id="REQ-2",
                rationale="No required criterion needs the optional document.",
                impact="The final pack discloses that the document is absent.",
            )
            replayed = record_optional_omission(
                ledger,
                lease,
                operation_id="omit:req-2",
                requirement_id="REQ-2",
                rationale="No required criterion needs the optional document.",
                impact="The final pack discloses that the document is absent.",
            )
            self.assertEqual(omission, replayed)
            progress = requirement_progress(ledger)
            self.assertTrue(progress["final_ready"])
            self.assertEqual(progress["required_covered"], ["REQ-1"])
            self.assertEqual(
                progress["optional_omissions"][0]["requirement_id"], "REQ-2"
            )
            with self.assertRaises(SchedulerError):
                record_optional_omission(
                    ledger,
                    lease,
                    operation_id="omit:req-1",
                    requirement_id="REQ-1",
                    rationale="Not allowed.",
                    impact="Would weaken required coverage.",
                )

            invalid = canonical_context()
            invalid["requirements"][0]["coverage_state"] = "omitted"
            with self.assertRaises(InterventionRequired):
                record_context_revision(
                    ledger,
                    lease,
                    request_id="start-request-1",
                    revision_id="context-invalid",
                    reason="attempt to weaken required coverage",
                    context=invalid,
                )

    def test_ready_order_cap_three_and_replay_are_deterministic(self) -> None:
        graph = [
            graph_node(
                f"child-{letter}",
                ["REQ-1"],
                f".trellis/scripts/loop_v1/{letter}.py",
                "wide",
            )
            for letter in "edcba"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=graph)
            decision = schedule_ready(
                ledger, lease, decision_id="decision-1", live_capacity=10
            )
            replayed = schedule_ready(
                ledger, lease, decision_id="decision-1", live_capacity=10
            )
            self.assertEqual(decision, replayed)
            self.assertEqual(
                decision["ready_order"],
                ["child-a", "child-b", "child-c", "child-d", "child-e"],
            )
            self.assertEqual(decision["selected"], ["child-a", "child-b", "child-c"])
            self.assertEqual(decision["hard_cap"], 3)
            self.assertEqual(decision["effective_parallel"], 3)

            released = release_child_resources(
                ledger,
                lease,
                operation_id="release:child-a",
                child_id="child-a",
                reason="dispatch was retired before packet issue",
            )
            self.assertTrue(released["released_claims"])
            next_decision = schedule_ready(
                ledger, lease, decision_id="decision-2", live_capacity=10
            )
            self.assertEqual(next_decision["dispatch_count"], 1)
            self.assertEqual(next_decision["effective_parallel"], 3)

        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=graph)
            decision = schedule_ready(
                ledger, lease, decision_id="decision-live-2", live_capacity=2
            )
            self.assertEqual(decision["selected"], ["child-a", "child-b"])
            self.assertEqual(decision["hard_cap"], 2)

    def test_same_child_requirement_dependencies_are_co_delivered(self) -> None:
        combined_graph = [
            graph_node(
                "child-combined",
                ["REQ-1", "REQ-2"],
                ".trellis/scripts/loop_v1/combined.py",
                "wide",
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=combined_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="co-delivered", live_capacity=1
            )
            self.assertEqual(decision["status"], "scheduled")
            self.assertEqual(decision["ready_order"], ["child-combined"])
            self.assertEqual(decision["selected"], ["child-combined"])

        external_graph = [
            graph_node(
                "child-a",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/a.py",
                "wide",
            ),
            graph_node(
                "child-b",
                ["REQ-2"],
                ".trellis/scripts/loop_v1/b.py",
                "wide",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=external_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="external-blocked", live_capacity=2
            )
            self.assertEqual(decision["status"], "scheduled")
            self.assertEqual(decision["ready_order"], ["child-a"])
            self.assertEqual(decision["selected"], ["child-a"])

    def test_alias_path_exclusive_and_shared_capacity_conflicts(self) -> None:
        exclusive_graph = [
            graph_node(
                "child-a",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/area/**",
                "repo",
            ),
            graph_node(
                "child-b",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/area/file.py",
                "canonical-repo",
            ),
            graph_node(
                "child-c",
                ["REQ-1"],
                ".trellis/spec/project/loop-v1-runtime.md",
                "REPO",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=exclusive_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="exclusive", live_capacity=3
            )
            self.assertEqual(decision["selected"], ["child-a"])
            self.assertIn("child-b", decision["conflicts"])
            self.assertIn("child-c", decision["conflicts"])

        shared_graph = [
            graph_node(
                f"child-{letter}",
                ["REQ-1"],
                f".trellis/scripts/loop_v1/{letter}.py",
                "cache",
            )
            for letter in "abc"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=shared_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="shared", live_capacity=3
            )
            self.assertEqual(decision["selected"], ["child-a", "child-b"])
            self.assertEqual(
                decision["conflicts"]["child-c"],
                ["capacity:resource:shared-cache"],
            )

    def test_missing_or_ambiguous_declarations_force_serial(self) -> None:
        missing_graph = [
            graph_node("child-a", ["REQ-1"], None, None),
            graph_node(
                "child-b",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/b.py",
                "wide",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=missing_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="missing", live_capacity=3
            )
            self.assertEqual(decision["serial_reason"], "missing_declarations")
            self.assertEqual(decision["selected"], ["child-a"])
            self.assertEqual(decision["effective_parallel"], 1)

        ambiguous_graph = [
            graph_node(
                "child-a",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/*.py",
                "wide",
            ),
            graph_node(
                "child-b",
                ["REQ-1"],
                ".trellis/spec/project/loop-v1-runtime.md",
                "wide",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=ambiguous_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="ambiguous", live_capacity=3
            )
            self.assertEqual(decision["serial_reason"], "ambiguous_declarations")
            self.assertEqual(len(decision["selected"]), 1)

    def test_graph_cycles_pause_and_satisfied_edges_repair(self) -> None:
        cycle_graph = [
            graph_node(
                "child-a",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/a.py",
                "wide",
                depends_on=["child-b"],
            ),
            graph_node(
                "child-b",
                ["REQ-2"],
                ".trellis/spec/project/loop-v1-runtime.md",
                "wide",
                depends_on=["child-a"],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), graph=cycle_graph)
            decision = schedule_ready(
                ledger, lease, decision_id="cycle", live_capacity=3
            )
            self.assertEqual(decision["status"], "paused")
            self.assertEqual(decision["reason"], "dependency_cycle")
            paused = repair_or_pause_graph(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="repair-cycle",
            )
            replayed = repair_or_pause_graph(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="repair-cycle",
            )
            self.assertEqual(paused, replayed)
            self.assertEqual(paused["status"], "paused")

            changed = canonical_context()
            changed["graph"] = cycle_graph
            changed["requirements"][0]["coverage_state"] = "covered"
            record_context_revision(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="context-after-pause",
                reason="make one cycle edge safe after the pause decision",
                context=changed,
            )
            still_paused = repair_or_pause_graph(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="repair-cycle",
            )
            self.assertEqual(still_paused, paused)

        repairable_graph = [
            graph_node(
                "child-a",
                ["REQ-1"],
                ".trellis/scripts/loop_v1/a.py",
                "wide",
            ),
            graph_node(
                "child-b",
                ["REQ-2"],
                ".trellis/spec/project/loop-v1-runtime.md",
                "wide",
                depends_on=["child-a"],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(
                Path(tmp), graph=repairable_graph, states={"REQ-1": "covered"}
            )
            repaired = repair_or_pause_graph(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="repair-safe",
            )
            replayed = repair_or_pause_graph(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="repair-safe",
            )
            self.assertEqual(repaired, replayed)
            self.assertEqual(repaired["removed_edges"], [["child-b", "child-a"]])
            self.assertEqual(repaired["status"], "repaired")

    def test_replacement_split_and_merge_preserve_requirement_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp))
            initial = requirement_progress(ledger)
            self.assertEqual(initial["coverage_plan"]["REQ-1"], ["child-a"])
            self.assertEqual(initial["coverage_plan"]["REQ-2"], ["child-b"])

            replacement = canonical_context()
            replacement["graph"] = [
                graph_node(
                    "child-a1",
                    ["REQ-1"],
                    ".trellis/scripts/loop_v1/context.py",
                    "wide",
                ),
                graph_node(
                    "child-a2",
                    ["REQ-1"],
                    ".trellis/scripts/loop_v1/scheduler.py",
                    "wide",
                ),
                graph_node(
                    "child-merged",
                    ["REQ-1", "REQ-2"],
                    ".trellis/spec/project/loop-v1-runtime.md",
                    "wide",
                    depends_on=["child-a1", "child-a2"],
                ),
            ]
            record_context_revision(
                ledger,
                lease,
                request_id="start-request-1",
                revision_id="context-replacement",
                reason="replace, split, and merge children inside the envelope",
                context=replacement,
            )

            reshaped = requirement_progress(ledger)
            self.assertEqual(reshaped["required_total"], 1)
            self.assertEqual(reshaped["required_unmet"], ["REQ-1"])
            self.assertEqual(
                reshaped["coverage_plan"]["REQ-1"],
                ["child-a1", "child-a2", "child-merged"],
            )
            self.assertEqual(reshaped["coverage_plan"]["REQ-2"], ["child-merged"])

    def test_failed_child_replacement_preserves_covered_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp), states={"REQ-2": "covered"})
            issue_child_packet(
                ledger, lease, packet_assignment("child-a", "packet-a", "REQ-1")
            )
            packet_b = issue_child_packet(
                ledger, lease, packet_assignment("child-b", "packet-b", "REQ-2")
            )
            accept_child_result(
                ledger,
                lease,
                structured_result(
                    packet_b,
                    result_id="result-b",
                    requirement_id="REQ-2",
                    path=".trellis/spec/project/loop-v1-runtime.md",
                ),
            )
            with ledger._write_transaction(lease) as connection:
                connection.execute(
                    "UPDATE child_operations SET state = 'stale' "
                    "WHERE child_id = 'child-a'"
                )
            connection = ledger._connect(read_only=True)
            try:
                sibling_before = tuple(
                    connection.execute(
                        "SELECT state, context_digest, updated_at "
                        "FROM child_operations WHERE child_id = 'child-b'"
                    ).fetchone()
                )
            finally:
                connection.close()
            problem = record_problem_attempt(
                ledger,
                lease,
                problem_id="caller-selected-id",
                round_number=0,
                operation_phase="worker_result",
                root_condition="worker result cannot be accepted",
                requirement_ids=["REQ-1"],
                diagnosis="the prior child became stale",
                action="replace only the failed coverage",
                commands=[
                    {
                        "command": "python3 test-child-a.py",
                        "exit_code": 1,
                        "output_digest": "child-a-failed",
                        "status": "failed",
                    }
                ],
                artifact_ids=["packet-a"],
                result="failed",
            )
            replacement_graph = canonical_context()["graph"]
            replacement_graph[0]["child_id"] = "child-a-repair"
            replacement_graph[1]["depends_on"] = ["child-a-repair"]

            replacement = prepare_replacement_revision(
                ledger,
                lease,
                request_id="start-request-1",
                problem_id=problem["problem_id"],
                source_context_digest=freshness_token(ledger)["context_digest"],
                replaced_child_ids=["child-a"],
                replacement_graph=replacement_graph,
            )

            self.assertEqual(replacement["replacement_child_ids"], ["child-a-repair"])
            progress = requirement_progress(ledger)
            self.assertEqual(progress["optional_covered"], ["REQ-2"])
            self.assertEqual(progress["coverage_plan"]["REQ-1"], ["child-a-repair"])
            self.assertEqual(progress["coverage_plan"]["REQ-2"], ["child-b"])
            connection = ledger._connect(read_only=True)
            try:
                old_state = connection.execute(
                    "SELECT state FROM child_operations WHERE child_id = 'child-a'"
                ).fetchone()[0]
                sibling_after = tuple(
                    connection.execute(
                        "SELECT state, context_digest, updated_at "
                        "FROM child_operations WHERE child_id = 'child-b'"
                    ).fetchone()
                )
            finally:
                connection.close()
            self.assertEqual(old_state, "stale")
            self.assertEqual(sibling_after, sibling_before)
            self.assertEqual(sibling_after[0], "result_validated")

    def test_integration_selection_ignores_completion_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, lease = setup_runtime(Path(tmp))
            packet_b = issue_child_packet(
                ledger, lease, packet_assignment("child-b", "packet-b", "REQ-2")
            )
            packet_a = issue_child_packet(
                ledger, lease, packet_assignment("child-a", "packet-a", "REQ-1")
            )
            accept_child_result(
                ledger,
                lease,
                structured_result(
                    packet_b,
                    result_id="result-b",
                    requirement_id="REQ-2",
                    path=".trellis/spec/project/loop-v1-runtime.md",
                ),
            )
            accept_child_result(
                ledger,
                lease,
                structured_result(
                    packet_a,
                    result_id="result-a",
                    requirement_id="REQ-1",
                    path=".trellis/scripts/loop_v1/context.py",
                ),
            )
            selection = deterministic_integration_selection(ledger)
            self.assertEqual(selection["status"], "ready")
            self.assertEqual(selection["selected"], ["child-a", "child-b"])


if __name__ == "__main__":
    unittest.main()
