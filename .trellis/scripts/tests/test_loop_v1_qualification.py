from __future__ import annotations

import argparse
import copy
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.io import read_json
from common.task_store import cmd_create
from downstream_deployer import planning as deployer_module
from loop_v1 import (
    LedgerError,
    ParentLedger,
    approve_start_request,
    create_start_request,
)
from loop_v1 import qualification as qualification_module
from loop_v1.qualification import (
    SOURCE_DEVELOPMENT_PURPOSE,
    QualificationError,
    check_overlay_conformance,
    configured_qualification,
    enable_loop_v1_admission,
    generate_artifact_qualification_receipt,
    generate_conformance_receipt,
    generate_local_runtime_receipt,
    generate_target_verification_receipt,
    read_qualification_receipt,
    require_local_effect,
    run_qualification_matrix,
    verify_artifact_qualification_receipt,
    verify_conformance_receipt,
    verify_local_runtime_receipt,
    verify_target_verification_receipt,
)
from test_loop_v1_context import start_envelope


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def copy_path(source_root: Path, target_root: Path, relative: Path) -> None:
    source = source_root / relative
    target = target_root / relative
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def prepare_receipt_repo(root: Path) -> None:
    manifest = qualification_module.load_overlay_manifest(
        REPO_ROOT / qualification_module.MANIFEST_PATH
    )
    paths = set(qualification_module.RUNTIME_PATHS)
    paths.update(
        Path(entry["path"])
        for entry in manifest["entries"]
        if entry["owner"] == "overlay"
    )
    for relative in sorted(paths):
        copy_path(REPO_ROOT, root, relative)
    raw_installed_root = os.environ.get(qualification_module._INSTALLED_ROOT_ENV)
    installed_root = Path(raw_installed_root).resolve() if raw_installed_root else REPO_ROOT
    for entry in manifest["entries"]:
        if entry["owner"] != "official":
            continue
        relative = Path(entry["path"])
        source = installed_root / relative
        if source.exists():
            copy_path(installed_root, root, relative)
            continue
        if raw_installed_root:
            raise AssertionError(f"explicit installed fixture path is missing: {relative}")
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative == Path(".trellis/.version"):
            destination.write_text("test-fixture\n", encoding="utf-8")
        elif relative == Path(".trellis/.template-hashes.json"):
            destination.write_text(
                '{"__version":2,"hashes":{}}\n',
                encoding="utf-8",
            )
        else:
            raise AssertionError(f"official fixture path is missing: {relative}")
    for source in qualification_module._qualification_test_files(REPO_ROOT):
        copy_path(REPO_ROOT, root, source.relative_to(REPO_ROOT))
    git(root, "init", "-q")
    git(root, "config", "user.name", "Loop Qualification Test")
    git(root, "config", "user.email", "loop-qualification@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture: committed runtime")


def prepare_dual_root_repo(base: Path) -> tuple[Path, Path]:
    runtime = base / "runtime"
    installed = base / "installed"
    runtime.mkdir()
    prepare_receipt_repo(runtime)
    prepare_current_tool_installed_root(installed)
    for relative in (
        Path(".trellis/.version"),
        Path(".trellis/.template-hashes.json"),
    ):
        (runtime / relative).unlink()
    git(runtime, "add", "--all")
    git(runtime, "commit", "-q", "-m", "fixture: remove local install identity")
    return runtime, installed


def prepare_current_tool_installed_root(root: Path) -> Path:
    root.mkdir()
    trellis_root = root / ".trellis"
    trellis_root.mkdir()
    (trellis_root / ".version").write_text("0.6.5", encoding="utf-8")
    (root / qualification_module.MATERIALIZATION_METADATA_PATH).write_text(
        '{"__version":2,"hashes":{}}\n',
        encoding="utf-8",
    )
    deployer_module._materialize_official(root)
    for backup in trellis_root.glob(".backup-*"):
        shutil.rmtree(backup)
    return root


def frozen_candidate_compatibility(root: Path) -> dict[str, object]:
    fixture_root = root / qualification_module.OLDEST_DOWNSTREAM_FIXTURE_PATH
    commands = qualification_module.OLDEST_DOWNSTREAM_SMOKE_COMMANDS
    evidence: dict[str, object] = {
        "baseline_materialization": {
            "command": ["trellis", "update", "--force", "--migrate"],
            "returncode": 0,
        },
        "candidate_payload_digest": qualification_module.digest_json(
            {"fixture": "oldest-downstream-candidate"}
        ),
        "fixture": {
            "helper_sha256": sha256(
                (fixture_root / "task_utils.py").read_bytes()
            ).hexdigest(),
            "official_release": "0.6.5",
            "target_id": qualification_module.DEPLOYMENT_TARGETS[0],
        },
        "fixture_digest": sha256(
            (fixture_root / "fixture.json").read_bytes()
        ).hexdigest(),
        "issues": [],
        "manifest_digest": sha256(
            (root / qualification_module.MANIFEST_PATH).read_bytes()
        ).hexdigest(),
        "materialization": {
            "command": ["trellis", "update", "--force", "--migrate"],
            "returncode": 0,
        },
        "materialization_metadata": {
            ".trellis/.template-hashes.json": {
                "format": "trellis-template-hashes-v2",
                "schema_version": 2,
            }
        },
        "overlay": {
            "path_set_digest": qualification_module.digest_json([]),
            "payload_digest": qualification_module.digest_json({}),
        },
        "restored_paths_digest": qualification_module.digest_json([]),
        "smoke_checks": [
            {
                "command_digest": qualification_module.digest_json(list(command)),
                "index": index,
                "output_digest": "sha256:"
                + sha256(f"fixture:{index}".encode("utf-8")).hexdigest(),
                "returncode": 0,
            }
            for index, command in enumerate(commands)
        ],
        "status": "passed",
    }
    evidence["evidence_digest"] = qualification_module.digest_json(evidence)
    return evidence


def run_qualification_cli(
    runtime: Path,
    installed: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loop_v1.qualification",
            "--repo-root",
            str(runtime),
            "--installed-root",
            str(installed),
            *args,
        ],
        cwd=SCRIPT_DIR.parent,
        env={**os.environ, "PYTHONPATH": str(SCRIPT_DIR)},
        capture_output=True,
        text=True,
        check=False,
    )


def qualification_result(
    root: Path,
    *,
    installed_root: Path | None = None,
) -> dict[str, object]:
    scenarios: list[dict[str, object]] = []
    for qualification_id in qualification_module.QUALIFICATION_IDS:
        positive_id, negative_id = qualification_module._QUALIFICATION_MATRIX[
            qualification_id
        ]

        def evidence(test_id: str, polarity: str) -> dict[str, object]:
            source = qualification_module._test_source_path(root, test_id)
            return {
                "command": [sys.executable, "-m", "unittest", "-q", test_id],
                "returncode": 0,
                "source_digest": sha256(source.read_bytes()).hexdigest(),
                "stderr_digest": sha256(
                    f"{qualification_id}:{polarity}:stderr".encode("utf-8")
                ).hexdigest(),
                "stdout_digest": sha256(
                    f"{qualification_id}:{polarity}:stdout".encode("utf-8")
                ).hexdigest(),
                "test_id": test_id,
            }

        scenarios.append(
            {
                "id": qualification_id,
                "negative": evidence(negative_id, "negative"),
                "positive": evidence(positive_id, "positive"),
                "status": "passed",
            }
        )
    deployer_tests: list[dict[str, object]] = []
    for index, test_id in enumerate(qualification_module.DEPLOYER_TEST_IDS):
        source = qualification_module._test_source_path(root, test_id)
        deployer_tests.append(
            {
                "command": [sys.executable, "-m", "unittest", "-q", test_id],
                "returncode": 0,
                "source_digest": sha256(source.read_bytes()).hexdigest(),
                "stderr_digest": sha256(
                    f"deployer:{index}:stderr".encode("utf-8")
                ).hexdigest(),
                "stdout_digest": sha256(
                    f"deployer:{index}:stdout".encode("utf-8")
                ).hexdigest(),
                "test_id": test_id,
            }
        )
    candidate_compatibility = frozen_candidate_compatibility(root)
    return {
        "candidate_compatibility": candidate_compatibility,
        "deployer_digest": qualification_module.digest_json(deployer_tests),
        "deployer_tests": deployer_tests,
        "external_requirements": {
            "ci": False,
            "hooks": False,
            "network": False,
            "remote": False,
        },
        "qualification_digest": qualification_module.digest_json(scenarios),
        "scenarios": scenarios,
        "status": "passed",
    }


def qualify_repo(root: Path) -> Path:
    prepare_receipt_repo(root)
    receipt = generate_conformance_receipt(
        root,
        qualification_result(root),
        root / ".trellis/spec/project/receipts/loop-v1",
    )
    relative = receipt.relative_to(root).as_posix()
    (root / ".trellis/config.yaml").write_text(
        "loop_v1:\n"
        "  admission_enabled: true\n"
        "  parent_default: loop_v1\n"
        "  runtime_mode: harness_source\n"
        f"  qualification_receipt: {relative}\n"
        f"  qualification_receipt_digest: {receipt.stem}\n",
        encoding="utf-8",
    )
    return receipt


def rotate_receipt(root: Path) -> Path:
    result = qualification_result(root)
    result["scenarios"][0]["positive"]["stdout_digest"] = sha256(
        b"rotated qualification evidence"
    ).hexdigest()
    result["qualification_digest"] = qualification_module.digest_json(
        result["scenarios"]
    )
    return generate_conformance_receipt(
        root,
        result,
        root / ".trellis/spec/project/receipts/loop-v1",
    )


def authorize_parent(root: Path, receipt_id: str) -> tuple[ParentLedger, object]:
    ledger, lease = ParentLedger.initialize(
        root,
        "qualified-parent",
        selector="loop_v1",
        writer_id="qualification-writer",
    )
    envelope = start_envelope()
    envelope["conformance_receipt"] = receipt_id
    request = create_start_request(
        ledger,
        lease,
        request_id="qualification-start",
        envelope=envelope,
    )
    approve_start_request(
        ledger,
        lease,
        request_id="qualification-start",
        request_digest=request["request_digest"],
        response_identity="user:qualification",
        response_at="2026-07-13T20:00:00Z",
        direct_user_action=True,
    )
    return ledger, lease


def parent_status(ledger: ParentLedger) -> str:
    connection = sqlite3.connect(ledger.reference()["sqlite_uri"], uri=True)
    try:
        return connection.execute("SELECT status FROM parent_runs").fetchone()[0]
    finally:
        connection.close()


class LoopV1QualificationFixtureTests(unittest.TestCase):
    def test_managed_forward_bundle_rejects_soft_archive_caller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = root / ".trellis" / "workflow.md"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "Run `task.py complete-child <child> --commit <hash>`.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                qualification_module._managed_child_completion_issues(root),
                [],
            )

            script = root / ".trellis" / "scripts" / "forward.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "command = 'task.py soft-archive child --commit deadbeef'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                qualification_module._managed_child_completion_issues(root),
                [
                    "managed forward child completion caller uses soft-archive: "
                    ".trellis/scripts/forward.py"
                ],
            )
            script.unlink()

            task_cli = root / ".trellis" / "scripts" / "task.py"
            task_cli.write_text(
                "# compatibility command: soft-archive\n",
                encoding="utf-8",
            )
            self.assertEqual(
                qualification_module._managed_child_completion_issues(root),
                [
                    "managed task CLI does not expose complete-child: "
                    ".trellis/scripts/task.py"
                ],
            )
            task_cli.write_text(
                "# canonical command: complete-child\n",
                encoding="utf-8",
            )

            workflow.write_text(
                "Run `task.py soft-archive <child> --commit <hash>`.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                qualification_module._managed_child_completion_issues(root),
                [
                    "managed forward child completion caller uses soft-archive: "
                    ".trellis/workflow.md"
                ],
            )

    def test_valid_receipt_allows_ledger_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            verification = verify_conformance_receipt(
                root, receipt, expected_digest=receipt.stem
            )
            self.assertTrue(verification["valid"], verification["issues"])
            ledger, lease = authorize_parent(root, f"sha256:{receipt.stem}")

            operation = ledger.prepare_operation(
                lease,
                operation_id="qualified-write",
                kind="qualification-test",
                input_fingerprint="qualified-input",
            )

            self.assertEqual(operation["phase"], "prepared")
            self.assertEqual(parent_status(ledger), "authorized")

    def test_source_development_does_not_requalify_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            source = root / ".trellis/scripts/loop_v1/qualification.py"
            source.write_text(
                source.read_text(encoding="utf-8") + "\n# source development\n",
                encoding="utf-8",
            )

            release = configured_qualification(root)
            self.assertFalse(release.valid)
            development = configured_qualification(
                root,
                purpose=SOURCE_DEVELOPMENT_PURPOSE,
            )
            self.assertTrue(development.valid, development.issues)
            self.assertEqual(development.receipt_id, f"sha256:{receipt.stem}")

            with mock.patch(
                "loop_v1.qualification.verify_conformance_receipt",
                side_effect=AssertionError("release qualification reached source write"),
            ):
                ledger, lease = authorize_parent(root, f"sha256:{receipt.stem}")
                operation = ledger.prepare_operation(
                    lease,
                    operation_id="source-development-write",
                    kind="qualification-test",
                    input_fingerprint="source-development-input",
                )
            self.assertEqual(operation["phase"], "prepared")

    def test_rotation_applies_to_new_admission_not_active_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = qualify_repo(root)
            original_id = f"sha256:{original.stem}"
            ledger, lease = authorize_parent(root, original_id)
            rotated = rotate_receipt(root)
            rotated_id = f"sha256:{rotated.stem}"
            (root / ".trellis/config.yaml").write_text(
                "loop_v1:\n"
                "  admission_enabled: true\n"
                "  parent_default: loop_v1\n"
                "  runtime_mode: harness_source\n"
                f"  qualification_receipt: {rotated.relative_to(root).as_posix()}\n"
                f"  qualification_receipt_digest: {rotated.stem}\n",
                encoding="utf-8",
            )

            continued = ledger.prepare_operation(
                lease,
                operation_id="original-binding-continues",
                kind="qualification-test",
                input_fingerprint="original-binding-input",
            )
            self.assertEqual(continued["phase"], "prepared")
            self.assertEqual(parent_status(ledger), "authorized")
            reopened, replayed_lease = ParentLedger.initialize(
                root,
                "qualified-parent",
                selector="loop_v1",
                writer_id="qualification-writer",
                existing_lease=lease,
            )
            self.assertEqual(reopened.path, ledger.path)
            self.assertEqual(replayed_lease, lease)

            stale_ledger, stale_lease = ParentLedger.initialize(
                root,
                "stale-admission",
                selector="loop_v1",
                writer_id="stale-writer",
            )
            stale_envelope = start_envelope()
            stale_envelope["conformance_receipt"] = original_id
            stale_request = create_start_request(
                stale_ledger,
                stale_lease,
                request_id="stale-start",
                envelope=stale_envelope,
            )
            with self.assertRaises(QualificationError):
                approve_start_request(
                    stale_ledger,
                    stale_lease,
                    request_id="stale-start",
                    request_digest=stale_request["request_digest"],
                    response_identity="user:stale",
                    response_at="2026-07-26T18:00:00Z",
                    direct_user_action=True,
                )
            self.assertEqual(parent_status(stale_ledger), "initialized")

            current_ledger, current_lease = ParentLedger.initialize(
                root,
                "current-admission",
                selector="loop_v1",
                writer_id="current-writer",
            )
            current_envelope = start_envelope()
            current_envelope["conformance_receipt"] = rotated_id
            current_request = create_start_request(
                current_ledger,
                current_lease,
                request_id="current-start",
                envelope=current_envelope,
            )
            approval = approve_start_request(
                current_ledger,
                current_lease,
                request_id="current-start",
                request_digest=current_request["request_digest"],
                response_identity="user:current",
                response_at="2026-07-26T18:01:00Z",
                direct_user_action=True,
            )
            self.assertEqual(approval["execution_binding"]["receipt_id"], rotated_id)

    def test_legacy_execution_binding_uses_original_receipt_after_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = qualify_repo(root)
            original_id = f"sha256:{original.stem}"
            ledger, lease = authorize_parent(root, original_id)
            with ledger._write_transaction(lease) as connection:
                row = connection.execute(
                    "SELECT outcome_json FROM operations WHERE operation_id = ?",
                    ("start-approval:qualification-start",),
                ).fetchone()
                outcome = json.loads(row["outcome_json"])
                outcome.pop("execution_binding")
                encoded = json.dumps(
                    outcome, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                connection.execute(
                    "UPDATE operations SET outcome_json = ?, output_fingerprint = ? "
                    "WHERE operation_id = ?",
                    (
                        encoded,
                        sha256(encoded.encode("utf-8")).hexdigest(),
                        "start-approval:qualification-start",
                    ),
                )
            rotated = rotate_receipt(root)
            (root / ".trellis/config.yaml").write_text(
                "loop_v1:\n"
                "  admission_enabled: true\n"
                "  parent_default: loop_v1\n"
                "  runtime_mode: harness_source\n"
                f"  qualification_receipt: {rotated.relative_to(root).as_posix()}\n"
                f"  qualification_receipt_digest: {rotated.stem}\n",
                encoding="utf-8",
            )

            operation = ledger.prepare_operation(
                lease,
                operation_id="legacy-original-binding",
                kind="qualification-test",
                input_fingerprint="legacy-original-input",
            )
            self.assertEqual(operation["phase"], "prepared")
            self.assertEqual(parent_status(ledger), "authorized")

    def test_explicit_revocation_is_targeted_terminal_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            receipt_id = f"sha256:{receipt.stem}"
            ledger, lease = authorize_parent(root, receipt_id)
            before = ledger.authority_snapshot()
            with self.assertRaisesRegex(
                LedgerError, "target does not match execution binding"
            ):
                ledger.revoke_execution_binding(
                    lease,
                    operation_id="wrong-target",
                    actor="user:test",
                    reason="wrong receipt",
                    revoked_at="2026-07-26T18:02:00Z",
                    direct_user_action=True,
                    execution_receipt="sha256:" + "0" * 64,
                )
            self.assertEqual(ledger.authority_snapshot(), before)
            with self.assertRaisesRegex(LedgerError, "direct user action"):
                ledger.revoke_execution_binding(
                    lease,
                    operation_id="indirect-revocation",
                    actor="user:test",
                    reason="indirect",
                    revoked_at="2026-07-26T18:03:00Z",
                    direct_user_action=False,
                    execution_receipt=receipt_id,
                )
            self.assertEqual(ledger.authority_snapshot(), before)

            revoked = ledger.revoke_execution_binding(
                lease,
                operation_id="targeted-revocation",
                actor="user:test",
                reason="receipt withdrawn",
                revoked_at="2026-07-26T18:04:00Z",
                direct_user_action=True,
                execution_receipt=receipt_id,
            )
            self.assertEqual(revoked["status"], "revoked")
            self.assertEqual(revoked["target"], {
                "execution_receipt": receipt_id,
                "run_id": "qualified-parent",
            })
            self.assertEqual(parent_status(ledger), "revoked")
            after = ledger.authority_snapshot()
            self.assertTrue(
                any(
                    row["event_type"] == "execution_binding_revoked"
                    for row in after["tables"]["ledger_events"]
                )
            )
            with self.assertRaisesRegex(QualificationError, "explicitly revoked"):
                ledger.prepare_operation(
                    lease,
                    operation_id="must-not-run-after-revocation",
                    kind="qualification-test",
                    input_fingerprint="revoked-input",
                )
            self.assertEqual(ledger.authority_snapshot(), after)
            self.assertEqual(
                ledger.revoke_execution_binding(
                    lease,
                    operation_id="targeted-revocation",
                    actor="user:test",
                    reason="receipt withdrawn",
                    revoked_at="2026-07-26T18:04:00Z",
                    direct_user_action=True,
                    execution_receipt=receipt_id,
                ),
                revoked,
            )

    def test_disable_marker_blocks_new_admission_without_pausing_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            ledger, lease = authorize_parent(root, f"sha256:{receipt.stem}")
            before = ledger.authority_snapshot()
            command = [
                sys.executable,
                "-m",
                "loop_v1.qualification",
                "--repo-root",
                str(root),
                "disable",
                "--reason",
                "qualification drill",
            ]
            environment = {**os.environ, "PYTHONPATH": str(SCRIPT_DIR)}

            first = subprocess.run(
                command,
                cwd=SCRIPT_DIR.parent,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stderr, "")
            rollback = json.loads(first.stdout)

            self.assertEqual(rollback["status"], "disabled")
            self.assertEqual(rollback["paused_parents"], [])
            self.assertEqual(rollback["failures"], [])
            self.assertTrue(rollback["preserved"])
            self.assertEqual(parent_status(ledger), "authorized")
            after = ledger.authority_snapshot()
            self.assertEqual(after, before)

            replay = subprocess.run(
                command,
                cwd=SCRIPT_DIR.parent,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(replay.stderr, "")
            self.assertEqual(json.loads(replay.stdout), rollback)
            replayed = ledger.authority_snapshot()
            self.assertEqual(replayed["tables"]["operations"], after["tables"]["operations"])
            self.assertEqual(
                replayed["tables"]["ledger_events"],
                after["tables"]["ledger_events"],
            )
            with self.assertRaises(QualificationError):
                ParentLedger.initialize(
                    root,
                    "blocked-new-admission",
                    selector="loop_v1",
                    writer_id="blocked-writer",
                )
            operation = ledger.prepare_operation(
                lease,
                operation_id="active-run-continues",
                kind="qualification-test",
                input_fingerprint="active-input",
            )
            self.assertEqual(operation["phase"], "prepared")

    def test_disabled_config_blocks_direct_runtime_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".trellis").mkdir()
            (root / ".trellis/config.yaml").write_text(
                "loop_v1:\n  admission_enabled: false\n",
                encoding="utf-8",
            )

            with self.assertRaises(QualificationError):
                ParentLedger.initialize(
                    root,
                    "must-not-initialize",
                    selector="loop_v1",
                    writer_id="blocked-writer",
                )

            self.assertFalse((root / ".trellis/.runtime").exists())

    def test_local_qualification_requires_no_remote_ci_hooks_or_network(self) -> None:
        self.assertEqual(
            set(qualification_module._SAFE_EFFECTS),
            {
                "git_read",
                "local_process",
                "local_read",
                "local_runtime_write",
                "qualification_receipt_write",
            },
        )
        for positive, negative in qualification_module._QUALIFICATION_MATRIX.values():
            self.assertTrue(positive.startswith("test_loop_v1_"))
            self.assertTrue(negative.startswith("test_loop_v1_"))

    def test_unknown_and_prohibited_effects_fail_closed(self) -> None:
        for effect in ("unknown", "network", "ci", "hook", "push", "release"):
            with self.subTest(effect=effect), self.assertRaises(QualificationError):
                require_local_effect(effect)


class LoopV1QualificationTests(unittest.TestCase):
    def test_overlay_manifest_owns_prd_governance_surface_exactly_once(self) -> None:
        manifest = qualification_module.load_overlay_manifest(
            REPO_ROOT / qualification_module.MANIFEST_PATH
        )
        expected = {
            ".trellis/workflow.md": "file",
            ".agents/skills/trellis-brainstorm": "tree",
            ".agents/skills/trellis-continue": "tree",
            ".agents/skills/trellis-meta": "tree",
            ".agents/skills/trellis-start": "tree",
            ".trellis/scripts/prd.py": "file",
            ".trellis/scripts/tests/test_prd_governance.py": "file",
            ".trellis/spec/project/git-commit-push-policy.md": "file",
            ".trellis/spec/project/index.md": "file",
            ".trellis/spec/project/prd-governance.md": "file",
            ".trellis/spec/project/staged-delivery-overlay.md": "file",
            ".trellis/templates/v3": "tree",
        }

        for path, scope in expected.items():
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path).exists())
                self.assertEqual(
                    [entry for entry in manifest["entries"] if entry["path"] == path],
                    [{"owner": "overlay", "path": path, "scope": scope}],
                )
        self.assertEqual(manifest["overlay_version"], "loop-v1.0.10-local")

    def test_overlay_manifest_owns_every_loop_payload_exactly_once(self) -> None:
        manifest = qualification_module.load_overlay_manifest(
            REPO_ROOT / qualification_module.MANIFEST_PATH
        )
        self.assertEqual(
            [
                entry
                for entry in manifest["entries"]
                if entry["path"] == ".trellis/workflow.md"
            ],
            [
                {
                    "owner": "overlay",
                    "path": ".trellis/workflow.md",
                    "scope": "file",
                }
            ],
        )
        self.assertEqual(
            [
                entry
                for entry in manifest["entries"]
                if entry["path"] == ".trellis/.template-hashes.json"
            ],
            [
                {
                    "owner": "official",
                    "path": ".trellis/.template-hashes.json",
                    "scope": "file",
                }
            ],
        )
        expected: set[Path] = set()
        for relative in qualification_module.RUNTIME_PATHS:
            source = REPO_ROOT / relative
            if source.is_dir():
                expected.update(
                    path.relative_to(REPO_ROOT)
                    for path in source.rglob("*")
                    if path.is_file()
                )
            else:
                expected.add(relative)
        expected.update(
            path.relative_to(REPO_ROOT)
            for path in qualification_module._qualification_test_files(REPO_ROOT)
        )
        expected.update(
            path.relative_to(REPO_ROOT)
            for path in (REPO_ROOT / ".trellis/spec/project").glob("loop-v1-*")
            if path.is_file()
        )
        expected.update(
            path.relative_to(REPO_ROOT)
            for path in (REPO_ROOT / ".trellis/templates/v3/handoff").rglob("*")
            if path.is_file()
        )

        for relative in sorted(expected):
            owners = [
                entry["owner"]
                for entry in manifest["entries"]
                if Path(entry["path"]) == relative
                or (
                    entry["scope"] == "tree"
                    and Path(entry["path"]) in relative.parents
                )
            ]
            with self.subTest(path=relative.as_posix()):
                self.assertEqual(owners, ["overlay"])

    def test_clean_clone_advances_official_and_applies_complete_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_receipt_repo(root)
            before = git(root, "status", "--porcelain")

            result = qualification_module.run_clean_clone_overlay_conformance(root)

            self.assertEqual(result["status"], "passed", result["issues"])
            self.assertTrue(result["official_update"]["fast_forwarded"])
            self.assertNotEqual(
                result["official_update"]["base_tree"],
                result["official_update"]["updated_tree"],
            )
            self.assertEqual(
                result["application"], result["owners"]["overlay"]
            )
            self.assertTrue(
                all(
                    evidence["preserved"]
                    for evidence in result["preservation"].values()
                )
            )
            self.assertEqual(git(root, "status", "--porcelain"), before)

    def test_oldest_downstream_candidate_uses_real_updater_and_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "runtime"
            root.mkdir()
            prepare_receipt_repo(root)
            installed = prepare_current_tool_installed_root(base / "installed")
            before = git(root, "status", "--porcelain")

            passed = (
                qualification_module.run_oldest_downstream_candidate_conformance(
                    root,
                    installed_root=installed,
                )
            )

            self.assertEqual(passed["status"], "passed", passed["issues"])
            self.assertEqual(
                passed["materialization"],
                {
                    "command": ["trellis", "update", "--force", "--migrate"],
                    "returncode": 0,
                },
            )
            self.assertEqual(passed["fixture"]["official_release"], "0.6.5")
            self.assertEqual(passed["fixture"]["target_id"], "RAG v2")
            self.assertEqual(
                [check["returncode"] for check in passed["smoke_checks"]],
                [0, 0],
            )
            self.assertEqual(
                [check["command_digest"] for check in passed["smoke_checks"]],
                [
                    qualification_module.digest_json(list(command))
                    for command in qualification_module.OLDEST_DOWNSTREAM_SMOKE_COMMANDS
                ],
            )
            self.assertEqual(git(root, "status", "--porcelain"), before)

            manifest = read_json(root / qualification_module.MANIFEST_PATH)
            manifest["entries"] = [
                entry
                for entry in manifest["entries"]
                if entry["path"] != ".trellis/scripts/state_machine.py"
            ]
            incomplete = root / "incomplete-overlay.json"
            incomplete.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            failed = (
                qualification_module.run_oldest_downstream_candidate_conformance(
                    root,
                    incomplete,
                    installed_root=installed,
                )
            )

            self.assertEqual(failed["status"], "failed")
            self.assertTrue(
                any("CHECK_FAILED" in issue for issue in failed["issues"]),
                failed["issues"],
            )

    def test_dual_root_receipt_binds_installed_identity_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime, installed = prepare_dual_root_repo(base)
            identity_paths = (
                Path(".trellis/.version"),
                Path(".trellis/.template-hashes.json"),
            )
            installed_before = {
                path.relative_to(installed): path.read_bytes()
                for path in installed.rglob("*")
                if path.is_file()
            }
            output = runtime / ".trellis/spec/project/receipts/loop-v1"

            with self.assertRaisesRegex(
                QualificationError,
                "qualification root conformance failed",
            ):
                run_qualification_matrix(runtime)
            overlay = check_overlay_conformance(
                runtime,
                installed_root=installed,
            )
            receipt = generate_conformance_receipt(
                runtime,
                qualification_result(runtime, installed_root=installed),
                output,
                installed_root=installed,
            )
            verification = verify_conformance_receipt(
                runtime,
                receipt,
                installed_root=installed,
            )

            self.assertEqual(overlay["status"], "passed", overlay["issues"])
            self.assertTrue(verification["valid"], verification["issues"])
            self.assertNotIn(str(installed), receipt.read_text(encoding="utf-8"))
            for relative in identity_paths:
                self.assertFalse((runtime / relative).exists())

            forbidden_receipts = installed / "forbidden-receipts"
            with self.assertRaisesRegex(
                QualificationError,
                "must not write inside installed root",
            ):
                generate_conformance_receipt(
                    runtime,
                    qualification_result(runtime, installed_root=installed),
                    forbidden_receipts,
                    installed_root=installed,
                )
            self.assertFalse(forbidden_receipts.exists())

            for overlapping_root in (runtime, base):
                with self.subTest(overlapping_root=overlapping_root), self.assertRaisesRegex(
                    QualificationError,
                    "must be disjoint",
                ):
                    check_overlay_conformance(
                        runtime,
                        installed_root=overlapping_root,
                    )
            nested_installed = runtime / "nested-installed"
            nested_installed.mkdir()
            with self.assertRaisesRegex(QualificationError, "must be disjoint"):
                check_overlay_conformance(
                    runtime,
                    installed_root=nested_installed,
                )
            nested_installed.rmdir()

            linked = base / "linked-installed"
            linked.symlink_to(installed, target_is_directory=True)
            with self.assertRaisesRegex(QualificationError, "must not be a symlink"):
                check_overlay_conformance(runtime, installed_root=linked)
            payload_link_root = base / "payload-link-installed"
            payload_link_root.mkdir()
            (payload_link_root / ".trellis").symlink_to(
                installed / ".trellis",
                target_is_directory=True,
            )
            linked_payload = check_overlay_conformance(
                runtime,
                installed_root=payload_link_root,
            )
            self.assertEqual(linked_payload["status"], "failed")
            self.assertTrue(
                any("symlink component" in issue for issue in linked_payload["issues"])
            )

            missing_root = base / "missing-installed"
            missing = verify_conformance_receipt(
                runtime,
                receipt,
                installed_root=missing_root,
            )
            self.assertFalse(missing["valid"])
            self.assertIn("installed root is not a directory", missing["issues"])

            cli_qualification = base / "cli-qualification.json"
            completed = run_qualification_cli(
                runtime,
                installed,
                "run",
                "--output",
                str(cli_qualification),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(read_json(cli_qualification)["status"], "passed")

            forbidden_output = installed / "forbidden-qualification.json"
            completed = run_qualification_cli(
                runtime,
                installed,
                "run",
                "--output",
                str(forbidden_output),
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("must not write inside installed root", completed.stdout)
            self.assertFalse(forbidden_output.exists())

            completed = run_qualification_cli(runtime, installed, "overlay-check")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "passed")

            cli_output = output / "cli"
            completed = run_qualification_cli(
                runtime,
                installed,
                "receipt",
                "--qualification",
                str(cli_qualification),
                "--output-dir",
                str(cli_output),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            cli_receipt = Path(json.loads(completed.stdout)["path"])
            completed = run_qualification_cli(
                runtime,
                installed,
                "verify",
                "--receipt",
                str(cli_receipt),
                "--expected-digest",
                cli_receipt.stem,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])

            activation = base / "activation"
            git(base, "clone", "-q", str(runtime), str(activation))
            for relative in identity_paths:
                copy_path(installed, activation, relative)
            activated = verify_conformance_receipt(activation, receipt)
            self.assertTrue(activated["valid"], activated["issues"])

            relative_receipt = receipt.relative_to(runtime).as_posix()
            (runtime / ".trellis/config.yaml").write_text(
                "loop_v1:\n"
                "  admission_enabled: true\n"
                "  parent_default: loop_v1\n"
                "  runtime_mode: harness_source\n"
                f"  qualification_receipt: {relative_receipt}\n"
                f"  qualification_receipt_digest: {receipt.stem}\n",
                encoding="utf-8",
            )
            marker = runtime / qualification_module.DISABLE_MARKER
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker_bytes = b'{"disabled":true}\n'
            marker.write_bytes(marker_bytes)
            with self.assertRaisesRegex(QualificationError, "not a directory"):
                enable_loop_v1_admission(
                    runtime,
                    installed_root=missing_root,
                )
            self.assertEqual(marker.read_bytes(), marker_bytes)
            enabled = enable_loop_v1_admission(
                runtime,
                installed_root=installed,
            )
            self.assertEqual(enabled["status"], "enabled")
            self.assertFalse(marker.exists())
            self.assertTrue(
                configured_qualification(runtime, installed_root=installed).valid
            )

            environment_key = qualification_module._INSTALLED_ROOT_ENV
            runtime_status = git(runtime, "status", "--porcelain")
            receipt_bytes = receipt.read_bytes()
            with mock.patch.dict(
                os.environ,
                {environment_key: str(installed)},
            ):
                self.assertTrue(configured_qualification(runtime).valid)
            with mock.patch.dict(
                os.environ,
                {environment_key: str(missing_root)},
            ):
                self.assertTrue(
                    configured_qualification(
                        runtime,
                        installed_root=installed,
                    ).valid
                )
            for located_root, expected_issue in (
                (missing_root, "not a directory"),
                (runtime, "must be disjoint"),
                (linked, "must not be a symlink"),
            ):
                with self.subTest(located_root=located_root), mock.patch.dict(
                    os.environ,
                    {environment_key: str(located_root)},
                ):
                    located = configured_qualification(runtime)
                    self.assertFalse(located.valid)
                    self.assertTrue(
                        any(expected_issue in issue for issue in located.issues),
                        located.issues,
                    )
            self.assertEqual(git(runtime, "status", "--porcelain"), runtime_status)
            self.assertEqual(receipt.read_bytes(), receipt_bytes)
            self.assertNotIn(str(installed), receipt_bytes.decode("utf-8"))

            marker.write_bytes(marker_bytes)
            with mock.patch.dict(
                os.environ,
                {environment_key: str(missing_root)},
            ):
                completed = run_qualification_cli(runtime, installed, "enable")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "enabled")
            self.assertFalse(marker.exists())

            version = installed / ".trellis/.version"
            original_version = version.read_bytes()
            version.write_text("drift\n", encoding="utf-8")
            drifted = verify_conformance_receipt(
                runtime,
                receipt,
                installed_root=installed,
            )
            self.assertFalse(drifted["valid"])
            self.assertIn("overlay manifest or payload differs", drifted["issues"])
            with mock.patch.dict(
                os.environ,
                {environment_key: str(installed)},
            ):
                located_drift = configured_qualification(runtime)
            self.assertFalse(located_drift.valid)
            self.assertIn(
                "overlay manifest or payload differs",
                located_drift.issues,
            )
            version.write_bytes(original_version)

            template_hashes = installed / ".trellis/.template-hashes.json"
            original_template_hashes = template_hashes.read_bytes()
            template_hashes.write_text(
                '{"__version":2,"hashes":{"target-local.txt":"'
                + "d" * 64
                + '"}}',
                encoding="utf-8",
            )
            target_local = verify_conformance_receipt(
                runtime,
                receipt,
                installed_root=installed,
            )
            self.assertTrue(target_local["valid"], target_local["issues"])
            invalid_metadata = (
                '{"__version":2,"hashes":{"../escape":"bad"}}',
                '{"__version":2,"hashes":{'
                '".trellis/.template-hashes.json":"' + "e" * 64 + '"}}',
                '{"__version":2,"hashes":{"managed.txt":123}}',
            )
            for payload in invalid_metadata:
                with self.subTest(payload=payload):
                    template_hashes.write_text(payload, encoding="utf-8")
                    malformed = verify_conformance_receipt(
                        runtime,
                        receipt,
                        installed_root=installed,
                    )
                    self.assertFalse(malformed["valid"])
                    self.assertTrue(
                        any(
                            "template hash path" in issue
                            or "materialization metadata" in issue
                            for issue in malformed["issues"]
                        )
                    )
            template_hashes.write_bytes(original_template_hashes)

            self.assertEqual(
                installed_before,
                {
                    path.relative_to(installed): path.read_bytes()
                    for path in installed.rglob("*")
                    if path.is_file()
                },
            )
            for relative in identity_paths:
                self.assertFalse((runtime / relative).exists())

    def test_overlay_manifest_rejects_invalid_ownership_before_application(
        self,
    ) -> None:
        base_entries = [
            {"path": "official.txt", "scope": "file", "owner": "official"},
            {"path": "overlay.txt", "scope": "file", "owner": "overlay"},
            {"path": "project.txt", "scope": "file", "owner": "project"},
            {"path": "generated.txt", "scope": "file", "owner": "generated"},
        ]
        cases = {
            "unknown owner": ("unknown owner", [
                *base_entries[:-1],
                {"path": "generated.txt", "scope": "file", "owner": "mystery"},
            ]),
            "unknown scope": ("unknown scope", [
                base_entries[0],
                {"path": "overlay.txt", "scope": "glob", "owner": "overlay"},
                *base_entries[2:],
            ]),
            "overlaps": ("overlaps", [
                base_entries[0],
                {"path": "managed", "scope": "tree", "owner": "overlay"},
                {"path": "managed/local.txt", "scope": "file", "owner": "project"},
                base_entries[3],
            ]),
            "project collision": ("overlaps", [
                base_entries[0],
                {"path": "README.md", "scope": "file", "owner": "overlay"},
                {"path": "README.md", "scope": "file", "owner": "project"},
                base_entries[3],
            ]),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (label, (message, entries)) in enumerate(cases.items()):
                with self.subTest(message=label):
                    path = root / f"manifest-{index}.json"
                    path.write_text(
                        json.dumps(
                            {
                                "entries": entries,
                                "manifest_schema_version": 1,
                                "overlay_version": "fixture-v1",
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(QualificationError, message):
                        qualification_module.load_overlay_manifest(path)

    def test_overlay_preflight_rejects_missing_symlink_drift_and_partial_write(
        self,
    ) -> None:
        entries = [
            {"path": "official.txt", "scope": "file", "owner": "official"},
            {"path": "overlay-a.txt", "scope": "file", "owner": "overlay"},
            {"path": "overlay-b.txt", "scope": "file", "owner": "overlay"},
            {"path": "project.txt", "scope": "file", "owner": "project"},
            {"path": "generated.txt", "scope": "file", "owner": "generated"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            for name in ("official.txt", "overlay-a.txt", "overlay-b.txt"):
                (source / name).write_text(f"source:{name}\n", encoding="utf-8")
                (target / name).write_text(f"source:{name}\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "entries": entries,
                        "manifest_schema_version": 1,
                        "overlay_version": "fixture-v1",
                    }
                ),
                encoding="utf-8",
            )
            manifest = qualification_module.load_overlay_manifest(manifest_path)

            missing = copy.deepcopy(manifest)
            missing["entries"][1]["path"] = "missing.txt"
            with self.assertRaisesRegex(QualificationError, "missing"):
                qualification_module._preflight_overlay_application(
                    source, root / "missing-target", missing
                )

            linked = source / "linked.txt"
            linked.symlink_to(source / "overlay-a.txt")
            symlinked = copy.deepcopy(manifest)
            symlinked["entries"][1]["path"] = "linked.txt"
            with self.assertRaisesRegex(QualificationError, "symlink"):
                qualification_module._preflight_overlay_application(
                    source, root / "symlink-target", symlinked
                )

            (target / "overlay-a.txt").write_text("drift\n", encoding="utf-8")
            drifted = check_overlay_conformance(
                target,
                manifest_path,
                official_root=source,
                overlay_root=source,
            )
            self.assertEqual(drifted["status"], "failed")
            self.assertIn(
                "overlay payload differs from its pinned source", drifted["issues"]
            )

            partial_target = root / "partial-target"
            partial_target.mkdir()
            collision = partial_target / "overlay-b.txt"
            collision.write_text("project-owned\n", encoding="utf-8")
            with self.assertRaisesRegex(QualificationError, "collision"):
                qualification_module._apply_overlay_payload(
                    source, partial_target, manifest
                )
            self.assertFalse((partial_target / "overlay-a.txt").exists())
            self.assertEqual(collision.read_text(encoding="utf-8"), "project-owned\n")

    def test_receipt_is_stable_hash_addressed_and_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_receipt_repo(root)
            result = qualification_result(root)
            output = root / ".trellis/spec/project/receipts/loop-v1"

            first = generate_conformance_receipt(root, result, output)
            second = generate_conformance_receipt(root, result, output)

            self.assertEqual(first, second)
            self.assertEqual(first.stem, read_json(first)["receipt_digest"])
            self.assertTrue(verify_conformance_receipt(root, first)["valid"])
            runtime_file = root / ".trellis/scripts/downstream_deployer/planning.py"
            runtime_file.write_text(
                runtime_file.read_text(encoding="utf-8") + "\n# drift\n",
                encoding="utf-8",
            )
            drifted = verify_conformance_receipt(root, first)
            self.assertFalse(drifted["valid"])
            self.assertIn("installed runtime bundle differs from receipt", drifted["issues"])

    def test_receipt_requires_exact_committed_deployer_test_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_receipt_repo(root)
            output = root / ".trellis/spec/project/receipts/loop-v1"
            result = qualification_result(root)

            missing = copy.deepcopy(result)
            missing.pop("deployer_tests")
            with self.assertRaisesRegex(QualificationError, "requires deployer tests"):
                generate_conformance_receipt(root, missing, output)

            substituted = copy.deepcopy(result)
            substituted["deployer_tests"][0]["test_id"] = (
                "test_loop_v1_qualification.LoopV1QualificationTests."
                "test_receipt_is_stable_hash_addressed_and_detects_drift"
            )
            with self.assertRaisesRegex(QualificationError, "unexpected deployer tests"):
                generate_conformance_receipt(root, substituted, output)

            deployer = root / ".trellis/scripts/downstream_deployer/planning.py"
            deployer.write_text(
                deployer.read_text(encoding="utf-8") + "\n# uncommitted\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(QualificationError, "not exact in qualifying commit"):
                generate_conformance_receipt(
                    root,
                    qualification_result(root),
                    output,
                )

    def test_receipt_requires_passing_candidate_compatibility_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_receipt_repo(root)
            output = root / ".trellis/spec/project/receipts/loop-v1"
            result = qualification_result(root)

            missing = copy.deepcopy(result)
            missing.pop("candidate_compatibility")
            with self.assertRaisesRegex(
                QualificationError,
                "requires candidate compatibility",
            ):
                generate_conformance_receipt(root, missing, output)

            failed = copy.deepcopy(result)
            failed["candidate_compatibility"]["status"] = "failed"
            with self.assertRaisesRegex(
                QualificationError,
                "candidate compatibility evidence did not pass",
            ):
                generate_conformance_receipt(root, failed, output)

            missing_capability = copy.deepcopy(result)
            evidence = missing_capability["candidate_compatibility"]
            evidence["smoke_checks"].pop()
            payload = dict(evidence)
            payload.pop("evidence_digest")
            evidence["evidence_digest"] = qualification_module.digest_json(payload)
            with self.assertRaisesRegex(
                QualificationError,
                "candidate compatibility evidence differs",
            ):
                generate_conformance_receipt(root, missing_capability, output)

            receipt_path = generate_conformance_receipt(root, result, output)
            receipt = read_json(receipt_path)
            evidence = receipt["qualification"]["candidate_compatibility"]
            evidence["smoke_checks"].pop()
            evidence_payload = dict(evidence)
            evidence_payload.pop("evidence_digest")
            evidence["evidence_digest"] = qualification_module.digest_json(
                evidence_payload
            )
            receipt_payload = dict(receipt)
            receipt_payload.pop("receipt_digest")
            receipt_payload.pop("receipt_id")
            digest = qualification_module.digest_json(receipt_payload)
            receipt["receipt_digest"] = digest
            receipt["receipt_id"] = f"sha256:{digest}"
            forged = output / f"{digest}.json"
            forged.write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            verification = verify_conformance_receipt(root, forged)
            self.assertFalse(verification["valid"])
            self.assertIn(
                "candidate compatibility evidence differs",
                verification["issues"],
            )

    def test_receipt_rejects_forged_overlay_application_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_receipt_repo(root)
            output = root / ".trellis/spec/project/receipts/loop-v1"
            receipt_path = generate_conformance_receipt(
                root, qualification_result(root), output
            )
            receipt = read_json(receipt_path)
            receipt["overlay_application"]["preservation"] = {}
            payload = dict(receipt)
            payload.pop("receipt_digest")
            payload.pop("receipt_id")
            digest = qualification_module.digest_json(payload)
            receipt["receipt_digest"] = digest
            receipt["receipt_id"] = f"sha256:{digest}"
            forged = output / f"{digest}.json"
            forged.write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            verification = verify_conformance_receipt(root, forged)

            self.assertFalse(verification["valid"])
            self.assertIn(
                "overlay application did not preserve local owner bytes",
                verification["issues"],
            )

    def test_source_development_parent_create_accepts_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            source = root / ".trellis/scripts/loop_v1/qualification.py"
            source.write_text(
                source.read_text(encoding="utf-8") + "\n# unfinished source\n",
                encoding="utf-8",
            )
            self.assertFalse(configured_qualification(root).valid)
            args = argparse.Namespace(
                title="Qualified parent",
                slug="qualified-parent-task",
                assignee="jym",
                priority="P2",
                description="",
                parent=None,
                package=None,
                tier="parent",
                owner="codex",
                touches=[],
                workflow_mode="loop_v1",
            )
            stderr = io.StringIO()
            with ExitStack() as stack:
                stack.enter_context(mock.patch("common.task_store.get_repo_root", return_value=root))
                stack.enter_context(mock.patch("common.task_store.generate_task_date_prefix", return_value="07-13"))
                stack.enter_context(mock.patch("common.task_store.run_git", return_value=(0, "main\n", "")))
                stack.enter_context(mock.patch("common.task_store._write_template_files"))
                stack.enter_context(mock.patch("common.task_store._init_state_if_supported"))
                stack.enter_context(mock.patch("common.task_store._has_subagent_platform", return_value=False))
                stack.enter_context(mock.patch("common.task_store.run_task_hooks"))
                stack.enter_context(mock.patch("common.active_task.resolve_context_key", return_value=None))
                with redirect_stderr(stderr):
                    returncode = cmd_create(args)

            self.assertEqual(returncode, 0, stderr.getvalue())
            task = read_json(
                root / ".trellis/tasks/07-13-qualified-parent-task/task.json"
            )
            self.assertEqual(task["meta"]["workflow_mode"], "loop_v1")
            self.assertTrue(receipt.is_file())

    def test_start_approval_rejects_receipt_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qualify_repo(root)
            ledger, lease = ParentLedger.initialize(
                root,
                "receipt-substitution",
                selector="loop_v1",
                writer_id="qualification-writer",
            )
            envelope = start_envelope()
            envelope["conformance_receipt"] = "sha256:" + "0" * 64
            request = create_start_request(
                ledger,
                lease,
                request_id="substituted-start",
                envelope=envelope,
            )

            with self.assertRaises(QualificationError):
                approve_start_request(
                    ledger,
                    lease,
                    request_id="substituted-start",
                    request_digest=request["request_digest"],
                    response_identity="user:qualification",
                    response_at="2026-07-13T20:00:00Z",
                    direct_user_action=True,
                )

            self.assertEqual(parent_status(ledger), "initialized")

    def test_official_overlay_clean_clone_conformance_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            official = base / "official"
            overlay = base / "overlay"
            target = base / "target"
            for root in (official, overlay, target):
                root.mkdir()
            (official / "official.txt").write_text("official\n", encoding="utf-8")
            (overlay / "overlay.txt").write_text("overlay\n", encoding="utf-8")
            (target / "official.txt").write_text("official\n", encoding="utf-8")
            (target / "overlay.txt").write_text("overlay\n", encoding="utf-8")
            manifest = base / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_schema_version": 1,
                        "overlay_version": "fixture-v1",
                        "entries": [
                            {"path": "official.txt", "scope": "file", "owner": "official"},
                            {"path": "overlay.txt", "scope": "file", "owner": "overlay"},
                            {"path": "project.txt", "scope": "file", "owner": "project"},
                            {"path": "generated.txt", "scope": "file", "owner": "generated"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            passed = check_overlay_conformance(
                target,
                manifest,
                official_root=official,
                overlay_root=overlay,
            )
            self.assertEqual(passed["status"], "passed")
            (target / "official.txt").write_text("project drift\n", encoding="utf-8")
            before = (target / "official.txt").read_bytes()

            failed = check_overlay_conformance(
                target,
                manifest,
                official_root=official,
                overlay_root=overlay,
            )

            self.assertEqual(failed["status"], "failed")
            self.assertEqual((target / "official.txt").read_bytes(), before)

    def test_q01_q12_machine_run_is_complete_and_digest_bound(self) -> None:
        raw_installed_root = os.environ.get(qualification_module._INSTALLED_ROOT_ENV)
        if raw_installed_root:
            result = run_qualification_matrix(
                REPO_ROOT,
                installed_root=Path(raw_installed_root),
            )
        else:
            with tempfile.TemporaryDirectory() as tmp:
                installed = prepare_current_tool_installed_root(
                    Path(tmp) / "installed"
                )
                result = run_qualification_matrix(
                    REPO_ROOT,
                    installed_root=installed,
                )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            [scenario["id"] for scenario in result["scenarios"]],
            list(qualification_module.QUALIFICATION_IDS),
        )
        self.assertEqual(
            result["qualification_digest"],
            qualification_module.digest_json(
                [
                    qualification_module._stable_scenario(scenario)
                    for scenario in result["scenarios"]
                ]
            ),
        )
        self.assertEqual(
            [row["test_id"] for row in result["deployer_tests"]],
            list(qualification_module.DEPLOYER_TEST_IDS),
        )
        self.assertEqual(
            result["deployer_digest"],
            qualification_module.digest_json(
                [
                    qualification_module._stable_test_run(row)
                    for row in result["deployer_tests"]
                ]
            ),
        )
        self.assertFalse(
            any(
                "duration_ms" in run
                for scenario in result["scenarios"]
                for run in (scenario["positive"], scenario["negative"])
            )
            or any("duration_ms" in run for run in result["deployer_tests"])
        )
        self.assertEqual(result["candidate_compatibility"]["status"], "passed")


class QualificationLayerTests(unittest.TestCase):
    def _layered_receipts(self, base: Path) -> tuple[Path, Path, Path]:
        root = base / "runtime"
        root.mkdir()
        prepare_receipt_repo(root)
        artifact = generate_artifact_qualification_receipt(
            root,
            qualification_result(root),
            base / "artifact-receipts",
        )
        local = generate_local_runtime_receipt(
            root,
            artifact,
            base / "local-receipts",
        )
        return root, artifact, local

    def _target_plan(self, target: Path) -> dict[str, object]:
        return {
            "candidate": {"payload_digest": "b" * 64},
            "plan_digest": "a" * 64,
            "source": {"qualification_receipt_id": "sha256:" + "c" * 64},
            "target": {
                "branch": "main",
                "checks_digest": "d" * 64,
                "git_policy_digest": "e" * 64,
                "head": "f" * 40,
                "id": target.name,
                "instructions": {"AGENTS.md": "1" * 64},
                "preimage_digest": "2" * 64,
                "tree": "3" * 40,
                "verification_policy_digest": "4" * 64,
            },
        }

    def test_artifact_and_local_receipts_are_distinct_and_portable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, artifact, local = self._layered_receipts(base)
            artifact_data = read_json(artifact)

            self.assertTrue(
                verify_artifact_qualification_receipt(root, artifact)["valid"]
            )
            self.assertTrue(
                verify_local_runtime_receipt(root, artifact, local)["valid"]
            )
            self.assertEqual(artifact_data["layer"], "artifact")
            self.assertNotIn("target", artifact_data)
            self.assertNotIn("runtime", artifact_data)
            self.assertNotIn(str(root), artifact.read_text(encoding="utf-8"))
            self.assertEqual(read_qualification_receipt(artifact)["kind"], "artifact")
            with self.assertRaisesRegex(QualificationError, "source-owned"):
                generate_artifact_qualification_receipt(
                    root,
                    qualification_result(root),
                    base / "rejected-receipts",
                    manifest_path=base / "outside-manifest.json",
                )

            legacy = generate_conformance_receipt(
                root,
                qualification_result(root),
                base / "legacy-receipts",
            )
            legacy_read = read_qualification_receipt(legacy)
            self.assertTrue(legacy_read["readable"])
            self.assertEqual(legacy_read["kind"], "legacy")
            self.assertEqual(legacy_read["layers"]["artifact"], "inseparable")

    def test_local_and_artifact_invalidation_are_dependency_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, artifact, local = self._layered_receipts(base)
            local_data = read_json(local)
            changed_tools = dict(local_data["tools"])
            changed_tools["git"] = "git version changed"

            with mock.patch.object(
                qualification_module,
                "_local_tool_capabilities",
                return_value=changed_tools,
            ):
                local_verification = verify_local_runtime_receipt(root, artifact, local)
            self.assertFalse(local_verification["valid"])
            self.assertIn("local runtime tools differs", local_verification["issues"])

            workflow = root / ".trellis/workflow.md"
            workflow.write_text(
                workflow.read_text(encoding="utf-8") + "\nartifact drift\n",
                encoding="utf-8",
            )
            artifact_verification = verify_artifact_qualification_receipt(root, artifact)
            local_after_artifact_drift = verify_local_runtime_receipt(root, artifact, local)
            self.assertFalse(artifact_verification["valid"])
            self.assertFalse(local_after_artifact_drift["valid"])
            self.assertIn(
                "artifact receipt dependency is invalid",
                local_after_artifact_drift["issues"],
            )

    def test_installed_runtime_drift_does_not_invalidate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "runtime"
            root.mkdir()
            prepare_receipt_repo(root)
            artifact = generate_artifact_qualification_receipt(
                root,
                qualification_result(root),
                base / "artifact-receipts",
            )
            installed = prepare_current_tool_installed_root(base / "installed")
            installed_version = installed / ".trellis/.version"
            installed_version.write_bytes((root / ".trellis/.version").read_bytes())
            local = generate_local_runtime_receipt(
                root,
                artifact,
                base / "local-receipts",
                installed_root=installed,
            )
            installed_version.write_text("drift\n", encoding="utf-8")

            artifact_verification = verify_artifact_qualification_receipt(root, artifact)
            local_verification = verify_local_runtime_receipt(
                root,
                artifact,
                local,
                installed_root=installed,
            )

            self.assertTrue(artifact_verification["valid"], artifact_verification["issues"])
            self.assertFalse(local_verification["valid"])
            self.assertTrue(
                any(
                    "installed runtime payload differs" in issue
                    for issue in local_verification["issues"]
                )
            )

    def test_target_receipt_is_bound_to_one_plan_and_target_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, artifact, local = self._layered_receipts(base)
            target = base / "target"
            target.mkdir()
            plan = self._target_plan(target)
            final_receipt = {"receipt_id": "sha256:" + "5" * 64}

            def verified(
                _source: Path,
                _target: Path,
                plan_value: dict[str, object],
                final_value: dict[str, object],
                *,
                verification_commands: object,
            ) -> dict[str, object]:
                self.assertEqual(verification_commands, (("true",),))
                return {
                    "checks_result_digest": "6" * 64,
                    "committed": False,
                    "plan_digest": plan_value["plan_digest"],
                    "receipt_id": final_value["receipt_id"],
                    "status": "verified",
                    "target_id": plan_value["target"]["id"],
                }

            with mock.patch(
                "downstream_deployer.transaction.verify_transaction",
                side_effect=verified,
            ):
                receipt = generate_target_verification_receipt(
                    root,
                    target,
                    artifact,
                    local,
                    plan,
                    final_receipt,
                    (("true",),),
                    base / "target-receipts",
                )
                verification = verify_target_verification_receipt(
                    root,
                    target,
                    artifact,
                    local,
                    receipt,
                    plan,
                    final_receipt,
                    (("true",),),
                )
                changed_plan = copy.deepcopy(plan)
                changed_plan["target"]["head"] = "7" * 40
                invalidated = verify_target_verification_receipt(
                    root,
                    target,
                    artifact,
                    local,
                    receipt,
                    changed_plan,
                    final_receipt,
                    (("true",),),
                )

            self.assertTrue(verification["valid"], verification["issues"])
            self.assertFalse(invalidated["valid"])
            self.assertIn("target verification target differs", invalidated["issues"])

    def test_layer_cli_generates_and_reads_an_artifact_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "runtime"
            root.mkdir()
            prepare_receipt_repo(root)
            result_path = base / "qualification.json"
            result_path.write_text(
                json.dumps(qualification_result(root), sort_keys=True),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = qualification_module.main(
                    [
                        "--repo-root",
                        str(root),
                        "artifact-receipt",
                        "--qualification",
                        str(result_path),
                        "--output-dir",
                        str(base / "artifact-receipts"),
                    ]
                )
            generated = json.loads(output.getvalue())
            receipt = base / "artifact-receipts" / (
                generated["receipt_id"].removeprefix("sha256:") + ".json"
            )
            output = io.StringIO()
            with redirect_stdout(output):
                read_code = qualification_module.main(
                    [
                        "--repo-root",
                        str(root),
                        "receipt-read",
                        "--receipt",
                        str(receipt),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(generated["status"], "generated")
            self.assertEqual(read_code, 0)
            self.assertEqual(json.loads(output.getvalue())["kind"], "artifact")

    def test_layer_config_keeps_legacy_admission_until_explicit_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = qualify_repo(root)
            config = root / ".trellis/config.yaml"
            config.write_text(
                config.read_text(encoding="utf-8")
                + "qualification_layers:\n  admission_mode: legacy\n",
                encoding="utf-8",
            )
            self.assertTrue(configured_qualification(root).valid)

            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "admission_mode: legacy",
                    "admission_mode: composite",
                ),
                encoding="utf-8",
            )
            status = configured_qualification(root)
            self.assertFalse(status.valid)
            self.assertTrue(
                any("requires explicit migration/cutover" in issue for issue in status.issues)
            )
            self.assertEqual(read_qualification_receipt(receipt)["kind"], "legacy")


if __name__ == "__main__":
    unittest.main()
