from __future__ import annotations

import copy
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from loop_v1 import (
    ContextError,
    ControlError,
    DirtOverlapError,
    FinalGateError,
    FreshnessError,
    OperationConflict,
    OrchestratorError,
    ParentLedger,
    ReadinessError,
    RecoveryError,
    WriterLease,
    accept_child_result,
    advance_operator,
    cancel_operator,
    continue_recovery,
    ingest_operator,
    initialize_operator,
    ledger_path,
    observe_worker_candidate,
    operator_status,
    pause_parent,
    pause_operator,
    recover_final_checks,
    respond_final,
    respond_start,
    record_problem_attempt,
    revoke_operator,
    resume_operator,
    scan_repository_dirt,
)
from loop_v1.context import _digest_json, record_context_revision
from loop_v1.integration import _canonical_recovery_observations
from loop_v1.orchestrator import (
    _current_graph_digest,
    _load_runtime,
    _record_recovery_observation,
    _recovery_failure_observations,
    _consumed_zero_diff_reconciliation,
    _reviewed_zero_diff_recovery,
    guide_replacement,
)
from loop_v1.qualification import QualificationError, QualificationStatus
from test_loop_v1_worker_commit import git, initialize_git_repo


RECEIPT_ID = f"sha256:{'a' * 64}"
RUN_ID = "pilot-run"


@contextmanager
def qualified_runtime():
    status = QualificationStatus(True, True, RECEIPT_ID, ())
    with mock.patch(
        "loop_v1.orchestrator.operation_qualification", return_value=status
    ), mock.patch(
        "loop_v1.qualification.configured_qualification", return_value=status
    ), mock.patch(
        "loop_v1.qualification.capture_execution_binding",
        return_value={"mode": "unenforced"},
    ):
        yield


def create_fixture(
    root: Path,
    *,
    workflow_mode: str = "loop_v1",
    authorization_profile: str | None = "strict",
) -> dict[str, object]:
    repo = root / "repo"
    initialize_git_repo(repo)
    task_dir = repo / ".trellis" / "tasks" / "07-14-pilot-run"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "base_branch": "main",
                "id": RUN_ID,
                "meta": {
                    **(
                        {"authorization_profile": authorization_profile}
                        if authorization_profile is not None
                        else {}
                    ),
                    "workflow_mode": workflow_mode,
                },
                "status": "planning",
                "tier": "parent",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    git(repo, "add", ".trellis/tasks/07-14-pilot-run/task.json")
    git(repo, "commit", "-m", "add Loop parent task")
    base_head = git(repo, "rev-parse", "HEAD")
    base_tree = git(repo, "rev-parse", "HEAD^{tree}")
    dirt = scan_repository_dirt(repo)
    requirements = [
        {
            "acceptance": ["src child is integrated"],
            "dependencies": [],
            "required": True,
            "requirement_id": "REQ-1",
        },
        {
            "acceptance": ["tests child is integrated"],
            "dependencies": [],
            "required": True,
            "requirement_id": "REQ-2",
        },
    ]
    graph = [
        {
            "child_id": "child-a",
            "depends_on": [],
            "requirements": ["REQ-1"],
            "resources": ["work-slot"],
            "touches": ["src/**"],
        },
        {
            "child_id": "child-b",
            "depends_on": [],
            "requirements": ["REQ-2"],
            "resources": ["work-slot"],
            "touches": ["tests/**"],
        },
    ]
    envelope = {
        "acceptance": ["both exact child trees are integrated"],
        "allowed_scope": ["bounded local text"],
        "allowed_touches": ["src/**", "tests/**"],
        "approved_agent_surfaces": ["codex"],
        "base_branch": "main",
        "base_head": base_head,
        "conformance_receipt": RECEIPT_ID,
        "cost_budget": None,
        "default_parallel": 2,
        "dirty_path_fingerprint": dirt["digest"],
        "goal": "Integrate two bounded local text changes.",
        "initial_child_graph": graph,
        "local_effects": ["local Git and ignored runtime writes"],
        "max_parallel": 2,
        "prohibited_actions": ["push", "release", "deploy"],
        "read_only_external_inputs": [],
        "requirements": requirements,
        "resources": [
            {"capacity": 2, "mode": "shared", "resource_key": "work-slot"}
        ],
        "retry_policy": {"same_problem_rounds": 3},
        "review_policy": {"exact_tree": True},
        "reviewer_capacity": 1,
        "risks": ["bounded local text"],
        "token_budget": 10000,
        "verification_policy": {
            "operator": {
                "children": {
                    "child-a": {
                        "result_deadline": "2026-07-14T23:00:00Z",
                        "tests": ["git diff --check"],
                    },
                    "child-b": {
                        "result_deadline": "2026-07-14T23:00:00Z",
                        "tests": ["git diff --check"],
                    },
                },
                "effect_proofs": [
                    {
                        "classification": "local_declared",
                        "effect_id": "local-runtime",
                        "evidence_digest": "sha256:local-runtime",
                        "status": "passed",
                    },
                    {
                        "classification": "prohibited_external",
                        "effect_id": "external-boundary",
                        "evidence_digest": "sha256:no-external-effect",
                        "status": "not_performed",
                    },
                ],
                "environment": {
                    "git_version": "git-test",
                    "input_digest": "sha256:inputs",
                    "platform_digest": "sha256:platform",
                    "python_version": "python-test",
                    "runtime_version": "loop-v1-operator-test",
                    "tool_config_digest": "sha256:tool-config",
                },
                "final_integration_checks": ["git diff --check"],
                "integration_checks": ["git diff --check"],
                "post_merge_checks": ["git diff --check"],
                "skipped_checks": [],
            }
        },
        "worker_capacity": 2,
    }
    context = {
        "context_slices": [
            {
                "digest": "slice-operator",
                "excerpt": "Workers edit only their assigned text path.",
                "kind": "spec",
                "slice_id": "slice-public",
                "source_ref": "operator-test",
                "visibility": "public",
            }
        ],
        "decisions": [{"id": "DEC-1", "summary": "Parent owns Git mutation."}],
        "dependency_state": {"REQ-1": "ready", "REQ-2": "ready"},
        "facts": [{"id": "FACT-1", "summary": "The fixture is local-only."}],
        "graph": graph,
        "integration": {
            "base_branch": "main",
            "base_head": base_head,
            "base_tree_id": base_tree,
            "integration_head": base_head,
            "integration_tree_id": base_tree,
        },
        "prohibitions": ["push", "release", "deploy"],
        "requirements": [
            {**item, "coverage_state": "uncovered", "revision": 1}
            for item in requirements
        ],
        "risks": ["bounded local text"],
    }
    return {
        "init": {
            "context": context,
            "context_reason": "initial operator context",
            "context_revision_id": "operator-context-1",
            "envelope": envelope,
            "request_id": "operator-start-1",
            "task_dir": ".trellis/tasks/07-14-pilot-run",
        },
        "repo": repo,
    }


def create_single_child_fixture(
    root: Path, *, authorization_profile: str | None = "strict"
) -> dict[str, object]:
    fixture = create_fixture(
        root,
        authorization_profile=authorization_profile,
    )
    init = fixture["init"]
    envelope = init["envelope"]
    context = init["context"]
    envelope["requirements"] = envelope["requirements"][:1]
    envelope["initial_child_graph"] = envelope["initial_child_graph"][:1]
    envelope["default_parallel"] = 1
    envelope["max_parallel"] = 1
    envelope["worker_capacity"] = 1
    envelope["verification_policy"]["operator"]["children"] = {
        "child-a": envelope["verification_policy"]["operator"]["children"][
            "child-a"
        ]
    }
    context["requirements"] = context["requirements"][:1]
    context["graph"] = context["graph"][:1]
    context["dependency_state"] = {"REQ-1": "ready"}
    return fixture


def configure_task_b_fixture(fixture: dict[str, object]) -> dict[str, object]:
    init = fixture["init"]
    requirements = [
        {
            "acceptance": ["README contract is integrated"],
            "dependencies": [],
            "required": True,
            "requirement_id": "DOC-BF-001",
        },
        {
            "acceptance": ["AGENTS contract is integrated"],
            "dependencies": ["DOC-BF-001"],
            "required": True,
            "requirement_id": "DOC-BF-002",
        },
        {
            "acceptance": ["documentation contracts agree"],
            "dependencies": ["DOC-BF-001", "DOC-BF-002"],
            "required": True,
            "requirement_id": "DOC-BF-003",
        },
        {
            "acceptance": ["required review recovery is proven"],
            "dependencies": [],
            "required": True,
            "requirement_id": "DOC-BF-004",
        },
    ]
    graph = [
        {
            "child_id": "readme-contract",
            "depends_on": [],
            "requirements": ["DOC-BF-001", "DOC-BF-004"],
            "resources": ["work-slot"],
            "touches": ["README.md", "README-repair.md"],
        },
        {
            "child_id": "agents-maintenance-gate",
            "depends_on": ["readme-contract"],
            "requirements": ["DOC-BF-002", "DOC-BF-003"],
            "resources": ["work-slot"],
            "touches": ["AGENTS.md"],
        },
    ]
    envelope = init["envelope"]
    envelope["allowed_touches"] = ["README.md", "README-repair.md", "AGENTS.md"]
    envelope["default_parallel"] = 1
    envelope["initial_child_graph"] = copy.deepcopy(graph)
    envelope["max_parallel"] = 1
    envelope["requirements"] = copy.deepcopy(requirements)
    envelope["verification_policy"]["operator"]["children"] = {
        child_id: {
            "result_deadline": "2026-07-14T23:00:00Z",
            "tests": ["git diff --check"],
        }
        for child_id in ("readme-contract", "agents-maintenance-gate")
    }
    envelope["worker_capacity"] = 1
    context = init["context"]
    context["dependency_state"] = {
        "DOC-BF-001": "ready",
        "DOC-BF-002": "blocked",
        "DOC-BF-003": "blocked",
        "DOC-BF-004": "ready",
    }
    context["graph"] = copy.deepcopy(graph)
    context["requirements"] = [
        {**item, "coverage_state": "uncovered", "revision": 1}
        for item in requirements
    ]
    return fixture


def configure_guided_replacement_fixture(
    fixture: dict[str, object], *, branched: bool = False
) -> dict[str, object]:
    child_ids = ["runtime", "adapter", "qualification"]
    if branched:
        child_ids.append("passing-sibling")
    requirements = [
        {
            "acceptance": [f"{child_id} is integrated"],
            "dependencies": [],
            "required": True,
            "requirement_id": f"REQ-{child_id}",
        }
        for child_id in child_ids
    ]
    if branched:
        child_ids.append("z-coverage-sibling")
    graph = [
        {
            "child_id": "runtime",
            "depends_on": [],
            "requirements": ["REQ-runtime"],
            "resources": ["work-slot"],
            "touches": ["runtime.txt"],
        },
        {
            "child_id": "adapter",
            "depends_on": ["runtime"],
            "requirements": ["REQ-adapter"],
            "resources": ["work-slot"],
            "touches": ["adapter.txt"],
        },
        {
            "child_id": "qualification",
            "depends_on": ["adapter"],
            "requirements": ["REQ-qualification"],
            "resources": ["work-slot"],
            "touches": ["qualification.txt"],
        },
    ]
    if branched:
        graph.insert(
            2,
            {
                "child_id": "passing-sibling",
                "depends_on": ["runtime"],
                "requirements": ["REQ-passing-sibling"],
                "resources": ["work-slot"],
                "touches": ["passing-sibling.txt"],
            }
        )
        graph.append(
            {
                "child_id": "z-coverage-sibling",
                "depends_on": ["passing-sibling"],
                "requirements": ["REQ-adapter"],
                "resources": ["work-slot"],
                "touches": ["z-coverage-sibling.txt"],
            }
        )
    envelope = fixture["init"]["envelope"]
    envelope["allowed_touches"] = [f"{child_id}.txt" for child_id in child_ids]
    envelope["default_parallel"] = 1 if branched else 2
    envelope["initial_child_graph"] = copy.deepcopy(graph)
    envelope["max_parallel"] = 1 if branched else 2
    envelope["requirements"] = copy.deepcopy(requirements)
    envelope["verification_policy"]["operator"]["children"] = {
        child_id: {
            "result_deadline": "2026-07-14T23:00:00Z",
            "tests": ["git diff --check"],
        }
        for child_id in child_ids
    }
    envelope["worker_capacity"] = 1 if branched else 2
    context = fixture["init"]["context"]
    context["dependency_state"] = {
        item["requirement_id"]: "ready" for item in requirements
    }
    context["graph"] = copy.deepcopy(graph)
    context["requirements"] = [
        {**item, "coverage_state": "uncovered", "revision": 1}
        for item in requirements
    ]
    return fixture


def start_response(action: dict[str, object]) -> dict[str, object]:
    request = action["payload"]["request"]
    return {
        "action_digest": action["action_digest"],
        "action_id": action["action_id"],
        "direct_user_action": True,
        "request_digest": request["request_digest"],
        "request_id": request["request_id"],
        "response_at": "2026-07-14T20:00:00Z",
        "response_identity": "user:operator-start",
    }


def worker_result(entry: dict[str, object], relative: str, contents: str) -> dict[str, object]:
    worktree = Path(entry["worktree"])
    target = worktree / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    packet = entry["packet"]
    execution_base = packet.get("execution_base", packet["base"])
    observation = observe_worker_candidate(
        worktree, base_head=execution_base["head"]
    )
    requirement_id = packet["requirements"][0]["requirement_id"]
    return {
        "actual_touches": observation["actual_touches"],
        "artifacts": [
            {"digest": f"artifact:{entry['child_id']}:{path}", "path": path}
            for path in observation["actual_touches"]
        ],
        "base_head": observation["base_head"],
        "base_tree_id": observation["base_tree_id"],
        "child_id": entry["child_id"],
        "commands": [
            {
                "command": "git diff --check",
                "output_digest": f"output:{entry['child_id']}",
                "status": "passed",
            }
        ],
        "coverage": [requirement_id],
        "diff_identity": observation["diff_identity"],
        "findings": [],
        "packet_id": packet["packet_id"],
        "result_id": f"result-{entry['child_id']}",
        "result_tree_id": observation["tree_id"],
        "risks": [],
        **packet["identity"],
    }


def ingest_message(action: dict[str, object], message_type: str, payload: object) -> dict[str, object]:
    return {
        "action_digest": action["action_digest"],
        "action_id": action["action_id"],
        "message_type": message_type,
        "payload": payload,
    }


def review_payload(*, passed: bool) -> dict[str, object]:
    return {
        "advisory_findings": [],
        "dispositions": [],
        "required_findings": []
        if passed
        else [{"finding_id": "required-1", "summary": "repair required"}],
        "reviewer_identity": "model:codex",
        "verdict": "passed" if passed else "failed",
    }


def initialize_single_child(root: Path) -> tuple[dict[str, object], Path, dict[str, object]]:
    fixture = create_single_child_fixture(root)
    repo = fixture["repo"]
    start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
    respond_start(repo, RUN_ID, start_response(start))
    dispatch = advance_operator(repo, RUN_ID)["next_action"]
    return fixture, repo, dispatch


class LoopV1OrchestratorTests(unittest.TestCase):
    def test_consumed_zero_diff_guidance_reconciles_only_unstarted_replacement(
        self,
    ) -> None:
        source_child_id = "child-a-repair-1"
        replacement_child_id = "child-a-repair-2"
        problem_id = "problem-final-check"
        source_context = {
            "graph": [{"child_id": source_child_id}],
            "requirements": [{"coverage_state": "covered"}],
        }
        replacement_context = {
            "graph": [{"child_id": replacement_child_id}],
            "requirements": [{"coverage_state": "uncovered"}],
        }
        snapshot = {
            "tables": {
                "child_operations": [
                    {"child_id": source_child_id, "state": "integrated"},
                    {"child_id": replacement_child_id, "state": "dispatched"},
                ],
                "context_revisions": [
                    {
                        "context_json": json.dumps(source_context),
                        "digest": "source-context",
                        "previous_digest": None,
                        "reason": "integrated zero-diff child",
                        "sequence": 1,
                    },
                    {
                        "context_json": json.dumps(replacement_context),
                        "digest": "replacement-context",
                        "previous_digest": "source-context",
                        "reason": (
                            f"recovery replacement for {problem_id}: "
                            f"{source_child_id}"
                        ),
                        "sequence": 2,
                    },
                ],
                "operations": [
                    {
                        "kind": "reviewed_zero_diff_integration",
                        "outcome_json": json.dumps(
                            {
                                "child_id": source_child_id,
                                "problem_id": problem_id,
                                "recovery_operation_id": "recover-final-checks-1",
                            }
                        ),
                        "phase": "authority_committed",
                    }
                ],
            }
        }

        reconciliation = _consumed_zero_diff_reconciliation(snapshot)

        self.assertEqual(reconciliation["source_child_id"], source_child_id)
        self.assertEqual(
            reconciliation["replacement_child_id"], replacement_child_id
        )
        snapshot["tables"]["operations"].append(
            {
                "kind": "child_worktree_create",
                "outcome_json": json.dumps({"child_id": replacement_child_id}),
                "phase": "authority_committed",
            }
        )
        self.assertIsNone(_consumed_zero_diff_reconciliation(snapshot))

    def test_reviewed_zero_diff_routing_requires_exact_final_check_recovery(
        self,
    ) -> None:
        source_child_id = "child-a"
        child_id = "child-a-repair-1"
        failed_operation_id = "final-checks-failed"
        problem = {
            "artifact_ids": [source_child_id, failed_operation_id],
            "exhausted": False,
            "operation_phase": "final_integration_checks",
            "problem_id": "problem-1",
            "requirement_ids": ["REQ-1"],
            "result": "failed",
            "round": 1,
        }
        guidance = {
            "affected_child_ids": [source_child_id],
            "direct_user_action": True,
            "failed_operation_id": failed_operation_id,
            "operation_id": "recover-final-checks-1",
            "requirement_ids": ["REQ-1"],
        }
        snapshot = {
            "tables": {
                "operations": [
                    {
                        "kind": "problem_attempt",
                        "outcome_json": json.dumps(problem),
                        "phase": "authority_committed",
                    },
                    {
                        "kind": "final_check_recovery_guidance",
                        "outcome_json": json.dumps(guidance),
                        "phase": "authority_committed",
                    },
                ],
                "context_revisions": [
                    {
                        "context_json": json.dumps(
                            {"graph": [{"child_id": source_child_id}]}
                        ),
                        "digest": "source-context",
                        "previous_digest": None,
                        "reason": "initial context",
                    },
                    {
                        "context_json": json.dumps(
                            {"graph": [{"child_id": child_id}]}
                        ),
                        "digest": "replacement-context",
                        "previous_digest": "source-context",
                        "reason": (
                            "recovery replacement for problem-1: "
                            f"{source_child_id}"
                        ),
                    },
                ],
            }
        }
        review = {"actual_touches": [], "coverage": ["REQ-1"]}

        self.assertEqual(
            _reviewed_zero_diff_recovery(snapshot, child_id, review),
            (problem, guidance),
        )
        self.assertIsNone(
            _reviewed_zero_diff_recovery(
                snapshot, child_id, {**review, "actual_touches": ["README.md"]}
            )
        )
        snapshot["tables"]["operations"][1]["outcome_json"] = json.dumps(
            {**guidance, "direct_user_action": False}
        )
        self.assertIsNone(
            _reviewed_zero_diff_recovery(snapshot, child_id, review)
        )

    def test_module_cli_help_is_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "loop_v1.orchestrator", "--help"],
            cwd=SCRIPT_DIR.parent,
            env={**os.environ, "PYTHONPATH": str(SCRIPT_DIR)},
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("respond-final", result.stdout)
        self.assertIn("archive-pre-admission", result.stdout)
        self.assertIn("retire-task-evidence", result.stdout)
        self.assertNotIn("RuntimeWarning", result.stderr)

    def test_status_for_unknown_run_leaves_no_runtime_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with self.assertRaisesRegex(
                OrchestratorError, "ledger is not initialized"
            ):
                operator_status(repo, "unknown-run")
            self.assertFalse(ledger_path(repo, "unknown-run").parent.exists())

    def test_cli_rejects_machine_absolute_io_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            input_path = root / "outside-input.json"
            input_path.write_text("{}\n", encoding="utf-8")
            environment = {**os.environ, "PYTHONPATH": str(SCRIPT_DIR)}
            command = [
                sys.executable,
                "-m",
                "loop_v1.orchestrator",
                "--repo-root",
                str(repo),
                "--run-id",
                "unknown-run",
            ]

            invalid_input = subprocess.run(
                [*command, "init", "--input", str(input_path)],
                cwd=SCRIPT_DIR.parent,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(invalid_input.returncode, 1)
            self.assertIn(
                "input must be repository-relative",
                json.loads(invalid_input.stdout)["error"],
            )

            output_path = root / "outside-output.json"
            invalid_output = subprocess.run(
                [*command, "--output", str(output_path), "status"],
                cwd=SCRIPT_DIR.parent,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(invalid_output.returncode, 1)
            self.assertIn(
                "output must be repository-relative",
                json.loads(invalid_output.stdout)["error"],
            )
            self.assertFalse(output_path.exists())

            readme = repo / "README.md"
            readme.write_text("preserve me\n", encoding="utf-8")
            unsafe_output = subprocess.run(
                [*command, "--output", "README.md", "status"],
                cwd=SCRIPT_DIR.parent,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(unsafe_output.returncode, 1)
            self.assertIn(
                "ignored outputs directory",
                json.loads(unsafe_output.stdout)["error"],
            )
            self.assertEqual(readme.read_text(encoding="utf-8"), "preserve me\n")
            self.assertFalse(ledger_path(repo, "unknown-run").parent.exists())

    def test_two_child_operator_path_reaches_exact_final_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            initialized = initialize_operator(repo, RUN_ID, fixture["init"])
            start = initialized["next_action"]
            self.assertEqual(start["action_type"], "start_response")
            self.assertNotIn("fence_token", json.dumps(initialized))
            self.assertEqual(
                stat.S_IMODE(
                    (ledger_path(repo, RUN_ID).parent / "operator-state.json").stat().st_mode
                ),
                0o600,
            )
            output_path = ledger_path(repo, RUN_ID).parent / "outputs" / "status.json"
            cli_status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "loop_v1.orchestrator",
                    "--repo-root",
                    str(repo),
                    "--run-id",
                    RUN_ID,
                    "--output",
                    output_path.relative_to(repo).as_posix(),
                    "status",
                ],
                cwd=SCRIPT_DIR.parent,
                env={**os.environ, "PYTHONPATH": str(SCRIPT_DIR)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli_status.returncode, 0, cli_status.stderr)
            self.assertEqual(cli_status.stdout, "")
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["run_id"], RUN_ID
            )
            self.assertNotIn("fence_token", output_path.read_text(encoding="utf-8"))
            replay = initialize_operator(repo, RUN_ID, fixture["init"])
            self.assertEqual(replay["next_action"], start)

            respond_start(repo, RUN_ID, start_response(start))
            state_path = ledger_path(repo, RUN_ID).parent / "operator-state.json"
            state_path.unlink()
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(dispatch["action_type"], "dispatch_workers")
            self.assertEqual(advance_operator(repo, RUN_ID)["next_action"], dispatch)
            self.assertEqual(
                [item["child_id"] for item in dispatch["payload"]["children"]],
                ["child-a", "child-b"],
            )
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            first = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entries["child-b"], "tests/check.txt", "child-b\n"),
                ),
            )
            self.assertEqual(first["remaining_child_ids"], ["child-a"])
            second = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entries["child-a"], "src/app.txt", "child-a\n"),
                ),
            )
            self.assertEqual(second["remaining_child_ids"], [])

            for expected_child in ("child-a", "child-b"):
                review_action = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(review_action["action_type"], "precommit_review")
                self.assertEqual(review_action["payload"]["child_id"], expected_child)
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        review_action,
                        "precommit_review",
                        {
                            "advisory_findings": [],
                            "dispositions": [],
                            "required_findings": [],
                            "reviewer_identity": "model:codex",
                            "verdict": "passed",
                        },
                    ),
                )

            final_review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(final_review["action_type"], "final_review")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    final_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": final_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:fresh-final",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )
            pause_operator(
                repo,
                RUN_ID,
                {
                    "operation_id": "pause-after-final-verification",
                    "reason": "prove final verification epoch isolation",
                    "requested_by": "user:test",
                    "requires_human_resume": True,
                },
            )
            resume_operator(
                repo,
                RUN_ID,
                {
                    "authority_identity": "user:epoch-isolation",
                    "available_resources": ["work-slot"],
                    "direct_user_action": True,
                    "new_writer_id": "loop-v1-operator:epoch-isolation",
                    "operation_id": "resume-after-final-verification",
                    "tool_receipt": RECEIPT_ID,
                },
            )
            final_action = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(final_action["action_type"], "final_response")
            verifications = [
                row
                for row in ParentLedger(repo, RUN_ID).authority_snapshot()["tables"][
                    "operations"
                ]
                if row["kind"] == "final_integration_checks"
            ]
            self.assertEqual({row["epoch"] for row in verifications}, {1, 2})
            self.assertEqual(len({row["operation_id"] for row in verifications}), 2)
            state_path.unlink()
            self.assertEqual(
                advance_operator(repo, RUN_ID)["next_action"], final_action
            )
            request = final_action["payload"]["request"]
            respond_final(
                repo,
                RUN_ID,
                {
                    "action_digest": final_action["action_digest"],
                    "action_id": final_action["action_id"],
                    "direct_user_action": True,
                    "request_digest": request["request_digest"],
                    "request_id": request["request"]["request_id"],
                    "response_at": "2026-07-14T22:00:00Z",
                    "response_identity": "user:operator-final",
                },
            )
            archived = advance_operator(repo, RUN_ID)
            self.assertEqual(archived["parent_status"], "archived")
            self.assertIsNone(archived["next_action"])
            self.assertEqual(git(repo, "show", "HEAD:src/app.txt"), "child-a")
            self.assertEqual(git(repo, "show", "HEAD:tests/check.txt"), "child-b")

    def test_default_single_user_run_uses_one_start_intent_and_auto_finalizes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_single_child_fixture(
                Path(tmp),
                authorization_profile=None,
            )
            repo = fixture["repo"]
            initialized = initialize_operator(repo, RUN_ID, fixture["init"])
            self.assertEqual(initialized["parent_status"], "authorized")
            self.assertIsNone(initialized["next_action"])
            (ledger_path(repo, RUN_ID).parent / "operator-state.json").unlink()

            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entry = dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entry, "src/app.txt", "single user\n"),
                ),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    review,
                    "precommit_review",
                    review_payload(passed=True),
                ),
            )
            final_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    final_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": final_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:single-user-final",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )

            archived = advance_operator(repo, RUN_ID)
            self.assertEqual(archived["parent_status"], "archived")
            self.assertIsNone(archived["next_action"])
            connection = sqlite3.connect(
                ledger_path(repo, RUN_ID).resolve().as_uri() + "?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            try:
                gates = connection.execute(
                    "SELECT kind, response_identity FROM gate_requests ORDER BY kind"
                ).fetchall()
                operations = connection.execute(
                    "SELECT kind, outcome_json FROM operations "
                    "WHERE phase = 'authority_committed'"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                [(row["kind"], row["response_identity"] is not None) for row in gates],
                [("final", False), ("start", True)],
            )
            direct_user_operations = [
                row["kind"]
                for row in operations
                if json.loads(row["outcome_json"]).get("direct_user_action") is True
            ]
            self.assertEqual(direct_user_operations, ["start_request_approved"])
            self.assertIn(
                "local_command_authority",
                [row["kind"] for row in operations],
            )
            local_authority = next(
                json.loads(row["outcome_json"])
                for row in operations
                if row["kind"] == "local_command_authority"
            )
            self.assertEqual(
                local_authority["authorized_local_actions"],
                [
                    "local_child_commit",
                    "local_final_merge",
                    "local_integration",
                    "local_repair",
                    "local_review",
                    "local_worker",
                ],
            )
            self.assertTrue(
                {
                    "cancel",
                    "cross_repository_deploy",
                    "destructive_cleanup",
                    "force_ref",
                    "publish",
                    "qualification_activation",
                    "release",
                    "tag",
                }.isdisjoint(local_authority["authorized_local_actions"])
            )

    def test_single_user_start_does_not_authorize_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_single_child_fixture(
                Path(tmp),
                authorization_profile=None,
            )
            repo = fixture["repo"]
            initialize_operator(repo, RUN_ID, fixture["init"])
            status = operator_status(repo, RUN_ID)
            action = status["next_action"]

            with self.assertRaisesRegex(ControlError, "direct human action"):
                cancel_operator(
                    repo,
                    RUN_ID,
                    {
                        "action_digest": (
                            action["action_digest"] if action is not None else None
                        ),
                        "action_id": action["action_id"] if action is not None else None,
                        "actor": "automation",
                        "direct_user_action": False,
                        "expected_authority_digest": status["authority_digest"],
                        "operation_id": "single-user-cancel",
                        "reason": "must remain explicit",
                        "requested_at": "2026-07-25T22:00:00Z",
                        "superseded_by": None,
                    },
                )
            self.assertEqual(operator_status(repo, RUN_ID)["parent_status"], "authorized")

    def test_direct_revoke_stops_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]

            revoked = revoke_operator(
                repo,
                RUN_ID,
                {
                    "actor": "user:test-revoke",
                    "direct_user_action": True,
                    "execution_receipt": None,
                    "operation_id": "operator-test-revoke",
                    "reason": "receipt withdrawn",
                    "revoked_at": "2026-07-26T19:00:00Z",
                },
            )

            self.assertEqual(dispatch["action_type"], "dispatch_workers")
            self.assertEqual(revoked["parent_status"], "revoked")
            self.assertIsNone(revoked["next_action"])
            self.assertEqual(revoked["revocation"]["status"], "revoked")
            self.assertEqual(advance_operator(repo, RUN_ID)["parent_status"], "revoked")

    def test_unknown_authorization_profile_fails_before_ledger_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_single_child_fixture(
                Path(tmp),
                authorization_profile="shared",
            )
            repo = fixture["repo"]

            with self.assertRaisesRegex(
                OrchestratorError,
                "authorization_profile",
            ):
                initialize_operator(repo, RUN_ID, fixture["init"])
            self.assertFalse(ledger_path(repo, RUN_ID).exists())

    def test_worker_validation_failure_dispatches_bounded_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            _, repo, dispatch = initialize_single_child(Path(tmp))
            entry = dispatch["payload"]["children"][0]
            failed = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entry, "src/app.txt", "trailing space \n"),
                ),
            )

            self.assertEqual(failed["remaining_child_ids"], [])
            self.assertEqual(failed["recovery"]["status"], "repairing")
            with mock.patch(
                "loop_v1.orchestrator.create_child_worktree",
                side_effect=DirtOverlapError("simulated pre-worktree failure"),
            ):
                with self.assertRaisesRegex(
                    DirtOverlapError, "simulated pre-worktree failure"
                ):
                    advance_operator(repo, RUN_ID)
            replacement = advance_operator(repo, RUN_ID)
            action = replacement["next_action"]
            self.assertEqual(replacement["parent_status"], "authorized")
            self.assertEqual(action["action_type"], "dispatch_workers")
            repair = action["payload"]["children"][0]
            self.assertEqual(repair["child_id"], "child-a-repair-1")
            self.assertEqual(repair["packet"]["attempt"], 2)
            self.assertEqual(repair["packet"]["round"], 2)
            self.assertEqual(replacement["child_states"]["child-a"], "invalidated")

    def test_worker_reported_test_failure_dispatches_bounded_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            _, repo, dispatch = initialize_single_child(Path(tmp))
            entry = dispatch["payload"]["children"][0]
            authority_before = operator_status(repo, RUN_ID)["authority_digest"]
            with self.assertRaises(ContextError):
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        dispatch, "worker_result", {"child_id": "child-a"}
                    ),
                )
            unchanged = operator_status(repo, RUN_ID)
            self.assertEqual(unchanged["authority_digest"], authority_before)
            self.assertEqual(unchanged["next_action"], dispatch)
            self.assertEqual(unchanged["recovery"]["status"], "clear")
            payload = worker_result(entry, "src/app.txt", "worker failed\n")
            payload["commands"][0]["status"] = "skipped"

            failed = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", payload),
            )
            replacement = advance_operator(repo, RUN_ID)

            self.assertEqual(failed["remaining_child_ids"], [])
            self.assertEqual(failed["recovery"]["status"], "repairing")
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0]["child_id"],
                "child-a-repair-1",
            )
            recovery_context = replacement["next_action"]["payload"]["children"][0][
                "recovery_context"
            ][0]
            self.assertEqual(recovery_context["repair_round"], 1)
            self.assertEqual(recovery_context["required_findings"], [])
            self.assertEqual(replacement["child_states"]["child-a"], "invalidated")

    def test_guided_replacement_reopens_exact_path_closed_prerequisite_slice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_guided_replacement_fixture(create_fixture(Path(tmp)))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))

            for child_id in ("runtime", "adapter"):
                dispatch = advance_operator(repo, RUN_ID)["next_action"]
                entry = dispatch["payload"]["children"][0]
                self.assertEqual(entry["child_id"], child_id)
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        dispatch,
                        "worker_result",
                        worker_result(entry, f"{child_id}.txt", f"{child_id}\n"),
                    ),
                )
                review = advance_operator(repo, RUN_ID)["next_action"]
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        review, "precommit_review", review_payload(passed=True)
                    ),
                )

            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entry = dispatch["payload"]["children"][0]
            _, ledger, lease = _load_runtime(repo, RUN_ID)
            _record_recovery_observation(
                ledger,
                lease,
                operation_phase="worker_result",
                root_condition="worker_required_test_failed",
                requirement_ids=["REQ-qualification"],
                affected_child_ids=["qualification"],
                artifact_ids=["prior-qualification-observation"],
                graph_digest=_current_graph_digest(ledger.authority_snapshot()),
                problem_round=0,
                recovery_generation=1,
            )
            failed_result = worker_result(
                entry, "qualification.txt", "qualification failed\n"
            )
            failed_result["commands"][0]["status"] = "failed"
            failed = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed_result),
            )
            guidance_input = {
                **failed["replacement_guidance"],
                "operation_id": "guide-runtime-adapter",
                "prerequisite_child_ids": ["adapter", "runtime"],
            }

            guided = guide_replacement(repo, RUN_ID, guidance_input)
            replay = guide_replacement(repo, RUN_ID, guidance_input)
            with self.assertRaisesRegex(
                OperationConflict, "already has accepted guidance"
            ):
                guide_replacement(
                    repo,
                    RUN_ID,
                    {**guidance_input, "operation_id": "second-guidance"},
                )
            changed = copy.deepcopy(guidance_input)
            changed["prerequisite_child_ids"] = ["runtime"]
            with self.assertRaises(OperationConflict):
                guide_replacement(repo, RUN_ID, changed)
            replacement = advance_operator(repo, RUN_ID)
            replay_after_replacement = guide_replacement(
                repo, RUN_ID, guidance_input
            )

            self.assertEqual(
                guided["replacement_guidance"]["prerequisite_child_ids"],
                ["runtime", "adapter"],
            )
            self.assertEqual(
                guided["replacement_guidance"]["expanded_requirement_ids"],
                ["REQ-adapter", "REQ-qualification", "REQ-runtime"],
            )
            self.assertEqual(replay["replacement_guidance"], guided["replacement_guidance"])
            self.assertEqual(
                replay_after_replacement["replacement_guidance"],
                guided["replacement_guidance"],
            )
            self.assertEqual(
                replacement["recovery"]["replacement_child_ids"],
                [
                    "adapter-repair-1",
                    "qualification-repair-1",
                    "runtime-repair-1",
                ],
            )
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0]["child_id"],
                "runtime-repair-1",
            )
            with self.assertRaisesRegex(
                OrchestratorError, "quiescent source dispatch"
            ):
                guide_replacement(
                    repo,
                    RUN_ID,
                    {
                        **guidance_input,
                        "operation_id": "late-new-guidance",
                    },
                )
            repair_entry = replacement["next_action"]["payload"]["children"][0]
            repair_result = worker_result(
                repair_entry, "runtime.txt", "runtime repair failed\n"
            )
            repair_result["commands"][0]["status"] = "failed"
            repair_failure = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    replacement["next_action"], "worker_result", repair_result
                ),
            )
            self.assertEqual(
                repair_failure["recovery_failure"]["problem_id"],
                failed["recovery_failure"]["problem_id"],
            )
            self.assertEqual(repair_failure["recovery_failure"]["round"], 1)

    def test_guidance_is_quiescent_and_byte_stable_after_failure_first_wave(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            self.assertEqual(set(entries), {"child-a", "child-b"})
            failed_result = worker_result(
                entries["child-a"], "src/app.txt", "child-a failed\n"
            )
            failed_result["commands"][0]["status"] = "failed"
            failure_first = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed_result),
            )
            second_result = copy.deepcopy(failed_result)
            second_result["result_id"] = "child-a-second-observation"
            second_failure = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", second_result),
            )
            success_last = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(
                        entries["child-b"], "tests/app.txt", "child-b\n"
                    ),
                ),
            )

            self.assertNotIn("replacement_guidance", failure_first)
            self.assertNotIn("replacement_guidance", second_failure)
            self.assertEqual(success_last["remaining_child_ids"], [])
            guidance = success_last["replacement_guidance"]
            observations = [
                item
                for item in _recovery_failure_observations(
                    ParentLedger(repo, RUN_ID).authority_snapshot()
                )
                if item.get("operation_phase") == "worker_result"
                and item.get("root_condition") == "worker_required_test_failed"
                and item.get("affected_child_ids") == ["child-a"]
            ]
            ordered = _canonical_recovery_observations(observations)
            reversed_ordered = _canonical_recovery_observations(
                list(reversed(observations))
            )
            self.assertEqual(
                ordered,
                reversed_ordered,
            )
            expected_digest = sha256(
                json.dumps(
                    ordered,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(
                guidance["problem_attempt_evidence_digest"], expected_digest
            )

    def test_guided_replacement_rejects_missing_path_and_passing_branch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_guided_replacement_fixture(
                create_fixture(Path(tmp)), branched=True
            )
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))

            runtime_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            runtime_entry = runtime_dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    runtime_dispatch,
                    "worker_result",
                    worker_result(runtime_entry, "runtime.txt", "runtime\n"),
                ),
            )
            runtime_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    runtime_review, "precommit_review", review_payload(passed=True)
                ),
            )

            for child_id in ("adapter", "passing-sibling"):
                child_dispatch = advance_operator(repo, RUN_ID)["next_action"]
                child_entry = child_dispatch["payload"]["children"][0]
                self.assertEqual(child_entry["child_id"], child_id)
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        child_dispatch,
                        "worker_result",
                        worker_result(
                            child_entry, f"{child_id}.txt", f"{child_id}\n"
                        ),
                    ),
                )
                review = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(review["payload"]["child_id"], child_id)
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        review, "precommit_review", review_payload(passed=True)
                    ),
                )

            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entry = dispatch["payload"]["children"][0]
            failed_result = worker_result(
                entry, "qualification.txt", "qualification failed\n"
            )
            failed_result["commands"][0]["status"] = "failed"
            failed = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed_result),
            )
            authority_before = failed["authority_digest"]
            for operation_id, prerequisites, error in (
                (
                    "guide-empty",
                    [],
                    "must not be empty",
                ),
                (
                    "guide-duplicate",
                    ["adapter", "adapter"],
                    "must not contain duplicates",
                ),
                (
                    "guide-sibling",
                    ["passing-sibling"],
                    "must be transitive ancestors",
                ),
                (
                    "guide-missing-adapter",
                    ["runtime"],
                    "descendant outside the slice",
                ),
                (
                    "guide-passing-branch",
                    ["runtime", "adapter"],
                    "descendant outside the slice",
                ),
                (
                    "guide-overlap",
                    ["adapter"],
                    "coverage overlaps an unselected child",
                ),
            ):
                with self.subTest(operation_id=operation_id), self.assertRaisesRegex(
                    (ContextError, RecoveryError), error
                ):
                    guide_replacement(
                        repo,
                        RUN_ID,
                        {
                            **failed["replacement_guidance"],
                            "operation_id": operation_id,
                            "prerequisite_child_ids": prerequisites,
                        },
                    )
                status = operator_status(repo, RUN_ID)
                self.assertEqual(status["authority_digest"], authority_before)
                self.assertEqual(
                    status["active_child_states"]["passing-sibling"], "integrated"
                )

    def test_rejected_worker_result_replay_is_idempotent_and_identity_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entry = next(
                item
                for item in dispatch["payload"]["children"]
                if item["child_id"] == "child-a"
            )
            payload = worker_result(entry, "src/app.txt", "failed\n")
            payload["commands"][0]["status"] = "failed"

            first = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", payload),
            )
            replay = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", payload),
            )
            new_result = copy.deepcopy(payload)
            new_result["result_id"] = "another-result-child-a"
            new_result["commands"][0]["output_digest"] = "another-failed-output"
            same_attempt = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", new_result),
            )
            changed = copy.deepcopy(payload)
            changed["commands"][0]["output_digest"] = "different-output"

            self.assertEqual(first["recovery_failure"]["round"], 0)
            self.assertEqual(replay["recovery_failure"]["round"], 0)
            self.assertEqual(same_attempt["recovery_failure"]["round"], 0)
            with self.assertRaises(OperationConflict):
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(dispatch, "worker_result", changed),
                )
            self.assertEqual(
                operator_status(repo, RUN_ID)["recovery"]["active_problems"][0][
                    "round"
                ],
                0,
            )

    def test_worker_result_identity_cannot_impersonate_failed_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            failed = worker_result(entries["child-a"], "src/app.txt", "failed\n")
            failed["result_id"] = "child-b"
            failed["commands"][0]["status"] = "failed"

            result = ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed),
            )

            self.assertEqual(result["remaining_child_ids"], ["child-b"])

    def test_same_wave_shared_coverage_failures_use_one_problem_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            init = fixture["init"]
            init["envelope"]["requirements"] = init["envelope"]["requirements"][:1]
            init["context"]["requirements"] = init["context"]["requirements"][:1]
            init["context"]["dependency_state"] = {"REQ-1": "ready"}
            init["envelope"]["initial_child_graph"][1]["requirements"] = ["REQ-1"]
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, init)["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            results = {}
            for child_id, path in (
                ("child-a", "src/app.txt"),
                ("child-b", "tests/check.txt"),
            ):
                payload = worker_result(entries[child_id], path, f"{child_id} failed\n")
                payload["commands"][0]["status"] = "failed"
                results[child_id] = ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(dispatch, "worker_result", payload),
                )

            self.assertEqual(results["child-a"]["recovery_failure"]["round"], 0)
            self.assertEqual(results["child-b"]["recovery_failure"]["round"], 0)
            self.assertEqual(results["child-b"]["remaining_child_ids"], [])
            replacement = advance_operator(repo, RUN_ID)
            self.assertEqual(
                sorted(
                    item["child_id"]
                    for item in replacement["next_action"]["payload"]["children"]
                ),
                ["child-a-repair-1", "child-b-repair-1"],
            )
            self.assertEqual(
                replacement["recovery"]["active_problems"][0]["round"], 0
            )

    def test_shared_coverage_mixed_worker_boundaries_repair_exact_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            init = fixture["init"]
            init["envelope"]["requirements"] = init["envelope"]["requirements"][:1]
            init["context"]["requirements"] = init["context"]["requirements"][:1]
            init["context"]["dependency_state"] = {"REQ-1": "ready"}
            init["envelope"]["initial_child_graph"][1]["requirements"] = ["REQ-1"]
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, init)["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            failed_result = worker_result(
                entries["child-a"], "src/app.txt", "worker reported failure\n"
            )
            failed_result["commands"][0]["status"] = "failed"
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed_result),
            )
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(
                        entries["child-b"], "tests/check.txt", "trailing space \n"
                    ),
                ),
            )

            first_replacement = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(
                sorted(
                    item["child_id"]
                    for item in first_replacement["payload"]["children"]
                ),
                ["child-a-repair-1", "child-b-repair-1"],
            )
            self.assertEqual(
                {
                    item["child_id"]: {
                        (context["operation_phase"], context["root_condition"])
                        for context in item["recovery_context"]
                    }
                    for item in first_replacement["payload"]["children"]
                },
                {
                    "child-a-repair-1": {
                        ("worker_result", "worker_required_test_failed"),
                        (
                            "worker_validation",
                            "worker_candidate_validation_failed",
                        ),
                    },
                    "child-b-repair-1": {
                        ("worker_result", "worker_required_test_failed"),
                        (
                            "worker_validation",
                            "worker_candidate_validation_failed",
                        ),
                    },
                },
            )
            self.assertEqual(
                {
                    (
                        item["operation_phase"],
                        item["root_condition"],
                        item["round"],
                    )
                    for item in operator_status(repo, RUN_ID)["recovery"][
                        "active_problems"
                    ]
                },
                {
                    ("worker_result", "worker_required_test_failed", 0),
                    (
                        "worker_validation",
                        "worker_candidate_validation_failed",
                        0,
                    ),
                },
            )

    def test_required_precommit_finding_dispatches_replacement_without_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            _, repo, dispatch = initialize_single_child(Path(tmp))
            entry = dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entry, "src/app.txt", "candidate\n"),
                ),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(review, "precommit_review", review_payload(passed=False)),
            )

            with mock.patch(
                "loop_v1.orchestrator._prepare_recovery_replacement",
                side_effect=RuntimeError("crash after problem evidence"),
            ), self.assertRaisesRegex(RuntimeError, "crash after problem evidence"):
                advance_operator(repo, RUN_ID)
            self.assertEqual(
                operator_status(repo, RUN_ID)["recovery"]["active_problems"][0][
                    "round"
                ],
                0,
            )

            replacement = advance_operator(repo, RUN_ID)

            self.assertEqual(replacement["parent_status"], "authorized")
            self.assertEqual(
                replacement["next_action"]["action_type"], "dispatch_workers"
            )
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0]["child_id"],
                "child-a-repair-1",
            )
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0][
                    "recovery_context"
                ][0]["required_findings"][0]["summary"],
                "repair required",
            )
            self.assertEqual(replacement["recovery"]["status"], "repairing")

    def test_task_b_replacement_unblocks_same_child_requirement_dependency(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_task_b_fixture(create_fixture(Path(tmp)))
            init = fixture["init"]

            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, init)["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(
                [item["child_id"] for item in dispatch["payload"]["children"]],
                ["readme-contract"],
            )
            candidate_entry = dispatch["payload"]["children"][0]
            candidate_result = worker_result(
                candidate_entry, "README.md", "candidate\n"
            )
            candidate_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", candidate_result),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(review["payload"]["child_id"], "readme-contract")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(review, "precommit_review", review_payload(passed=False)),
            )

            replacement = advance_operator(repo, RUN_ID)
            replacement_action = replacement["next_action"]
            self.assertEqual(replacement["parent_status"], "authorized")
            self.assertEqual(replacement_action["action_type"], "dispatch_workers")
            self.assertEqual(
                [
                    item["child_id"]
                    for item in replacement_action["payload"]["children"]
                ],
                ["readme-contract-repair-1"],
            )
            replacement_entry = replacement_action["payload"]["children"][0]
            replacement_result = worker_result(
                replacement_entry, "README.md", "repaired\n"
            )
            replacement_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    replacement_action, "worker_result", replacement_result
                ),
            )
            replacement_review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(
                replacement_review["payload"]["child_id"],
                "readme-contract-repair-1",
            )
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    replacement_review,
                    "precommit_review",
                    review_payload(passed=True),
                ),
            )

            next_step = advance_operator(repo, RUN_ID)
            next_action = next_step["next_action"]
            self.assertEqual(next_step["parent_status"], "authorized")
            self.assertEqual(next_action["action_type"], "dispatch_workers")
            self.assertEqual(
                [item["child_id"] for item in next_action["payload"]["children"]],
                ["agents-maintenance-gate"],
            )
            self.assertEqual(
                [
                    item["requirement_id"]
                    for item in next_action["payload"]["children"][0]["packet"][
                        "requirements"
                    ]
                ],
                ["DOC-BF-002", "DOC-BF-003"],
            )
            self.assertEqual(
                next_step["child_states"]["readme-contract-repair-1"],
                "integrated",
            )

    def test_final_review_subset_replaces_complete_multi_requirement_child(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_task_b_fixture(create_fixture(Path(tmp)))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))

            readme_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            readme_entry = readme_dispatch["payload"]["children"][0]
            readme_result = worker_result(
                readme_entry, "README.md", "readme contract\n"
            )
            readme_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(readme_dispatch, "worker_result", readme_result),
            )
            readme_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    readme_review, "precommit_review", review_payload(passed=True)
                ),
            )

            agents_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            agents_entry = agents_dispatch["payload"]["children"][0]
            self.assertEqual(agents_entry["child_id"], "agents-maintenance-gate")
            agents_result = worker_result(
                agents_entry, "AGENTS.md", "agents maintenance gate\n"
            )
            agents_result["coverage"] = ["DOC-BF-002", "DOC-BF-003"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(agents_dispatch, "worker_result", agents_result),
            )
            agents_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    agents_review, "precommit_review", review_payload(passed=True)
                ),
            )

            final_review = advance_operator(repo, RUN_ID)["next_action"]
            required_findings = [
                {
                    "finding_id": "final-doc-bf-004",
                    "summary": "repair DOC-BF-004 without broadening reviewer scope",
                }
            ]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    final_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": ["DOC-BF-004"],
                        "dispositions": [],
                        "fresh_context_receipt": final_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": required_findings,
                        "reviewer_identity": "model:codex:task-b-final",
                        "specialist_results": [],
                        "verdict": "failed",
                    },
                ),
            )

            replacement = advance_operator(repo, RUN_ID)
            repair_entry = replacement["next_action"]["payload"]["children"][0]
            self.assertEqual(repair_entry["child_id"], "readme-contract-repair-1")
            self.assertEqual(
                [
                    item["requirement_id"]
                    for item in repair_entry["packet"]["requirements"]
                ],
                ["DOC-BF-001", "DOC-BF-004"],
            )
            self.assertEqual(
                replacement["active_child_states"]["agents-maintenance-gate"],
                "integrated",
            )
            self.assertNotIn("readme-contract", replacement["active_child_states"])
            self.assertEqual(
                repair_entry["recovery_context"][0]["required_findings"],
                required_findings,
            )

            repair_result = worker_result(
                repair_entry, "README-repair.md", "repaired readme contract\n"
            )
            repair_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    replacement["next_action"], "worker_result", repair_result
                ),
            )
            repair_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    repair_review, "precommit_review", review_payload(passed=True)
                ),
            )

            fresh_final = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(
                fresh_final["action_type"],
                "final_review",
                fresh_final.get("payload", {}).get("children"),
            )
            self.assertNotEqual(fresh_final["action_id"], final_review["action_id"])
            self.assertNotEqual(
                fresh_final["payload"]["integration"]["integration_head"],
                final_review["payload"]["integration"]["integration_head"],
            )
            self.assertEqual(fresh_final["payload"]["recovery"]["status"], "repairing")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    fresh_final,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": fresh_final["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:task-b-final-pass",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )

            final_response = advance_operator(repo, RUN_ID)
            self.assertEqual(
                final_response["next_action"]["action_type"], "final_response"
            )
            self.assertEqual(final_response["recovery"]["status"], "clear")

    def test_review_recovery_preserves_passing_sibling_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            for child_id, path in (
                ("child-a", "src/app.txt"),
                ("child-b", "tests/check.txt"),
            ):
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        dispatch,
                        "worker_result",
                        worker_result(entries[child_id], path, f"{child_id}\n"),
                    ),
                )
            blocked_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    blocked_review, "precommit_review", review_payload(passed=False)
                ),
            )
            sibling_review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(sibling_review["payload"]["child_id"], "child-b")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    sibling_review, "precommit_review", review_payload(passed=True)
                ),
            )

            replacement = advance_operator(repo, RUN_ID)

            self.assertEqual(replacement["child_states"]["child-b"], "integrated")
            self.assertEqual(replacement["active_child_states"]["child-b"], "integrated")
            self.assertEqual(replacement["child_states"]["child-a"], "invalidated")
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0]["child_id"],
                "child-a-repair-1",
            )

    def test_final_integration_checks_wait_for_complete_graph_and_run_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_task_b_fixture(create_fixture(Path(tmp)))
            final_command = "test -f README.md && test -f AGENTS.md"
            fixture["init"]["envelope"]["verification_policy"]["operator"][
                "final_integration_checks"
            ] = [final_command]
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))

            with mock.patch(
                "loop_v1.integration._run_parent_check",
                wraps=sys.modules["loop_v1.integration"]._run_parent_check,
            ) as run_check:
                readme_dispatch = advance_operator(repo, RUN_ID)["next_action"]
                readme_entry = readme_dispatch["payload"]["children"][0]
                readme_result = worker_result(
                    readme_entry, "README.md", "readme contract\n"
                )
                readme_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        readme_dispatch, "worker_result", readme_result
                    ),
                )
                readme_review = advance_operator(repo, RUN_ID)["next_action"]
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        readme_review,
                        "precommit_review",
                        review_payload(passed=True),
                    ),
                )

                agents_dispatch = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(
                    agents_dispatch["payload"]["children"][0]["child_id"],
                    "agents-maintenance-gate",
                )
                self.assertEqual(
                    [
                        call
                        for call in run_check.call_args_list
                        if call.args[1] == final_command
                    ],
                    [],
                )
                first_snapshot = ParentLedger(repo, RUN_ID).authority_snapshot()
                self.assertFalse(
                    any(
                        row["kind"] == "final_integration_checks"
                        for row in first_snapshot["tables"]["operations"]
                    )
                )

                agents_entry = agents_dispatch["payload"]["children"][0]
                agents_result = worker_result(
                    agents_entry, "AGENTS.md", "agents maintenance gate\n"
                )
                agents_result["coverage"] = ["DOC-BF-002", "DOC-BF-003"]
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        agents_dispatch, "worker_result", agents_result
                    ),
                )
                agents_review = advance_operator(repo, RUN_ID)["next_action"]
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        agents_review,
                        "precommit_review",
                        review_payload(passed=True),
                    ),
                )

                final_review = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(final_review["action_type"], "final_review")
                self.assertEqual(
                    len(
                        [
                            call
                            for call in run_check.call_args_list
                            if call.args[1] == final_command
                        ]
                    ),
                    1,
                )
                snapshot = ParentLedger(repo, RUN_ID).authority_snapshot()
                operations = [
                    row
                    for row in snapshot["tables"]["operations"]
                    if row["kind"] == "final_integration_checks"
                ]
                self.assertEqual(len(operations), 1)
                self.assertEqual(operations[0]["phase"], "authority_committed")
                outcome = json.loads(operations[0]["outcome_json"])
                current_row = max(
                    snapshot["tables"]["context_revisions"],
                    key=lambda row: int(row["sequence"]),
                )
                context = json.loads(current_row["context_json"])
                self.assertEqual(outcome["status"], "passed")
                self.assertEqual(outcome["checks"], [final_command])
                self.assertEqual(outcome["checks_result"][0]["command"], final_command)
                self.assertEqual(
                    outcome["integration_head"],
                    context["integration"]["integration_head"],
                )
                self.assertEqual(
                    outcome["integration_tree_id"],
                    context["integration"]["integration_tree_id"],
                )
                self.assertEqual(outcome["freshness"]["context_digest"], current_row["digest"])
                self.assertEqual(
                    git(repo, "rev-parse", "--verify", outcome["integration_ref"]),
                    outcome["integration_head"],
                )
                self.assertTrue(outcome["effect_boundary_clean"])
                self.assertTrue(outcome["freshness_matches"])
                self.assertTrue(outcome["identity_matches"])

                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        final_review,
                        "final_review",
                        {
                            "affected_requirement_ids": [],
                            "advisory_findings": [],
                            "dispositions": [],
                            "fresh_context_receipt": final_review["payload"][
                                "fresh_context_receipt"
                            ],
                            "required_findings": [],
                            "reviewer_identity": "model:codex:fresh-final",
                            "specialist_results": [],
                            "verdict": "passed",
                        },
                    ),
                )
                final_response = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(final_response["action_type"], "final_response")
                self.assertEqual(
                    len(
                        [
                            call
                            for call in run_check.call_args_list
                            if call.args[1] == final_command
                        ]
                    ),
                    1,
                )
                replay_snapshot = ParentLedger(repo, RUN_ID).authority_snapshot()
                self.assertEqual(
                    len(
                        [
                            row
                            for row in replay_snapshot["tables"]["operations"]
                            if row["kind"] == "final_integration_checks"
                        ]
                    ),
                    1,
                )

    def test_failed_final_checks_require_explicit_slice_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = configure_task_b_fixture(create_fixture(Path(tmp)))
            fixture["init"]["envelope"]["verification_policy"]["operator"][
                "final_integration_checks"
            ] = ["test -f never-present"]
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))

            readme_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            readme_entry = readme_dispatch["payload"]["children"][0]
            readme_result = worker_result(
                readme_entry, "README.md", "readme contract\n"
            )
            readme_result["coverage"] = ["DOC-BF-001", "DOC-BF-004"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(readme_dispatch, "worker_result", readme_result),
            )
            readme_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    readme_review, "precommit_review", review_payload(passed=True)
                ),
            )

            agents_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            agents_entry = agents_dispatch["payload"]["children"][0]
            agents_result = worker_result(
                agents_entry, "AGENTS.md", "agents maintenance gate\n"
            )
            agents_result["coverage"] = ["DOC-BF-002", "DOC-BF-003"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(agents_dispatch, "worker_result", agents_result),
            )
            agents_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    agents_review, "precommit_review", review_payload(passed=True)
                ),
            )

            intervention = advance_operator(repo, RUN_ID)
            self.assertEqual(intervention["parent_status"], "paused")
            self.assertEqual(
                intervention["next_action"]["action_type"], "human_intervention"
            )
            self.assertEqual(
                intervention["next_action"]["payload"]["reason"],
                "ambiguous_product_semantics",
            )
            self.assertEqual(
                intervention["active_child_states"],
                {
                    "agents-maintenance-gate": "integrated",
                    "readme-contract": "integrated",
                },
            )

            snapshot = ParentLedger(repo, RUN_ID).authority_snapshot()
            operations = snapshot["tables"]["operations"]
            final_checks = [
                row for row in operations if row["kind"] == "final_integration_checks"
            ]
            self.assertEqual(len(final_checks), 1)
            self.assertEqual(final_checks[0]["phase"], "authority_committed")
            outcome = json.loads(final_checks[0]["outcome_json"])
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(
                outcome["root_condition"], "final_integration_check_failed"
            )
            self.assertEqual(
                git(repo, "rev-parse", "--verify", outcome["integration_ref"]),
                outcome["integration_head"],
            )
            current_row = max(
                snapshot["tables"]["context_revisions"],
                key=lambda row: int(row["sequence"]),
            )
            self.assertEqual(
                outcome["freshness"]["context_digest"], current_row["digest"]
            )
            self.assertFalse(
                any(
                    row["kind"]
                    in {
                        "final_integration_review",
                        "problem_attempt",
                        "recovery_failure_observed",
                    }
                    for row in operations
                )
            )
            self.assertFalse(
                any(
                    "-repair-" in str(row["child_id"])
                    for row in snapshot["tables"]["child_operations"]
                )
            )
            (repo / "operator-note.txt").write_text(
                "non-overlapping resume dirt\n", encoding="utf-8"
            )
            resume_operator(
                repo,
                RUN_ID,
                {
                    "authority_identity": "user:final-check-recovery",
                    "available_resources": ["work-slot"],
                    "direct_user_action": True,
                    "new_writer_id": "loop-v1-operator:final-check-recovery",
                    "operation_id": "resume-final-check-recovery",
                    "tool_receipt": RECEIPT_ID,
                },
            )
            guidance = {
                "action": "repair the frozen final-check environment contract",
                "affected_child_ids": ["readme-contract"],
                "diagnosis": "the selected child does not locate its pinned runtime",
                "direct_user_action": True,
                "failed_operation_id": final_checks[0]["operation_id"],
                "operation_id": "recover-final-check-environment",
            }
            first = recover_final_checks(repo, RUN_ID, guidance)
            replay = recover_final_checks(repo, RUN_ID, guidance)
            self.assertEqual(
                first["final_check_recovery"], replay["final_check_recovery"]
            )
            with self.assertRaisesRegex(OperationConflict, "another operation"):
                recover_final_checks(
                    repo,
                    RUN_ID,
                    {**guidance, "affected_child_ids": ["agents-maintenance-gate"]},
                )
            replacement = advance_operator(repo, RUN_ID)
            self.assertEqual(
                [
                    item["child_id"]
                    for item in replacement["next_action"]["payload"]["children"]
                ],
                ["readme-contract-repair-1"],
            )
            self.assertEqual(
                replacement["active_child_states"]["agents-maintenance-gate"],
                "integrated",
            )

    def test_failed_integration_preserves_ref_and_dispatches_only_failed_slice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_single_child_fixture(Path(tmp))
            fixture["init"]["envelope"]["verification_policy"]["operator"][
                "integration_checks"
            ] = ["test -f integration-ok"]
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entry = dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entry, "src/app.txt", "candidate\n"),
                ),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(review, "precommit_review", review_payload(passed=True)),
            )
            state = json.loads(
                (ledger_path(repo, RUN_ID).parent / "operator-state.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = ParentLedger(repo, RUN_ID)
            lease = WriterLease(**state["lease"])
            old_problem = {
                "action": "repair the merge boundary",
                "artifact_ids": ["old-candidate"],
                "commands": [],
                "diagnosis": "an earlier merge candidate failed",
                "operation_phase": "integration_candidate",
                "problem_id": "old-integration-problem",
                "requirement_ids": ["REQ-1"],
                "root_condition": "candidate_merge_failed",
            }
            record_problem_attempt(
                ledger,
                lease,
                round_number=0,
                result="failed",
                **old_problem,
            )
            record_problem_attempt(
                ledger,
                lease,
                round_number=1,
                result="passed",
                **old_problem,
            )
            base_head = fixture["init"]["context"]["integration"]["base_head"]

            replacement = advance_operator(repo, RUN_ID)

            integration_ref = f"refs/heads/trellis/loop-v1/{RUN_ID}/integration"
            self.assertEqual(git(repo, "rev-parse", integration_ref), base_head)
            self.assertEqual(replacement["parent_status"], "authorized")
            self.assertEqual(
                replacement["next_action"]["payload"]["children"][0]["child_id"],
                "child-a-repair-1",
            )
            self.assertEqual(replacement["child_states"]["child-a"], "invalidated")

    def test_final_review_repairs_only_affected_nonfirst_requirement_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            for child_id, path in (
                ("child-a", "src/app.txt"),
                ("child-b", "tests/check.txt"),
            ):
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        dispatch,
                        "worker_result",
                        worker_result(entries[child_id], path, f"{child_id}\n"),
                    ),
                )
            for expected_child in ("child-a", "child-b"):
                review = advance_operator(repo, RUN_ID)["next_action"]
                self.assertEqual(review["payload"]["child_id"], expected_child)
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        review, "precommit_review", review_payload(passed=True)
                    ),
                )
            final_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    final_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": ["REQ-2"],
                        "dispositions": [],
                        "fresh_context_receipt": final_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [
                            {
                                "finding_id": "final-req-2",
                                "summary": "repair only REQ-2",
                            }
                        ],
                        "reviewer_identity": "model:codex:final-req-2",
                        "specialist_results": [],
                        "verdict": "failed",
                    },
                ),
            )

            replacement = advance_operator(repo, RUN_ID)

            self.assertEqual(replacement["child_states"]["child-a"], "integrated")
            self.assertEqual(replacement["child_states"]["child-b"], "integrated")
            self.assertNotIn("child-b", replacement["active_child_states"])
            self.assertEqual(
                [
                    item["child_id"]
                    for item in replacement["next_action"]["payload"]["children"]
                ],
                ["child-b-repair-1"],
            )

    def test_required_final_finding_repairs_then_requires_fresh_exact_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            _, repo, dispatch = initialize_single_child(Path(tmp))
            entry = dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(
                        entry, "src/pre_admission.py", "candidate\n"
                    ),
                ),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(review, "precommit_review", review_payload(passed=True)),
            )
            final_review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(final_review["action_type"], "final_review")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    final_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": ["REQ-1"],
                        "dispositions": [],
                        "fresh_context_receipt": final_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [
                            {"finding_id": "final-required", "summary": "repair it"}
                        ],
                        "reviewer_identity": "model:codex:fresh-final",
                        "specialist_results": [],
                        "verdict": "failed",
                    },
                ),
            )

            replacement = advance_operator(repo, RUN_ID)
            repair_entry = replacement["next_action"]["payload"]["children"][0]
            self.assertEqual(repair_entry["child_id"], "child-a-repair-1")
            self.assertNotEqual(
                repair_entry["packet"]["base"]["head"],
                repair_entry["packet"]["execution_base"]["head"],
            )
            self.assertEqual(
                repair_entry["packet"]["execution_base"]["head"],
                final_review["payload"]["integration"]["integration_head"],
            )
            self.assertEqual(
                git(Path(repair_entry["worktree"]), "rev-parse", "HEAD"),
                repair_entry["packet"]["execution_base"]["head"],
            )
            self.assertEqual(
                (Path(repair_entry["worktree"]) / "src/pre_admission.py").read_text(
                    encoding="utf-8"
                ),
                "candidate\n",
            )
            self.assertEqual(
                repair_entry["recovery_context"][0]["required_findings"][0][
                    "summary"
                ],
                "repair it",
            )
            self.assertEqual(replacement["child_states"]["child-a"], "integrated")
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    replacement["next_action"],
                    "worker_result",
                    worker_result(
                        repair_entry, "src/pre_admission.py", "repaired\n"
                    ),
                ),
            )
            repair_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    repair_review, "precommit_review", review_payload(passed=True)
                ),
            )

            fresh_final = advance_operator(repo, RUN_ID)["next_action"]
            repair_commit = git(
                Path(repair_entry["worktree"]), "rev-parse", "HEAD"
            )
            self.assertEqual(
                git(Path(repair_entry["worktree"]), "rev-parse", f"{repair_commit}^"),
                repair_entry["packet"]["execution_base"]["head"],
            )

            self.assertEqual(fresh_final["action_type"], "final_review")
            self.assertNotEqual(fresh_final["action_id"], final_review["action_id"])
            self.assertNotEqual(
                fresh_final["payload"]["integration"]["integration_head"],
                final_review["payload"]["integration"]["integration_head"],
            )
            self.assertEqual(
                fresh_final["payload"]["recovery"]["status"], "repairing"
            )
            self.assertEqual(operator_status(repo, RUN_ID)["run_id"], RUN_ID)
            self.assertEqual(
                list((repo / ".trellis" / "tasks").glob("*/task.json")),
                [repo / ".trellis" / "tasks" / "07-14-pilot-run" / "task.json"],
            )

            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    fresh_final,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": ["REQ-1"],
                        "dispositions": [],
                        "fresh_context_receipt": fresh_final["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:fresh-final-2",
                        "specialist_results": [],
                        "verdict": "failed",
                    },
                ),
            )
            second_replacement = advance_operator(repo, RUN_ID)
            second_entry = second_replacement["next_action"]["payload"]["children"][0]
            self.assertEqual(second_entry["child_id"], "child-a-repair-2")
            self.assertEqual(
                {
                    (item["root_condition"], item["round"])
                    for item in second_replacement["recovery"]["active_problems"]
                },
                {
                    ("failed_final_review", 0),
                    ("required_final_review_finding", 0),
                },
            )
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    second_replacement["next_action"],
                    "worker_result",
                    worker_result(second_entry, "src/repair-2.txt", "repaired again\n"),
                ),
            )
            second_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    second_review, "precommit_review", review_payload(passed=True)
                ),
            )
            passing_final = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    passing_final,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": passing_final["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:fresh-final-pass",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )
            final_response = advance_operator(repo, RUN_ID)
            self.assertEqual(
                final_response["next_action"]["action_type"], "final_response"
            )
            self.assertEqual(final_response["recovery"]["status"], "clear")

    def test_final_review_rejects_context_revision_after_action_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture, repo, dispatch = initialize_single_child(Path(tmp))
            entry = dispatch["payload"]["children"][0]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(entry, "src/app.txt", "candidate\n"),
                ),
            )
            review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(review, "precommit_review", review_payload(passed=True)),
            )
            final_review = advance_operator(repo, RUN_ID)["next_action"]
            state = json.loads(
                (ledger_path(repo, RUN_ID).parent / "operator-state.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = ParentLedger(repo, RUN_ID)
            lease = WriterLease(**state["lease"])
            snapshot = ledger.authority_snapshot()
            context_rows = snapshot["tables"]["context_revisions"]
            current_row = max(context_rows, key=lambda row: int(row["sequence"]))
            context = json.loads(current_row["context_json"])
            context["facts"].append(
                {"id": "FACT-DRIFT", "summary": "context changed after review issue"}
            )
            record_context_revision(
                ledger,
                lease,
                request_id=fixture["init"]["request_id"],
                revision_id="context-after-final-review-action",
                reason="prove final review freshness",
                context=context,
                expected_previous_digest=current_row["digest"],
            )

            with self.assertRaisesRegex(
                OrchestratorError, "final review action context is stale"
            ):
                ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        final_review,
                        "final_review",
                        {
                            "advisory_findings": [],
                            "affected_requirement_ids": [],
                            "dispositions": [],
                            "fresh_context_receipt": final_review["payload"][
                                "fresh_context_receipt"
                            ],
                            "required_findings": [],
                            "reviewer_identity": "model:codex:stale-final",
                            "specialist_results": [],
                            "verdict": "passed",
                        },
                    ),
                )

            fresh_review = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(fresh_review["action_type"], "final_review")
            self.assertNotEqual(fresh_review["action_id"], final_review["action_id"])
            self.assertNotEqual(
                fresh_review["payload"]["fresh_context_receipt"],
                final_review["payload"]["fresh_context_receipt"],
            )
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    fresh_review,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": fresh_review["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:fresh-final",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )

            snapshot = ledger.authority_snapshot()
            current_row = max(
                snapshot["tables"]["context_revisions"],
                key=lambda row: int(row["sequence"]),
            )
            context = json.loads(current_row["context_json"])
            context["facts"].append(
                {"id": "FACT-DRIFT-2", "summary": "context changed after review pass"}
            )
            record_context_revision(
                ledger,
                lease,
                request_id=fixture["init"]["request_id"],
                revision_id="context-after-final-review-pass",
                reason="prove passed final review freshness",
                context=context,
                expected_previous_digest=current_row["digest"],
            )
            review_after_pass = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(review_after_pass["action_type"], "final_review")
            self.assertNotEqual(review_after_pass["action_id"], fresh_review["action_id"])
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    review_after_pass,
                    "final_review",
                    {
                        "advisory_findings": [],
                        "affected_requirement_ids": [],
                        "dispositions": [],
                        "fresh_context_receipt": review_after_pass["payload"][
                            "fresh_context_receipt"
                        ],
                        "required_findings": [],
                        "reviewer_identity": "model:codex:fresh-final-2",
                        "specialist_results": [],
                        "verdict": "passed",
                    },
                ),
            )
            final_response = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(final_response["action_type"], "final_response")

            snapshot = ledger.authority_snapshot()
            current_row = max(
                snapshot["tables"]["context_revisions"],
                key=lambda row: int(row["sequence"]),
            )
            context = json.loads(current_row["context_json"])
            context["facts"].append(
                {
                    "id": "FACT-DRIFT-3",
                    "summary": "context changed before final approval",
                }
            )
            record_context_revision(
                ledger,
                lease,
                request_id=fixture["init"]["request_id"],
                revision_id="context-before-final-approval",
                reason="prove stale final response reconciliation",
                context=context,
                expected_previous_digest=current_row["digest"],
            )
            request = final_response["payload"]["request"]
            with self.assertRaisesRegex(FinalGateError, "drifted before direct approval"):
                respond_final(
                    repo,
                    RUN_ID,
                    {
                        "action_digest": final_response["action_digest"],
                        "action_id": final_response["action_id"],
                        "direct_user_action": True,
                        "request_digest": request["request_digest"],
                        "request_id": request["request"]["request_id"],
                        "response_at": "2026-07-14T22:00:00Z",
                        "response_identity": "user:stale-final-response",
                    },
                )
            redriven = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(redriven["action_type"], "final_review")
            self.assertNotEqual(redriven["action_id"], review_after_pass["action_id"])

    def test_round_three_exhaustion_waits_then_opens_generation_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            _, repo, dispatch = initialize_single_child(Path(tmp))
            result = None
            for repair_round in range(4):
                entry = dispatch["payload"]["children"][0]
                result = ingest_operator(
                    repo,
                    RUN_ID,
                    ingest_message(
                        dispatch,
                        "worker_result",
                        worker_result(entry, "src/app.txt", "trailing space \n"),
                    ),
                )
                if repair_round < 3:
                    dispatch = advance_operator(repo, RUN_ID)["next_action"]
                    self.assertEqual(dispatch["action_type"], "dispatch_workers")

            self.assertIsNotNone(result)
            self.assertEqual(result["parent_status"], "recovery_waiting")
            waiting = advance_operator(repo, RUN_ID)
            self.assertEqual(waiting["parent_status"], "recovery_waiting")
            self.assertIsNone(waiting["next_action"])
            self.assertEqual(
                waiting["required_input"],
                "new diagnosis or repair guidance",
            )
            self.assertEqual(waiting["recovery"]["status"], "recovery_waiting")
            problem_id = result["recovery_failure"]["problem_id"]
            with self.assertRaisesRegex(
                OrchestratorError,
                "continue recovery input fields differ",
            ):
                continue_recovery(
                    repo,
                    RUN_ID,
                    {
                        "action": "expand outside the envelope",
                        "diagnosis": "claim a new requirement",
                        "operation_id": "invalid-scope-expansion",
                        "problem_id": problem_id,
                        "requirement_ids": ["OUT-OF-SCOPE"],
                    },
                )
            self.assertEqual(
                operator_status(repo, RUN_ID)["authority_digest"],
                waiting["authority_digest"],
            )
            with self.assertRaisesRegex(
                OperationConflict,
                "another operation",
            ):
                continue_recovery(
                    repo,
                    RUN_ID,
                    {
                        "action": "reuse a colliding operation",
                        "diagnosis": "operation identity must remain typed",
                        "operation_id": f"problem:{problem_id}:round:3",
                        "problem_id": problem_id,
                    },
                )
            self.assertEqual(
                operator_status(repo, RUN_ID)["authority_digest"],
                waiting["authority_digest"],
            )
            continued = continue_recovery(
                repo,
                RUN_ID,
                {
                    "action": "use a new bounded implementation",
                    "diagnosis": "the prior implementation repeated the same failure",
                    "operation_id": "continue-generation-2",
                    "problem_id": problem_id,
                },
            )
            replay = continue_recovery(
                repo,
                RUN_ID,
                {
                    "action": "use a new bounded implementation",
                    "diagnosis": "the prior implementation repeated the same failure",
                    "operation_id": "continue-generation-2",
                    "problem_id": problem_id,
                },
            )
            self.assertEqual(
                replay["recovery_continuation"],
                continued["recovery_continuation"],
            )
            self.assertEqual(
                continued["recovery_continuation"]["recovery_generation"],
                2,
            )
            self.assertEqual(
                continued["recovery_continuation"]["integration_head"],
                waiting["integration"]["integration_head"],
            )
            self.assertEqual(
                continued["recovery_continuation"]["integration_tree_id"],
                waiting["integration"]["integration_tree_id"],
            )
            self.assertEqual(continued["parent_status"], "authorized")
            next_dispatch = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(next_dispatch["action_type"], "dispatch_workers")
            self.assertEqual(
                next_dispatch["payload"]["children"][0]["child_id"],
                "child-a-repair-4",
            )

    def test_q07_integration_and_final_check_positive_contract(self) -> None:
        self.test_failed_integration_preserves_ref_and_dispatches_only_failed_slice()
        self.test_final_integration_checks_wait_for_complete_graph_and_run_once()

    def test_q07_integration_and_final_check_negative_contract(self) -> None:
        self.test_round_three_exhaustion_waits_then_opens_generation_two()
        self.test_failed_final_checks_require_explicit_slice_before_replacement()

    def test_initial_repair_suffix_ids_do_not_confuse_replacement_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            init = fixture["init"]
            renamed = {"child-a": "foo", "child-b": "foo-repair-1"}
            for node in init["envelope"]["initial_child_graph"]:
                node["child_id"] = renamed[node["child_id"]]
            policies = init["envelope"]["verification_policy"]["operator"][
                "children"
            ]
            init["envelope"]["verification_policy"]["operator"]["children"] = {
                renamed[child_id]: value for child_id, value in policies.items()
            }
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, init)["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            entries = {
                item["child_id"]: item for item in dispatch["payload"]["children"]
            }
            self.assertEqual(entries["foo-repair-1"]["packet"]["attempt"], 1)
            failed = worker_result(entries["foo"], "src/app.txt", "failed\n")
            failed["commands"][0]["status"] = "failed"
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(dispatch, "worker_result", failed),
            )
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    dispatch,
                    "worker_result",
                    worker_result(
                        entries["foo-repair-1"], "tests/check.txt", "candidate\n"
                    ),
                ),
            )
            sibling_review = advance_operator(repo, RUN_ID)["next_action"]
            ingest_operator(
                repo,
                RUN_ID,
                ingest_message(
                    sibling_review, "precommit_review", review_payload(passed=True)
                ),
            )

            replacement = advance_operator(repo, RUN_ID)["next_action"]

            self.assertEqual(replacement["action_type"], "dispatch_workers")
            replacement_entry = replacement["payload"]["children"][0]
            self.assertEqual(replacement_entry["child_id"], "foo-repair-2")
            self.assertEqual(replacement_entry["packet"]["attempt"], 2)
            self.assertEqual(replacement_entry["packet"]["round"], 2)

    def test_resume_replaces_asymmetric_dependencies_one_for_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            init = fixture["init"]
            requirement = {
                "acceptance": ["dependent child is integrated"],
                "dependencies": ["REQ-1"],
                "required": True,
                "requirement_id": "REQ-3",
            }
            node = {
                "child_id": "child-c",
                "depends_on": ["child-a"],
                "requirements": ["REQ-3"],
                "resources": ["work-slot"],
                "touches": ["docs/**"],
            }
            init["envelope"]["requirements"].append(requirement)
            init["envelope"]["initial_child_graph"].append(node)
            init["envelope"]["allowed_touches"].append("docs/**")
            init["envelope"]["verification_policy"]["operator"]["children"][
                "child-c"
            ] = {
                "result_deadline": "2026-07-14T23:00:00Z",
                "tests": ["git diff --check"],
            }
            init["context"]["requirements"].append(
                {**requirement, "coverage_state": "uncovered", "revision": 1}
            )
            init["context"]["dependency_state"]["REQ-3"] = "blocked"
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, init)["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(
                sorted(item["child_id"] for item in dispatch["payload"]["children"]),
                ["child-a", "child-b"],
            )
            pause_operator(
                repo,
                RUN_ID,
                {
                    "operation_id": "asymmetric-pause",
                    "reason": "resume dependency test",
                    "requested_by": "user:test",
                    "requires_human_resume": True,
                },
            )
            resume_operator(
                repo,
                RUN_ID,
                {
                    "authority_identity": "user:test-resume",
                    "available_resources": ["work-slot"],
                    "direct_user_action": True,
                    "new_writer_id": "loop-v1-operator:asymmetric-resume",
                    "operation_id": "asymmetric-resume",
                    "tool_receipt": RECEIPT_ID,
                },
            )

            replacement = advance_operator(repo, RUN_ID)

            self.assertEqual(
                sorted(
                    item["child_id"]
                    for item in replacement["next_action"]["payload"]["children"]
                ),
                ["child-a-repair-1", "child-b-repair-1"],
            )
            snapshot = ParentLedger(repo, RUN_ID).authority_snapshot()
            current_row = max(
                snapshot["tables"]["context_revisions"],
                key=lambda row: int(row["sequence"]),
            )
            graph = {
                item["child_id"]: item
                for item in json.loads(current_row["context_json"])["graph"]
            }
            self.assertEqual(graph["child-c"]["depends_on"], ["child-a-repair-1"])

    def test_qualification_pause_reports_current_machine_boundary_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            advance_operator(repo, RUN_ID)
            state = json.loads(
                (ledger_path(repo, RUN_ID).parent / "operator-state.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = ParentLedger(repo, RUN_ID)
            lease = WriterLease(**state["lease"])
            invalid = QualificationStatus(
                True,
                False,
                None,
                ("local admission disable marker is active",),
            )
            with mock.patch(
                "loop_v1.qualification.execution_qualification", return_value=invalid
            ):
                with self.assertRaises(QualificationError):
                    ledger.assert_runtime_qualification(lease)
                intervention = advance_operator(repo, RUN_ID)

            action = intervention["next_action"]
            self.assertEqual(action["action_type"], "human_intervention")
            self.assertEqual(
                action["payload"]["reason"],
                "dependency_schema_security_or_secret_change",
            )
            self.assertEqual(
                action["payload"]["details"],
                "local admission disable marker is active",
            )
            resume_operator(
                repo,
                RUN_ID,
                {
                    "authority_identity": "user:requalified",
                    "available_resources": ["work-slot"],
                    "direct_user_action": True,
                    "new_writer_id": "loop-v1-operator:requalified",
                    "operation_id": "qualification-resume",
                    "tool_receipt": RECEIPT_ID,
                },
            )
            pause_operator(
                repo,
                RUN_ID,
                {
                    "operation_id": "explicit-pause-after-requalification",
                    "reason": "operator requested pause",
                    "requested_by": "user:test",
                    "requires_human_resume": True,
                },
            )
            explicit = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(explicit["payload"]["reason"], "explicit_user_pause")
            self.assertEqual(explicit["payload"]["details"], "explicit parent pause")

    def test_wrong_start_action_digest_fails_without_authorizing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            action = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            response = start_response(action)
            response["action_digest"] = f"sha256:{'0' * 64}"
            with self.assertRaisesRegex(OrchestratorError, "action digest differs"):
                respond_start(repo, RUN_ID, response)
            self.assertEqual(operator_status(repo, RUN_ID)["parent_status"], "initialized")

    def test_invalid_operator_policy_fails_before_ledger_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            policy = fixture["init"]["envelope"]["verification_policy"]["operator"]
            policy["effect_proofs"][0]["status"] = "unknown"

            with self.assertRaisesRegex(
                ReadinessError, "classification/status is invalid"
            ):
                initialize_operator(repo, RUN_ID, fixture["init"])
            self.assertFalse(ledger_path(repo, RUN_ID).exists())
            self.assertFalse(ledger_path(repo, RUN_ID).parent.exists())

    def test_final_integration_policy_is_required_nonempty_and_unique(self) -> None:
        cases = {
            "missing": (
                lambda policy: policy.pop("final_integration_checks"),
                "missing=.*final_integration_checks",
            ),
            "empty": (
                lambda policy: policy.__setitem__("final_integration_checks", []),
                "final integration checks must not be empty",
            ),
            "duplicate": (
                lambda policy: policy.__setitem__(
                    "final_integration_checks",
                    ["git diff --check", "git diff --check"],
                ),
                "final integration checks must not contain duplicates",
            ),
            "unknown": (
                lambda policy: policy.__setitem__(
                    "unknown_final_integration_checks", ["git diff --check"]
                ),
                "unknown=.*unknown_final_integration_checks",
            ),
        }
        with qualified_runtime():
            for name, (mutate, error) in cases.items():
                with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                    fixture = create_fixture(Path(tmp))
                    repo = fixture["repo"]
                    policy = fixture["init"]["envelope"]["verification_policy"][
                        "operator"
                    ]
                    mutate(policy)
                    with self.assertRaisesRegex(OrchestratorError, error):
                        initialize_operator(repo, RUN_ID, fixture["init"])
                    self.assertFalse(ledger_path(repo, RUN_ID).exists())

    def test_explicit_pause_resume_and_cancel_compose_existing_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            self.assertEqual(dispatch["action_type"], "dispatch_workers")
            late_entry = next(
                item
                for item in dispatch["payload"]["children"]
                if item["child_id"] == "child-a"
            )
            late_result = worker_result(
                late_entry, "src/app.txt", "late child-a result\n"
            )
            with self.assertRaisesRegex(
                OrchestratorError, "requires_human_resume must be boolean"
            ):
                pause_operator(
                    repo,
                    RUN_ID,
                    {
                        "operation_id": "invalid-pause",
                        "reason": "invalid boolean must not revoke dispatch",
                        "requested_by": "user:test",
                        "requires_human_resume": "yes",
                    },
                )
            self.assertEqual(operator_status(repo, RUN_ID)["next_action"], dispatch)

            paused = pause_operator(
                repo,
                RUN_ID,
                {
                    "operation_id": "operator-test-pause",
                    "reason": "operator recovery test",
                    "requested_by": "user:test",
                    "requires_human_resume": True,
                },
            )
            self.assertEqual(paused["parent_status"], "paused")
            resumed = resume_operator(
                repo,
                RUN_ID,
                {
                    "authority_identity": "user:test-resume",
                    "available_resources": ["work-slot"],
                    "direct_user_action": True,
                    "new_writer_id": "loop-v1-operator:resumed",
                    "operation_id": "operator-test-resume",
                    "tool_receipt": RECEIPT_ID,
                },
            )
            self.assertEqual(resumed["parent_status"], "authorized")
            self.assertEqual(resumed["epoch"], 2)
            state = json.loads(
                (ledger_path(repo, RUN_ID).parent / "operator-state.json").read_text(
                    encoding="utf-8"
                )
            )
            ledger = ParentLedger(repo, RUN_ID)
            resumed_lease = WriterLease(**state["lease"])
            authority_before = ledger.authority_digest()
            with self.assertRaises(FreshnessError):
                accept_child_result(ledger, resumed_lease, late_result)
            self.assertEqual(ledger.authority_digest(), authority_before)
            intervention = advance_operator(repo, RUN_ID)

            self.assertEqual(intervention["parent_status"], "authorized")
            self.assertEqual(
                intervention["next_action"]["action_type"], "dispatch_workers"
            )
            replacement_ids = [
                item["child_id"]
                for item in intervention["next_action"]["payload"]["children"]
            ]
            self.assertEqual(
                replacement_ids, ["child-a-repair-1", "child-b-repair-1"]
            )

            cancelled = cancel_operator(
                repo,
                RUN_ID,
                {
                    "action_digest": intervention["next_action"]["action_digest"],
                    "action_id": intervention["next_action"]["action_id"],
                    "actor": "user:test-cancel",
                    "direct_user_action": True,
                    "expected_authority_digest": intervention["authority_digest"],
                    "operation_id": "operator-test-cancel",
                    "reason": "operator control test complete",
                    "requested_at": "2026-07-14T22:30:00Z",
                    "superseded_by": None,
                },
            )
            self.assertEqual(cancelled["parent_status"], "cancelled")
            self.assertIsNone(cancelled["next_action"])

    def test_cancel_preflight_reject_preserves_operator_and_authority_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            advance_operator(repo, RUN_ID)
            state_path = ledger_path(repo, RUN_ID).parent / "operator-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            ledger = ParentLedger(repo, RUN_ID)
            lease = WriterLease(**state["lease"])
            ledger.prepare_operation(
                lease,
                operation_id="ambiguous-cancel-effect",
                kind="external_effect_without_reconciler",
                input_fingerprint="ambiguous-effect",
                intent={"effect_id": "unknown"},
            )
            status = operator_status(repo, RUN_ID)
            action = status["next_action"]
            state_before = state_path.read_bytes()
            authority_before = ledger.authority_digest()
            epoch_before = status["epoch"]

            with self.assertRaisesRegex(
                ControlError,
                "no ambiguous external effect",
            ):
                cancel_operator(
                    repo,
                    RUN_ID,
                    {
                        "action_digest": action["action_digest"],
                        "action_id": action["action_id"],
                        "actor": "user:test-cancel",
                        "direct_user_action": True,
                        "expected_authority_digest": status["authority_digest"],
                        "operation_id": "reject-ambiguous-cancel",
                        "reason": "must remain fail closed",
                        "requested_at": "2026-07-29T18:10:00Z",
                        "superseded_by": None,
                    },
                )

            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(ledger.authority_digest(), authority_before)
            self.assertEqual(operator_status(repo, RUN_ID)["epoch"], epoch_before)

    def test_cancel_projection_recovers_after_operator_state_write_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            status = advance_operator(repo, RUN_ID)
            action = status["next_action"]
            request = {
                "action_digest": action["action_digest"],
                "action_id": action["action_id"],
                "actor": "user:test-cancel",
                "direct_user_action": True,
                "expected_authority_digest": status["authority_digest"],
                "operation_id": "cancel-before-state-write-failure",
                "reason": "inject operator projection write failure",
                "requested_at": "2026-07-29T18:20:00Z",
                "superseded_by": None,
            }

            with mock.patch(
                "loop_v1.orchestrator._write_state",
                side_effect=OSError("injected operator-state write failure"),
            ), self.assertRaisesRegex(OSError, "injected operator-state"):
                cancel_operator(repo, RUN_ID, request)

            recovered = operator_status(repo, RUN_ID)
            state = json.loads(
                (ledger_path(repo, RUN_ID).parent / "operator-state.json").read_text(
                    encoding="utf-8"
                )
            )
            consumed = [
                item
                for item in state["action_history"]
                if item["action_id"] == action["action_id"]
            ]
            self.assertEqual(recovered["parent_status"], "cancelled")
            self.assertIsNone(recovered["next_action"])
            self.assertEqual(len(consumed), 1)
            self.assertEqual(consumed[0]["input_digest"], f"sha256:{_digest_json(request)}")

    def test_resume_operator_recovers_lease_after_fence_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            advance_operator(repo, RUN_ID)
            pause_operator(
                repo,
                RUN_ID,
                {
                    "operation_id": "pause-before-fence-crash",
                    "reason": "inject resume crash",
                    "requested_by": "user:test",
                    "requires_human_resume": True,
                },
            )
            request = {
                "authority_identity": "user:fence-crash",
                "available_resources": ["work-slot"],
                "direct_user_action": True,
                "new_writer_id": "loop-v1-operator:fence-crash",
                "operation_id": "resume-after-fence-crash",
                "tool_receipt": RECEIPT_ID,
            }
            state_path = ledger_path(repo, RUN_ID).parent / "operator-state.json"
            state_before = state_path.read_bytes()
            with mock.patch(
                "loop_v1.integration.reconcile_parent",
                side_effect=RuntimeError("injected crash after fence"),
            ), self.assertRaisesRegex(RuntimeError, "injected crash"):
                resume_operator(repo, RUN_ID, request)

            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(ParentLedger(repo, RUN_ID).authority_snapshot()["tables"][
                "parent_runs"
            ][0]["epoch"], 2)

            resumed = resume_operator(repo, RUN_ID, request)

            self.assertEqual(resumed["parent_status"], "authorized")
            self.assertEqual(resumed["epoch"], 2)
            rotations = [
                row
                for row in ParentLedger(repo, RUN_ID).authority_snapshot()["tables"][
                    "operations"
                ]
                if row["kind"] == "writer_rotation"
            ]
            self.assertEqual(len(rotations), 1)

    def test_paused_status_suppresses_obsolete_pending_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, qualified_runtime():
            fixture = create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = initialize_operator(repo, RUN_ID, fixture["init"])["next_action"]
            respond_start(repo, RUN_ID, start_response(start))
            dispatch = advance_operator(repo, RUN_ID)["next_action"]
            state_path = ledger_path(repo, RUN_ID).parent / "operator-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            ledger = ParentLedger(repo, RUN_ID)
            lease = WriterLease(**state["lease"])

            pause_parent(
                ledger,
                lease,
                operation_id="authority-only-pause",
                reason="qualification quarantine",
                requested_by="qualification:disable",
                requires_human_resume=True,
            )

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["pending_action"],
                dispatch,
            )
            status = operator_status(repo, RUN_ID)
            self.assertEqual(status["parent_status"], "paused")
            self.assertEqual(status["status"], "paused")
            self.assertIsNone(status["next_action"])
            self.assertTrue(status["resume_safe_point"]["eligible"])
            self.assertEqual(
                status["resume_safe_point"]["failed_predicates"],
                [],
            )
            self.assertEqual(
                status["resume_safe_point"]["unresolved_operations"],
                [],
            )

    def test_non_loop_parent_fails_before_runtime_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = create_fixture(root, workflow_mode="harness_state_machine")
            repo = fixture["repo"]
            input_path = repo / "init.json"
            input_path.write_text(
                json.dumps(fixture["init"], sort_keys=True) + "\n", encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "loop_v1.orchestrator",
                    "--repo-root",
                    str(repo),
                    "--run-id",
                    RUN_ID,
                    "init",
                    "--input",
                    "init.json",
                ],
                cwd=SCRIPT_DIR.parent,
                env={**os.environ, "PYTHONPATH": str(SCRIPT_DIR)},
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "")
            error = json.loads(result.stdout)
            self.assertEqual(error["status"], "failed")
            self.assertEqual(error["error_type"], "OrchestratorError")
            self.assertIn("requires a loop_v1 parent", error["error"])
            self.assertNotIn("fence_token", result.stdout)
            self.assertFalse(ledger_path(repo, RUN_ID).exists())
            self.assertFalse(ledger_path(repo, RUN_ID).parent.exists())


if __name__ == "__main__":
    unittest.main()
