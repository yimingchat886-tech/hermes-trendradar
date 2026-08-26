from __future__ import annotations

import base64
import io
import json
import tarfile
import tempfile
import unittest
from hashlib import sha512
from pathlib import Path
from urllib.parse import quote

from _uil_helpers import git, make_repo, write
from taskrun.authority import AuthorityError, git_common_dir
from taskrun.upstream_candidate import (
    CLI_PACKAGE,
    CORE_PACKAGE,
    REGISTRY,
    _package_tree,
    candidate_status,
    refresh_candidate,
)


def package_tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, data in files.items():
            info = tarfile.TarInfo(f"package/{path}")
            info.mode = 0o755 if path.startswith("bin/") else 0o644
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(sha512(data).digest()).decode("ascii")


class UpstreamCandidateTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict[str, bytes], Path]:
        executable_bytes = b"#!/bin/sh\nprintf '0.6.15\\n'\n"
        cli_tarball = package_tarball(
            {
                "package.json": json.dumps(
                    {"name": CLI_PACKAGE, "version": "0.6.15"}
                ).encode(),
                "bin/trellis.js": executable_bytes,
                "templates/new-capability.md": b"upstream\n",
            }
        )
        core_tarball = package_tarball(
            {
                "package.json": json.dumps(
                    {"name": CORE_PACKAGE, "version": "0.6.15"}
                ).encode(),
                "dist/index.js": b"export const version = '0.6.15';\n",
            }
        )
        cli_url = f"{REGISTRY}/cli.tgz"
        core_url = f"{REGISTRY}/core.tgz"
        cli_document = {
            "name": CLI_PACKAGE,
            "dist-tags": {"latest": "0.6.15"},
            "versions": {
                "0.6.15": {
                    "dependencies": {CORE_PACKAGE: "0.6.15"},
                    "dist": {"integrity": integrity(cli_tarball), "tarball": cli_url},
                }
            },
        }
        core_document = {
            "name": CORE_PACKAGE,
            "dist-tags": {"latest": "0.6.15"},
            "versions": {
                "0.6.15": {
                    "dependencies": {},
                    "dist": {"integrity": integrity(core_tarball), "tarball": core_url},
                }
            },
        }
        responses = {
            f"{REGISTRY}/{quote(CLI_PACKAGE, safe='')}": json.dumps(cli_document).encode(),
            f"{REGISTRY}/{quote(CORE_PACKAGE, safe='')}": json.dumps(core_document).encode(),
            cli_url: cli_tarball,
            core_url: core_tarball,
        }
        executable = root / "trellis"
        executable.write_bytes(executable_bytes)
        executable.chmod(0o755)
        return responses, executable

    def test_refresh_is_content_addressed_idempotent_and_worktree_clean(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            responses, executable = self._fixture(root)
            fetch = responses.__getitem__
            generations = iter((b"first", b"second"))
            def generator(_cli: Path, _version: str) -> dict[str, dict[str, tuple[bytes, int]]]:
                observed = next(generations)
                return {
                    "claude-codex-native": {
                        ".trellis/.developer": (
                            b"name=uil-base\ninitialized_at=" + observed + b"\n", 0o644
                        ),
                        ".trellis/new-upstream-file.md": (b"generated\n", 0o644),
                    }
                }
            before = git(root, "status", "--porcelain=v1").stdout
            first = refresh_candidate(
                root, cli_executable=str(executable), fetch=fetch,
                profile_generator=generator,
            )
            second = refresh_candidate(
                root, cli_executable=str(executable), fetch=fetch,
                profile_generator=generator,
            )
            self.assertEqual(first["candidate_id"], second["candidate_id"])
            self.assertEqual(candidate_status(root)["latest_available"], first["candidate_id"])
            self.assertEqual(git(root, "status", "--porcelain=v1").stdout, before)
            paths = {row["path"] for row in first["full_inventory"]}
            self.assertIn("packages/cli/templates/new-capability.md", paths)
            self.assertIn("generated/claude-codex-native/.trellis/new-upstream-file.md", paths)

    def test_archive_links_and_traversal_are_rejected(self) -> None:
        for name, link in (("package/link", "outside"), ("package/../escape", None)):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = link
                else:
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b"x"))
                    info = None
                if info is not None:
                    archive.addfile(info)
            with self.assertRaises(AuthorityError):
                _package_tree(buffer.getvalue())

    def test_status_rejects_stored_candidate_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            responses, executable = self._fixture(root)
            candidate = refresh_candidate(
                root,
                cli_executable=str(executable),
                fetch=responses.__getitem__,
                profile_generator=lambda _cli, _version: {
                    "claude-codex-native": {
                        ".trellis/.version": (b"0.6.15", 0o644)
                    }
                },
            )
            artifact = (
                git_common_dir(root)
                / "trellis/upstream/candidates"
                / str(candidate["candidate_id"])
                / "generated/claude-codex-native/.trellis/.version"
            )
            artifact.write_bytes(b"drift")
            with self.assertRaisesRegex(AuthorityError, "file drifted"):
                candidate_status(root)

    def test_prerelease_latest_is_not_available(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = make_repo(Path(temp) / "repo")
            responses, executable = self._fixture(root)
            document_url = f"{REGISTRY}/{quote(CLI_PACKAGE, safe='')}"
            document = json.loads(responses[document_url])
            document["dist-tags"]["latest"] = "0.7.0-beta.3"
            responses[document_url] = json.dumps(document).encode()
            with self.assertRaisesRegex(AuthorityError, "not a stable semantic version"):
                refresh_candidate(
                    root, cli_executable=str(executable), fetch=responses.__getitem__,
                    profile_generator=lambda _cli, _version: {},
                )


def candidate_fixture(root: Path) -> tuple[dict[str, bytes], Path]:
    return UpstreamCandidateTests()._fixture(root)


if __name__ == "__main__":
    unittest.main()
