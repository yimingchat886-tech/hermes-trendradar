from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from taskrun.qualification_scope import classify_changed_scope

SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[1]
ACTIVE_RECEIPT = Path(
    ".trellis/spec/project/receipts/loop-v1/"
    "22008e4f5aab4ef0a17663f69a4bd3605a64fa1719aa9195b81be05bde0a57d5.json"
)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
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


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class QualificationScopeTests(unittest.TestCase):
    def test_original_taskrun_work_commit_is_outside_legacy_receipt(self) -> None:
        result = classify_changed_scope(
            REPO_ROOT,
            ACTIVE_RECEIPT,
            "5cbcf98cc3f19e822fa2d52ceb326caec83d6d5f^",
            "5cbcf98cc3f19e822fa2d52ceb326caec83d6d5f",
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["gate"], "changed_scope_clear")
        self.assertEqual(result["receipt_bound"], [])
        self.assertIn(
            ".trellis/scripts/taskrun/execution.py",
            result["outside_receipt_boundary"],
        )

    def test_true_receipt_bound_change_requires_complete_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            git(root, "config", "user.name", "Scope Test")
            git(root, "config", "user.email", "scope@example.invalid")
            manifest_path = root / (
                ".trellis/spec/project/loop-v1-overlay-manifest.json"
            )
            manifest = {
                "entries": [
                    {
                        "owner": "overlay",
                        "path": ".trellis/scripts/loop_v1",
                        "scope": "tree",
                    }
                ],
                "manifest_schema_version": 2,
            }
            write(manifest_path, json.dumps(manifest, sort_keys=True) + "\n")
            bound = root / ".trellis/scripts/loop_v1/runtime.py"
            outside = root / ".trellis/scripts/taskrun/execution.py"
            write(bound, "BOUND = 1\n")
            write(outside, "OUTSIDE = 1\n")
            git(root, "add", ".trellis")
            git(root, "commit", "-q", "-m", "fixture")
            base = git(root, "rev-parse", "HEAD")

            payload = {
                "overlay": {
                    "manifest_digest": sha256(
                        manifest_path.read_bytes()
                    ).hexdigest()
                },
                "qualification": {"test_source_digests": {}},
                "runtime": {
                    "files": {
                        bound.relative_to(root).as_posix(): sha256(
                            bound.read_bytes()
                        ).hexdigest()
                    },
                    "git_commit": base,
                },
            }
            digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
            receipt = {
                **payload,
                "receipt_digest": digest,
                "receipt_id": f"sha256:{digest}",
            }
            receipt_path = root / f".trellis/spec/project/receipts/{digest}.json"
            write(
                receipt_path,
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            )

            write(outside, "OUTSIDE = 2\n")
            git(root, "add", outside.relative_to(root).as_posix())
            git(root, "commit", "-q", "-m", "outside")
            outside_head = git(root, "rev-parse", "HEAD")
            clear = classify_changed_scope(
                root,
                receipt_path,
                base,
                outside_head,
            )
            self.assertEqual(clear["status"], "passed")
            self.assertEqual(clear["receipt_bound"], [])

            write(bound, "BOUND = 2\n")
            git(root, "add", bound.relative_to(root).as_posix())
            git(root, "commit", "-q", "-m", "bound")
            blocked = classify_changed_scope(
                root,
                receipt_path,
                outside_head,
                "HEAD",
            )
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["gate"], "complete_qualification_required")
            self.assertEqual(
                blocked["receipt_bound"],
                [
                    {
                        "path": ".trellis/scripts/loop_v1/runtime.py",
                        "reasons": [
                            "manifest:overlay:tree",
                            "runtime",
                        ],
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
