"""Classify a committed diff against one receipt-bound Loop payload."""

from __future__ import annotations

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

MANIFEST_PATH = ".trellis/spec/project/loop-v1-overlay-manifest.json"


class QualificationScopeError(RuntimeError):
    """Raised when changed-scope evidence cannot be classified safely."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str:
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
        raise QualificationScopeError(
            completed.stderr.strip() or "Git scope evidence is unavailable"
        )
    return completed.stdout


def _read_receipt(root: Path, raw_path: Path) -> dict[str, Any]:
    path = raw_path if raw_path.is_absolute() else root / raw_path
    if path.is_symlink():
        raise QualificationScopeError("receipt path must not be a symlink")
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise QualificationScopeError("receipt must be a file inside the repository")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationScopeError("receipt is unreadable") from exc
    if not isinstance(receipt, dict):
        raise QualificationScopeError("receipt must contain one JSON object")
    digest = receipt.get("receipt_digest")
    payload = dict(receipt)
    payload.pop("receipt_digest", None)
    payload.pop("receipt_id", None)
    if (
        not isinstance(digest, str)
        or _digest_json(payload) != digest
        or receipt.get("receipt_id") != f"sha256:{digest}"
        or path.stem != digest
    ):
        raise QualificationScopeError("receipt identity or payload digest differs")
    return receipt


def _historical_manifest(
    root: Path,
    receipt: Mapping[str, object],
) -> tuple[str, list[Mapping[str, object]]]:
    runtime = receipt.get("runtime")
    overlay = receipt.get("overlay")
    if not isinstance(runtime, Mapping) or not isinstance(overlay, Mapping):
        raise QualificationScopeError("receipt runtime or overlay identity is missing")
    commit = _git(root, "rev-parse", f"{runtime.get('git_commit')}^{{commit}}").strip()
    manifest_text = _git(root, "show", f"{commit}:{MANIFEST_PATH}")
    if sha256(manifest_text.encode("utf-8")).hexdigest() != overlay.get(
        "manifest_digest"
    ):
        raise QualificationScopeError("receipt-bound manifest digest differs")
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise QualificationScopeError("receipt-bound manifest is invalid") from exc
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or any(
        not isinstance(entry, Mapping) for entry in entries
    ):
        raise QualificationScopeError("receipt-bound manifest entries are invalid")
    return commit, entries


def _manifest_reason(
    path: Path,
    entries: Sequence[Mapping[str, object]],
) -> str | None:
    for entry in entries:
        if entry.get("owner") not in {"official", "overlay"}:
            continue
        bound = Path(str(entry.get("path", "")))
        if path == bound or (
            entry.get("scope") == "tree" and bound in path.parents
        ):
            return f"manifest:{entry['owner']}:{entry.get('scope')}"
    return None


def classify_changed_scope(
    repo_root: Path,
    receipt_path: Path,
    base_ref: str,
    head_ref: str = "HEAD",
) -> dict[str, object]:
    """Return a fail-closed classification for one committed Git diff."""
    root = Path(repo_root).resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve() != root:
        raise QualificationScopeError("repo root must be the exact Git worktree root")
    receipt = _read_receipt(root, Path(receipt_path))
    receipt_commit, entries = _historical_manifest(root, receipt)
    base_commit = _git(root, "rev-parse", f"{base_ref}^{{commit}}").strip()
    head_commit = _git(root, "rev-parse", f"{head_ref}^{{commit}}").strip()
    changed = sorted(
        path
        for path in _git(
            root,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"{base_commit}..{head_commit}",
        ).split("\0")
        if path
    )

    runtime = receipt.get("runtime")
    qualification = receipt.get("qualification")
    if not isinstance(runtime, Mapping) or not isinstance(qualification, Mapping):
        raise QualificationScopeError("receipt scope evidence is missing")
    runtime_files = runtime.get("files")
    test_sources = qualification.get("test_source_digests")
    if not isinstance(runtime_files, Mapping) or not isinstance(test_sources, Mapping):
        raise QualificationScopeError("receipt file maps are missing")

    bound_rows: list[dict[str, object]] = []
    outside: list[str] = []
    for raw_path in changed:
        reasons: list[str] = []
        if raw_path in runtime_files:
            reasons.append("runtime")
        if raw_path in test_sources:
            reasons.append("qualification_test")
        manifest_reason = _manifest_reason(Path(raw_path), entries)
        if manifest_reason:
            reasons.append(manifest_reason)
        if reasons:
            bound_rows.append({"path": raw_path, "reasons": sorted(reasons)})
        else:
            outside.append(raw_path)

    blocked = bool(bound_rows)
    return {
        "base_commit": base_commit,
        "changed_paths": changed,
        "gate": (
            "complete_qualification_required" if blocked else "changed_scope_clear"
        ),
        "head_commit": head_commit,
        "outside_receipt_boundary": outside,
        "receipt_bound": bound_rows,
        "receipt_id": receipt["receipt_id"],
        "receipt_runtime_commit": receipt_commit,
        "status": "blocked" if blocked else "passed",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        result = classify_changed_scope(
            args.repo_root,
            args.receipt,
            args.base,
            args.head,
        )
    except QualificationScopeError as exc:
        print(
            _canonical_json(
                {
                    "error": str(exc),
                    "gate": "changed_scope_unknown",
                    "status": "error",
                }
            )
        )
        return 2
    print(_canonical_json(result))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
