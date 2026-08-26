"""Source-only downstream target registry."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .authority import AuthorityError, digest, git


REGISTRY_PATH = Path(".trellis/deploy/targets.json")
GIT_OPERATIONS = (
    "BISECT_LOG", "CHERRY_PICK_HEAD", "MERGE_HEAD", "REBASE_HEAD", "REVERT_HEAD",
    "rebase-apply", "rebase-merge",
)


def normalize_git_origin(raw: str) -> str:
    value = raw.strip()
    if not value or "\x00" in value:
        raise AuthorityError("Git origin is empty or invalid")
    if re.fullmatch(r"[^/@\s]+@[^/:\s]+:[^\s]+", value):
        user_host, path = value.split(":", 1)
        host = user_host.rsplit("@", 1)[1]
    else:
        candidate = value if "://" in value else f"https://{value}"
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"https", "ssh"}
            or not parsed.hostname
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AuthorityError(f"Unsupported Git origin: {raw}")
        host = parsed.hostname
        path = parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    parts = normalized_path.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise AuthorityError(f"Git origin must identify owner/repository: {raw}")
    return f"{host.lower()}/{parts[0].lower()}/{parts[1].lower()}"


def _validate_registry(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise AuthorityError("Downstream registry schema is unsupported")
    targets = value.get("targets")
    if not isinstance(targets, list) or not targets:
        raise AuthorityError("Downstream registry targets are unavailable")
    normalized: list[dict[str, str]] = []
    ids: set[str] = set()
    roots: set[str] = set()
    origins: set[str] = set()
    for raw in targets:
        if not isinstance(raw, Mapping):
            raise AuthorityError("Downstream registry target is invalid")
        target_id = str(raw.get("id") or "")
        root = str(raw.get("root") or "")
        expected_origin = normalize_git_origin(str(raw.get("expected_origin") or ""))
        normalized_root = os.path.normpath(root)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", target_id):
            raise AuthorityError(f"Invalid downstream target id: {target_id}")
        if not Path(root).is_absolute() or normalized_root != root:
            raise AuthorityError(f"Downstream target root must be an exact absolute path: {root}")
        if target_id in ids or normalized_root in roots or expected_origin in origins:
            raise AuthorityError("Downstream registry ids, roots, and origins must be unique")
        ids.add(target_id)
        roots.add(normalized_root)
        origins.add(expected_origin)
        normalized.append(
            {"expected_origin": expected_origin, "id": target_id, "root": root}
        )
    return {"schema_version": 1, "targets": normalized}


def load_registry(repo_root: Path) -> dict[str, object]:
    path = Path(repo_root).resolve() / REGISTRY_PATH
    if path.is_symlink() or not path.is_file():
        raise AuthorityError(f"Downstream registry is unavailable: {REGISTRY_PATH}")
    return _validate_registry(json.loads(path.read_text(encoding="utf-8")))


def registry_snapshot(repo_root: Path) -> dict[str, object]:
    registry = load_registry(repo_root)
    return {"digest": digest(registry), **registry}


def preflight_registry(repo_root: Path) -> dict[str, object]:
    snapshot = registry_snapshot(repo_root)
    inspected: list[dict[str, object]] = []
    for target in snapshot["targets"]:  # type: ignore[index]
        root = Path(target["root"])  # type: ignore[index]
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise AuthorityError(f"Downstream target root is missing or unsafe: {root}")
        try:
            top = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        except (AuthorityError, subprocess.SubprocessError) as exc:
            raise AuthorityError(f"Downstream target is not a Git repository: {root}") from exc
        if top != root.resolve():
            raise AuthorityError(f"Downstream target root is not the Git top level: {root}")
        actual_origin = normalize_git_origin(git(root, "remote", "get-url", "origin").stdout.strip())
        if actual_origin != target["expected_origin"]:  # type: ignore[index]
            raise AuthorityError(
                f"Downstream target origin mismatch for {target['id']}: "  # type: ignore[index]
                f"expected {target['expected_origin']}, got {actual_origin}"  # type: ignore[index]
            )
        active = []
        for name in GIT_OPERATIONS:
            raw_path = Path(git(root, "rev-parse", "--git-path", name).stdout.strip())
            path = raw_path if raw_path.is_absolute() else root / raw_path
            if path.exists():
                active.append(name)
        if active:
            raise AuthorityError(
                f"Downstream Git operation is in progress for {target['id']}: {', '.join(active)}"  # type: ignore[index]
            )
        status = git(root, "status", "--porcelain=v1", "-z").stdout.split("\0")
        inspected.append(
            {
                **target,  # type: ignore[arg-type]
                "branch": git(root, "symbolic-ref", "--short", "HEAD").stdout.strip(),
                "dirty_entries": len([item for item in status if item]),
                "head": git(root, "rev-parse", "HEAD^{commit}").stdout.strip(),
            }
        )
    return {**snapshot, "targets": inspected}
