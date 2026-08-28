"""Stable tracked GitNexus contract and local index proof."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from .authority import AuthorityError, digest, git


MARKER_START = "<!-- gitnexus:start -->"
MARKER_END = "<!-- gitnexus:end -->"
SKILL_ROOT = Path(".claude/skills/gitnexus")


def marker_block(target_id: str) -> bytes:
    return (
        f"{MARKER_START}\n"
        "# GitNexus\n\n"
        f"Repository id: `{target_id}`. Keep the tracked tree stable: rebuild with "
        "`gitnexus analyze --index-only`; never commit `.gitnexus/`.\n\n"
        "Use `gitnexus query` for unfamiliar flows, `context` for one symbol, "
        "`impact --direction upstream` before symbol edits, and `detect-changes "
        "--scope compare --base-ref <origin/HEAD>` before commit. Missing or stale "
        "index evidence is degraded, not proof of zero risk. Detailed workflows are "
        "under `.agents/skills/gitnexus/` and `.claude/skills/gitnexus/`.\n"
        f"{MARKER_END}"
    ).encode("utf-8")


def _upsert_marker(existing: bytes, target_id: str) -> bytes:
    try:
        text = existing.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthorityError("Agent guidance is not UTF-8") from exc
    starts = [index for index in range(len(text)) if text.startswith(MARKER_START, index)]
    ends = [index for index in range(len(text)) if text.startswith(MARKER_END, index)]
    block = marker_block(target_id).decode("utf-8")
    if not starts and not ends:
        prefix = text.rstrip("\n")
        return (((prefix + "\n\n") if prefix else "") + block + "\n").encode("utf-8")
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise AuthorityError("GitNexus Agent marker is malformed or duplicated")
    end = ends[0] + len(MARKER_END)
    current = text[starts[0]:end]
    legacy = current.startswith(
        f"{MARKER_START}\n# GitNexus — Code Intelligence\n\n"
        "This project is indexed by GitNexus as"
    ) and all(
        heading in current
        for heading in ("## Always Do", "## Never Do", "## Resources", "## CLI")
    )
    if current != block and not legacy:
        raise AuthorityError("GitNexus Agent marker contains unmanaged content")
    return (text[:starts[0]] + block + text[end:]).encode("utf-8")


def _merge_config(existing: bytes | None, target_id: str) -> bytes:
    if existing is None:
        value: dict[str, object] = {}
    else:
        try:
            parsed = json.loads(existing.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthorityError("Existing .gitnexusrc is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AuthorityError("Existing .gitnexusrc must be a JSON object")
        value = dict(parsed)
    nested = value.get("analyze", {})
    if not isinstance(nested, Mapping):
        raise AuthorityError("Existing .gitnexusrc analyze block is invalid")
    for container in (value, nested):
        if "indexOnly" in container and container["indexOnly"] is not True:
            raise AuthorityError("Existing .gitnexusrc conflicts with indexOnly=true")
        if "name" in container and container["name"] != target_id:
            raise AuthorityError("Existing .gitnexusrc conflicts with the registry target id")
    value["analyze"] = {**nested, "indexOnly": True, "name": target_id}
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _ensure_ignore(existing: bytes) -> bytes:
    try:
        text = existing.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthorityError(".gitignore is not UTF-8") from exc
    lines = text.splitlines()
    if any(line.strip() in {"!.gitnexus", "!.gitnexus/", "!/.gitnexus", "!/.gitnexus/"} for line in lines):
        raise AuthorityError(".gitignore explicitly exposes .gitnexus")
    if any(line.strip() in {".gitnexus", ".gitnexus/", "/.gitnexus", "/.gitnexus/"} for line in lines):
        return existing
    return (text.rstrip("\n") + "\n/.gitnexus/\n").encode("utf-8")


def _read_file(root: Path, path: str) -> bytes | None:
    candidate = root / path
    if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
        raise AuthorityError(f"GitNexus managed path is unsafe: {path}")
    return candidate.read_bytes() if candidate.is_file() else None


def plan_gitnexus_assets(
    source_root: Path, target_root: Path, target_id: str
) -> dict[str, object]:
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    if git(target, "ls-files", ".gitnexus", ".gitnexus/**").stdout.strip():
        raise AuthorityError("Target already tracks .gitnexus runtime data")
    assets: dict[str, bytes] = {
        ".gitnexusrc": _merge_config(_read_file(target, ".gitnexusrc"), target_id),
        ".gitignore": _ensure_ignore(_read_file(target, ".gitignore") or b""),
    }
    for path in ("AGENTS.md", "CLAUDE.md"):
        assets[path] = _upsert_marker(_read_file(target, path) or b"", target_id)
    skill_root = source / SKILL_ROOT
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise AuthorityError("Canonical GitNexus skills are unavailable")
    for path in sorted(skill_root.rglob("*")):
        if path.is_symlink():
            raise AuthorityError(f"GitNexus skill path is a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(skill_root).as_posix()
        data = path.read_bytes()
        assets[f".claude/skills/gitnexus/{relative}"] = data
        assets[f".agents/skills/gitnexus/{relative}"] = data
    rows = [
        {"digest": sha256(data).hexdigest(), "path": path}
        for path, data in sorted(assets.items())
    ]
    changed = sorted(
        path for path, data in assets.items() if _read_file(target, path) != data
    )
    return {"asset_digest": digest(rows), "assets": assets, "changed_paths": changed}


def apply_gitnexus_assets(target_root: Path, plan: Mapping[str, object]) -> None:
    root = Path(target_root).resolve()
    assets = plan.get("assets")
    if not isinstance(assets, Mapping):
        raise AuthorityError("GitNexus asset plan is invalid")
    for raw, data in assets.items():
        if not isinstance(raw, str) or not isinstance(data, bytes):
            raise AuthorityError("GitNexus asset plan entry is invalid")
        path = root / raw
        current = root
        for part in Path(raw).parts[:-1]:
            current /= part
            if current.is_symlink():
                raise AuthorityError(f"GitNexus asset has a symlink parent: {raw}")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise AuthorityError(f"GitNexus asset path is unsafe: {raw}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _working_snapshot(repo_root: Path) -> str:
    root = Path(repo_root).resolve()
    listing = git(root, "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0")
    rows: list[tuple[str, str]] = []
    for raw in sorted(path for path in listing if path):
        path = root / raw
        if path.is_symlink():
            rows.append((raw, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            rows.append((raw, sha256(path.read_bytes()).hexdigest()))
    status = git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    ).stdout
    return digest({"files": rows, "status": status})


def prove_gitnexus(
    repo_root: Path,
    target_id: str,
    *,
    branch: str | None = None,
    executable: str = "gitnexus",
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    resolved = shutil.which(executable)
    if not resolved:
        return {
            "degraded": "GitNexus CLI is unavailable; no install was attempted",
            "target_id": target_id,
            "verified": False,
        }
    before = _working_snapshot(root)
    analyze = [resolved, "analyze", "--index-only", "--name", target_id]
    if branch:
        analyze.extend(("--branch", branch, "--allow-duplicate-name"))
    repo_selector = str(root) if branch else target_id
    commands = (
        analyze,
        [resolved, "status"],
        [resolved, "query", "repository architecture", "--repo", repo_selector]
        + (["--branch", branch] if branch else []),
    )
    results = [
        subprocess.run(command, cwd=root, check=False, capture_output=True, timeout=180)
        for command in commands
    ]
    after = _working_snapshot(root)
    if before != after:
        raise AuthorityError("GitNexus index refresh changed the tracked working tree")
    if any(result.returncode for result in results):
        return {
            "degraded": "GitNexus analyze/status/query proof failed",
            "output_digest": digest(
                [sha256(result.stdout + b"\0" + result.stderr).hexdigest() for result in results]
            ),
            "return_codes": [result.returncode for result in results],
            "target_id": target_id,
            "verified": False,
        }
    return {
        "output_digest": digest(
            [sha256(result.stdout + b"\0" + result.stderr).hexdigest() for result in results]
        ),
        "target_id": target_id,
        "tracked_tree_clean": True,
        "verified": True,
    }
