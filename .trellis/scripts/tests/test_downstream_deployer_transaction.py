from __future__ import annotations

import json
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downstream_deployer import planning, transaction
from downstream_deployer.planning import plan_target
from downstream_deployer.transaction import TransactionError
from loop_v1.qualification import DEPLOYMENT_TARGETS, digest_json
from test_downstream_deployer_plan import (
    git,
    materialize_candidate,
    prepare_repositories,
    qualified,
)


def materializer(source: Path):
    def materialize(candidate: Path) -> dict[str, object]:
        materialize_candidate(source, candidate)
        empty = sha256(b"").hexdigest()
        return {
            "command": ["trellis", "update", "--force", "--migrate"],
            "returncode": 0,
            "stderr_digest": empty,
            "stdout_digest": empty,
        }

    return materialize


def checks(*, target_failure: bool = False) -> tuple[tuple[str, ...], ...]:
    assertion = (
        "assert not Path('.git').exists()"
        if target_failure
        else "assert Path('official.txt').read_text() == 'official:new\\n'"
    )
    return (
        (
            sys.executable,
            "-c",
            f"from pathlib import Path; {assertion}",
        ),
    )


def create_plan(
    source: Path,
    target: Path,
    scratch: Path,
    commands: tuple[tuple[str, ...], ...],
) -> tuple[Path, dict[str, object]]:
    candidate = scratch / "candidate-artifact"
    with (
        mock.patch(
            "downstream_deployer.planning.configured_qualification",
            return_value=qualified(),
        ),
        mock.patch(
            "downstream_deployer.planning._materialize_official",
            side_effect=materializer(source),
        ),
    ):
        plan = plan_target(
            source,
            target,
            DEPLOYMENT_TARGETS[0],
            manifest_path=source / "manifest.json",
            scratch_root=scratch,
            candidate_output=candidate,
            verification_commands=commands,
        )
    return candidate, plan


def commit_authority(plan: dict[str, object]) -> dict[str, object]:
    return {
        "authority_id": "sha256:" + "b" * 64,
        "authority_kind": "one_off",
        "base_head": plan["target"]["head"],
        "branch": plan["target"]["branch"],
        "enabled": True,
        "lineage_anchor": plan["target"]["head"],
        "plan_digest": plan["plan_digest"],
        "source_receipt_id": plan["source"]["qualification_receipt_id"],
    }


def apply_fixture(
    source: Path,
    target: Path,
    candidate: Path,
    plan: dict[str, object],
    **kwargs,
) -> dict[str, object]:
    with mock.patch(
        "downstream_deployer.planning.configured_qualification",
        return_value=qualified(),
    ):
        return transaction.apply_transaction(
            source,
            target,
            candidate,
            plan,
            **kwargs,
        )


class DownstreamDeployerTransactionTests(unittest.TestCase):
    def test_exact_promotion_receipt_and_local_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            recovery = base / "recovery"
            recovery.mkdir()
            candidate, plan = create_plan(
                source,
                target,
                scratch,
                checks(),
            )
            base_head = git(target, "rev-parse", "HEAD")
            self.assertTrue(plan["candidate"]["artifact_persisted"])
            self.assertNotIn(str(base), json.dumps(plan, sort_keys=True))

            result = apply_fixture(
                source,
                target,
                candidate,
                plan,
                verification_commands=checks(),
                recovery_root=recovery,
                local_commit_authority=commit_authority(plan),
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(
                (target / "official.txt").read_text(encoding="utf-8"),
                "official:new\n",
            )
            self.assertEqual(
                (target / ".trellis/.version").read_text(encoding="utf-8"),
                "fixture-v2",
            )
            self.assertEqual(
                json.loads(
                    (target / ".trellis/.template-hashes.json").read_text(
                        encoding="utf-8"
                    )
                )["hashes"],
                {"target-materialized.txt": "c" * 64},
            )
            self.assertEqual(
                (target / "overlay/managed.txt").read_text(encoding="utf-8"),
                "overlay:new\n",
            )
            projection = json.loads(
                (target / ".trellis/deploy/adoption.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(projection["plan_digest"], plan["plan_digest"])
            self.assertEqual(
                projection["target"]["receipt_id"],
                result["candidate_receipt"]["receipt_id"],
            )
            commit = result["commit"]
            self.assertEqual(commit["parent"], base_head)
            self.assertEqual(commit["authority_kind"], "one_off")
            self.assertEqual(git(target, "status", "--porcelain=v1"), "")
            self.assertEqual(
                set(
                    git(
                        target,
                        "diff-tree",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        "HEAD",
                    ).splitlines()
                ),
                {
                    ".trellis/deploy/adoption.json",
                    "official.txt",
                    "overlay/managed.txt",
                },
            )
            author, email, message = git(
                target,
                "show",
                "-s",
                "--format=%an%x00%ae%x00%B",
                "HEAD",
            ).split("\0", 2)
            self.assertEqual(author, "Trellis Loop Updater")
            self.assertEqual(email, "trellis-loop-updater@localhost")
            self.assertIn("Trellis-Grant: sha256:" + "b" * 64, message)
            self.assertIn("Trellis-Plan: sha256:" + plan["plan_digest"], message)
            self.assertEqual(git(target, "config", "user.name"), "Deployer Test")
            self.assertEqual(len(list(recovery.iterdir())), 1)
            encoded = json.dumps(result, sort_keys=True)
            self.assertNotIn(str(base), encoded)
            self.assertNotIn("official:new", encoded)
            with mock.patch(
                "downstream_deployer.planning.configured_qualification",
                return_value=qualified(),
            ):
                verified = transaction.verify_transaction(
                    source,
                    target,
                    plan,
                    result["final_receipt"],
                    verification_commands=checks(),
                )
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(
                verified["receipt_id"],
                result["final_receipt"]["receipt_id"],
            )

    def test_apply_does_not_imply_local_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            recovery = base / "recovery"
            recovery.mkdir()
            candidate, plan = create_plan(source, target, scratch, checks())
            base_head = git(target, "rev-parse", "HEAD")
            task = source / ".trellis/tasks/active/task.json"
            task.parent.mkdir(parents=True)
            task.write_text('{"status":"in_progress"}\n', encoding="utf-8")
            (source / "BOARD.md").write_text("active task evidence\n", encoding="utf-8")
            source_status = git(source, "status", "--porcelain=v1")

            result = apply_fixture(
                source,
                target,
                candidate,
                plan,
                verification_commands=checks(),
                recovery_root=recovery,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertIsNone(result["commit"])
            self.assertEqual(git(target, "rev-parse", "HEAD"), base_head)
            self.assertEqual(git(source, "status", "--porcelain=v1"), source_status)
            self.assertEqual(
                transaction._changed_paths(target),
                {
                    ".trellis/deploy/adoption.json",
                    "official.txt",
                    "overlay/managed.txt",
                },
            )

    def test_preflight_drift_and_authority_reject_without_writes(self) -> None:
        cases = ("candidate", "authority", "source", "hook_drift")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source, target, scratch = prepare_repositories(base)
                recovery = base / "recovery"
                recovery.mkdir()
                candidate, plan = create_plan(
                    source,
                    target,
                    scratch,
                    checks(),
                )
                before = planning._repo_identity(target, "TARGET")
                authority = commit_authority(plan)
                expected = "CANDIDATE_DRIFT"
                if case == "candidate":
                    (candidate / "official.txt").write_text(
                        "drift\n",
                        encoding="utf-8",
                    )
                elif case == "source":
                    (source / "overlay/managed.txt").write_text(
                        "overlay:drift\n",
                        encoding="utf-8",
                    )
                    expected = "SOURCE_DRIFT"
                elif case == "hook_drift":
                    hook = target / ".git/hooks/pre-commit"
                    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    hook.chmod(0o755)
                    expected = "TARGET_DRIFT"
                else:
                    authority["plan_digest"] = "0" * 64
                    expected = "COMMIT_AUTHORITY_INVALID"
                with self.assertRaisesRegex(TransactionError, expected):
                    apply_fixture(
                        source,
                        target,
                        candidate,
                        plan,
                        verification_commands=checks(),
                        recovery_root=recovery,
                        local_commit_authority=authority,
                    )
                after = planning._repo_identity(target, "TARGET")
                if case == "hook_drift":
                    for field in ("head", "snapshot_digest", "status_digest", "tree"):
                        self.assertEqual(after[field], before[field])
                else:
                    self.assertEqual(after, before)
                self.assertEqual(list(recovery.iterdir()), [])

    def test_mutation_failure_recovers_or_reports_unknown(self) -> None:
        for outcome in ("recovered", "unknown_outcome", "hook_failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source, target, scratch = prepare_repositories(base)
                recovery = base / "recovery"
                recovery.mkdir()
                if outcome == "hook_failure":
                    hook = target / ".git/hooks/pre-commit"
                    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    hook.chmod(0o755)
                    commands = checks()
                else:
                    commands = checks(target_failure=True)
                candidate, plan = create_plan(
                    source,
                    target,
                    scratch,
                    commands,
                )
                before = planning._repo_identity(target, "TARGET")
                authority = (
                    commit_authority(plan) if outcome == "hook_failure" else None
                )
                restore = (
                    mock.patch(
                        "downstream_deployer.transaction._restore_preimage",
                        side_effect=TransactionError("RECOVERY_FAULT"),
                    )
                    if outcome == "unknown_outcome"
                    else mock.patch(
                        "downstream_deployer.transaction._restore_preimage",
                        wraps=transaction._restore_preimage,
                    )
                )
                with restore:
                    result = apply_fixture(
                        source,
                        target,
                        candidate,
                        plan,
                        verification_commands=commands,
                        recovery_root=recovery,
                        local_commit_authority=authority,
                    )
                expected_status = (
                    "recovered" if outcome == "hook_failure" else outcome
                )
                self.assertEqual(result["status"], expected_status)
                self.assertNotEqual(result["status"], "succeeded")
                self.assertEqual(
                    result["reason_code"],
                    "GIT_COMMIT_FAILED"
                    if outcome == "hook_failure"
                    else "CHECK_FAILED",
                )
                if outcome == "unknown_outcome":
                    self.assertEqual(
                        result["recovery"]["reason_code"],
                        "RECOVERY_FAULT",
                    )
                    self.assertNotEqual(git(target, "status", "--porcelain=v1"), "")
                else:
                    self.assertEqual(planning._repo_identity(target, "TARGET"), before)
                self.assertEqual(len(list(recovery.iterdir())), 1)

    def test_standalone_recovery_requires_exact_consumed_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            recovery = base / "recovery"
            recovery.mkdir()
            candidate, plan = create_plan(
                source,
                target,
                scratch,
                checks(target_failure=True),
            )
            before = planning._repo_identity(target, "TARGET")
            with mock.patch(
                "downstream_deployer.transaction._restore_preimage",
                side_effect=TransactionError("RECOVERY_FAULT"),
            ):
                result = apply_fixture(
                    source,
                    target,
                    candidate,
                    plan,
                    verification_commands=checks(target_failure=True),
                    recovery_root=recovery,
                )
            self.assertEqual(result["status"], "unknown_outcome")
            persisted = json.loads(
                (next(recovery.iterdir()) / "preimage.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(persisted["target"]),
                {"branch", "head", "identity_digest"},
            )
            self.assertNotIn("snapshot", persisted["target"])
            binding = {
                "adoption_receipt_id": "sha256:" + "a" * 64,
                "base_head": plan["target"]["head"],
                "branch": plan["target"]["branch"],
                "check_policy_digest": "1" * 64,
                "commit_policy_digest": "2" * 64,
                "deployment_policy_digest": "3" * 64,
                "effect_digest": "4" * 64,
                "failed_transaction_id": result["transaction_id"],
                "lineage_anchor": plan["target"]["head"],
                "notification_policy_digest": "5" * 64,
                "ownership_digest": "6" * 64,
                "plan_digest": plan["plan_digest"],
                "preimage_digest": result["recovery"]["preimage_digest"],
                "preservation_digest": "7" * 64,
                "recovery_policy_digest": "8" * 64,
                "repository_id": "repo:" + target.name,
                "source_receipt_id": plan["source"]["qualification_receipt_id"],
                "target_id": target.name,
            }
            authority = {
                "authority_id": "sha256:" + "b" * 64,
                "authority_kind": "one_off",
                "binding_digest": digest_json(binding),
                "consumption_id": "recover-consumption",
                "effect": "recover",
                "enabled": True,
            }

            receipt = transaction.recover_transaction(
                target,
                recovery,
                result["transaction_id"],
                authority,
                binding,
            )

            self.assertEqual(receipt["status"], "recovered")
            self.assertEqual(planning._repo_identity(target, "TARGET"), before)

    def test_apply_rejects_symlinked_recovery_root_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            recovery = base / "recovery"
            recovery.mkdir()
            recovery_link = base / "recovery-link"
            recovery_link.symlink_to(recovery, target_is_directory=True)
            candidate, plan = create_plan(source, target, scratch, checks())
            before = planning._repo_identity(target, "TARGET")

            with self.assertRaisesRegex(
                TransactionError,
                "RECOVERY_ROOT_INVALID",
            ):
                apply_fixture(
                    source,
                    target,
                    candidate,
                    plan,
                    verification_commands=checks(),
                    recovery_root=recovery_link,
                )

            self.assertEqual(planning._repo_identity(target, "TARGET"), before)


if __name__ == "__main__":
    unittest.main()
