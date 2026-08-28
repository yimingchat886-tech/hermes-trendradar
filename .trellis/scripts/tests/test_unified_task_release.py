from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _uil_helpers import make_repo
from taskrun.authority import AuthorityError, digest, git_common_dir
from taskrun.release import build_adoption_report, build_release, validate_release
from taskrun.upstream_candidate import refresh_candidate
from test_upstream_candidate import candidate_fixture


class AdoptedReleaseTests(unittest.TestCase):
    def test_release_binds_complete_candidate_and_adoption_report(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo", catalog=True)
            responses, executable = candidate_fixture(root)
            candidate = refresh_candidate(
                root,
                cli_executable=str(executable),
                fetch=responses.__getitem__,
                profile_generator=lambda _cli, _version: {
                    "claude-codex-native": {
                        ".trellis/.version": (b"0.6.15", 0o644),
                        ".trellis/new-upstream.md": (b"optional\n", 0o755),
                        ".trellis/scripts/task.py": (b"official task\n", 0o644),
                    }
                },
            )
            report = build_adoption_report(root, str(candidate["candidate_id"]))
            release = build_release(
                root,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "a" * 64},
                upstream_candidate_id=str(candidate["candidate_id"]),
                adoption_report=report,
            )
            self.assertEqual(validate_release(root, release), release["release_id"])
            self.assertEqual(release["schema_generation"], 2)
            self.assertEqual(
                release["upstream_candidate"]["candidate_id"], candidate["candidate_id"]
            )
            self.assertIn(
                ".trellis/.version",
                {row["path"] for row in release["trellis_base_artifact"]["payload"]},
            )
            executable = next(
                row for row in release["trellis_base_artifact"]["payload"]
                if row["path"] == ".trellis/new-upstream.md"
            )
            self.assertEqual(executable["mode"], 0o755)
            artifact = (
                git_common_dir(root)
                / "trellis/releases/artifacts"
                / release["release_id"]
                / "base/.trellis/new-upstream.md"
            )
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o755)
            artifact.chmod(0o644)
            with self.assertRaisesRegex(AuthorityError, "artifact drifted"):
                build_release(
                    root,
                    managed_paths=[".trellis/scripts/task.py"],
                    semantic_qualification={
                        "passed": True,
                        "suite_digest": "a" * 64,
                    },
                    upstream_candidate_id=str(candidate["candidate_id"]),
                    adoption_report=report,
                )
            disposition = next(
                row for row in report["dispositions"]
                if row["path"].endswith("/.trellis/scripts/task.py")
            )
            self.assertEqual(disposition["disposition"], "port")

            rejected = {**report, "dispositions": [dict(row) for row in report["dispositions"]]}
            rejected_row = next(
                row for row in rejected["dispositions"]
                if row["path"].endswith("/.trellis/new-upstream.md")
            )
            rejected_row.update(
                {
                    "disposition": "reject",
                    "reason": "not adopted",
                    "risk": "feature unavailable",
                    "verification": "base payload omission asserted",
                }
            )
            rejected_body = {
                key: value for key, value in rejected.items()
                if key != "report_digest"
            }
            rejected["report_digest"] = digest(rejected_body)
            rejected_release = build_release(
                root,
                managed_paths=[".trellis/scripts/task.py"],
                semantic_qualification={"passed": True, "suite_digest": "b" * 64},
                upstream_candidate_id=str(candidate["candidate_id"]),
                adoption_report=rejected,
            )
            self.assertNotIn(
                ".trellis/new-upstream.md",
                {row["path"] for row in rejected_release["trellis_base_artifact"]["payload"]},
            )

            incomplete = {**report, "dispositions": report["dispositions"][:-1]}
            body = {key: value for key, value in incomplete.items() if key != "report_digest"}
            incomplete["report_digest"] = digest(body)
            with self.assertRaisesRegex(AuthorityError, "coverage is incomplete"):
                build_release(
                    root,
                    managed_paths=[".trellis/scripts/task.py"],
                    semantic_qualification={"passed": True, "suite_digest": "a" * 64},
                    upstream_candidate_id=str(candidate["candidate_id"]),
                    adoption_report=incomplete,
                    write=False,
                )


if __name__ == "__main__":
    unittest.main()
