from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from downstream_deployer import planning as deployer_planning
from loop_v1 import qualification as qualification_module
from loop_v1.qualification import (
    generate_artifact_qualification_receipt,
    generate_local_runtime_receipt,
    operation_qualification,
)
from test_loop_v1_qualification import (
    copy_path,
    git,
    prepare_receipt_repo,
    qualification_result,
)


def prepare_downstream_runtime(base: Path) -> tuple[Path, Path]:
    source = base / "canonical-source"
    source.mkdir()
    prepare_receipt_repo(source)
    artifact = generate_artifact_qualification_receipt(
        source,
        qualification_result(source),
        base / "artifact-receipts",
    )

    target = base / "downstream-project"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))
    deployer_planning._materialize_official(target)
    manifest = qualification_module.load_overlay_manifest(
        source / qualification_module.MANIFEST_PATH
    )
    for entry in manifest["entries"]:
        if entry["owner"] == "overlay":
            copy_path(source, target, Path(entry["path"]))
    copy_path(source, target, Path(".trellis/.version"))
    for backup in (target / ".trellis").glob(".backup-*"):
        shutil.rmtree(backup)
    receipts = target / ".trellis/spec/project/receipts/loop-v1"
    receipts.mkdir(parents=True, exist_ok=True)
    local_artifact = receipts / artifact.name
    shutil.copy2(artifact, local_artifact)
    local = generate_local_runtime_receipt(target, local_artifact, receipts)
    (target / ".trellis/config.yaml").write_text(
        "qualification_layers:\n"
        "  admission_mode: installed_runtime\n"
        f"  artifact_receipt: {local_artifact.relative_to(target).as_posix()}\n"
        f"  artifact_receipt_digest: {local_artifact.stem}\n"
        f"  local_runtime_receipt: {local.relative_to(target).as_posix()}\n"
        f"  local_runtime_receipt_digest: {local.stem}\n"
        "loop_v1:\n"
        "  admission_enabled: true\n"
        "  parent_default: loop_v1\n"
        "  runtime_mode: downstream_project\n",
        encoding="utf-8",
    )
    (target / ".gitignore").write_text(
        ".trellis/.runtime/\n.trellis/workspace/\nBOARD.md\n",
        encoding="utf-8",
    )
    (target / "src").mkdir(exist_ok=True)
    (target / "src/app.txt").write_text("base\n", encoding="utf-8")
    (target / "project.txt").write_text("project base\n", encoding="utf-8")
    git(target, "init", "-q", "-b", "main")
    git(target, "config", "user.name", "Downstream Runtime Test")
    git(target, "config", "user.email", "downstream@example.invalid")
    git(target, "add", ".")
    git(target, "commit", "-q", "-m", "fixture: installed runtime")
    return source, target


_OFFLINE_FLOW = textwrap.dedent(
    """
    import json
    import os
    import runpy
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    target = Path(os.environ["DOWNSTREAM_ROOT"]).resolve()
    source = Path(os.environ["OFFLINE_SOURCE"]).resolve()
    flow_root = Path(os.environ["FLOW_ROOT"]).resolve()

    def guard_source_open(event, args):
        if event != "open" or not args or not isinstance(args[0], (str, bytes)):
            return
        opened = Path(os.path.abspath(os.fsdecode(args[0])))
        if opened == source or source in opened.parents:
            raise RuntimeError("canonical source path was opened")

    sys.addaudithook(guard_source_open)
    sys.argv = [
        str(target / ".trellis/scripts/task.py"),
        "create",
        "Offline downstream parent",
        "--slug",
        "offline-downstream-parent",
        "--tier",
        "parent",
        "--workflow-mode",
        "loop_v1",
        "--assignee",
        "jym",
    ]
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise

    import test_loop_v1_worker_commit as fixture
    from loop_v1 import (
        ParentLedger,
        accept_child_result,
        acknowledge_integration,
        advance_integration_ref,
        approve_start_request,
        build_integration_candidate,
        cancel_parent,
        commit_reviewed_candidate,
        create_child_worktree,
        create_start_request,
        issue_child_packet,
        observe_worker_candidate,
        prepare_integration_candidate,
        record_context_revision,
        record_precommit_review,
        scan_repository_dirt,
        schedule_ready,
        validate_child_candidate,
    )
    from loop_v1.qualification import operation_qualification
    from test_loop_v1_integration import INTEGRATION_REF

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(target), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    qualification = operation_qualification(target)
    if not qualification.valid:
        raise AssertionError(qualification.issues)
    base_head = git("rev-parse", "HEAD")
    base_tree = git("rev-parse", "HEAD^{tree}")
    dirt = scan_repository_dirt(target)
    ledger, lease = ParentLedger.initialize(
        target,
        "offline-downstream-run",
        selector="loop_v1",
        writer_id="offline-downstream-writer",
    )
    envelope = fixture.envelope(base_head, dirt["digest"])
    envelope["conformance_receipt"] = qualification.receipt_id
    request = create_start_request(
        ledger,
        lease,
        request_id="offline-start",
        envelope=envelope,
    )
    approve_start_request(
        ledger,
        lease,
        request_id="offline-start",
        request_digest=request["request_digest"],
        response_identity="user:offline-start",
        response_at="2026-07-25T18:00:00Z",
        direct_user_action=True,
    )
    record_context_revision(
        ledger,
        lease,
        request_id="offline-start",
        revision_id="offline-context",
        reason="source-offline downstream fixture",
        context=fixture.context(base_head, base_tree),
    )
    decision = schedule_ready(
        ledger,
        lease,
        decision_id="offline-schedule",
        live_capacity=1,
    )
    packet = issue_child_packet(
        ledger,
        lease,
        {
            "packet_id": "offline-packet",
            "child_id": "child-a",
            "requirements": ["REQ-1"],
            "forbidden_touches": [],
            "tests": ["git diff --check"],
            "context_slice_ids": ["slice-public"],
            "parent_contact": "local:offline",
            "result_deadline": "2026-07-25T19:00:00Z",
            "result_lease": "offline-result-lease",
            "attempt": 1,
            "round": 1,
        },
    )
    worktree = flow_root / "worktree"
    create_child_worktree(
        ledger,
        lease,
        operation_id="offline-worktree",
        child_id="child-a",
        packet_id=packet["packet_id"],
        worktree=worktree,
        branch="loop-v1/offline-child",
        integration_worktree=flow_root / "integration",
    )
    (worktree / "src/app.txt").write_text("integrated downstream change\\n")
    observation = observe_worker_candidate(worktree, base_head=base_head)
    result = {
        "result_id": "offline-result",
        "packet_id": packet["packet_id"],
        "child_id": packet["child_id"],
        "actual_touches": observation["actual_touches"],
        "diff_identity": observation["diff_identity"],
        "base_head": observation["base_head"],
        "base_tree_id": observation["base_tree_id"],
        "result_tree_id": observation["tree_id"],
        "commands": [{
            "command": "git diff --check",
            "status": "passed",
            "output_digest": "offline-check",
        }],
        "coverage": ["REQ-1"],
        "risks": [],
        "findings": [],
        "artifacts": [{"path": "src/app.txt", "digest": "offline-artifact"}],
        **packet["identity"],
    }
    accept_child_result(ledger, lease, result)
    validation = validate_child_candidate(
        ledger,
        lease,
        validation_id="offline-validation",
        result=result,
        worktree=worktree,
    )
    review = record_precommit_review(
        ledger,
        lease,
        review_id="offline-review",
        validation_id=validation["validation_id"],
        reviewer_identity="model:codex",
        verdict="passed",
        required_findings=[],
        advisory_findings=[],
        dispositions=[],
    )
    commit_reviewed_candidate(
        ledger,
        lease,
        operation_id="offline-commit",
        review_id=review["review_id"],
        message="offline downstream child",
        author_name="Offline Parent",
        author_email="offline@example.invalid",
    )
    git("update-ref", INTEGRATION_REF, base_head)
    prepare_integration_candidate(
        ledger,
        lease,
        integration_id="offline-integration",
        child_id="child-a",
        integration_ref=INTEGRATION_REF,
        candidate_worktree=flow_root / "candidate",
        candidate_branch="loop-v1/offline-candidate",
        checks=["git diff --check"],
        author_name="Offline Integrator",
        author_email="offline@example.invalid",
    )
    candidate = build_integration_candidate(
        ledger,
        lease,
        integration_id="offline-integration",
    )
    advance_integration_ref(
        ledger,
        lease,
        integration_id="offline-integration",
    )
    integrated = acknowledge_integration(
        ledger,
        lease,
        integration_id="offline-integration",
    )
    cancelled = cancel_parent(
        ledger,
        lease,
        operation_id="offline-cancel",
        reason="fixture completed",
        actor="user:jym",
        requested_at="2026-07-25T18:30:00Z",
        direct_user_action=True,
        expected_authority_digest=f"sha256:{ledger.authority_digest()}",
    )
    with sqlite3.connect(ledger.reference()["sqlite_uri"], uri=True) as connection:
        parent_status = connection.execute(
            "SELECT status FROM parent_runs"
        ).fetchone()[0]
    print(json.dumps({
        "candidate_status": candidate["status"],
        "cancelled": cancelled["status"],
        "integrated": integrated["status"],
        "parent_status": parent_status,
        "qualification_receipt": qualification.receipt_id,
        "scheduled": decision["selected"],
    }, sort_keys=True))
    """
)


class DownstreamInstalledRuntimeTests(unittest.TestCase):
    def test_source_offline_runtime_advances_integrates_and_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target = prepare_downstream_runtime(base)
            status = operation_qualification(target)
            self.assertTrue(status.valid, status.issues)

            (target / "project.txt").write_text("uncommitted project dirt\n")
            (target / "user-note.txt").write_text("user file\n")
            self.assertTrue(operation_qualification(target).valid)
            shutil.rmtree(source)

            completed = subprocess.run(
                [sys.executable, "-c", _OFFLINE_FLOW],
                cwd=target,
                env={
                    **os.environ,
                    "DOWNSTREAM_ROOT": str(target),
                    "FLOW_ROOT": str(base / "flow"),
                    "OFFLINE_SOURCE": str(source),
                    "PYTHONPATH": os.pathsep.join(
                        (
                            str(target / ".trellis/scripts"),
                            str(target / ".trellis/scripts/tests"),
                        )
                    ),
                },
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            result = json.loads(completed.stdout.splitlines()[-1])
            self.assertEqual(result["scheduled"], ["child-a"])
            self.assertEqual(result["candidate_status"], "candidate_green")
            self.assertEqual(result["integrated"], "integrated")
            self.assertEqual(result["cancelled"], "cancelled")
            self.assertEqual(result["parent_status"], "cancelled")
            self.assertFalse(source.exists())

    def test_managed_runtime_drift_blocks_before_new_task_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target = prepare_downstream_runtime(base)
            shutil.rmtree(source)
            runtime = target / ".trellis/scripts/loop_v1/qualification.py"
            runtime.write_text(
                runtime.read_text(encoding="utf-8") + "\n# managed drift\n",
                encoding="utf-8",
            )
            tasks = target / ".trellis/tasks"
            before = tuple(
                (
                    path.relative_to(tasks).as_posix(),
                    path.read_bytes() if path.is_file() else None,
                )
                for path in sorted(tasks.rglob("*"))
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(target / ".trellis/scripts/task.py"),
                    "create",
                    "Drifted downstream parent",
                    "--slug",
                    "drifted-downstream-parent",
                    "--tier",
                    "parent",
                    "--workflow-mode",
                    "loop_v1",
                    "--assignee",
                    "jym",
                ],
                cwd=target,
                env={
                    **os.environ,
                    "PYTHONPATH": str(target / ".trellis/scripts"),
                },
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("artifact release identity differs", completed.stderr)
            self.assertEqual(
                tuple(
                    (
                        path.relative_to(tasks).as_posix(),
                        path.read_bytes() if path.is_file() else None,
                    )
                    for path in sorted(tasks.rglob("*"))
                ),
                before,
            )


if __name__ == "__main__":
    unittest.main()
