from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

import loop_v1.acceptance as acceptance_runtime
import loop_v1.integration as integration_runtime
import loop_v1.qualification as qualification_runtime
import test_loop_v1_qualification as qualification_fixture
import test_loop_v1_worker_commit as worker_fixture
from loop_v1 import (
    DirtOverlapError,
    FinalGateError,
    FinalMergeError,
    FinalReviewError,
    ParentLedger,
    accept_child_result,
    acknowledge_integration,
    advance_integration_ref,
    approve_final_request,
    approve_start_request,
    archive_parent_run,
    build_integration_candidate,
    commit_reviewed_candidate,
    compute_final_readiness,
    create_child_worktree,
    create_final_request,
    create_start_request,
    deterministic_integration_selection,
    execute_final_merge,
    generate_acceptance_pack,
    issue_child_packet,
    observe_worker_candidate,
    pause_parent,
    prepare_integration_candidate,
    record_context_revision,
    record_final_review,
    record_precommit_review,
    resume_parent,
    scan_repository_dirt,
    schedule_ready,
    validate_child_candidate,
)
from test_loop_v1_integration import (
    INTEGRATION_REF,
    commit_operation,
    committed_runtime,
    zero_diff_recovery_runtime,
)
from test_loop_v1_recovery import integrate
from test_loop_v1_worker_commit import git
from loop_v1.integration import resume_safe_point_status
from loop_v1.qualification import (
    QualificationStatus,
    SOURCE_DEVELOPMENT_PURPOSE,
    configured_qualification,
    generate_conformance_receipt,
)


ENVIRONMENT = {
    "git_version": "git-test",
    "input_digest": "sha256:inputs",
    "platform_digest": "sha256:platform",
    "python_version": "python-test",
    "runtime_version": "loop-v1-bootstrap",
    "tool_config_digest": "sha256:tool-config",
}
EFFECT_PROOFS = [
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
]
TOOL_RECEIPT = "receipt-worker-commit-fixture"


def ledger_rows(runtime: dict[str, object], query: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(runtime["ledger"].reference()["sqlite_uri"], uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query).fetchall()]
    finally:
        connection.close()


def ready_runtime(
    root: Path, *, risk: str | None = None
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    if risk is None:
        runtime = committed_runtime(root)
    else:
        original = worker_fixture.envelope

        def risky_envelope(base_head: str, dirt_digest: str) -> dict[str, object]:
            value = original(base_head, dirt_digest)
            value["risks"] = [*value["risks"], risk]
            return value

        with patch.object(worker_fixture, "envelope", side_effect=risky_envelope):
            runtime = committed_runtime(root)
    integrate(runtime)
    specialists = []
    if risk is not None:
        specialists = [
            {
                "evidence_digest": "sha256:specialist-pass",
                "specialist": "security_privacy",
                "status": "passed",
            }
        ]
    record_final_review(
        runtime["ledger"],
        runtime["lease"],
        review_id="final-review-1",
        integration_ref=INTEGRATION_REF,
        reviewer_identity="model:codex:fresh-final-review",
        fresh_context_receipt="fresh-context-receipt-1",
        verdict="passed",
        required_findings=[],
        advisory_findings=[],
        dispositions=[],
        specialist_results=specialists,
    )
    pack = generate_acceptance_pack(
        runtime["ledger"],
        pack_id="acceptance-1",
        integration_ref=INTEGRATION_REF,
        tool_receipt=TOOL_RECEIPT,
        environment=ENVIRONMENT,
        effect_proofs=EFFECT_PROOFS,
        skipped_checks=[
            {
                "check_id": "remote-ci",
                "reason": "local acceptance has no remote dependency",
                "status": "not_applicable",
            }
        ],
    )
    readiness = compute_final_readiness(
        runtime["ledger"],
        pack_result=pack,
        integration_ref=INTEGRATION_REF,
        tool_receipt=TOOL_RECEIPT,
    )
    return runtime, pack, readiness


def qualification_rotation_runtime(
    root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    template = root / "qualified-template"
    historical = qualification_fixture.qualify_repo(template)
    git(template, "branch", "-M", "main")
    (template / "src").mkdir()
    (template / "tests").mkdir(exist_ok=True)
    (template / ".gitignore").write_text(".trellis/.runtime/\n", encoding="utf-8")
    (template / "src/app.txt").write_text("base\n", encoding="utf-8")
    (template / "tests/check.txt").write_text("base\n", encoding="utf-8")
    git(
        template,
        "add",
        ".trellis/config.yaml",
        historical.relative_to(template).as_posix(),
        ".gitignore",
        "src",
        "tests",
    )
    git(template, "commit", "-m", "bind historical qualification")
    tool_receipt = f"sha256:{historical.stem}"
    original_envelope = worker_fixture.envelope

    def initialize(repo: Path) -> tuple[str, str]:
        shutil.copytree(template, repo)
        head = git(repo, "rev-parse", "HEAD")
        return head, git(repo, "rev-parse", f"{head}^{{tree}}")

    def qualified_envelope(base_head: str, dirt_digest: str) -> dict[str, object]:
        value = original_envelope(base_head, dirt_digest)
        value["allowed_touches"] = [
            "src/**",
            "tests/**",
            ".trellis/config.yaml",
            ".trellis/spec/project/receipts/loop-v1/**",
        ]
        value["conformance_receipt"] = tool_receipt
        return value

    with (
        patch.object(worker_fixture, "initialize_git_repo", side_effect=initialize),
        patch.object(worker_fixture, "envelope", side_effect=qualified_envelope),
        patch(f"{__name__}.TOOL_RECEIPT", tool_receipt),
    ):
        runtime, pack, readiness = ready_runtime(root)
    runtime["tool_receipt"] = tool_receipt
    return runtime, pack, readiness


def write_minimal_receipt(
    repo: Path,
    *,
    runtime_commit: str,
    label: str,
    malformed: bool = False,
    identity_mismatch: bool = False,
) -> Path:
    payload = {
        "issues": [],
        "receipt_schema_version": (
            qualification_fixture.qualification_module.RECEIPT_SCHEMA_VERSION
        ),
        "runtime": {"git_commit": runtime_commit},
        "status": "qualified",
        "test_label": label,
    }
    digest = qualification_fixture.qualification_module.digest_json(payload)
    path = (
        repo
        / ".trellis/spec/project/receipts/loop-v1"
        / f"{digest}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if malformed:
        path.write_text("{not-json\n", encoding="utf-8")
    else:
        receipt_id = f"sha256:{digest}"
        if identity_mismatch:
            receipt_id = "sha256:" + "0" * 64
        path.write_text(
            json.dumps(
                {
                    **payload,
                    "receipt_digest": digest,
                    "receipt_id": receipt_id,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return path


def minimal_qualification_rotation_repo(
    root: Path,
    *,
    case: str = "valid",
) -> dict[str, object]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Qualification Rotation Test")
    git(repo, "config", "user.email", "rotation@example.test")
    historical = write_minimal_receipt(
        repo,
        runtime_commit="historical-runtime",
        label="historical",
    )
    historical_digest = historical.stem
    config = repo / ".trellis/config.yaml"
    config.write_text(
        "loop_v1:\n"
        "  admission_enabled: true\n"
        "  parent_default: loop_v1\n"
        "  runtime_mode: harness_source\n"
        f"  qualification_receipt: "
        f".trellis/spec/project/receipts/loop-v1/{historical_digest}.json\n"
        f"  qualification_receipt_digest: {historical_digest}\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "publication")
    publication_head = git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "docs/runtime.md").write_text("runtime repair\n", encoding="utf-8")
    git(repo, "add", "docs/runtime.md")
    git(repo, "commit", "-m", "runtime repair")
    qualified_runtime_commit = git(repo, "rev-parse", "HEAD")
    receipt_runtime = (
        publication_head if case == "wrong_predecessor" else qualified_runtime_commit
    )
    current = write_minimal_receipt(
        repo,
        runtime_commit=receipt_runtime,
        label="current",
        malformed=case == "malformed_receipt",
        identity_mismatch=case == "identity_mismatch",
    )
    current_digest = current.stem
    suffix = "# unrelated formatting drift\n" if case == "config_comment" else ""
    parent_default = (
        "current_trellis" if case == "config_other_key" else "loop_v1"
    )
    config.write_text(
        "loop_v1:\n"
        "  admission_enabled: true\n"
        f"  parent_default: {parent_default}\n"
        "  runtime_mode: harness_source\n"
        f"  qualification_receipt: "
        f".trellis/spec/project/receipts/loop-v1/{current_digest}.json\n"
        f"  qualification_receipt_digest: {current_digest}\n"
        f"{suffix}",
        encoding="utf-8",
    )
    git(repo, "add", ".trellis/config.yaml", current.relative_to(repo).as_posix())
    if case == "mixed_commit":
        evidence = repo / "docs/evidence.md"
        evidence.parent.mkdir(exist_ok=True)
        evidence.write_text("non-overlapping evidence\n", encoding="utf-8")
        git(repo, "add", evidence.relative_to(repo).as_posix())
    if case == "multiple_receipts":
        extra = write_minimal_receipt(
            repo,
            runtime_commit=qualified_runtime_commit,
            label="extra",
        )
        git(repo, "add", extra.relative_to(repo).as_posix())
    if case == "old_receipt_modified":
        historical.write_text(
            historical.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        git(repo, "add", historical.relative_to(repo).as_posix())
    git(repo, "commit", "-m", "bind current qualification")
    binding_commit = git(repo, "rev-parse", "HEAD")
    if case == "multiple_binding_commits":
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "  parent_default: loop_v1\n",
                "  parent_default: loop_v1\n# later config touch\n",
            ),
            encoding="utf-8",
        )
        git(repo, "add", ".trellis/config.yaml")
        git(repo, "commit", "-m", "second binding touch")
    return {
        "binding": {
            "mode": SOURCE_DEVELOPMENT_PURPOSE,
            "receipt_digest": historical_digest,
            "receipt_id": f"sha256:{historical_digest}",
            "receipt_path": historical.relative_to(repo).as_posix(),
        },
        "binding_commit": binding_commit,
        "current_receipt_id": f"sha256:{current_digest}",
        "publication_head": publication_head,
        "qualified_runtime_commit": qualified_runtime_commit,
        "repo": repo,
        "retained_main_head": git(repo, "rev-parse", "HEAD"),
    }


def final_request(
    runtime: dict[str, object],
    pack: dict[str, object],
    readiness: dict[str, object],
    *,
    checks: list[str] | None = None,
) -> dict[str, object]:
    return create_final_request(
        runtime["ledger"],
        runtime["lease"],
        request_id=pack["pack"]["final_request_id"],
        pack_result=pack,
        readiness=readiness,
        integration_ref=INTEGRATION_REF,
        post_merge_checks=checks or ["git diff --check"],
        author_name="Loop Final Integrator",
        author_email="loop-final@example.test",
    )


def approve(
    runtime: dict[str, object],
    pack: dict[str, object],
    request: dict[str, object],
) -> dict[str, object]:
    return approve_final_request(
        runtime["ledger"],
        runtime["lease"],
        request_id=pack["pack"]["final_request_id"],
        request_digest=request["request_digest"],
        pack_result=pack,
        integration_ref=INTEGRATION_REF,
        response_identity="user:final-approval-1",
        response_at="2026-07-13T21:00:00Z",
        direct_user_action=True,
    )


def two_child_runtime(root: Path) -> dict[str, object]:
    repo = root / "repo"
    base_head, base_tree = worker_fixture.initialize_git_repo(repo)
    dirt = scan_repository_dirt(repo)
    envelope = worker_fixture.envelope(base_head, dirt["digest"])
    requirements = [
        {
            "requirement_id": "REQ-1",
            "required": True,
            "acceptance": ["child-a exact tree is integrated"],
            "dependencies": [],
        },
        {
            "requirement_id": "REQ-2",
            "required": True,
            "acceptance": ["child-b exact tree is integrated"],
            "dependencies": [],
        },
    ]
    graph = [
        {
            "child_id": "child-a",
            "requirements": ["REQ-1"],
            "touches": ["src/**"],
            "resources": ["work-slot"],
            "depends_on": [],
        },
        {
            "child_id": "child-b",
            "requirements": ["REQ-2"],
            "touches": ["tests/**"],
            "resources": ["work-slot"],
            "depends_on": [],
        },
    ]
    envelope.update(
        {
            "goal": "Qualify one complete two-child local Loop lifecycle.",
            "requirements": requirements,
            "resources": [
                {"resource_key": "work-slot", "mode": "shared", "capacity": 2}
            ],
            "initial_child_graph": graph,
            "default_parallel": 2,
            "max_parallel": 2,
            "worker_capacity": 2,
        }
    )
    context = worker_fixture.context(base_head, base_tree)
    context.update(
        {
            "requirements": [
                {**item, "revision": 1, "coverage_state": "uncovered"}
                for item in requirements
            ],
            "graph": copy.deepcopy(graph),
            "dependency_state": {"REQ-1": "ready", "REQ-2": "ready"},
        }
    )
    ledger, lease = ParentLedger.initialize(
        repo,
        "two-child-qualification",
        selector="loop_v1",
        writer_id="two-child-parent",
    )
    start = create_start_request(
        ledger,
        lease,
        request_id="two-child-start",
        envelope=envelope,
    )
    approval = approve_start_request(
        ledger,
        lease,
        request_id="two-child-start",
        request_digest=start["request_digest"],
        response_identity="user:two-child-start",
        response_at="2026-07-13T18:00:00Z",
        direct_user_action=True,
    )
    record_context_revision(
        ledger,
        lease,
        request_id="two-child-start",
        revision_id="two-child-context",
        reason="initial two-child qualification context",
        context=context,
    )
    decision = schedule_ready(
        ledger,
        lease,
        decision_id="two-child-schedule",
        live_capacity=2,
    )
    packets: dict[str, dict[str, object]] = {}
    worktrees: dict[str, Path] = {}
    changes = {
        "child-a": ("REQ-1", Path("src/app.txt"), "child-a\n"),
        "child-b": ("REQ-2", Path("tests/check.txt"), "child-b\n"),
    }
    for child_id in ("child-a", "child-b"):
        requirement_id, relative, contents = changes[child_id]
        packet = issue_child_packet(
            ledger,
            lease,
            {
                "packet_id": f"packet-{child_id}",
                "child_id": child_id,
                "requirements": [requirement_id],
                "forbidden_touches": [],
                "tests": ["git diff --check"],
                "context_slice_ids": ["slice-public"],
                "parent_contact": "channel:two-child-qualification",
                "result_deadline": "2026-07-13T20:00:00Z",
                "result_lease": f"lease-{child_id}",
                "attempt": 1,
                "round": 1,
            },
        )
        worktree = root / "worktrees" / child_id
        create_child_worktree(
            ledger,
            lease,
            operation_id=f"worktree-{child_id}",
            child_id=child_id,
            packet_id=packet["packet_id"],
            worktree=worktree,
            branch=f"loop-v1/{child_id}",
            integration_worktree=root / "integration",
        )
        (worktree / relative).write_text(contents, encoding="utf-8")
        packets[child_id] = packet
        worktrees[child_id] = worktree
    return {
        "approval": approval,
        "base_head": base_head,
        "base_tree": base_tree,
        "changes": changes,
        "decision": decision,
        "ledger": ledger,
        "lease": lease,
        "packets": packets,
        "repo": repo,
        "root": root,
        "worktrees": worktrees,
    }


def commit_two_child_candidate(
    runtime: dict[str, object], child_id: str
) -> dict[str, object]:
    packet = runtime["packets"][child_id]
    worktree = runtime["worktrees"][child_id]
    requirement_id = runtime["changes"][child_id][0]
    observation = observe_worker_candidate(worktree, base_head=runtime["base_head"])
    result = {
        "result_id": f"result-{child_id}",
        "packet_id": packet["packet_id"],
        "child_id": child_id,
        "actual_touches": observation["actual_touches"],
        "diff_identity": observation["diff_identity"],
        "base_head": observation["base_head"],
        "base_tree_id": observation["base_tree_id"],
        "result_tree_id": observation["tree_id"],
        "commands": [
            {
                "command": "git diff --check",
                "status": "passed",
                "output_digest": f"output-{child_id}",
            }
        ],
        "coverage": [requirement_id],
        "risks": [],
        "findings": [],
        "artifacts": [
            {"path": path, "digest": f"artifact:{child_id}:{index}"}
            for index, path in enumerate(observation["actual_touches"], start=1)
        ],
        **packet["identity"],
    }
    accept_child_result(runtime["ledger"], runtime["lease"], result)
    validation = validate_child_candidate(
        runtime["ledger"],
        runtime["lease"],
        validation_id=f"validation-{child_id}",
        result=result,
        worktree=worktree,
    )
    review = record_precommit_review(
        runtime["ledger"],
        runtime["lease"],
        review_id=f"review-{child_id}",
        validation_id=validation["validation_id"],
        reviewer_identity="model:codex",
        verdict="passed",
        required_findings=[],
        advisory_findings=[],
        dispositions=[],
    )
    commit = commit_reviewed_candidate(
        runtime["ledger"],
        runtime["lease"],
        operation_id=f"commit-{child_id}",
        review_id=review["review_id"],
        message=f"{child_id} candidate",
        author_name="Loop Parent",
        author_email="parent@example.test",
    )
    if commit["tree_id"] != observation["tree_id"]:
        raise AssertionError("parent commit does not match the observed exact tree")
    return commit


def integrate_two_child_candidates(
    runtime: dict[str, object], child_ids: tuple[str, ...]
) -> list[dict[str, object]]:
    git(runtime["repo"], "update-ref", INTEGRATION_REF, runtime["base_head"])
    outcomes = []
    for child_id in child_ids:
        integration_id = f"integration-{child_id}"
        prepare_integration_candidate(
            runtime["ledger"],
            runtime["lease"],
            integration_id=integration_id,
            child_id=child_id,
            integration_ref=INTEGRATION_REF,
            candidate_worktree=runtime["root"] / f"integration-{child_id}",
            candidate_branch=f"loop-v1/candidate-{child_id}",
            checks=["git diff --check"],
            author_name="Loop Integrator",
            author_email="integrator@example.test",
        )
        candidate = build_integration_candidate(
            runtime["ledger"], runtime["lease"], integration_id=integration_id
        )
        advance_integration_ref(
            runtime["ledger"], runtime["lease"], integration_id=integration_id
        )
        outcomes.append(
            acknowledge_integration(
                runtime["ledger"], runtime["lease"], integration_id=integration_id
            )
        )
        if candidate["status"] != "candidate_green":
            raise AssertionError("two-child integration candidate was not green")
    return outcomes


class LoopV1AcceptanceTests(unittest.TestCase):
    def test_acceptance_traces_reviewed_zero_diff_integration_without_git(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = zero_diff_recovery_runtime(Path(tmp))
            integration_runtime.integrate_reviewed_zero_diff_candidate(
                runtime["ledger"],
                runtime["lease"],
                operation_id="zero-diff-integration:child-a",
                review_id=runtime["review"]["review_id"],
                recovery_operation_id=runtime["recovery_operation_id"],
                problem_id=runtime["problem"]["problem_id"],
            )
            commit_operation(
                runtime,
                "final-checks-passed",
                "final_integration_checks",
                {
                    "checks_result": [
                        {
                            "command": "git diff --check",
                            "exit_code": 0,
                            "output_digest": "sha256:passed",
                            "status": "passed",
                        }
                    ],
                    "integration_head": runtime["base_head"],
                    "integration_tree_id": runtime["base_tree"],
                    "root_condition": None,
                    "status": "passed",
                },
            )
            connection = runtime["ledger"]._connect(read_only=True)
            try:
                ready, evidence = acceptance_runtime._children_ready(connection)
                children = acceptance_runtime._child_evidence(connection)
            finally:
                connection.close()

            self.assertTrue(ready)
            self.assertTrue(evidence[0]["zero_diff_integration"]["passed"])
            self.assertTrue(children[0]["zero_diff_integration"]["passed"])
            self.assertIsNone(children[0]["commit"])
            self.assertIsNone(children[0]["integration"])

    def test_two_child_happy_path_qualifies_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = two_child_runtime(Path(tmp))
            self.assertEqual(runtime["approval"]["status"], "approved")
            self.assertEqual(runtime["decision"]["selected"], ["child-a", "child-b"])
            self.assertEqual(runtime["decision"]["hard_cap"], 2)

            commits = {
                child_id: commit_two_child_candidate(runtime, child_id)
                for child_id in ("child-b", "child-a")
            }
            self.assertEqual(
                deterministic_integration_selection(runtime["ledger"])["selected"],
                ["child-a", "child-b"],
            )
            integrated = integrate_two_child_candidates(
                runtime, ("child-a", "child-b")
            )
            self.assertEqual(
                [item["child_commit_id"] for item in integrated],
                [commits["child-a"]["commit_id"], commits["child-b"]["commit_id"]],
            )
            record_final_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="two-child-final-review",
                integration_ref=INTEGRATION_REF,
                reviewer_identity="model:codex:fresh-two-child-final",
                fresh_context_receipt="fresh-two-child-final",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
                specialist_results=[],
            )
            pack = generate_acceptance_pack(
                runtime["ledger"],
                pack_id="two-child-acceptance",
                integration_ref=INTEGRATION_REF,
                tool_receipt=TOOL_RECEIPT,
                environment=ENVIRONMENT,
                effect_proofs=EFFECT_PROOFS,
                skipped_checks=[],
            )
            readiness = compute_final_readiness(
                runtime["ledger"],
                pack_result=pack,
                integration_ref=INTEGRATION_REF,
                tool_receipt=TOOL_RECEIPT,
            )
            self.assertTrue(readiness["ready"])
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            merged = execute_final_merge(
                runtime["ledger"],
                runtime["lease"],
                request_id=pack["pack"]["final_request_id"],
                pack_result=pack,
            )
            self.assertEqual(
                merged["merge_tree_id"], pack["pack"]["git"]["integration_tree_id"]
            )
            self.assertEqual(
                merged["parents"],
                [
                    pack["pack"]["git"]["base_head"],
                    pack["pack"]["git"]["integration_head"],
                ],
            )
            self.assertTrue(all(item["status"] == "passed" for item in merged["checks"]))
            self.assertTrue(merged["effect_boundary_clean"])
            archived = archive_parent_run(
                runtime["ledger"],
                runtime["lease"],
                request_id=pack["pack"]["final_request_id"],
            )
            self.assertEqual(archived["status"], "archived")
            self.assertEqual(
                ledger_rows(runtime, "SELECT status FROM parent_runs")[0]["status"],
                "archived",
            )
            effects = ledger_rows(runtime, "SELECT outcome_json FROM effects")
            self.assertEqual(len(effects), 1)
            self.assertFalse(json.loads(effects[0]["outcome_json"])["push"])

    def test_two_child_path_rejects_incomplete_required_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = two_child_runtime(Path(tmp))
            commit_two_child_candidate(runtime, "child-a")
            integrate_two_child_candidates(runtime, ("child-a",))
            with self.assertRaisesRegex(FinalReviewError, "complete integrated"):
                record_final_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="incomplete-two-child-review",
                    integration_ref=INTEGRATION_REF,
                    reviewer_identity="model:codex:fresh-incomplete-review",
                    fresh_context_receipt="fresh-incomplete-review",
                    verdict="passed",
                    required_findings=[],
                    advisory_findings=[],
                    dispositions=[],
                    specialist_results=[],
                )
            pack = generate_acceptance_pack(
                runtime["ledger"],
                pack_id="incomplete-two-child-pack",
                integration_ref=INTEGRATION_REF,
                tool_receipt=TOOL_RECEIPT,
                environment=ENVIRONMENT,
                effect_proofs=EFFECT_PROOFS,
                skipped_checks=[],
            )
            readiness = compute_final_readiness(
                runtime["ledger"],
                pack_result=pack,
                integration_ref=INTEGRATION_REF,
                tool_receipt=TOOL_RECEIPT,
            )
            self.assertFalse(readiness["ready"])
            request_id = pack["pack"]["final_request_id"]
            with self.assertRaises(FinalGateError):
                create_final_request(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                    readiness=readiness,
                    integration_ref=INTEGRATION_REF,
                    post_merge_checks=["git diff --check"],
                    author_name="Loop Final Integrator",
                    author_email="loop-final@example.test",
                )
            with self.assertRaises(FinalGateError):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            with self.assertRaises(FinalGateError):
                archive_parent_run(
                    runtime["ledger"], runtime["lease"], request_id=request_id
                )
            self.assertEqual(git(runtime["repo"], "rev-parse", "HEAD"), runtime["base_head"])

    def test_final_review_rejects_incomplete_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = committed_runtime(Path(tmp))
            with self.assertRaisesRegex(FinalReviewError, "complete integrated"):
                record_final_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="too-early",
                    integration_ref=INTEGRATION_REF,
                    reviewer_identity="model:codex:fresh-too-early",
                    fresh_context_receipt="fresh-too-early",
                    verdict="passed",
                    required_findings=[],
                    advisory_findings=[],
                    dispositions=[],
                    specialist_results=[],
                )

    def test_pack_regeneration_is_stable_and_readiness_has_direct_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, first, readiness = ready_runtime(Path(tmp))
            first_json = Path(first["json_path"]).read_bytes()
            first_markdown = Path(first["markdown_path"]).read_bytes()

            second = generate_acceptance_pack(
                runtime["ledger"],
                pack_id="acceptance-1",
                integration_ref=INTEGRATION_REF,
                tool_receipt=TOOL_RECEIPT,
                environment=ENVIRONMENT,
                effect_proofs=list(reversed(EFFECT_PROOFS)),
                skipped_checks=[
                    {
                        "check_id": "remote-ci",
                        "reason": "local acceptance has no remote dependency",
                        "status": "not_applicable",
                    }
                ],
            )

            self.assertEqual(first["pack_digest"], second["pack_digest"])
            self.assertEqual(first_json, Path(second["json_path"]).read_bytes())
            self.assertEqual(first_markdown, Path(second["markdown_path"]).read_bytes())
            self.assertNotIn(b"fence_token", first_json)
            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["predicates"])
            self.assertTrue(
                all(
                    "evidence" in item and item["passed"]
                    for item in readiness["predicates"]
                )
            )
            self.assertEqual(first["pack"]["children"][0]["state"], "integrated")
            self.assertEqual(
                first["pack"]["requirements"][0]["coverage_state"], "covered"
            )
            with self.assertRaisesRegex(FinalReviewError, "without a declared trigger"):
                record_final_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="untriggered-specialist",
                    integration_ref=INTEGRATION_REF,
                    reviewer_identity="model:codex:fresh-untriggered",
                    fresh_context_receipt="fresh-untriggered",
                    verdict="passed",
                    required_findings=[],
                    advisory_findings=[],
                    dispositions=[],
                    specialist_results=[
                        {
                            "evidence_digest": "sha256:untriggered",
                            "specialist": "security_privacy",
                            "status": "passed",
                        }
                    ],
                )

    def test_final_review_is_independent_and_specialists_are_trigger_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = worker_fixture.envelope

            def risky_envelope(base_head: str, dirt_digest: str) -> dict[str, object]:
                value = original(base_head, dirt_digest)
                value["risks"] = [*value["risks"], "security boundary change"]
                return value

            with patch.object(worker_fixture, "envelope", side_effect=risky_envelope):
                runtime = committed_runtime(Path(tmp))
            integrate(runtime)
            common = dict(
                integration_ref=INTEGRATION_REF,
                fresh_context_receipt="fresh-context",
                verdict="passed",
                required_findings=[],
                advisory_findings=[],
                dispositions=[],
            )
            with self.assertRaises(FinalReviewError):
                record_final_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="same-context",
                    reviewer_identity="model:codex",
                    specialist_results=[],
                    **common,
                )
            with self.assertRaisesRegex(
                FinalReviewError, "specialist review is missing"
            ):
                record_final_review(
                    runtime["ledger"],
                    runtime["lease"],
                    review_id="missing-specialist",
                    reviewer_identity="model:codex:fresh-specialist",
                    specialist_results=[],
                    **common,
                )
            passed = record_final_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="triggered-specialist",
                reviewer_identity="model:codex:fresh-specialist",
                specialist_results=[
                    {
                        "evidence_digest": "sha256:security-pass",
                        "specialist": "security_privacy",
                        "status": "passed",
                    }
                ],
                **common,
            )
            self.assertEqual(passed["status"], "reviewed")
            self.assertEqual(
                [
                    item["specialist"]
                    for item in passed["specialists"]
                    if item["applicable"]
                ],
                ["security_privacy"],
            )
            blocked = record_final_review(
                runtime["ledger"],
                runtime["lease"],
                review_id="required-finding",
                reviewer_identity="model:codex:fresh-followup",
                fresh_context_receipt="fresh-followup",
                integration_ref=INTEGRATION_REF,
                verdict="failed",
                affected_requirement_ids=["REQ-1"],
                required_findings=[{"finding_id": "RF-1", "summary": "must fix"}],
                advisory_findings=[],
                dispositions=[],
                specialist_results=[
                    {
                        "evidence_digest": "sha256:security-pass",
                        "specialist": "security_privacy",
                        "status": "passed",
                    }
                ],
            )
            self.assertEqual(blocked["status"], "review_blocked")

    def test_each_named_drift_input_invalidates_nonsticky_readiness(self) -> None:
        scenarios = (
            "base",
            "main",
            "integration",
            "review",
            "pack",
            "projection",
            "receipt",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                runtime, pack, _ = ready_runtime(Path(tmp))
                supplied = pack
                receipt = TOOL_RECEIPT
                if scenario == "base":
                    supplied = copy.deepcopy(pack)
                    supplied["pack"]["git"]["base_head"] = "0" * 40
                    supplied["pack_digest"] = acceptance_runtime._digest_json(
                        supplied["pack"]
                    )
                    Path(supplied["json_path"]).write_text(
                        json.dumps(
                            {
                                "pack": supplied["pack"],
                                "pack_digest": supplied["pack_digest"],
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                elif scenario == "main":
                    (runtime["repo"] / "untracked-drift.txt").write_text(
                        "drift\n", encoding="utf-8"
                    )
                elif scenario == "integration":
                    old = git(runtime["repo"], "rev-parse", INTEGRATION_REF)
                    tree = git(runtime["repo"], "rev-parse", f"{old}^{{tree}}")
                    drift = git(
                        runtime["repo"],
                        "commit-tree",
                        tree,
                        "-p",
                        old,
                        "-m",
                        "integration drift",
                    )
                    git(runtime["repo"], "update-ref", INTEGRATION_REF, drift, old)
                elif scenario == "review":
                    record_final_review(
                        runtime["ledger"],
                        runtime["lease"],
                        review_id="final-review-2",
                        integration_ref=INTEGRATION_REF,
                        reviewer_identity="model:codex:fresh-final-review-2",
                        fresh_context_receipt="fresh-context-receipt-2",
                        verdict="passed",
                        required_findings=[],
                        advisory_findings=[],
                        dispositions=[],
                        specialist_results=[],
                    )
                elif scenario == "pack":
                    Path(pack["json_path"]).write_text("{}\n", encoding="utf-8")
                elif scenario == "projection":
                    (
                        runtime["ledger"].path.parent / "projections" / "summary.json"
                    ).write_text("{}\n", encoding="utf-8")
                else:
                    receipt = "receipt-drift"

                readiness = compute_final_readiness(
                    runtime["ledger"],
                    pack_result=supplied,
                    integration_ref=INTEGRATION_REF,
                    tool_receipt=receipt,
                )
                self.assertFalse(readiness["ready"])
                self.assertTrue(
                    any(not item["passed"] for item in readiness["predicates"])
                )

    def test_final_request_requires_exact_direct_response_and_never_allows_push(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            self.assertEqual(
                request["request"]["allowed_actions"],
                ["local_merge", "parent_archive", "verification"],
            )
            self.assertIn("push", request["request"]["prohibited_external_actions"])
            self.assertEqual(final_request(runtime, pack, readiness), request)
            with self.assertRaises(FinalGateError):
                approve_final_request(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=pack["pack"]["final_request_id"],
                    request_digest=request["request_digest"],
                    pack_result=pack,
                    integration_ref=INTEGRATION_REF,
                    response_identity="not-direct",
                    response_at="2026-07-13T21:00:00Z",
                    direct_user_action=False,
                )
            with self.assertRaises(FinalGateError):
                approve_final_request(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=pack["pack"]["final_request_id"],
                    request_digest="wrong-digest",
                    pack_result=pack,
                    integration_ref=INTEGRATION_REF,
                    response_identity="user:wrong",
                    response_at="2026-07-13T21:00:00Z",
                    direct_user_action=True,
                )
            approved = approve(runtime, pack, request)
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(approve(runtime, pack, request), approved)

    def test_exact_no_ff_merge_then_archive_replays_without_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with self.assertRaises(FinalGateError):
                archive_parent_run(
                    runtime["ledger"], runtime["lease"], request_id=request_id
                )

            merged = execute_final_merge(
                runtime["ledger"],
                runtime["lease"],
                request_id=request_id,
                pack_result=pack,
            )
            self.assertEqual(
                merged["parents"],
                [
                    pack["pack"]["git"]["base_head"],
                    pack["pack"]["git"]["integration_head"],
                ],
            )
            self.assertEqual(
                merged["merge_tree_id"], pack["pack"]["git"]["integration_tree_id"]
            )
            self.assertEqual(
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                ),
                merged,
            )
            archived = archive_parent_run(
                runtime["ledger"], runtime["lease"], request_id=request_id
            )
            self.assertEqual(archived["status"], "archived")
            self.assertEqual(
                archive_parent_run(
                    runtime["ledger"], runtime["lease"], request_id=request_id
                ),
                archived,
            )
            self.assertEqual(
                ledger_rows(runtime, "SELECT status FROM parent_runs")[0]["status"],
                "archived",
            )
            effects = ledger_rows(runtime, "SELECT * FROM effects")
            self.assertEqual(
                [item["classification"] for item in effects], ["local_declared"]
            )
            self.assertFalse(json.loads(effects[0]["outcome_json"])["push"])

    def test_drift_after_approval_invalidates_gate_without_merging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            (runtime["repo"] / "user-dirt.txt").write_text(
                "preserve me\n", encoding="utf-8"
            )
            request_id = pack["pack"]["final_request_id"]
            with self.assertRaises(FinalGateError):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            gate = ledger_rows(
                runtime,
                "SELECT invalidation_reason FROM gate_requests WHERE kind = 'final'",
            )[0]
            self.assertIsNotNone(gate["invalidation_reason"])
            self.assertEqual(
                git(runtime["repo"], "rev-parse", "HEAD"), runtime["base_head"]
            )
            self.assertEqual(
                (runtime["repo"] / "user-dirt.txt").read_text(encoding="utf-8"),
                "preserve me\n",
            )

    def test_crash_after_merge_recovers_without_second_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            original = acceptance_runtime._commit_final_merge_authority
            with patch.object(
                acceptance_runtime,
                "_commit_final_merge_authority",
                side_effect=RuntimeError("crash before acknowledgement"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "crash before acknowledgement"
                ):
                    execute_final_merge(
                        runtime["ledger"],
                        runtime["lease"],
                        request_id=request_id,
                        pack_result=pack,
                    )
            merge_head = git(runtime["repo"], "rev-parse", "HEAD")
            self.assertEqual(
                runtime["ledger"].get_operation(f"final-merge:{request_id}")["phase"],
                "effect_observed",
            )
            with patch.object(
                acceptance_runtime, "_commit_final_merge_authority", original
            ):
                recovered = execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            self.assertEqual(recovered["merge_head"], merge_head)
            self.assertEqual(git(runtime["repo"], "rev-parse", "HEAD"), merge_head)

    def test_paused_resume_recovers_retained_final_merge(self) -> None:
        cases = (
            ("prepared", "same_branch"),
            ("effect_observed", "same_branch"),
            ("prepared", "published_descendant"),
            ("effect_observed", "published_descendant"),
        )
        for window, topology in cases:
            with (
                self.subTest(window=window, topology=topology),
                tempfile.TemporaryDirectory() as tmp,
            ):
                runtime, pack, readiness = ready_runtime(Path(tmp))
                request = final_request(runtime, pack, readiness)
                approve(runtime, pack, request)
                request_id = pack["pack"]["final_request_id"]
                target = (
                    runtime["ledger"]
                    if window == "prepared"
                    else acceptance_runtime
                )
                attribute = (
                    "advance_operation"
                    if window == "prepared"
                    else "_commit_final_merge_authority"
                )
                with (
                    patch.object(
                        target,
                        attribute,
                        side_effect=RuntimeError(f"crash in {window}"),
                    ),
                    self.assertRaisesRegex(RuntimeError, f"crash in {window}"),
                ):
                    execute_final_merge(
                        runtime["ledger"],
                        runtime["lease"],
                        request_id=request_id,
                        pack_result=pack,
                    )
                merge_head = git(runtime["repo"], "rev-parse", "HEAD")
                publication_head = merge_head
                if topology == "published_descendant":
                    git(
                        runtime["repo"],
                        "checkout",
                        "-b",
                        "published-main",
                        f"{merge_head}^1",
                    )
                    git(
                        runtime["repo"],
                        "merge",
                        "--no-ff",
                        merge_head,
                        "-m",
                        "transparent publication wrapper",
                    )
                    publication_head = git(runtime["repo"], "rev-parse", "HEAD")
                    (runtime["repo"] / "docs").mkdir()
                    (runtime["repo"] / "docs/runtime.md").write_text(
                        "later non-overlapping runtime repair\n",
                        encoding="utf-8",
                    )
                    git(runtime["repo"], "add", "docs/runtime.md")
                    git(
                        runtime["repo"],
                        "commit",
                        "-m",
                        "later non-overlapping runtime repair",
                    )
                    (runtime["repo"] / "recovery-note.txt").write_text(
                        "preserved recovery evidence\n",
                        encoding="utf-8",
                    )
                else:
                    git(
                        runtime["repo"],
                        "commit",
                        "--allow-empty",
                        "-m",
                        "transparent publication wrapper",
                    )
                    (runtime["repo"] / ".gitignore").write_text(
                        ".trellis/.runtime/\nlocal-recovery-output/\n",
                        encoding="utf-8",
                    )
                retained_head = git(runtime["repo"], "rev-parse", "HEAD")
                retained_dirt = scan_repository_dirt(runtime["repo"])
                pause_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="pause-final-merge-recovery",
                    reason="recover retained final merge",
                    requested_by="test",
                    requires_human_resume=False,
                )
                safe_point = resume_safe_point_status(runtime["ledger"])
                self.assertTrue(safe_point["eligible"])
                self.assertEqual(
                    safe_point["unresolved_operations"],
                    [
                        {
                            "epoch": 1,
                            "kind": "final_local_merge",
                            "operation_id": f"final-merge:{request_id}",
                            "phase": window,
                        }
                    ],
                )

                resumed, new_lease = resume_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="resume-final-merge-recovery",
                    new_writer_id="final-merge-recovery-writer",
                    authority_identity="user:final-merge-recovery",
                    direct_user_action=True,
                    tool_receipt=TOOL_RECEIPT,
                    available_resources=["repo"],
                )

                self.assertEqual(resumed["status"], "resumed")
                self.assertEqual(
                    resumed["reconciliation"]["actions"],
                    [
                        {
                            "operation_id": f"final-merge:{request_id}",
                            "result": "final_merged",
                        }
                    ],
                )
                recovered = execute_final_merge(
                    runtime["ledger"],
                    new_lease,
                    request_id=request_id,
                    pack_result=pack,
                )
                self.assertEqual(recovered["merge_head"], merge_head)
                self.assertEqual(recovered["publication_head"], publication_head)
                self.assertEqual(recovered["retained_main_head"], retained_head)
                self.assertEqual(
                    recovered["descendant_drift_paths"],
                    ["docs/runtime.md"]
                    if topology == "published_descendant"
                    else [],
                )
                self.assertEqual(
                    recovered["retained_dirt_digest"],
                    retained_dirt["digest"],
                )
                archived = archive_parent_run(
                    runtime["ledger"],
                    new_lease,
                    request_id=request_id,
                )
                self.assertEqual(archived["status"], "archived")
                self.assertEqual(
                    scan_repository_dirt(runtime["repo"])["digest"],
                    retained_dirt["digest"],
                )

    def test_paused_resume_accepts_exact_strict_qualification_rotation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = qualification_rotation_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with (
                patch.object(
                    runtime["ledger"],
                    "advance_operation",
                    side_effect=RuntimeError("crash after merge effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after merge effect"),
            ):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            merge_head = git(runtime["repo"], "rev-parse", "HEAD")
            git(
                runtime["repo"],
                "checkout",
                "-b",
                "published-main",
                f"{merge_head}^1",
            )
            git(
                runtime["repo"],
                "merge",
                "--no-ff",
                merge_head,
                "-m",
                "transparent publication wrapper",
            )
            publication_head = git(runtime["repo"], "rev-parse", "HEAD")
            (runtime["repo"] / "docs").mkdir(exist_ok=True)
            (runtime["repo"] / "docs/runtime-repair.md").write_text(
                "qualified runtime predecessor\n",
                encoding="utf-8",
            )
            git(runtime["repo"], "add", "docs/runtime-repair.md")
            git(runtime["repo"], "commit", "-m", "runtime repair predecessor")
            first_qualified_runtime_commit = git(
                runtime["repo"], "rev-parse", "HEAD"
            )
            first_receipt = generate_conformance_receipt(
                runtime["repo"],
                qualification_fixture.qualification_result(runtime["repo"]),
                runtime["repo"] / ".trellis/spec/project/receipts/loop-v1",
                runtime_commit=first_qualified_runtime_commit,
            )
            config = runtime["repo"] / ".trellis/config.yaml"
            prior = config.read_text(encoding="utf-8")
            historical_digest = runtime["tool_receipt"].removeprefix("sha256:")
            config.write_text(
                prior.replace(
                    f"qualification_receipt: "
                    f".trellis/spec/project/receipts/loop-v1/"
                    f"{historical_digest}.json",
                    f"qualification_receipt: "
                    f".trellis/spec/project/receipts/loop-v1/"
                    f"{first_receipt.stem}.json",
                ).replace(
                    f"qualification_receipt_digest: {historical_digest}",
                    f"qualification_receipt_digest: {first_receipt.stem}",
                ),
                encoding="utf-8",
            )
            first_relative_receipt = first_receipt.relative_to(
                runtime["repo"]
            ).as_posix()
            git(
                runtime["repo"],
                "add",
                ".trellis/config.yaml",
                first_relative_receipt,
            )
            (runtime["repo"] / "docs/rotation-evidence.md").write_text(
                "non-overlapping binding evidence\n",
                encoding="utf-8",
            )
            git(runtime["repo"], "add", "docs/rotation-evidence.md")
            git(runtime["repo"], "commit", "-m", "bind strict qualification")
            first_binding_commit = git(runtime["repo"], "rev-parse", "HEAD")
            self.assertEqual(
                git(
                    runtime["repo"],
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    first_binding_commit,
                ).splitlines(),
                [
                    "M\t.trellis/config.yaml",
                    f"A\t{first_relative_receipt}",
                    "A\tdocs/rotation-evidence.md",
                ],
            )
            (runtime["repo"] / "docs/runtime-repair-2.md").write_text(
                "second qualified runtime predecessor\n",
                encoding="utf-8",
            )
            git(runtime["repo"], "add", "docs/runtime-repair-2.md")
            git(runtime["repo"], "commit", "-m", "second runtime repair predecessor")
            qualified_runtime_commit = git(runtime["repo"], "rev-parse", "HEAD")
            receipt = generate_conformance_receipt(
                runtime["repo"],
                qualification_fixture.qualification_result(runtime["repo"]),
                runtime["repo"] / ".trellis/spec/project/receipts/loop-v1",
                runtime_commit=qualified_runtime_commit,
            )
            config.write_text(
                config.read_text(encoding="utf-8")
                .replace(
                    f"qualification_receipt: {first_relative_receipt}",
                    "qualification_receipt: "
                    f"{receipt.relative_to(runtime['repo']).as_posix()}",
                )
                .replace(
                    f"qualification_receipt_digest: {first_receipt.stem}",
                    f"qualification_receipt_digest: {receipt.stem}",
                ),
                encoding="utf-8",
            )
            relative_receipt = receipt.relative_to(runtime["repo"]).as_posix()
            git(
                runtime["repo"],
                "add",
                ".trellis/config.yaml",
                relative_receipt,
            )
            git(runtime["repo"], "commit", "-m", "bind second strict qualification")
            binding_commit = git(runtime["repo"], "rev-parse", "HEAD")
            self.assertEqual(
                git(
                    runtime["repo"],
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    binding_commit,
                ).splitlines(),
                [
                    "M\t.trellis/config.yaml",
                    f"A\t{relative_receipt}",
                ],
            )
            for purpose in (SOURCE_DEVELOPMENT_PURPOSE, "source_release"):
                status = configured_qualification(
                    runtime["repo"],
                    purpose=purpose,
                )
                self.assertTrue(status.valid, status.issues)
                self.assertEqual(status.receipt_id, f"sha256:{receipt.stem}")
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-qualified-rotation",
                reason="recover exact qualification rotation",
                requested_by="test",
                requires_human_resume=False,
            )
            authority = runtime["ledger"].authority_digest()
            git(runtime["repo"], "checkout", "-b", "third-overlap")
            (runtime["repo"] / "src/third-overlap.txt").write_text(
                "unproved third overlap\n",
                encoding="utf-8",
            )
            git(runtime["repo"], "add", "src/third-overlap.txt")
            git(runtime["repo"], "commit", "-m", "unproved third overlap")

            rejected = resume_safe_point_status(runtime["ledger"])

            self.assertEqual(
                rejected["failed_predicates"],
                ["unresolved_operations_reconcilable"],
            )
            self.assertFalse(rejected["eligible"])
            self.assertEqual(runtime["ledger"].authority_digest(), authority)
            self.assertIsNone(
                runtime["ledger"].get_operation(
                    "resume-preflight:resume-qualified-rotation"
                )
            )
            self.assertIsNone(
                runtime["ledger"].get_operation(f"parent-archive:{request_id}")
            )
            git(runtime["repo"], "checkout", "published-main")

            safe_point = resume_safe_point_status(runtime["ledger"])

            self.assertEqual(safe_point["failed_predicates"], [])
            self.assertTrue(safe_point["eligible"])
            proof = safe_point["qualification_rotation_proof"]
            self.assertEqual(proof["publication_head"], publication_head)
            self.assertEqual(proof["binding_commit"], binding_commit)
            self.assertEqual(
                proof["binding_commits"],
                [first_binding_commit, binding_commit],
            )
            self.assertEqual(
                [
                    item["qualified_runtime_commit"]
                    for item in proof["qualification_rotation_chain"]
                ],
                [first_qualified_runtime_commit, qualified_runtime_commit],
            )
            self.assertEqual(
                proof["qualification_rotation_chain"][0][
                    "attached_non_overlapping_changes"
                ],
                [{"path": "docs/rotation-evidence.md", "status": "A"}],
            )
            self.assertEqual(
                proof["mixed_commit_non_overlapping_paths"],
                ["docs/rotation-evidence.md"],
            )
            self.assertEqual(
                proof["qualified_runtime_commit"],
                qualified_runtime_commit,
            )
            self.assertEqual(
                proof["execution_receipt_id"],
                runtime["tool_receipt"],
            )
            self.assertEqual(
                safe_point["qualification_rotation_proof_digest"],
                proof["proof_digest"],
            )
            self.assertEqual(runtime["ledger"].authority_digest(), authority)
            merge_count = git(
                runtime["repo"],
                "rev-list",
                "--merges",
                "--count",
                "HEAD",
            )

            resumed, new_lease = resume_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="resume-qualified-rotation",
                new_writer_id="qualified-rotation-writer",
                authority_identity="user:qualified-rotation",
                direct_user_action=True,
                tool_receipt=runtime["tool_receipt"],
                available_resources=["repo"],
            )

            self.assertEqual(resumed["status"], "resumed")
            self.assertEqual(
                resumed["qualification_rotation_proof_digest"],
                proof["proof_digest"],
            )
            recovered = execute_final_merge(
                runtime["ledger"],
                new_lease,
                request_id=request_id,
                pack_result=pack,
            )
            self.assertEqual(recovered["qualification_rotation_proof"], proof)
            self.assertEqual(
                recovered["qualification_rotation_proof_digest"],
                proof["proof_digest"],
            )
            archived = archive_parent_run(
                runtime["ledger"],
                new_lease,
                request_id=request_id,
            )
            self.assertEqual(archived["status"], "archived")
            self.assertEqual(
                archive_parent_run(
                    runtime["ledger"],
                    new_lease,
                    request_id=request_id,
                ),
                archived,
            )
            self.assertEqual(
                git(runtime["repo"], "rev-list", "--merges", "--count", "HEAD"),
                merge_count,
            )
            config.write_text(
                config.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(FinalMergeError):
                archive_parent_run(
                    runtime["ledger"],
                    new_lease,
                    request_id=request_id,
                )

    def test_qualification_rotation_proof_is_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = minimal_qualification_rotation_repo(Path(tmp))
            status = QualificationStatus(
                True,
                True,
                fixture["current_receipt_id"],
                (),
            )
            head = git(fixture["repo"], "rev-parse", "HEAD")
            dirt = git(fixture["repo"], "status", "--short")
            with (
                patch.object(
                    qualification_runtime,
                    "verify_conformance_receipt",
                    return_value={
                        "issues": [],
                        "receipt_id": fixture["current_receipt_id"],
                        "valid": True,
                    },
                ),
                patch.object(
                    qualification_runtime,
                    "configured_qualification",
                    return_value=status,
                ),
            ):
                proof = acceptance_runtime._qualification_rotation_proof(
                    fixture["repo"],
                    publication_head=fixture["publication_head"],
                    retained_main_head=fixture["retained_main_head"],
                    execution_binding=fixture["binding"],
                    allowed_touches=(
                        ".trellis/config.yaml",
                        ".trellis/spec/project/receipts/loop-v1/**",
                    ),
                )
                replay = acceptance_runtime._qualification_rotation_proof(
                    fixture["repo"],
                    publication_head=fixture["publication_head"],
                    retained_main_head=fixture["retained_main_head"],
                    execution_binding=fixture["binding"],
                    allowed_touches=(
                        ".trellis/config.yaml",
                        ".trellis/spec/project/receipts/loop-v1/**",
                    ),
                )

            self.assertEqual(proof, replay)
            self.assertEqual(
                proof["kind"],
                "source_release_qualification_rotation",
            )
            self.assertEqual(
                proof["contract_version"],
                "qualification_rotation_chain_v2",
            )
            self.assertEqual(
                proof["qualified_runtime_commit"],
                fixture["qualified_runtime_commit"],
            )
            self.assertEqual(proof["binding_commits"], [fixture["binding_commit"]])
            self.assertEqual(
                len(proof["qualification_rotation_chain"]),
                1,
            )
            self.assertEqual(
                proof["qualification_rotation_chain"][0]["from_receipt_id"],
                fixture["binding"]["receipt_id"],
            )
            self.assertEqual(
                proof["qualification_rotation_chain"][0]["to_receipt_id"],
                fixture["current_receipt_id"],
            )
            self.assertEqual(
                proof["qualification_rotation_paths"],
                [
                    ".trellis/config.yaml",
                    fixture["current_receipt_id"]
                    .replace(
                        "sha256:",
                        ".trellis/spec/project/receipts/loop-v1/",
                    )
                    + ".json",
                ],
            )
            self.assertEqual(git(fixture["repo"], "rev-parse", "HEAD"), head)
            self.assertEqual(git(fixture["repo"], "status", "--short"), dirt)

    def test_qualification_rotation_proof_classifies_mixed_commit_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = minimal_qualification_rotation_repo(
                Path(tmp),
                case="mixed_commit",
            )
            status = QualificationStatus(
                True,
                True,
                fixture["current_receipt_id"],
                (),
            )
            direct = {
                "issues": [],
                "receipt_id": fixture["current_receipt_id"],
                "valid": True,
            }
            with (
                patch.object(
                    qualification_runtime,
                    "verify_conformance_receipt",
                    return_value=direct,
                ),
                patch.object(
                    qualification_runtime,
                    "configured_qualification",
                    return_value=status,
                ),
            ):
                proof = acceptance_runtime._qualification_rotation_proof(
                    fixture["repo"],
                    publication_head=fixture["publication_head"],
                    retained_main_head=fixture["retained_main_head"],
                    execution_binding=fixture["binding"],
                    allowed_touches=(
                        ".trellis/config.yaml",
                        ".trellis/spec/project/receipts/loop-v1/**",
                    ),
                )
                with self.assertRaises(FinalMergeError):
                    acceptance_runtime._qualification_rotation_proof(
                        fixture["repo"],
                        publication_head=fixture["publication_head"],
                        retained_main_head=fixture["retained_main_head"],
                        execution_binding=fixture["binding"],
                        allowed_touches=(
                            ".trellis/config.yaml",
                            ".trellis/spec/project/receipts/loop-v1/**",
                            "docs/**",
                        ),
                    )

            self.assertEqual(
                proof["qualification_rotation_chain"][0][
                    "attached_non_overlapping_changes"
                ],
                [{"path": "docs/evidence.md", "status": "A"}],
            )
            self.assertEqual(
                proof["mixed_commit_non_overlapping_paths"],
                ["docs/evidence.md"],
            )

    def test_qualification_rotation_proof_rejects_noncanonical_variants(
        self,
    ) -> None:
        cases = (
            "config_comment",
            "config_other_key",
            "multiple_binding_commits",
            "multiple_receipts",
            "old_receipt_modified",
            "malformed_receipt",
            "identity_mismatch",
            "wrong_predecessor",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                fixture = minimal_qualification_rotation_repo(
                    Path(tmp),
                    case=case,
                )
                status = QualificationStatus(
                    True,
                    True,
                    fixture["current_receipt_id"],
                    (),
                )
                head = git(fixture["repo"], "rev-parse", "HEAD")
                dirt = git(fixture["repo"], "status", "--short")
                with (
                    patch.object(
                        qualification_runtime,
                        "verify_conformance_receipt",
                        return_value={
                            "issues": [],
                            "receipt_id": fixture["current_receipt_id"],
                            "valid": True,
                        },
                    ),
                    patch.object(
                        qualification_runtime,
                        "configured_qualification",
                        return_value=status,
                    ),
                    self.assertRaises(FinalMergeError),
                ):
                    acceptance_runtime._qualification_rotation_proof(
                        fixture["repo"],
                        publication_head=fixture["publication_head"],
                        retained_main_head=fixture["retained_main_head"],
                        execution_binding=fixture["binding"],
                        allowed_touches=(
                            ".trellis/config.yaml",
                            ".trellis/spec/project/receipts/loop-v1/**",
                        ),
                    )
                self.assertEqual(git(fixture["repo"], "rev-parse", "HEAD"), head)
                self.assertEqual(git(fixture["repo"], "status", "--short"), dirt)

    def test_qualification_rotation_proof_rejects_verification_disagreement(
        self,
    ) -> None:
        cases = ("invalid_strict", "purpose_disagreement", "verification_error")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                fixture = minimal_qualification_rotation_repo(Path(tmp))
                valid = QualificationStatus(
                    True,
                    True,
                    fixture["current_receipt_id"],
                    (),
                )

                def configured(
                    _repo: Path,
                    *,
                    purpose: str,
                    **_kwargs: object,
                ) -> QualificationStatus:
                    if case == "purpose_disagreement" and purpose == "source_release":
                        return QualificationStatus(
                            True,
                            False,
                            fixture["current_receipt_id"],
                            ("source release differs",),
                        )
                    return valid

                direct = {
                    "issues": ["strict receipt invalid"]
                    if case == "invalid_strict"
                    else [],
                    "receipt_id": fixture["current_receipt_id"],
                    "valid": case != "invalid_strict",
                }
                with (
                    patch.object(
                        qualification_runtime,
                        "verify_conformance_receipt",
                        return_value=direct,
                        side_effect=(
                            qualification_runtime.QualificationError(
                                "strict verification failed"
                            )
                            if case == "verification_error"
                            else None
                        ),
                    ),
                    patch.object(
                        qualification_runtime,
                        "configured_qualification",
                        side_effect=configured,
                    ),
                    self.assertRaises(FinalMergeError),
                ):
                    acceptance_runtime._qualification_rotation_proof(
                        fixture["repo"],
                        publication_head=fixture["publication_head"],
                        retained_main_head=fixture["retained_main_head"],
                        execution_binding=fixture["binding"],
                        allowed_touches=(
                            ".trellis/config.yaml",
                            ".trellis/spec/project/receipts/loop-v1/**",
                        ),
                    )

    def test_qualification_rotation_replay_rejects_config_or_receipt_drift(
        self,
    ) -> None:
        for target in ("config", "receipt"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                fixture = minimal_qualification_rotation_repo(Path(tmp))
                status = QualificationStatus(
                    True,
                    True,
                    fixture["current_receipt_id"],
                    (),
                )
                direct = {
                    "issues": [],
                    "receipt_id": fixture["current_receipt_id"],
                    "valid": True,
                }
                with (
                    patch.object(
                        qualification_runtime,
                        "verify_conformance_receipt",
                        return_value=direct,
                    ),
                    patch.object(
                        qualification_runtime,
                        "configured_qualification",
                        return_value=status,
                    ),
                ):
                    proof = acceptance_runtime._qualification_rotation_proof(
                        fixture["repo"],
                        publication_head=fixture["publication_head"],
                        retained_main_head=fixture["retained_main_head"],
                        execution_binding=fixture["binding"],
                        allowed_touches=(
                            ".trellis/config.yaml",
                            ".trellis/spec/project/receipts/loop-v1/**",
                        ),
                    )
                    path = (
                        fixture["repo"] / ".trellis/config.yaml"
                        if target == "config"
                        else fixture["repo"]
                        / proof["qualification_rotation_paths"][1]
                    )
                    path.write_text(
                        path.read_text(encoding="utf-8") + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(FinalMergeError):
                        acceptance_runtime._assert_qualification_rotation_proof(
                            fixture["repo"],
                            proof,
                            execution_binding=fixture["binding"],
                        )

    def test_paused_resume_blocks_new_dirt_from_post_merge_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(
                runtime,
                pack,
                readiness,
                checks=[
                    "if [ -e post-check-marker.txt ]; then "
                    "touch post-check-dirt.txt; else touch post-check-marker.txt; fi"
                ],
            )
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with (
                patch.object(
                    runtime["ledger"],
                    "advance_operation",
                    side_effect=RuntimeError("crash after merge effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after merge effect"),
            ):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-check-dirt",
                reason="reject post-check dirt mutation",
                requested_by="test",
                requires_human_resume=False,
            )
            resumed, _ = resume_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="resume-check-dirt",
                new_writer_id="check-dirt-recovery-writer",
                authority_identity="user:check-dirt-recovery",
                direct_user_action=True,
                tool_receipt=TOOL_RECEIPT,
                available_resources=["repo"],
            )
            self.assertEqual(resumed["status"], "blocked")
            self.assertIn(
                "post-merge verification did not pass",
                resumed["reconciliation"]["unresolved"][0]["reason"],
            )

    def test_paused_resume_keeps_semantic_final_merge_drift_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with (
                patch.object(
                    runtime["ledger"],
                    "advance_operation",
                    side_effect=RuntimeError("crash after merge effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after merge effect"),
            ):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            (runtime["repo"] / "src/app.txt").write_text(
                "semantic drift\n", encoding="utf-8"
            )
            git(runtime["repo"], "add", "src/app.txt")
            git(runtime["repo"], "commit", "-m", "semantic drift")
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-semantic-drift",
                reason="reject semantic final merge drift",
                requested_by="test",
                requires_human_resume=False,
            )

            safe_point = resume_safe_point_status(runtime["ledger"])
            authority = runtime["ledger"].authority_digest()
            self.assertFalse(safe_point["eligible"])
            self.assertEqual(
                safe_point["failed_predicates"],
                ["unresolved_operations_reconcilable"],
            )
            with self.assertRaises(DirtOverlapError):
                resume_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="resume-semantic-drift",
                    new_writer_id="semantic-drift-recovery-writer",
                    authority_identity="user:semantic-drift-recovery",
                    direct_user_action=True,
                    tool_receipt=TOOL_RECEIPT,
                    available_resources=["repo"],
                )
            self.assertEqual(runtime["ledger"].authority_digest(), authority)
            self.assertEqual(
                ledger_rows(runtime, "SELECT status FROM parent_runs")[0]["status"],
                "paused",
            )

    def test_resume_rejects_qualification_proof_drift_after_fence_rotation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with (
                patch.object(
                    runtime["ledger"],
                    "advance_operation",
                    side_effect=RuntimeError("crash after merge effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after merge effect"),
            ):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-proof-drift",
                reason="reject proof drift",
                requested_by="test",
                requires_human_resume=False,
            )
            original = integration_runtime._resume_safe_point
            fake_proof = {
                "kind": "source_release_qualification_rotation",
                "proof_digest": "sha256:" + "9" * 64,
            }

            def drifted_safe_point(
                ledger: ParentLedger,
                envelope: dict[str, object],
            ) -> dict[str, object]:
                safe = original(ledger, envelope)
                safe["projection"]["qualification_rotation_proof"] = fake_proof
                safe["projection"][
                    "qualification_rotation_proof_digest"
                ] = fake_proof["proof_digest"]
                return safe

            merge_count = git(
                runtime["repo"],
                "rev-list",
                "--merges",
                "--count",
                "HEAD",
            )
            with patch.object(
                integration_runtime,
                "_resume_safe_point",
                side_effect=drifted_safe_point,
            ):
                resumed, new_lease = resume_parent(
                    runtime["ledger"],
                    runtime["lease"],
                    operation_id="resume-proof-drift",
                    new_writer_id="proof-drift-writer",
                    authority_identity="user:proof-drift",
                    direct_user_action=True,
                    tool_receipt=TOOL_RECEIPT,
                    available_resources=["repo"],
                )

            self.assertEqual(new_lease.epoch, runtime["lease"].epoch + 1)
            self.assertEqual(resumed["status"], "blocked")
            self.assertIn(
                "qualification rotation proof changed",
                resumed["reconciliation"]["unresolved"][0]["reason"],
            )
            preflight = runtime["ledger"].get_operation(
                "resume-preflight:resume-proof-drift"
            )
            self.assertEqual(
                preflight["outcome"]["qualification_rotation_proof_digest"],
                fake_proof["proof_digest"],
            )
            self.assertEqual(
                runtime["ledger"].get_operation(f"final-merge:{request_id}")["phase"],
                "prepared",
            )
            self.assertIsNone(
                runtime["ledger"].get_operation(f"parent-archive:{request_id}")
            )
            self.assertEqual(
                git(runtime["repo"], "rev-list", "--merges", "--count", "HEAD"),
                merge_count,
            )

    def test_paused_resume_rejects_branch_change_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(runtime, pack, readiness)
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with (
                patch.object(
                    runtime["ledger"],
                    "advance_operation",
                    side_effect=RuntimeError("crash after merge effect"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash after merge effect"),
            ):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            git(runtime["repo"], "branch", "-m", "renamed-without-publication")
            pause_parent(
                runtime["ledger"],
                runtime["lease"],
                operation_id="pause-branch-change",
                reason="reject unproved branch change",
                requested_by="test",
                requires_human_resume=False,
            )
            authority = runtime["ledger"].authority_digest()

            safe_point = resume_safe_point_status(runtime["ledger"])

            self.assertFalse(safe_point["eligible"])
            self.assertEqual(
                safe_point["failed_predicates"],
                ["unresolved_operations_reconcilable"],
            )
            self.assertEqual(runtime["ledger"].authority_digest(), authority)

    def test_smoke_failure_preserves_merge_and_blocks_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, pack, readiness = ready_runtime(Path(tmp))
            request = final_request(
                runtime, pack, readiness, checks=["test -f definitely-missing"]
            )
            approve(runtime, pack, request)
            request_id = pack["pack"]["final_request_id"]
            with self.assertRaisesRegex(FinalMergeError, "is preserved"):
                execute_final_merge(
                    runtime["ledger"],
                    runtime["lease"],
                    request_id=request_id,
                    pack_result=pack,
                )
            merge_head = git(runtime["repo"], "rev-parse", "HEAD")
            self.assertNotEqual(merge_head, runtime["base_head"])
            self.assertEqual(
                runtime["ledger"].get_operation(f"final-merge:{request_id}")["phase"],
                "effect_observed",
            )
            with self.assertRaises(FinalGateError):
                archive_parent_run(
                    runtime["ledger"], runtime["lease"], request_id=request_id
                )
            self.assertEqual(
                ledger_rows(runtime, "SELECT status FROM parent_runs")[0]["status"],
                "authorized",
            )


class LoopV1CrossLayerContractTests(unittest.TestCase):
    def test_public_contracts_help_and_overlay_ownership_agree(self) -> None:
        repo_root = SCRIPT_DIR.parents[1]
        required_terms = {
            "README.md": (
                "source_development",
                "source_release",
                "installed_runtime",
                "recovery_waiting",
                "continue-recovery",
                "source_release_qualification_rotation",
                "qualification_rotation_chain_v2",
                "single_user",
                "strict",
            ),
            "HANDOFF.md": (
                "source_release",
                "installed_runtime",
                "canonical source checkout is unavailable",
                "source_release_qualification_rotation",
                "qualification_rotation_chain_v2",
                "single_user",
                "strict",
            ),
            ".trellis/spec/project/loop-v1-admission.md": (
                "source_development",
                "source_release",
                "installed_runtime",
                "single_user",
                "strict",
            ),
            ".trellis/spec/project/loop-v1-qualification.md": (
                "source_development",
                "source_release",
                "installed_runtime",
                "recovery_waiting",
                "source_release_qualification_rotation",
                "qualification_rotation_chain_v2",
            ),
            ".trellis/spec/project/loop-v1-runtime.md": (
                "execution_base",
                "recovery_waiting",
                "continue-recovery",
                "local_command_authority",
                "source_release_qualification_rotation",
                "qualification_rotation_chain_v2",
                "single_user",
                "strict",
            ),
            ".trellis/spec/project/downstream-deployer.md": (
                "source_release",
                "installed_runtime",
                "canonical source checkout is unavailable",
            ),
        }
        for relative, terms in required_terms.items():
            with self.subTest(contract=relative):
                contents = (repo_root / relative).read_text(encoding="utf-8")
                normalized = " ".join(contents.split())
                for term in terms:
                    self.assertIn(term, normalized)

        commands = {
            "orchestrator": (
                "loop_v1.orchestrator",
                ("single-user", "strict", "recovery_waiting", "continue-recovery"),
            ),
            "qualification": (
                "loop_v1.qualification",
                ("source_development", "source_release", "installed_runtime"),
            ),
            "downstream": (
                "downstream_deployer.cli",
                ("installed_runtime", "activation", "canonical source checkout"),
            ),
        }
        for label, (module, terms) in commands.items():
            with self.subTest(help=label):
                completed = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                normalized = " ".join(completed.stdout.split())
                for term in terms:
                    self.assertIn(term, normalized)

        manifest = json.loads(
            (
                repo_root
                / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            ).read_text(encoding="utf-8")
        )
        owners = {
            entry["path"]: entry["owner"]
            for entry in manifest["entries"]
        }
        self.assertEqual(owners["README.md"], "project")
        self.assertEqual(owners["HANDOFF.md"], "project")
        for relative in (
            ".trellis/scripts/tests/test_loop_v1_acceptance.py",
            ".trellis/spec/project/downstream-deployer.md",
            ".trellis/spec/project/loop-v1-admission.md",
            ".trellis/spec/project/loop-v1-qualification.md",
            ".trellis/spec/project/loop-v1-runtime.md",
        ):
            self.assertEqual(owners[relative], "overlay")


if __name__ == "__main__":
    unittest.main()
