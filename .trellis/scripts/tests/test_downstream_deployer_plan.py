from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downstream_deployer import planning
from downstream_deployer.planning import PlanError, plan_target
from loop_v1.qualification import DEPLOYMENT_TARGETS, QualificationStatus


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "--all")
    git(root, "commit", "-q", "-m", message)


def deployment_manifest() -> dict[str, object]:
    return {
        "deployment": {
            "adoption_projection": {
                "delete": "never",
                "owner": "project",
                "path": ".trellis/deploy/adoption.json",
                "source_disposition": "preserve",
                "write": "deployer-replace-only",
            },
            "intentional_deletions": [],
            "policy_schema_version": 1,
            "preserve": [
                ".trellis/config.yaml",
                ".trellis/tasks",
                ".trellis/workspace",
                ".trellis/.runtime",
                "AGENTS.md",
                "HANDOFF.md",
                "README.md",
            ],
            "source_id": "trellis-harness",
            "target_only_owner": "project",
            "targets": list(DEPLOYMENT_TARGETS),
        },
        "entries": [
            {
                "owner": "official",
                "path": ".trellis/.template-hashes.json",
                "scope": "file",
            },
            {"owner": "official", "path": ".trellis/.version", "scope": "file"},
            {"owner": "official", "path": "official.txt", "scope": "file"},
            {"owner": "overlay", "path": "overlay", "scope": "tree"},
            {"owner": "project", "path": ".trellis/config.yaml", "scope": "file"},
            {
                "owner": "project",
                "path": ".trellis/deploy/adoption.json",
                "scope": "file",
            },
            {"owner": "project", "path": ".trellis/tasks", "scope": "tree"},
            {"owner": "project", "path": ".trellis/workspace", "scope": "tree"},
            {"owner": "project", "path": "AGENTS.md", "scope": "file"},
            {"owner": "project", "path": "HANDOFF.md", "scope": "file"},
            {"owner": "project", "path": "README.md", "scope": "file"},
            {"owner": "generated", "path": ".trellis/.runtime", "scope": "tree"},
            {"owner": "generated", "path": "BOARD.md", "scope": "file"},
        ],
        "manifest_schema_version": 2,
        "official_release": "fixture",
        "overlay_version": "fixture-v1",
    }


def prepare_repositories(
    base: Path,
    target_id: str = DEPLOYMENT_TARGETS[0],
) -> tuple[Path, Path, Path]:
    source = base / "source"
    target = base / target_id
    scratch = base / "scratch"
    for root in (source, target, scratch):
        root.mkdir()

    (source / "overlay").mkdir()
    (source / ".trellis").mkdir()
    (source / "official.txt").write_text("official:new\n", encoding="utf-8")
    (source / "overlay/managed.txt").write_text("overlay:new\n", encoding="utf-8")
    (source / ".trellis/.gitignore").write_text(
        ".template-hashes.json\n.version\n",
        encoding="utf-8",
    )
    (source / ".trellis/.version").write_text("fixture-v2", encoding="utf-8")
    (source / ".trellis/.template-hashes.json").write_text(
        json.dumps(
            {
                "__version": 2,
                "hashes": {"source-managed.txt": "a" * 64},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(deployment_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    (target / "overlay").mkdir()
    (target / ".trellis").mkdir()
    (target / "official.txt").write_text("official:old\n", encoding="utf-8")
    (target / "overlay/managed.txt").write_text("overlay:old\n", encoding="utf-8")
    (target / ".trellis/config.yaml").write_text("target: true\n", encoding="utf-8")
    (target / ".trellis/.gitignore").write_text(
        ".template-hashes.json\n.version\n",
        encoding="utf-8",
    )
    (target / ".trellis/.version").write_text("fixture-v1", encoding="utf-8")
    (target / ".trellis/.template-hashes.json").write_text(
        json.dumps(
            {
                "__version": 2,
                "hashes": {"target-managed.txt": "b" * 64},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (target / "AGENTS.md").write_text("target agents\n", encoding="utf-8")
    (target / "HANDOFF.md").write_text("target handoff\n", encoding="utf-8")
    (target / "README.md").write_text("target readme\n", encoding="utf-8")
    (target / "BOARD.md").write_text("target board\n", encoding="utf-8")

    for root in (source, target):
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "Deployer Test")
        git(root, "config", "user.email", "deployer@example.invalid")
        commit_all(root, "fixture")
    return source, target, scratch


def qualified() -> QualificationStatus:
    return QualificationStatus(
        enforced=True,
        valid=True,
        receipt_id="sha256:" + "a" * 64,
        issues=(),
    )


def materialize_candidate(source: Path, candidate: Path) -> None:
    (candidate / "official.txt").write_bytes(
        (source / "official.txt").read_bytes()
    )
    (candidate / ".trellis/.version").write_bytes(
        (source / ".trellis/.version").read_bytes()
    )
    (candidate / ".trellis/.template-hashes.json").write_text(
        json.dumps(
            {
                "__version": 2,
                "hashes": {"target-materialized.txt": "c" * 64},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def declare_stale_deletion(
    source: Path,
    target: Path,
    preimage_sha256: str | None = None,
) -> None:
    stale = target / "overlay/stale.txt"
    stale.write_text("stale\n", encoding="utf-8")
    commit_all(target, "stale overlay")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["deployment"]["intentional_deletions"] = [
        {
            "owner": "overlay",
            "path": "overlay/stale.txt",
            "preimage_sha256": preimage_sha256
            or sha256(stale.read_bytes()).hexdigest(),
            "scope": "file",
        }
    ]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    commit_all(source, "declare deletion")


class DownstreamDeployerPlanTests(unittest.TestCase):
    def test_timestamped_backup_paths_have_stable_evidence_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            target.mkdir()
            target_snapshot = planning._snapshot(target)
            restored_runs = []

            for timestamp in ("2026-07-26T00-00-01", "2026-07-26T00-00-02"):
                candidate = base / f"candidate-{timestamp}"
                backup = (
                    candidate
                    / f".trellis/.backup-{timestamp}/parent/prd.md"
                )
                backup.parent.mkdir(parents=True)
                backup.write_text("ephemeral\n", encoding="utf-8")
                restored_runs.append(
                    planning._restore_preserved_paths(
                        target,
                        candidate,
                        target_snapshot,
                        deployment_manifest(),
                    )
                )

            self.assertEqual(
                restored_runs,
                [
                    [".trellis/.backup-TIMESTAMP/parent/prd.md"],
                    [".trellis/.backup-TIMESTAMP/parent/prd.md"],
                ],
            )

    def test_ah_map_is_one_off_plan_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_id = "AH-map"
            source, target, scratch = prepare_repositories(base, target_id)
            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=lambda root: (
                        materialize_candidate(source, root)
                        or {
                            "command": list(planning._OFFICIAL_COMMAND),
                            "returncode": 0,
                            "stderr_digest": sha256(b"").hexdigest(),
                            "stdout_digest": sha256(b"").hexdigest(),
                        }
                    ),
                ),
            ):
                plan = plan_target(
                    source,
                    target,
                    target_id,
                    manifest_path=source / "manifest.json",
                    scratch_root=scratch,
                )

            self.assertEqual(plan["target"]["id"], target_id)
            self.assertTrue(plan["read_only"]["verified"])
            self.assertEqual(list(scratch.iterdir()), [])

    def test_private_candidate_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            source_before = git(source, "status", "--porcelain=v1")
            target_before = git(target, "status", "--porcelain=v1")

            executable_root = base / "bin"
            adapter_candidate = base / "adapter-candidate"
            executable_root.mkdir()
            adapter_candidate.mkdir()
            executable = executable_root / "trellis"
            executable.write_text(
                "#!/bin/sh\nprintf 'materialized\\n' > official-marker.txt\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": os.defpath,
                    planning._TRELLIS_CLI_ENV: str(executable),
                },
            ):
                adapter_evidence = planning._materialize_official(adapter_candidate)
            self.assertEqual(
                adapter_evidence["command"],
                ["trellis", "update", "--force", "--migrate"],
            )
            self.assertEqual(adapter_evidence["returncode"], 0)
            self.assertEqual(
                (adapter_candidate / "official-marker.txt").read_text(
                    encoding="utf-8"
                ),
                "materialized\n",
            )
            with (
                mock.patch.dict(
                    os.environ,
                    {planning._TRELLIS_CLI_ENV: str(base / "missing-trellis")},
                ),
                self.assertRaisesRegex(
                    PlanError, "TRELLIS_CLI is not an executable file"
                ),
            ):
                planning._materialize_official(adapter_candidate)

            def materialize(candidate: Path) -> dict[str, object]:
                materialize_candidate(source, candidate)
                (candidate / "README.md").write_text(
                    "official must not replace project bytes\n",
                    encoding="utf-8",
                )
                backup = candidate / ".trellis/.backup-probe/file.txt"
                backup.parent.mkdir(parents=True)
                backup.write_text("ephemeral\n", encoding="utf-8")
                (candidate / "overlay/managed.txt").write_text(
                    "official-stage\n",
                    encoding="utf-8",
                )
                empty = sha256(b"").hexdigest()
                return {
                    "command": ["trellis", "update", "--force", "--migrate"],
                    "returncode": 0,
                    "stderr_digest": empty,
                    "stdout_digest": empty,
                }

            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materialize,
                ),
            ):
                first = plan_target(
                    source,
                    target,
                    DEPLOYMENT_TARGETS[0],
                    manifest_path=source / "manifest.json",
                    scratch_root=scratch,
                )
                second = plan_target(
                    source,
                    target,
                    DEPLOYMENT_TARGETS[0],
                    manifest_path=source / "manifest.json",
                    scratch_root=scratch,
                )

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "planned")
            self.assertTrue(first["read_only"]["verified"])
            self.assertEqual(
                [phase["name"] for phase in first["phases"]],
                [
                    "target_preimage",
                    "official_materialization",
                    "post_materialization_preimage",
                    "overlay_application",
                    "candidate_complete",
                    "candidate_verification",
                ],
            )
            self.assertNotEqual(
                first["phases"][2]["evidence_digest"],
                first["phases"][4]["evidence_digest"],
            )
            self.assertEqual(
                first["candidate"]["predicted_mutations"],
                [
                    {
                        "change": "update",
                        "owner": "official",
                        "path": ".trellis/.template-hashes.json",
                    },
                    {
                        "change": "update",
                        "owner": "official",
                        "path": ".trellis/.version",
                    },
                    {"change": "update", "owner": "official", "path": "official.txt"},
                    {
                        "change": "update",
                        "owner": "overlay",
                        "path": "overlay/managed.txt",
                    },
                ],
            )
            self.assertEqual(first["scratch"]["lifecycle"], "cleaned")
            self.assertIn(
                "README.md",
                first["phases"][1]["evidence"]["preserved_paths"],
            )
            self.assertIn(
                ".trellis/.backup-probe/file.txt",
                first["phases"][1]["evidence"]["preserved_paths"],
            )
            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(git(source, "status", "--porcelain=v1"), source_before)
            self.assertEqual(git(target, "status", "--porcelain=v1"), target_before)
            encoded = json.dumps(first, sort_keys=True)
            self.assertNotIn(str(source), encoded)
            self.assertNotIn(str(target), encoded)
            self.assertNotIn(str(scratch), encoded)

            declare_stale_deletion(source, target)
            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materialize,
                ),
            ):
                deletion_plan = plan_target(
                    source,
                    target,
                    DEPLOYMENT_TARGETS[0],
                    manifest_path=source / "manifest.json",
                    scratch_root=scratch,
                )
            self.assertIn(
                {
                    "change": "delete",
                    "owner": "overlay",
                    "path": "overlay/stale.txt",
                },
                deletion_plan["candidate"]["predicted_mutations"],
            )
            self.assertEqual(
                deletion_plan["phases"][3]["deleted"],
                ["overlay/stale.txt"],
            )
            self.assertTrue((target / "overlay/stale.txt").is_file())
            self.assertEqual(list(scratch.iterdir()), [])

    def test_candidate_checks_gate_artifact_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            artifacts = base / "artifacts"
            artifacts.mkdir()
            candidate = artifacts / "candidate"
            source_before = planning._repo_identity(source, "SOURCE")
            target_before = planning._repo_identity(target, "TARGET")
            commands = (
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('overlay/managed.txt').read_text() == "
                    "'overlay:new\\n'",
                ),
            )

            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=lambda root: (
                        materialize_candidate(source, root)
                        or {
                            "command": list(planning._OFFICIAL_COMMAND),
                            "returncode": 0,
                            "stderr_digest": sha256(b"").hexdigest(),
                            "stdout_digest": sha256(b"").hexdigest(),
                        }
                    ),
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

            self.assertTrue(candidate.is_dir())
            self.assertEqual(
                plan["candidate"]["checks_result_digest"],
                plan["phases"][-1]["evidence_digest"],
            )

            failed_candidate = artifacts / "failed-candidate"
            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=lambda root: (
                        materialize_candidate(source, root)
                        or {
                            "command": list(planning._OFFICIAL_COMMAND),
                            "returncode": 0,
                            "stderr_digest": sha256(b"").hexdigest(),
                            "stdout_digest": sha256(b"").hexdigest(),
                        }
                    ),
                ),
            ):
                with self.assertRaisesRegex(PlanError, "CHECK_FAILED"):
                    plan_target(
                        source,
                        target,
                        DEPLOYMENT_TARGETS[0],
                        manifest_path=source / "manifest.json",
                        scratch_root=scratch,
                        candidate_output=failed_candidate,
                        verification_commands=(
                            (sys.executable, "-c", "raise SystemExit(9)"),
                        ),
                    )

            self.assertFalse(failed_candidate.exists())
            self.assertEqual(planning._repo_identity(source, "SOURCE"), source_before)
            self.assertEqual(planning._repo_identity(target, "TARGET"), target_before)

            mutated_candidate = artifacts / "mutated-candidate"
            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=lambda root: (
                        materialize_candidate(source, root)
                        or {
                            "command": list(planning._OFFICIAL_COMMAND),
                            "returncode": 0,
                            "stderr_digest": sha256(b"").hexdigest(),
                            "stdout_digest": sha256(b"").hexdigest(),
                        }
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    PlanError,
                    "CHECK_MUTATED_CANDIDATE",
                ):
                    plan_target(
                        source,
                        target,
                        DEPLOYMENT_TARGETS[0],
                        manifest_path=source / "manifest.json",
                        scratch_root=scratch,
                        candidate_output=mutated_candidate,
                        verification_commands=(
                            (
                                sys.executable,
                                "-c",
                                "from pathlib import Path; "
                                "Path('check-write').write_text('changed')",
                            ),
                        ),
                    )

            self.assertFalse(mutated_candidate.exists())

    def test_qualified_source_ignores_task_evidence_and_rejects_payload_aba(
        self,
    ) -> None:
        def materialize(source: Path):
            def run(candidate: Path) -> dict[str, object]:
                materialize_candidate(source, candidate)
                empty = sha256(b"").hexdigest()
                return {
                    "command": ["trellis", "update", "--force", "--migrate"],
                    "returncode": 0,
                    "stderr_digest": empty,
                    "stdout_digest": empty,
                }

            return run

        with tempfile.TemporaryDirectory() as tmp:
            source, target, scratch = prepare_repositories(Path(tmp))
            task = source / ".trellis/tasks/active/task.json"
            task.parent.mkdir(parents=True)
            task.write_text('{"status":"in_progress"}\n', encoding="utf-8")
            (source / "BOARD.md").write_text("active task evidence\n", encoding="utf-8")
            source_status = git(source, "status", "--porcelain=v1")
            target_before = planning._repo_identity(target, "TARGET")

            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materialize(source),
                ),
            ):
                plan = plan_target(
                    source,
                    target,
                    DEPLOYMENT_TARGETS[0],
                    manifest_path=source / "manifest.json",
                    scratch_root=scratch,
                )

            self.assertEqual(plan["status"], "planned")
            self.assertEqual(git(source, "status", "--porcelain=v1"), source_status)
            self.assertEqual(planning._repo_identity(target, "TARGET"), target_before)

        with tempfile.TemporaryDirectory() as tmp:
            source, target, scratch = prepare_repositories(Path(tmp))
            target_before = planning._repo_identity(target, "TARGET")
            original_apply = planning._apply_overlay

            def transient_overlay(
                source_root: Path,
                candidate: Path,
                manifest: dict[str, object],
            ) -> dict[str, str]:
                managed = source_root / "overlay/managed.txt"
                original = managed.read_bytes()
                managed.write_text("overlay:transient\n", encoding="utf-8")
                try:
                    return original_apply(source_root, candidate, manifest)
                finally:
                    managed.write_bytes(original)

            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materialize(source),
                ),
                mock.patch(
                    "downstream_deployer.planning._apply_overlay",
                    side_effect=transient_overlay,
                ),
            ):
                with self.assertRaisesRegex(
                    PlanError,
                    "SOURCE_MUTATED_DURING_PLAN",
                ):
                    plan_target(
                        source,
                        target,
                        DEPLOYMENT_TARGETS[0],
                        manifest_path=source / "manifest.json",
                        scratch_root=scratch,
                        candidate_output=scratch / "candidate",
                    )

            self.assertEqual(
                (source / "overlay/managed.txt").read_text(encoding="utf-8"),
                "overlay:new\n",
            )
            self.assertEqual(planning._repo_identity(target, "TARGET"), target_before)
            self.assertEqual(list(scratch.iterdir()), [])

    def test_planning_rejects_drift_and_ownership_conflicts(self) -> None:
        def run_case(
            prepare,
            expected: str,
            *,
            qualification: QualificationStatus | None = None,
            invalid_metadata: bool = False,
            invalid_version: bool = False,
            target_id: str = DEPLOYMENT_TARGETS[0],
        ) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                source, target, scratch = prepare_repositories(Path(tmp))
                prepare(source, target)

                def materialize(candidate: Path) -> dict[str, object]:
                    materialize_candidate(source, candidate)
                    if invalid_metadata:
                        (candidate / ".trellis/.template-hashes.json").write_text(
                            '{"__version":2,"hashes":{"../escape":"bad"}}',
                            encoding="utf-8",
                        )
                    if invalid_version:
                        (candidate / ".trellis/.version").write_text(
                            "wrong-release",
                            encoding="utf-8",
                        )
                    empty = sha256(b"").hexdigest()
                    return {
                        "command": ["trellis", "update", "--force", "--migrate"],
                        "returncode": 0,
                        "stderr_digest": empty,
                        "stdout_digest": empty,
                    }

                with (
                    mock.patch(
                        "downstream_deployer.planning.configured_qualification",
                        return_value=qualification or qualified(),
                    ),
                    mock.patch(
                        "downstream_deployer.planning._materialize_official",
                        side_effect=materialize,
                    ),
                ):
                    with self.assertRaisesRegex(PlanError, expected):
                        plan_target(
                            source,
                            target,
                            target_id,
                            manifest_path=source / "manifest.json",
                            scratch_root=scratch,
                        )
                self.assertEqual(list(scratch.iterdir()), [])

        def active_target(source: Path, target: Path) -> None:
            task = target / ".trellis/tasks/active/task.json"
            task.parent.mkdir(parents=True)
            task.write_text('{"status":"planning"}\n', encoding="utf-8")

        def type_conflict(source: Path, target: Path) -> None:
            (target / "overlay/managed.txt").unlink()
            (target / "overlay").rmdir()
            (target / "overlay").write_text("not a tree\n", encoding="utf-8")
            commit_all(target, "type conflict")

        def bad_deletion(source: Path, target: Path) -> None:
            declare_stale_deletion(source, target, "0" * 64)

        cases = [
            (
                "target binding",
                lambda source, target: None,
                "TARGET_BINDING_MISMATCH",
                {"target_id": DEPLOYMENT_TARGETS[1]},
            ),
            (
                "invalid qualification",
                lambda source, target: None,
                "SOURCE_QUALIFICATION_INVALID",
                {
                    "qualification": QualificationStatus(
                        enforced=True,
                        valid=False,
                        receipt_id=None,
                        issues=("drift",),
                    )
                },
            ),
            (
                "target active task",
                active_target,
                "TARGET_ACTIVE_TASK",
                {},
            ),
            (
                "target dirt",
                lambda source, target: (target / "dirty.txt").write_text(
                    "dirty\n",
                    encoding="utf-8",
                ),
                "TARGET_DIRTY",
                {},
            ),
            (
                "managed symlink",
                lambda source, target: (
                    (target / "overlay/managed.txt").unlink(),
                    (target / "overlay/managed.txt").symlink_to(target / "README.md"),
                    commit_all(target, "managed symlink"),
                ),
                "SYMLINK_REJECTED",
                {},
            ),
            (
                "managed type conflict",
                type_conflict,
                "MANAGED_TYPE_CONFLICT",
                {},
            ),
            (
                "undeclared overlay descendant",
                lambda source, target: (
                    (target / "overlay/stale.txt").write_text(
                        "stale\n", encoding="utf-8"
                    ),
                    commit_all(target, "stale overlay"),
                ),
                "UNDECLARED_OVERLAY_DESCENDANT",
                {},
            ),
            (
                "deletion preimage drift",
                bad_deletion,
                "DELETION_PREIMAGE_MISMATCH",
                {},
            ),
            (
                "official version drift",
                lambda source, target: None,
                "OFFICIAL_MATERIALIZATION_MISMATCH",
                {"invalid_version": True},
            ),
            (
                "invalid materialization metadata",
                lambda source, target: None,
                "template hash path",
                {"invalid_metadata": True},
            ),
        ]
        for label, prepare, expected, options in cases:
            with self.subTest(case=label):
                run_case(prepare, expected, **options)


if __name__ == "__main__":
    unittest.main()
