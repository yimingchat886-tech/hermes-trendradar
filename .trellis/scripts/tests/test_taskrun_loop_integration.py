from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from taskrun import (  # noqa: E402
    ExecutionError,
    InterventionRequired,
    InvalidTransition,
    MaterialDriftError,
    OperationConflict,
    OperatorError,
    RESULT_FIELDS,
    TaskRunOperator,
    validate_action_result,
)
from taskrun import execution as execution_module  # noqa: E402
from taskrun import operator as operator_module  # noqa: E402
from test_taskrun_operator import (  # noqa: E402
    OTHER_PREDECESSOR,
    SUCCESSOR,
    TASK,
    git,
    prepare_repo,
)


def action(
    action_id: str,
    path: str,
    resource: str,
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "checks": [f"python3 -m unittest {action_id}"],
        "dependencies": [],
        "problem_id": f"problem-{action_id}",
        "requirement_ids": ["FIXTURE-REQ-001"],
        "resources": [resource],
        "result_schema": sorted(RESULT_FIELDS),
        "touches": [path],
    }


def admit_loop(
    root: Path,
    actions: list[dict[str, object]],
    *,
    attempts: int,
    task: str = TASK,
    authorization_ref: str = "user-execution-signal:loop:1",
    candidate_commit_ref: str | None = None,
    candidate_commit_authorization_ref: str | None = None,
) -> TaskRunOperator:
    return TaskRunOperator.admit_loop(
        root,
        task,
        actor="operator",
        authorization_ref=authorization_ref,
        actions=actions,
        worker_ids=["worker-a", "worker-b"],
        reviewer_id="reviewer-a",
        provider_id="local",
        concurrency=2,
        attempts=attempts,
        candidate_commit_ref=candidate_commit_ref,
        candidate_commit_authorization_ref=candidate_commit_authorization_ref,
    )


def result(
    packet: dict[str, object],
    *,
    passed: bool = True,
    unknowns: list[str] | None = None,
    effects: list[str] | None = None,
    receipts: dict[str, str] | None = None,
    candidate_digest: str | None = None,
    tree_digest: str | None = None,
) -> dict[str, object]:
    intent = packet["intent"]
    assert isinstance(intent, dict)
    touch = intent["touches"][0]
    if touch.endswith("/**"):
        touch = f"{touch[:-3].rstrip('/')}/output.py"
    candidate = candidate_digest or sha256(
        f"{intent['action_id']}:{intent['attempt']}:{packet['worker_id']}".encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "action_id": intent["action_id"],
        "actual_effects": effects or [],
        "actual_touches": [touch],
        "artifact_digests": {},
        "attempt": intent["attempt"],
        "candidate_digest": candidate,
        "checks": {
            command: "passed" if passed else "failed"
            for command in intent["checks"]
        },
        "diff_digest": f"diff:{candidate}",
        "effect_receipts": receipts or {},
        "findings": [],
        "freshness": copy.deepcopy(intent["freshness"]),
        "input_digest": intent["input_digest"],
        "provider_id": packet["provider_id"],
        "requirement_ids": copy.deepcopy(intent["requirement_ids"]),
        "risks": [],
        "task_run_id": intent["task_run_id"],
        "transport_id": f"local:{packet['dispatch']['operation_id']}",
        "tree_digest": tree_digest or f"tree:{candidate}",
        "unknowns": unknowns or [],
        "worker_id": packet["worker_id"],
    }


def review(packet: dict[str, object], candidate_digest: str) -> dict[str, object]:
    intent = packet["intent"]
    assert isinstance(intent, dict)
    return {
        "action_id": intent["action_id"],
        "attempt": intent["attempt"],
        "candidate_digest": candidate_digest,
        "findings": [],
        "input_digest": intent["input_digest"],
        "reviewer_id": "reviewer-a",
        "task_run_id": intent["task_run_id"],
        "verdict": "accepted",
    }


def accept(operator: TaskRunOperator, packet: dict[str, object]) -> dict[str, object]:
    proposal = result(packet)
    operator.submit_result(proposal)
    operator.submit_review(review(packet, proposal["candidate_digest"]))
    return proposal


def final_review(operator: TaskRunOperator) -> dict[str, object]:
    state = operator.run.snapshot()
    components = []
    for item in state["execution"]["actions"].values():
        terminal = [
            decision
            for decision in item["decisions"]
            if decision["kind"] != "review_required"
        ]
        if terminal and terminal[-1]["kind"] == "continue":
            components.append(
                {
                    "action_id": item["intent"]["action_id"],
                    "attempt": item["intent"]["attempt"],
                    "candidate_digest": item["result"]["candidate_digest"],
                }
            )
    components.sort(key=lambda item: item["action_id"])
    digest = sha256(repr(components).encode("utf-8")).hexdigest()
    return {
        "candidate_digest": digest,
        "component_candidates": components,
        "findings": [],
        "review_id": f"aggregate:{digest}",
        "reviewer_id": "reviewer-a",
        "task_run_id": operator.task_run_id,
        "verdict": "accepted",
    }


def digest_json(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


def commit_once(
    root: Path,
    ref: str,
    packet: dict[str, object],
    binding: dict[str, str],
    *,
    extra_trailers: tuple[str, ...] = (),
) -> str:
    intent = packet["intent"]
    dispatch = packet["dispatch"]
    assert isinstance(intent, dict)
    assert isinstance(dispatch, dict)
    operation_id = dispatch["operation_id"]
    touch = intent["touches"][0]
    if touch.endswith("/**"):
        touch = f"{touch[:-3].rstrip('/')}/output.py"
    trailers = (
        f"TaskRun-Operation: {operation_id}",
        f"TaskRun-Input: {intent['input_digest']}",
        f"TaskRun-Fence: {dispatch['fence_digest']}",
        f"TaskRun-Authorization: {binding['authorization_ref']}",
    )
    matches = []
    for commit in git(root, "rev-list", ref).splitlines():
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()
        message = git(root, "show", "-s", "--format=%B", commit)
        changed_paths = git(
            root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).splitlines()
        if (
            parents == [commit, binding["expected_old_oid"]]
            and changed_paths == [touch]
            and execution_module._taskrun_commit_trailers_match(message, trailers)
        ):
            matches.append(commit)
    if len(matches) > 1:
        raise RuntimeError("candidate operation commit is ambiguous")
    if matches:
        return matches[0]
    if git(root, "rev-parse", "HEAD") != binding["expected_old_oid"]:
        raise RuntimeError("candidate ref failed expected-old CAS")
    target = root / touch
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(operation_id + "\n", encoding="utf-8")
    git(root, "add", touch)
    git(
        root,
        "commit",
        "-q",
            "-m",
            "candidate",
            "-m",
            "\n".join((*trailers, *extra_trailers)),
    )
    return git(root, "rev-parse", "HEAD")


def git_commit_receipt(
    packet: dict[str, object],
    binding: dict[str, str],
    commit_oid: str,
    tree_oid: str,
) -> str:
    intent = packet["intent"]
    dispatch = packet["dispatch"]
    assert isinstance(intent, dict)
    assert isinstance(dispatch, dict)
    return json.dumps(
        {
            "authorization_ref": binding["authorization_ref"],
            "commit_oid": commit_oid,
            "expected_old_oid": binding["expected_old_oid"],
            "fence_digest": dispatch["fence_digest"],
            "input_digest": intent["input_digest"],
            "operation_id": dispatch["operation_id"],
            "ref": binding["ref"],
            "schema": "taskrun-git-commit-receipt-v1",
            "task_run_id": intent["task_run_id"],
            "tree_oid": tree_oid,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class TaskRunLoopIntegrationTests(unittest.TestCase):
    def test_two_agents_exclude_resource_and_path_claims_with_fences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit_loop(
                root,
                [
                    action("a", "src/a.py", "shared"),
                    action("b", "src/b.py", "SHARED"),
                    action("c", "src/tree/**", "other-c"),
                    action("d", "src/tree/output.py", "other-d"),
                ],
                attempts=4,
            )

            packets = operator.next_actions()
            self.assertEqual(
                [item["intent"]["action_id"] for item in packets], ["a", "c"]
            )
            self.assertEqual(
                {item["worker_id"] for item in packets}, {"worker-a", "worker-b"}
            )
            self.assertTrue(all(item["dispatch"]["fence_digest"] for item in packets))
            self.assertFalse((root / ".trellis/.runtime/loop-v1").exists())

            event_count = len(operator.run.events())
            replay = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            ).next_actions()
            self.assertEqual(replay, packets)
            self.assertEqual(len(operator.run.events()), event_count)

            stale = result(packets[0])
            stale["freshness"]["dispatch"]["epoch"] += 1
            with self.assertRaisesRegex(ExecutionError, "freshness is stale"):
                operator.submit_result(stale)
            wrong_worker = result(packets[0])
            wrong_worker["worker_id"] = packets[1]["worker_id"]
            with self.assertRaisesRegex(ExecutionError, "worker fence is stale"):
                operator.submit_result(wrong_worker)
            self.assertEqual(len(operator.run.events()), event_count)

            accepted = accept(operator, packets[0])
            changed = copy.deepcopy(accepted)
            changed["candidate_digest"] = "f" * 64
            with self.assertRaises(OperationConflict):
                operator.submit_result(changed)

            pending = operator.next_actions()
            self.assertEqual(
                [item["intent"]["action_id"] for item in pending], ["b", "c"]
            )
            self.assertEqual(pending[1], packets[1])
            self.assertNotIn(
                "d", {item["intent"]["action_id"] for item in pending}
            )

    def test_candidate_ref_claim_is_repository_wide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "authority"
            root.mkdir()
            prepare_repo(root)
            candidate = base / "candidate"
            commit_ref = "refs/heads/shared-candidate"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "shared-candidate",
                str(candidate),
                "HEAD",
            )
            actions = [
                action("a", "src/a.py", "resource-a"),
                action("b", "src/b.py", "resource-b"),
            ]
            first = admit_loop(
                root,
                actions,
                attempts=2,
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref="candidate-commit:first:1",
            )
            second = admit_loop(
                root,
                actions,
                attempts=2,
                task=SUCCESSOR,
                authorization_ref="user-execution-signal:loop:2",
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref="candidate-commit:second:1",
            )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(first.next_actions)
                second_future = pool.submit(second.next_actions)
                groups = [first_future.result(), second_future.result()]

            self.assertEqual(sorted(len(group) for group in groups), [0, 1])
            owner_index = 0 if groups[0] else 1
            owner = (first, second)[owner_index]
            blocked = (first, second)[1 - owner_index]
            owner_packet = groups[owner_index][0]
            owner_operation = owner_packet["dispatch"]["operation_id"]
            owner_result = result(owner_packet)
            self.assertEqual(
                owner.submit_result(owner_result)["kind"], "review_required"
            )
            self.assertEqual(blocked.next_actions(), ())
            owner.submit_review(
                review(owner_packet, owner_result["candidate_digest"])
            )
            next_packet = blocked.next_actions()[0]
            self.assertNotEqual(
                owner_operation, next_packet["dispatch"]["operation_id"]
            )
            third = admit_loop(
                root,
                actions,
                attempts=2,
                task=OTHER_PREDECESSOR,
                authorization_ref="user-execution-signal:loop:3",
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref="candidate-commit:third:1",
            )
            self.assertEqual(third.next_actions(), ())
            self.assertEqual(
                blocked.submit_result(
                    result(
                        next_packet,
                        unknowns=["candidate_commit_outcome_unknown"],
                    )
                )["kind"],
                "pause",
            )
            self.assertEqual(third.next_actions(), ())

    def test_exact_slice_retry_preserves_sibling_and_requires_fresh_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit_loop(
                root,
                [
                    action("a", "src/a.py", "resource-a"),
                    action("b", "src/b.py", "resource-b"),
                ],
                attempts=3,
            )
            packets = {
                item["intent"]["action_id"]: item
                for item in operator.next_actions()
            }
            accepted = accept(operator, packets["a"])
            failed = result(packets["b"], passed=False)
            self.assertEqual(
                operator.submit_result(failed)["kind"], "retry_exact_slice"
            )

            retry = operator.next_actions()
            self.assertEqual(len(retry), 1)
            self.assertEqual(retry[0]["intent"]["action_id"], "b")
            self.assertEqual(retry[0]["intent"]["attempt"], 2)
            self.assertEqual(retry[0]["repair_budget"]["remaining"], 1)
            self.assertNotEqual(
                retry[0]["dispatch"]["fence_digest"],
                packets["b"]["dispatch"]["fence_digest"],
            )
            accept(operator, retry[0])

            state = operator.run.snapshot()
            self.assertIsNone(state["execution"]["candidate_ready"])
            self.assertEqual(
                [
                    item["result"]["candidate_digest"]
                    for item in state["execution"]["actions"].values()
                    if item["intent"]["action_id"] == "a"
                ],
                [accepted["candidate_digest"]],
            )
            aggregate = final_review(operator)
            stale = copy.deepcopy(aggregate)
            stale["component_candidates"][0]["candidate_digest"] = "e" * 64
            before = len(operator.run.events())
            with self.assertRaises(InvalidTransition):
                operator.submit_final_review(stale)
            self.assertEqual(len(operator.run.events()), before)
            ready = operator.submit_final_review(aggregate)
            self.assertEqual(ready["candidate_digest"], aggregate["candidate_digest"])

    def test_unknown_outcome_pauses_before_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit_loop(
                root,
                [
                    action("a", "src/a.py", "resource-a"),
                    action("b", "src/b.py", "resource-b"),
                ],
                attempts=2,
            )
            packet = operator.next_actions()[0]
            decision = operator.submit_result(
                result(packet, unknowns=["candidate_commit_outcome_unknown"])
            )
            self.assertEqual(decision["kind"], "pause")
            before = len(operator.run.events())
            with self.assertRaisesRegex(OperatorError, "paused TaskRun"):
                operator.next_actions()
            self.assertEqual(len(operator.run.events()), before)

    def test_material_drift_cancels_and_rebinds_a_loop_successor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "authority"
            root.mkdir()
            prepare_repo(root)
            candidate = base / "candidate"
            commit_ref = "refs/heads/drift-candidate"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "drift-candidate",
                str(candidate),
                "HEAD",
            )
            actions = [
                action("a", "src/a.py", "resource-a"),
                action("b", "src/b.py", "resource-b"),
            ]
            commit_authorization = "candidate-commit:drift:1"
            operator = admit_loop(
                root,
                actions,
                attempts=3,
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref=commit_authorization,
            )
            (root / ".trellis/scripts/taskrun/runtime.py").write_text(
                'RUNTIME = "drifted"\n', encoding="utf-8"
            )

            with self.assertRaises(MaterialDriftError):
                operator.next_actions()
            successor = operator.supersede(
                SUCCESSOR, authorization_ref="user-repair-signal:loop:1"
            )
            self.assertIsNotNone(successor)
            assert successor is not None
            self.assertEqual(
                operator.run.snapshot()["terminal"]["disposition"], "cancelled"
            )
            self.assertEqual(
                successor.run.snapshot()["execution"]["config"]["strategy"], "loop"
            )
            successor_envelope = successor.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]
            self.assertIsNone(successor_envelope["candidate_commit"])
            self.assertNotIn("git_commit", successor_envelope["allowed_effects"])
            self.assertIn("git_commit", successor_envelope["prohibited_effects"])
            self.assertNotIn(
                commit_authorization, successor_envelope["approval_refs"]
            )
            self.assertTrue(
                all(
                    f"git-ref:{commit_ref}" not in item.get("resources", [])
                    for item in successor_envelope["actions"]
                )
            )
            self.assertEqual(
                [item["intent"]["action_id"] for item in successor.next_actions()],
                ["a", "b"],
            )

    def test_kill_restart_replays_each_boundary_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_repo(root)
            operator = admit_loop(
                root,
                [
                    action("a", "src/a.py", "resource-a"),
                    action("b", "src/b.py", "resource-b"),
                ],
                attempts=2,
            )
            original_plan = operator.run.record_action_planned
            calls = 0

            def crash_after_first_plan(**kwargs: object) -> dict[str, object]:
                nonlocal calls
                outcome = original_plan(**kwargs)
                calls += 1
                if calls == 1:
                    raise RuntimeError("kill after writer commit")
                return outcome

            with mock.patch.object(
                operator.run,
                "record_action_planned",
                side_effect=crash_after_first_plan,
            ):
                with self.assertRaisesRegex(RuntimeError, "writer commit"):
                    operator.next_actions()

            reopened = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            packets = reopened.next_actions()
            dispatch_events = [
                event
                for event in reopened.run.events()
                if event["event_type"] == "action_planned"
            ]
            self.assertEqual(len(dispatch_events), 2)
            before = len(reopened.run.events())
            self.assertEqual(
                TaskRunOperator.reopen(
                    root, operator.task_run_id, actor="operator"
                ).next_actions(),
                packets,
            )
            self.assertEqual(len(reopened.run.events()), before)

            proposal = result(packets[0])
            with mock.patch.object(
                reopened.run,
                "record_strategy_decision",
                side_effect=RuntimeError("kill after ingest"),
            ):
                with self.assertRaisesRegex(RuntimeError, "after ingest"):
                    reopened.submit_result(proposal)
            reopened = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            reopened.submit_result(proposal)
            reopened.submit_review(review(packets[0], proposal["candidate_digest"]))
            accept(reopened, packets[1])

            aggregate = final_review(reopened)
            real_final = operator_module.record_final_candidate_review

            def crash_after_candidate(
                *args: object, **kwargs: object
            ) -> dict[str, object]:
                real_final(*args, **kwargs)
                raise RuntimeError("kill after candidate")

            with mock.patch.object(
                operator_module,
                "record_final_candidate_review",
                side_effect=crash_after_candidate,
            ):
                with self.assertRaisesRegex(RuntimeError, "after candidate"):
                    reopened.submit_final_review(aggregate)
            reopened = TaskRunOperator.reopen(
                root, operator.task_run_id, actor="operator"
            )
            ready = reopened.submit_final_review(aggregate)
            self.assertEqual(ready["candidate_digest"], aggregate["candidate_digest"])
            self.assertEqual(
                len(
                    [
                        event
                        for event in reopened.run.events()
                        if event["event_type"] == "final_candidate_reviewed"
                    ]
                ),
                1,
            )

    def test_candidate_commit_requires_bound_authority_and_replays_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            unauthorized_root = base / "unauthorized"
            unauthorized_root.mkdir()
            prepare_repo(unauthorized_root)
            unauthorized = admit_loop(
                unauthorized_root,
                [
                    action("a", "src/a.py", "resource-a"),
                    action("b", "src/b.py", "resource-b"),
                ],
                attempts=2,
            )
            packet = unauthorized.next_actions()[0]
            with self.assertRaisesRegex(InterventionRequired, "effect expansion"):
                unauthorized.submit_result(
                    result(
                        packet,
                        effects=["git_commit"],
                        receipts={"git_commit": "commit:unauthorized"},
                    )
                )
            fake_candidate = base / "fake-candidate"
            fake_ref = "refs/heads/fake-candidate"
            git(
                unauthorized_root,
                "worktree",
                "add",
                "-q",
                "-b",
                "fake-candidate",
                str(fake_candidate),
                "HEAD",
            )
            fake_operator = admit_loop(
                unauthorized_root,
                [
                    action("a", "src/a.py", "resource-a"),
                    action("b", "src/b.py", "resource-b"),
                ],
                attempts=2,
                task=SUCCESSOR,
                candidate_commit_ref=fake_ref,
                candidate_commit_authorization_ref="candidate-commit:fake:1",
            )
            fake_packet = fake_operator.next_actions()[0]
            self.assertNotEqual(
                packet["dispatch"]["operation_id"],
                fake_packet["dispatch"]["operation_id"],
            )
            fake_binding = fake_operator.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]["candidate_commit"]
            fake_oid = "f" * 40
            fake_candidate_digest = digest_json(
                {"commit_oid": fake_oid, "tree_oid": fake_oid}
            )
            fake_proposal = result(
                fake_packet,
                effects=["git_commit"],
                receipts={
                    "git_commit": git_commit_receipt(
                        fake_packet, fake_binding, fake_oid, fake_oid
                    )
                },
                candidate_digest=fake_candidate_digest,
                tree_digest=fake_oid,
            )
            self.assertEqual(
                fake_operator.submit_result(fake_proposal)["kind"], "pause"
            )
            self.assertEqual(
                fake_operator.run.snapshot()["execution"]["paused"]["reason"],
                "unknown_outcome",
            )
            self.assertIn(
                "git_commit_receipt_unverified",
                fake_operator.run.snapshot()["execution"]["actions"]["a:1"][
                    "result"
                ]["unknowns"],
            )

            root = base / "authority"
            root.mkdir()
            prepare_repo(root)
            candidate = base / "candidate"
            commit_ref = "refs/heads/taskrun-candidate"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "taskrun-candidate",
                str(candidate),
                "HEAD",
            )

            commit_authorization = "candidate-commit:fixture:1"
            commit_actions = [
                action("a", "src/a.py", "resource-a"),
                action("b", "src/b.py", "resource-b"),
            ]
            task_path = root / ".trellis/tasks" / TASK / "task.json"
            unbound_task = task_path.read_bytes()
            head_ref = git(root, "symbolic-ref", "HEAD")
            alias_ref = "refs/heads/authority-head-alias"
            candidate_alias_ref = "refs/heads/candidate-alias"
            git(root, "symbolic-ref", alias_ref, head_ref)
            git(root, "symbolic-ref", candidate_alias_ref, commit_ref)
            for forbidden_ref in (head_ref, alias_ref, candidate_alias_ref):
                with self.subTest(forbidden_ref=forbidden_ref), self.assertRaisesRegex(
                    OperatorError, "must be one independent direct branch"
                ):
                    admit_loop(
                        root,
                        commit_actions,
                        attempts=2,
                        candidate_commit_ref=forbidden_ref,
                        candidate_commit_authorization_ref=commit_authorization,
                    )
                self.assertEqual(task_path.read_bytes(), unbound_task)
                self.assertFalse((root / ".trellis/.runtime/taskrun").exists())
            with self.assertRaisesRegex(
                OperatorError, "requires separate authorization"
            ):
                admit_loop(
                    root,
                    commit_actions,
                    attempts=2,
                    authorization_ref=commit_authorization,
                    candidate_commit_ref=commit_ref,
                    candidate_commit_authorization_ref=commit_authorization,
                )
            self.assertEqual(task_path.read_bytes(), unbound_task)
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())
            operator = admit_loop(
                root,
                commit_actions,
                attempts=2,
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref=commit_authorization,
            )
            commit_packets = operator.next_actions()
            self.assertEqual(len(commit_packets), 1)
            packet = commit_packets[0]
            self.assertIn("git_commit", packet["intent"]["allowed_effects"])
            self.assertIn(
                commit_authorization, packet["intent"]["approval_refs"]
            )
            self.assertIn(f"git-ref:{commit_ref}", packet["claims"]["resources"])
            binding = operator.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]["candidate_commit"]
            envelope = operator.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]
            reused_authority = copy.deepcopy(envelope)
            reused_authority["candidate_commit"]["authorization_ref"] = (
                reused_authority["authorization_ref"]
            )
            with self.assertRaisesRegex(
                ExecutionError, "requires separate authorization"
            ):
                execution_module._validate_start_envelope(
                    reused_authority, "loop"
                )
            stale_base = copy.deepcopy(envelope)
            stale_base["candidate_commit"]["expected_old_oid"] = "e" * 40
            with self.assertRaisesRegex(ExecutionError, "must equal identities.base"):
                execution_module._validate_start_envelope(stale_base, "loop")
            commit = commit_once(
                candidate,
                commit_ref,
                packet,
                binding,
            )

            replay = admit_loop(
                root,
                commit_actions,
                attempts=2,
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref=commit_authorization,
            ).next_actions()[0]
            self.assertEqual(replay, packet)
            self.assertEqual(
                commit_once(
                    candidate,
                    commit_ref,
                    packet,
                    binding,
                ),
                commit,
            )
            self.assertEqual(git(candidate, "rev-list", "--count", "HEAD"), "2")

            tree = git(candidate, "rev-parse", f"{commit}^{{tree}}")
            candidate_digest = digest_json(
                {"commit_oid": commit, "tree_oid": tree}
            )
            proposal = result(
                replay,
                effects=["git_commit"],
                receipts={
                    "git_commit": git_commit_receipt(
                        replay, binding, commit, tree
                    )
                },
                candidate_digest=candidate_digest,
                tree_digest=tree,
            )
            for field, forged_value in (
                ("task_run_id", "task-forged"),
                ("operation_id", "taskrun:forged:a:1"),
                ("input_digest", "e" * 64),
                ("fence_digest", "e" * 64),
                ("authorization_ref", "candidate-commit:forged"),
                ("expected_old_oid", "e" * 40),
                ("ref", "refs/heads/forged"),
                ("commit_oid", "e" * 40),
                ("tree_oid", "e" * 40),
            ):
                forged = copy.deepcopy(proposal)
                receipt = json.loads(forged["effect_receipts"]["git_commit"])
                receipt[field] = forged_value
                forged["effect_receipts"]["git_commit"] = json.dumps(
                    receipt,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                checked = validate_action_result(
                    operator.run.snapshot(), forged, repo_root=root
                )
                self.assertIn(
                    "git_commit_receipt_unverified", checked["unknowns"]
                )
            self.assertEqual(
                operator.submit_result(proposal)["kind"], "review_required"
            )
            operator.submit_review(review(replay, proposal["candidate_digest"]))
            envelope = operator.run.snapshot()["execution"]["config"]["start_envelope"]
            self.assertIn("push", envelope["prohibited_effects"])
            self.assertIn("network", envelope["prohibited_effects"])
            self.assertEqual(
                git(root, "rev-parse", "HEAD"), binding["expected_old_oid"]
            )

            (candidate / "later.txt").write_text("later\n", encoding="utf-8")
            git(candidate, "add", "later.txt")
            git(
                candidate,
                "commit",
                "-q",
                "-m",
                "later candidate",
                "-m",
                f"TaskRun-Operation: {packet['dispatch']['operation_id']}-suffix",
            )
            self.assertEqual(
                commit_once(
                    candidate,
                    commit_ref,
                    packet,
                    binding,
                ),
                commit,
            )
            with self.assertRaises(MaterialDriftError):
                operator.final_request()

    def test_conflicting_taskrun_trailers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "authority"
            root.mkdir()
            prepare_repo(root)
            candidate = base / "candidate"
            commit_ref = "refs/heads/ambiguous-candidate"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "ambiguous-candidate",
                str(candidate),
                "HEAD",
            )
            operator = admit_loop(
                root,
                [action("a", "src/a.py", "resource-a")],
                attempts=2,
                candidate_commit_ref=commit_ref,
                candidate_commit_authorization_ref="candidate-commit:ambiguous:1",
            )
            packet = operator.next_actions()[0]
            binding = operator.run.snapshot()["execution"]["config"][
                "start_envelope"
            ]["candidate_commit"]
            intent = packet["intent"]
            dispatch = packet["dispatch"]
            expected_trailers = (
                f"TaskRun-Operation: {dispatch['operation_id']}",
                f"TaskRun-Input: {intent['input_digest']}",
                f"TaskRun-Fence: {dispatch['fence_digest']}",
                f"TaskRun-Authorization: {binding['authorization_ref']}",
            )
            for extra in (
                "TaskRun-Operation: taskrun:other:a:1",
                "taskrun-operation: taskrun:other:a:1",
                "TaskRun-Other: ambiguous",
            ):
                self.assertFalse(
                    execution_module._taskrun_commit_trailers_match(
                        "\n".join((*expected_trailers, extra)),
                        expected_trailers,
                    )
                )
            commit = commit_once(
                candidate,
                commit_ref,
                packet,
                binding,
                extra_trailers=("TaskRun-Other: ambiguous",),
            )
            tree = git(candidate, "rev-parse", f"{commit}^{{tree}}")
            proposal = result(
                packet,
                effects=["git_commit"],
                receipts={
                    "git_commit": git_commit_receipt(
                        packet, binding, commit, tree
                    )
                },
                candidate_digest=digest_json(
                    {"commit_oid": commit, "tree_oid": tree}
                ),
                tree_digest=tree,
            )
            checked = validate_action_result(
                operator.run.snapshot(), proposal, repo_root=root
            )
            self.assertIn("git_commit_receipt_unverified", checked["unknowns"])
            with self.assertRaises(MaterialDriftError):
                operator.submit_result(proposal)
            self.assertIsNone(
                operator.run.snapshot()["execution"]["actions"]["a:1"]["result"]
            )

    def test_fresh_admission_rejects_an_advanced_candidate_without_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "authority"
            root.mkdir()
            prepare_repo(root)
            candidate = base / "candidate"
            commit_ref = "refs/heads/advanced-candidate"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "advanced-candidate",
                str(candidate),
                "HEAD",
            )
            (candidate / "advanced.txt").write_text("advanced\n", encoding="utf-8")
            git(candidate, "add", "advanced.txt")
            git(candidate, "commit", "-q", "-m", "advance before admission")
            task_path = root / ".trellis/tasks" / TASK / "task.json"
            unbound_task = task_path.read_bytes()

            with self.assertRaisesRegex(
                OperatorError, "must equal repository base at admission"
            ):
                admit_loop(
                    root,
                    [
                        action("a", "src/a.py", "resource-a"),
                        action("b", "src/b.py", "resource-b"),
                    ],
                    attempts=2,
                    candidate_commit_ref=commit_ref,
                    candidate_commit_authorization_ref="candidate-commit:advanced:1",
                )

            self.assertEqual(task_path.read_bytes(), unbound_task)
            self.assertFalse((root / ".trellis/.runtime/taskrun").exists())


if __name__ == "__main__":
    unittest.main()
