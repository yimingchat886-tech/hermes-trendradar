from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loop_v1.qualification import DEPLOYMENT_TARGETS
from upstream_release.core import UpstreamReleaseError, plan_candidate, verify_candidate


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def _manifest() -> dict[str, object]:
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
            {
                "owner": "overlay",
                "path": ".trellis/spec/project/loop-v1-overlay-manifest.json",
                "scope": "file",
            },
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
        "official_release": "fixture-v2",
        "overlay_version": "fixture-overlay-v1",
    }


def _metadata(label: str) -> str:
    return json.dumps(
        {"__version": 2, "hashes": {f"{label}.txt": "a" * 64}},
        sort_keys=True,
    )


def _fixture(base: Path) -> tuple[Path, Path, Path]:
    source = base / "source"
    pin = base / "pin"
    scratch = base / "scratch"
    source.mkdir()
    pin.mkdir()
    scratch.mkdir()

    for path in (
        ".trellis/spec/project",
        ".trellis/deploy",
        ".trellis/tasks",
        ".trellis/workspace",
        ".trellis/.runtime",
        "overlay",
    ):
        (source / path).mkdir(parents=True, exist_ok=True)
    (source / ".trellis/.template-hashes.json").write_text(
        _metadata("source"), encoding="utf-8"
    )
    (source / ".trellis/.version").write_text("fixture-v1\n", encoding="utf-8")
    (source / "official.txt").write_text("official:old\n", encoding="utf-8")
    (source / "overlay/managed.txt").write_text("overlay:source\n", encoding="utf-8")
    (source / ".trellis/config.yaml").write_text("source: true\n", encoding="utf-8")
    (source / ".trellis/deploy/adoption.json").write_text("{}\n", encoding="utf-8")
    (source / "AGENTS.md").write_text("fixture agents\n", encoding="utf-8")
    (source / "HANDOFF.md").write_text("fixture handoff\n", encoding="utf-8")
    (source / "README.md").write_text("fixture readme\n", encoding="utf-8")
    (source / "BOARD.md").write_text("fixture board\n", encoding="utf-8")
    (source / ".trellis/spec/project/loop-v1-overlay-manifest.json").write_text(
        json.dumps(_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    (pin / ".trellis").mkdir()
    (pin / ".trellis/.template-hashes.json").write_text(
        _metadata("pin"), encoding="utf-8"
    )
    (pin / ".trellis/.version").write_text("fixture-v2\n", encoding="utf-8")
    (pin / "official.txt").write_text("official:new\n", encoding="utf-8")

    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.name", "Fixture")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "add", "--all")
    _git(source, "commit", "-q", "-m", "fixture")
    return source, pin, scratch


class UpstreamReleaseTests(unittest.TestCase):
    def test_plan_and_verify_are_path_redacted_and_source_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))
            candidate = scratch / "candidate"
            source_status = _git(source, "status", "--porcelain=v1")
            source_official = (source / "official.txt").read_bytes()

            plan = plan_candidate(source, pin, scratch, candidate_output=candidate)

            self.assertTrue(candidate.is_dir())
            self.assertFalse((candidate / ".git").exists())
            self.assertEqual(
                (candidate / "official.txt").read_text(encoding="utf-8"),
                "official:new\n",
            )
            self.assertEqual(source_official, (source / "official.txt").read_bytes())
            self.assertEqual(source_status, _git(source, "status", "--porcelain=v1"))
            self.assertNotIn(str(source), json.dumps(plan, sort_keys=True))
            self.assertEqual(plan["pin"]["id"], plan["release"]["official"]["id"])
            self.assertNotEqual(
                plan["release"]["official"]["id"],
                plan["release"]["overlay"]["id"],
            )
            self.assertEqual(
                plan["mutations"][0]["paths"],
                [".trellis/.template-hashes.json", ".trellis/.version", "official.txt"],
            )
            self.assertEqual(plan["mutations"][1]["owner"], "overlay")
            self.assertIn("overlay/managed.txt", plan["mutations"][1]["paths"])

            verification = verify_candidate(source, pin, candidate, plan)

            self.assertEqual(verification["status"], "verified")
            self.assertEqual(verification["release"], plan["release"])

    def test_plan_and_verify_preserve_absent_adoption_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))
            adoption = source / ".trellis/deploy/adoption.json"
            adoption.unlink()
            _git(source, "add", "--all")
            _git(source, "commit", "-q", "-m", "remove target-only projection")
            candidate = scratch / "candidate"

            plan = plan_candidate(source, pin, scratch, candidate_output=candidate)
            verification = verify_candidate(source, pin, candidate, plan)

            self.assertFalse((candidate / adoption.relative_to(source)).exists())
            self.assertEqual(verification["status"], "verified")

    def test_verify_rejects_candidate_only_adoption_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))
            adoption = source / ".trellis/deploy/adoption.json"
            adoption.unlink()
            _git(source, "add", "--all")
            _git(source, "commit", "-q", "-m", "remove target-only projection")
            candidate = scratch / "candidate"
            plan = plan_candidate(source, pin, scratch, candidate_output=candidate)
            candidate_adoption = candidate / adoption.relative_to(source)
            candidate_adoption.write_text("{}\n", encoding="utf-8")

            with self.assertRaises(UpstreamReleaseError):
                verify_candidate(source, pin, candidate, plan)

    def test_other_missing_project_file_still_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))
            (source / "AGENTS.md").unlink()
            _git(source, "add", "--all")
            _git(source, "commit", "-q", "-m", "remove required project file")

            with self.assertRaisesRegex(
                UpstreamReleaseError,
                "manifest-owned file is missing",
            ):
                plan_candidate(source, pin, scratch)

    def test_live_manifest_binds_the_0_6_8_codex_hook(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        manifest = json.loads(
            (
                repo_root
                / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            ).read_text(encoding="utf-8")
        )
        hook_path = ".codex/hooks/inject-subagent-context.py"
        hook_entries = [
            entry for entry in manifest["entries"] if entry["path"] == hook_path
        ]

        self.assertEqual(manifest["official_release"], "0.6.10")
        self.assertEqual(
            hook_entries,
            [{"owner": "overlay", "path": hook_path, "scope": "file"}],
        )
        self.assertEqual(
            sha256((repo_root / hook_path).read_bytes()).hexdigest(),
            "abffa237eb53f87ae6ffa434063b46b03d58a36a84cdb8fe88bfc5f243aab609",
        )

    def test_verify_rejects_candidate_drift_before_any_source_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))
            candidate = scratch / "candidate"
            plan = plan_candidate(source, pin, scratch, candidate_output=candidate)
            (candidate / "official.txt").write_text("tampered\n", encoding="utf-8")

            with self.assertRaises(UpstreamReleaseError):
                verify_candidate(source, pin, candidate, plan)

            self.assertEqual(
                (source / "official.txt").read_text(encoding="utf-8"),
                "official:old\n",
            )
            self.assertEqual(_git(source, "status", "--porcelain=v1"), "")

    def test_network_symlink_and_manifest_collision_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, pin, scratch = _fixture(Path(temporary))

            with self.assertRaises(UpstreamReleaseError):
                plan_candidate(source, "https://example.invalid/trellis", scratch)

            unknown = pin / "unowned.txt"
            unknown.write_text("unexpected\n", encoding="utf-8")
            with self.assertRaises(UpstreamReleaseError):
                plan_candidate(source, pin, scratch)
            unknown.unlink()

            link = pin / "official-link"
            link.symlink_to(pin / "official.txt")
            with self.assertRaises(UpstreamReleaseError):
                plan_candidate(source, pin, scratch)
            link.unlink()

            manifest_path = source / ".trellis/spec/project/loop-v1-overlay-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"].append(
                {"owner": "overlay", "path": "official.txt", "scope": "file"}
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _git(source, "add", "--all")
            _git(source, "commit", "-q", "-m", "collision")
            with self.assertRaises(UpstreamReleaseError):
                plan_candidate(source, pin, scratch)

            self.assertEqual(_git(source, "status", "--porcelain=v1"), "")


if __name__ == "__main__":
    unittest.main()
