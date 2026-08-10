"""Content-addressed releases, logical checks, and unified target slots."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from .authority import Authority, AuthorityError, canonical_json, digest, git, git_common_dir, utc_now
from .service import (
    _slug,
    _task_authorization_candidate_digest,
    _working_candidate_digest,
    _write_if_changed,
    touches_overlap,
)


ENV_ALLOWLIST = ("HOME", "PATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR")
DECLARED_ENV_ALLOWLIST = ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "TMPDIR")


def _safe_relative(root: Path, raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise AuthorityError(f"Unsafe release path: {raw}")
    current = Path(root)
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise AuthorityError(f"Release path has a symlink parent: {raw}")
    return Path(root) / relative


def _write_immutable(path: Path, data: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AuthorityError(f"Immutable release path is unsafe: {path}")
    if path.is_file():
        if path.read_bytes() != data:
            raise AuthorityError(f"Immutable release artifact drifted: {path}")
        return
    _write_if_changed(path, data)


def _artifact_path(
    common_dir: Path,
    locator_root: str,
    release_id: str,
    group: str,
    raw: str,
) -> Path:
    return _safe_relative(
        common_dir, f"{locator_root}/{release_id}/{group}/{raw}"
    )


def repository_snapshot(repo_root: Path, *, exclude: Sequence[str] = ()) -> str:
    root = Path(repo_root).resolve()
    listing = git(root, "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0")
    rows: list[tuple[str, str]] = []
    excluded = set(exclude)
    for raw in sorted(path for path in listing if path and path not in excluded):
        path = root / raw
        if path.is_symlink():
            rows.append((raw, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            rows.append((raw, sha256(path.read_bytes()).hexdigest()))
    return digest(rows)


def load_catalog(repo_root: Path) -> dict[str, Any]:
    path = Path(repo_root) / ".trellis/releases/check-catalog.json"
    if not path.is_file() or path.is_symlink():
        raise AuthorityError("Release check catalog is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("versions"), dict):
        raise AuthorityError("Release check catalog schema is unsupported")
    return value


def load_overlay(repo_root: Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = root / ".trellis/spec/project/loop-v1-overlay-manifest.json"
    if not path.is_file() or path.is_symlink():
        raise AuthorityError("Overlay ownership manifest is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    entries = value.get("entries")
    deployment = value.get("deployment")
    if (
        value.get("manifest_schema_version") != 3
        or not isinstance(entries, list)
        or not isinstance(deployment, Mapping)
    ):
        raise AuthorityError("Overlay ownership manifest schema is unsupported")
    seen: set[str] = set()
    for entry in entries:
        if (
            not isinstance(entry, Mapping)
            or entry.get("owner") not in {"generated", "overlay", "project"}
            or entry.get("scope") not in {"external", "file", "tree"}
            or not isinstance(entry.get("path"), str)
            or entry["path"] in seen
        ):
            raise AuthorityError("Overlay ownership entry is invalid")
        seen.add(entry["path"])
        if entry["scope"] != "external":
            _safe_relative(root, entry["path"])
    preserve = deployment.get("preserve")
    deletions = deployment.get("intentional_deletions")
    if (
        not isinstance(preserve, list)
        or not isinstance(deletions, list)
        or not all(isinstance(item, str) for item in [*preserve, *deletions])
    ):
        raise AuthorityError("Overlay deployment policy is invalid")
    for raw in [*preserve, *deletions]:
        _safe_relative(root, raw)
    if any(touches_overlap([deleted], preserve) for deleted in deletions):
        raise AuthorityError("Overlay deletion overlaps a preserved path")
    return value


def resolve_check(
    catalog: Mapping[str, object],
    check_id: str,
    *,
    version: str,
    phase: str,
    role: str,
    capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    versions = catalog.get("versions")
    release = versions.get(version) if isinstance(versions, Mapping) else None
    check = release.get(check_id) if isinstance(release, Mapping) else None
    if not isinstance(check, Mapping):
        raise AuthorityError(f"Unsupported logical check {check_id!r} for Trellis {version}")
    argv = check.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise AuthorityError("Resolved check argv is invalid")
    declared_env = check.get("environment", {})
    if (
        not isinstance(declared_env, Mapping)
        or any(key not in DECLARED_ENV_ALLOWLIST for key in declared_env)
        or not all(isinstance(value, str) for value in declared_env.values())
    ):
        raise AuthorityError("Resolved check environment is invalid")
    result = {
        "argv": argv,
        "capabilities": sorted(set(capabilities)),
        "check_id": check_id,
        "environment_allowlist": list(ENV_ALLOWLIST),
        "environment": dict(declared_env),
        "expect": {"returncode": 0},
        "mutation": check.get("mutation", "read_only"),
        "phase": phase,
        "role": role,
        "timeout": int(check.get("timeout", 60)),
        "version": version,
    }
    result["input_digest"] = digest(
        {
            key: result[key]
            for key in (
                "capabilities",
                "check_id",
                "environment",
                "phase",
                "role",
                "version",
            )
        }
    )
    result["argv_digest"] = digest(argv)
    return result


def run_check(repo_root: Path, resolved: Mapping[str, object]) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    before = repository_snapshot(root)
    env = {key: os.environ[key] for key in resolved["environment_allowlist"] if key in os.environ}
    env.update(resolved.get("environment", {}))
    result = subprocess.run(
        list(resolved["argv"]),
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        timeout=int(resolved["timeout"]),
    )
    after = repository_snapshot(root)
    output_digest = sha256(result.stdout + b"\0" + result.stderr).hexdigest()
    mutated = before != after
    passed = result.returncode == 0 and (resolved["mutation"] != "read_only" or not mutated)
    return {
        "after_digest": after,
        "argv": list(resolved["argv"]),
        "argv_digest": resolved["argv_digest"],
        "before_digest": before,
        "check_id": resolved["check_id"],
        "input_digest": resolved["input_digest"],
        "mutation_detected": mutated,
        "output_digest": output_digest,
        "passed": passed,
        "phase": resolved["phase"],
        "return_code": result.returncode,
    }


def _expand_payload(repo_root: Path, managed_paths: Sequence[str]) -> list[dict[str, str]]:
    root = Path(repo_root).resolve()
    visible = set(
        filter(
            None,
            git(root, "ls-files", "-co", "--exclude-standard", "-z").stdout.split(
                "\0"
            ),
        )
    )
    files: set[Path] = set()
    for raw in managed_paths:
        path = _safe_relative(root, raw)
        if path.is_symlink():
            raise AuthorityError(f"Managed payload contains a symlink: {raw}")
        if path.is_file():
            if raw not in visible:
                raise AuthorityError(f"Managed payload path is ignored: {raw}")
            files.add(path)
        elif path.is_dir():
            items = [
                root / item
                for item in visible
                if item == raw or item.startswith(raw.rstrip("/") + "/")
            ]
            if any(item.is_symlink() for item in items):
                raise AuthorityError(f"Managed payload tree contains a symlink: {raw}")
            files.update(item for item in items if item.is_file())
        else:
            raise AuthorityError(f"Managed payload path is missing: {raw}")
    return [
        {
            "digest": sha256(path.read_bytes()).hexdigest(),
            "path": path.relative_to(root).as_posix(),
        }
        for path in sorted(files)
    ]


def _official_base_payload(
    version: str,
    owned_paths: Sequence[str],
) -> tuple[list[dict[str, str]], dict[str, bytes], dict[str, str]]:
    executable = shutil.which("trellis")
    if not executable:
        raise AuthorityError("TOOL_ARTIFACT_MISSING: Trellis CLI is unavailable")
    executable_path = Path(executable).resolve()
    reported = subprocess.run(
        [str(executable_path), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if reported.returncode or reported.stdout.strip() != version:
        raise AuthorityError(
            f"TOOL_ARTIFACT_MISSING: exact Trellis {version} CLI is unavailable"
        )
    contents: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="uil-trellis-base-") as temp:
        root = Path(temp)
        git(root, "init", "-b", "main")
        env = {
            **os.environ,
            "HOME": str(root / "home"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        initialized = subprocess.run(
            [
                str(executable_path),
                "init",
                "--claude",
                "--codex",
                "--yes",
                "--user",
                "uil-base",
                "--workflow",
                "native",
            ],
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if initialized.returncode:
            detail = initialized.stderr.strip() or initialized.stdout.strip()
            raise AuthorityError(
                "TOOL_ARTIFACT_MISSING: official Trellis base extraction failed: "
                + detail
            )
        for raw in sorted(set([*owned_paths, ".trellis/.version"])):
            path = _safe_relative(root, raw)
            candidates = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
            for item in candidates:
                if item.is_file() and "__pycache__" not in item.parts:
                    relative = item.relative_to(root).as_posix()
                    contents[relative] = item.read_bytes()
    payload = [
        {"digest": sha256(data).hexdigest(), "path": path}
        for path, data in sorted(contents.items())
    ]
    return (
        payload,
        contents,
        {
            "executable_digest": sha256(executable_path.read_bytes()).hexdigest(),
            "version": version,
        },
    )


def build_release(
    repo_root: Path,
    *,
    managed_paths: Sequence[str],
    intentional_deletions: Sequence[str] = (),
    base_version: str = "0.6.14",
    supported_versions: Sequence[str] = ("0.6.12", "0.6.14"),
    semantic_qualification: Mapping[str, object],
    write: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    catalog = load_catalog(root)
    versions = set(catalog["versions"])
    if base_version not in supported_versions or not set(supported_versions) <= versions:
        raise AuthorityError("Release capability range is not covered by the check catalog")
    overlay = load_overlay(root)
    expected_paths = {
        entry["path"]
        for entry in overlay["entries"]
        if entry["owner"] == "overlay" and entry["scope"] != "external"
    }
    if set(managed_paths) != expected_paths:
        raise AuthorityError("Release payload does not match overlay ownership")
    if not intentional_deletions:
        intentional_deletions = overlay.get("deployment", {}).get(
            "intentional_deletions", []
        )
    for raw in intentional_deletions:
        _safe_relative(root, raw)
    payload = _expand_payload(root, managed_paths)
    cli_artifact = next(
        (
            item
            for item in payload
            if item["path"] == ".trellis/scripts/task.py"
        ),
        None,
    )
    if not cli_artifact:
        raise AuthorityError("Release payload must bind .trellis/scripts/task.py")
    base_payload, base_contents, base_generator = _official_base_payload(
        base_version,
        [*managed_paths, *intentional_deletions],
    )
    body = {
        "capability_range": sorted(set(supported_versions)),
        "artifact_locator": {
            "root": "trellis/releases/artifacts",
            "scheme": "git-common-dir-v2",
        },
        "check_catalog_digest": digest(catalog),
        "cli_artifact": dict(cli_artifact),
        "intentional_deletions": sorted(set(intentional_deletions)),
        "managed_payload": payload,
        "managed_payload_digest": digest(payload),
        "minimum_capabilities": ["logical-check-adapter", "unified-intent-loop-v1"],
        "overlay_digest": digest(overlay),
        "ownership_policy": "unified-intent-loop-v1",
        "schema_generation": 1,
        "semantic_qualification": dict(semantic_qualification),
        "trellis_base_artifact": {
            "application_order": "official-base-then-overlay-v1",
            "generator": base_generator,
            "payload": base_payload,
            "payload_digest": digest(base_payload),
        },
        "trellis_base_version": base_version,
    }
    release_id = digest(body)
    manifest = {"release_id": release_id, **body}
    if write:
        common_dir = git_common_dir(root)
        for item in payload:
            source = root / item["path"]
            target = _artifact_path(
                common_dir,
                "trellis/releases/artifacts",
                release_id,
                "overlay",
                item["path"],
            )
            _write_immutable(target, source.read_bytes())
        for item in base_payload:
            target = _artifact_path(
                common_dir,
                "trellis/releases/artifacts",
                release_id,
                "base",
                item["path"],
            )
            _write_immutable(target, base_contents[item["path"]])
        path = _safe_relative(root, f".trellis/releases/{release_id}.json")
        _write_immutable(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


def validate_release(repo_root: Path, manifest: Mapping[str, object]) -> str:
    root = Path(repo_root).resolve()
    body = {key: value for key, value in manifest.items() if key != "release_id"}
    release_id = digest(body)
    if manifest.get("release_id") != release_id:
        raise AuthorityError("Release manifest identity is invalid")
    payload = manifest.get("managed_payload")
    if not isinstance(payload, list) or manifest.get("managed_payload_digest") != digest(payload):
        raise AuthorityError("Release managed payload identity is invalid")
    cli_artifact = next(
        (
            item
            for item in payload
            if isinstance(item, Mapping)
            and item.get("path") == ".trellis/scripts/task.py"
        ),
        None,
    )
    if not cli_artifact or manifest.get("cli_artifact") != cli_artifact:
        raise AuthorityError("Release CLI artifact identity is invalid")
    base_artifact = manifest.get("trellis_base_artifact")
    base_payload = (
        base_artifact.get("payload")
        if isinstance(base_artifact, Mapping)
        else None
    )
    if (
        not isinstance(base_payload, list)
        or base_artifact.get("application_order")
        != "official-base-then-overlay-v1"
        or base_artifact.get("payload_digest") != digest(base_payload)
        or not isinstance(base_artifact.get("generator"), Mapping)
        or base_artifact["generator"].get("version")
        != manifest.get("trellis_base_version")
        or not isinstance(base_artifact["generator"].get("executable_digest"), str)
    ):
        raise AuthorityError("Release Trellis base artifact identity is invalid")
    version_entry = next(
        (
            item
            for item in base_payload
            if isinstance(item, Mapping)
            and item.get("path") == ".trellis/.version"
        ),
        None,
    )
    if version_entry != {
        "digest": sha256(
            str(manifest.get("trellis_base_version")).encode()
        ).hexdigest(),
        "path": ".trellis/.version",
    }:
        raise AuthorityError("Release Trellis base version artifact is invalid")
    deletions = manifest.get("intentional_deletions")
    if not isinstance(deletions, list) or not all(isinstance(raw, str) for raw in deletions):
        raise AuthorityError("Release deletion set is invalid")
    for raw in deletions:
        _safe_relative(root, raw)
    locator = manifest.get("artifact_locator")
    if locator != {
        "root": "trellis/releases/artifacts",
        "scheme": "git-common-dir-v2",
    }:
        raise AuthorityError("Release artifact locator is invalid")
    common_dir = git_common_dir(root)
    for group, items in (("base", base_payload), ("overlay", payload)):
        seen: set[str] = set()
        for item in items:
            if (
                not isinstance(item, Mapping)
                or not isinstance(item.get("path"), str)
                or item["path"] in seen
            ):
                raise AuthorityError("Release managed payload entry is invalid")
            seen.add(item["path"])
            path = _artifact_path(
                common_dir, str(locator["root"]), release_id, group, item["path"]
            )
            if path.is_symlink() or not path.is_file():
                raise AuthorityError(
                    f"Release {group} payload path is unsafe: {item['path']}"
                )
            if sha256(path.read_bytes()).hexdigest() != item.get("digest"):
                raise AuthorityError(
                    f"Release {group} payload drifted: {item['path']}"
                )
    if manifest.get("check_catalog_digest") != digest(load_catalog(root)):
        raise AuthorityError("Release check catalog drifted")
    overlay = load_overlay(root)
    if manifest.get("overlay_digest") != digest(overlay):
        raise AuthorityError("Release overlay ownership manifest drifted")
    evidence = manifest.get("semantic_qualification")
    if not isinstance(evidence, Mapping) or evidence.get("passed") is not True:
        raise AuthorityError("Release semantic qualification failed")
    return release_id


def qualify_release(
    repo_root: Path,
    manifest: Mapping[str, object],
    *,
    task_id: str | None = None,
) -> str:
    root = Path(repo_root).resolve()
    release_id = validate_release(root, manifest)
    commit_oid = _release_commit_oid(root, manifest)
    with Authority(root, create=True) as authority, authority.transaction():
        run = (
            authority.one("SELECT run_id FROM runs WHERE task_id=?", (task_id,))
            if task_id
            else None
        )
        if task_id and not run:
            raise AuthorityError("Release qualification task has not been run")
        authority.execute(
            """INSERT INTO release_bindings VALUES(?,?,?,?,?,?)
               ON CONFLICT(release_id) DO UPDATE SET
                 qualified=1,
                 commit_oid=COALESCE(excluded.commit_oid,release_bindings.commit_oid)""",
            (
                release_id,
                canonical_json(dict(manifest)),
                1,
                commit_oid,
                None,
                utc_now(),
            ),
        )
        authority.record_event(
            f"release:qualify:{release_id}:{task_id or 'standalone'}",
            "release_qualified",
            {"release_id": release_id},
            task_id=task_id,
            run_id=run["run_id"] if run else None,
            operation_input=manifest,
        )
    return release_id


def _release_commit_oid(
    repo_root: Path,
    manifest: Mapping[str, object],
) -> str | None:
    root = Path(repo_root).resolve()
    for item in manifest["managed_payload"]:
        path = root / item["path"]
        if (
            not path.is_file()
            or sha256(path.read_bytes()).hexdigest() != item["digest"]
            or git(
                root,
                "ls-files",
                "--error-unmatch",
                "--",
                item["path"],
                check=False,
            ).returncode
            or git(
                root,
                "diff",
                "--quiet",
                "HEAD",
                "--",
                item["path"],
                check=False,
            ).returncode
        ):
            return None
    return git(root, "rev-parse", "HEAD^{commit}").stdout.strip()


def bind_qualified_releases_to_head(
    authority: Authority,
    repo_root: Path,
) -> None:
    for row in authority.all(
        "SELECT release_id,manifest_json FROM release_bindings WHERE qualified=1"
    ):
        commit_oid = _release_commit_oid(
            repo_root,
            json.loads(row["manifest_json"]),
        )
        if commit_oid:
            authority.execute(
                "UPDATE release_bindings SET commit_oid=? WHERE release_id=?",
                (commit_oid, row["release_id"]),
            )


def latest_qualified_release(repo_root: Path) -> dict[str, Any]:
    with Authority(repo_root) as authority:
        row = authority.one(
            "SELECT manifest_json FROM release_bindings WHERE qualified=1 ORDER BY created_at DESC,release_id DESC LIMIT 1"
        )
        if not row:
            raise AuthorityError("No qualified Harness release is available")
        return json.loads(row["manifest_json"])


def _dirty_paths(repo_root: Path) -> list[str]:
    entries = git(repo_root, "status", "--porcelain=v1", "-z").stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        paths.add(entry[3:])
        if entry[:2] in {"R ", " R", "C ", " C"} and index < len(entries):
            paths.add(entries[index])
            index += 1
    return sorted(paths)


def _target_version(target: Path) -> str:
    version = target / ".trellis/.version"
    if version.is_file():
        return version.read_text(encoding="utf-8").strip()
    config = target / ".trellis/config.yaml"
    if config.is_file():
        match = __import__("re").search(r"(?m)^trellis_version:\s*['\"]?([^'\"\s]+)", config.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    raise AuthorityError("Target Trellis version is unavailable")


def _target_branch(target: Path) -> str:
    result = git(target, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode or not result.stdout.strip():
        raise AuthorityError("Target must be on a local branch")
    return result.stdout.strip()


def _index_digest(target: Path) -> str:
    return digest(git(target, "ls-files", "--stage", "-z").stdout)


def _managed_preimage_digest(target: Path, planned_paths: Sequence[str]) -> str:
    return digest(
        {
            "paths": sorted(set(planned_paths)),
            "tree": git(
                target,
                "ls-tree",
                "-r",
                "HEAD",
                "--",
                *sorted(set(planned_paths)),
            ).stdout,
        }
    )


def _slot_worktree(target: Path, run_id: str, target_id: str) -> Path:
    return target.parent / f".{target.name}-{run_id[4:12]}-{target_id}"


def _install_release_material(
    source_common: Path,
    locator_root: str,
    target: Path,
    worktree: Path,
    release: Mapping[str, object],
    base_payload: Sequence[Mapping[str, object]],
    payload: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    release_id = str(release["release_id"])
    manifest_path = f".trellis/releases/{release_id}.json"
    _write_immutable(
        _safe_relative(worktree, manifest_path),
        (json.dumps(release, indent=2, sort_keys=True) + "\n").encode(),
    )
    target_common = git_common_dir(target)
    for group, items in (("base", base_payload), ("overlay", payload)):
        for item in items:
            source_path = _artifact_path(
                source_common,
                locator_root,
                release_id,
                group,
                str(item["path"]),
            )
            target_path = _artifact_path(
                target_common,
                locator_root,
                release_id,
                group,
                str(item["path"]),
            )
            _write_immutable(target_path, source_path.read_bytes())
    validated_release_id = _validate_installed_release_material(
        target, worktree, release, base_payload, payload
    )
    return {
        "artifact_locator": dict(release["artifact_locator"]),
        "manifest_path": manifest_path,
        "validated_release_id": validated_release_id,
    }


def _validate_installed_release_material(
    target: Path,
    worktree: Path,
    release: Mapping[str, object],
    base_payload: Sequence[Mapping[str, object]],
    payload: Sequence[Mapping[str, object]],
) -> str:
    release_id = str(release["release_id"])
    manifest = _safe_relative(worktree, f".trellis/releases/{release_id}.json")
    installed_manifest = None
    if not manifest.is_symlink() and manifest.is_file():
        try:
            installed_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    if installed_manifest != release:
        raise AuthorityError("Target release manifest is unavailable or drifted")
    target_common = git_common_dir(target)
    for group, items in (("base", base_payload), ("overlay", payload)):
        for item in items:
            path = _artifact_path(
                target_common,
                str(release["artifact_locator"]["root"]),
                release_id,
                group,
                str(item["path"]),
            )
            if (
                path.is_symlink()
                or not path.is_file()
                or sha256(path.read_bytes()).hexdigest() != item["digest"]
            ):
                raise AuthorityError(
                    f"Target release {group} artifact is unavailable or drifted: "
                    + str(item["path"])
                )
    return release_id


def sync_targets(
    source_root: Path,
    task_id: str,
    targets: Sequence[Path],
    *,
    release: Mapping[str, object] | None = None,
    check_ids: Sequence[str] = ("trellis.task_cli.help",),
    current_source: bool = False,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    if current_source and release:
        raise AuthorityError("Choose a qualified release or current source, not both")
    if current_source:
        overlay = load_overlay(source)
        managed_paths = [
            entry["path"]
            for entry in overlay["entries"]
            if entry["owner"] == "overlay" and entry["scope"] != "external"
        ]
        catalog = load_catalog(source)
        source_checks = [
            run_check(
                source,
                resolve_check(
                    catalog,
                    check_id,
                    version=str(overlay["trellis_base_version"]),
                    phase="release_qualification",
                    role="source",
                ),
            )
            for check_id in (
                "trellis.task_cli.help",
                "trellis.unittest.focused",
                "trellis.diff.check",
            )
        ]
        if not all(check["passed"] for check in source_checks):
            raise AuthorityError("Current source release qualification failed")
        stable_results = [
            {
                key: check[key]
                for key in (
                    "argv_digest",
                    "check_id",
                    "input_digest",
                    "mutation_detected",
                    "return_code",
                )
            }
            for check in source_checks
        ]
        release = build_release(
            source,
            managed_paths=managed_paths,
            semantic_qualification={
                "passed": True,
                "suite_digest": digest(stable_results),
            },
        )
        qualify_release(source, release, task_id=task_id)
    else:
        release = dict(release or latest_qualified_release(source))
    release_id = validate_release(source, release)
    payload = list(release["managed_payload"])
    base_artifact = release.get("trellis_base_artifact")
    if not isinstance(base_artifact, Mapping) or not isinstance(
        base_artifact.get("payload"), list
    ):
        raise AuthorityError("Selected Harness release base artifact is invalid")
    base_payload = list(base_artifact["payload"])
    locator = release.get("artifact_locator")
    if not isinstance(locator, Mapping):
        raise AuthorityError("Selected Harness release artifact locator is invalid")
    source_common = git_common_dir(source)
    locator_root = str(locator.get("root"))
    for group, items in (("base", base_payload), ("overlay", payload)):
        for item in items:
            path = _artifact_path(
                source_common, locator_root, release_id, group, item["path"]
            )
            if (
                not path.is_file()
                or sha256(path.read_bytes()).hexdigest() != item["digest"]
            ):
                raise AuthorityError(
                    f"Selected Harness release artifact drifted: {item['path']}"
                )
    managed_paths = [item["path"] for item in payload]
    manifest_path = f".trellis/releases/{release_id}.json"
    planned_paths = [
        *(item["path"] for item in base_payload),
        *managed_paths,
        *release.get("intentional_deletions", []),
        manifest_path,
    ]
    catalog = load_catalog(source)
    with Authority(source) as authority:
        qualified = authority.one(
            "SELECT qualified FROM release_bindings WHERE release_id=?", (release_id,)
        )
        if not qualified or not qualified["qualified"]:
            raise AuthorityError("Selected Harness release is not qualified")
        run = authority.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        if not run:
            raise AuthorityError("Sync task has not been run")
        prepared: list[tuple[str, Path, list[str]]] = []
        target_ids: set[str] = set()
        for target_value in targets:
            target = Path(target_value).resolve()
            if not (target / ".git").exists() and git(target, "rev-parse", "--git-dir", check=False).returncode:
                raise AuthorityError(f"Target is not a Git repository: {target}")
            for raw in [
                *(item["path"] for item in base_payload),
                *(item["path"] for item in payload),
                *release.get("intentional_deletions", []),
                ".trellis/deploy/adoption.json",
            ]:
                if _safe_relative(target, raw).is_symlink():
                    raise AuthorityError(
                        f"Target managed path is a symlink: {raw}"
                    )
            dirty = _dirty_paths(target)
            overlap = [path for path in dirty if touches_overlap([path], planned_paths)]
            if overlap:
                raise AuthorityError(f"Target dirty paths overlap managed payload: {', '.join(overlap)}")
            target_id = _slug(target.name)
            if target_id in target_ids:
                raise AuthorityError(f"Duplicate target slot ID: {target_id}")
            target_ids.add(target_id)
            prepared.append((target_id, target, dirty))
        results: dict[str, str] = {}
        for target_id, target, dirty in prepared:
            with authority.transaction():
                existing = authority.one(
                    "SELECT * FROM target_slots WHERE run_id=? AND target_id=?",
                    (run["run_id"], target_id),
                )
                if existing and existing["state"] == "verified":
                    prior = json.loads(existing["receipt_json"])
                    prior_worktree = Path(existing["worktree_path"])
                    try:
                        unchanged = (
                            prior.get("release_id") == release_id
                            and prior_worktree.is_dir()
                            and prior.get("working_candidate_digest")
                            == _working_candidate_digest(
                                prior_worktree,
                                exclude=(".trellis/deploy/adoption.json",),
                            )
                            and _validate_installed_release_material(
                                target,
                                prior_worktree,
                                release,
                                base_payload,
                                payload,
                            )
                            == release_id
                        )
                    except AuthorityError:
                        unchanged = False
                    if unchanged:
                        results[target_id] = "verified"
                        continue
                closeout = authority.one(
                    """SELECT MAX(ordinal) AS ordinal FROM closeout_steps
                       WHERE task_id=? AND partition_id=?""",
                    (task_id, f"target:{target_id}"),
                )
                if closeout and closeout["ordinal"] is not None:
                    scoped_commit = authority.one(
                        """SELECT state,evidence_json FROM closeout_steps
                           WHERE task_id=? AND partition_id=?
                             AND step_name='scoped_commit'""",
                        (task_id, f"target:{target_id}"),
                    )
                    evidence = (
                        json.loads(scoped_commit["evidence_json"])
                        if scoped_commit
                        else {}
                    )
                    preimage = evidence.get("input_candidate_state", {})
                    safe_scoped_failure = (
                        scoped_commit
                        and scoped_commit["state"] == "failed"
                        and "commit_plan" not in evidence
                        and preimage.get("candidate_digest")
                        == _working_candidate_digest(
                            Path(existing["worktree_path"]),
                            exclude=(".trellis/deploy/adoption.json",),
                        )
                    )
                    if int(closeout["ordinal"]) >= 7 or (
                        scoped_commit and not safe_scoped_failure
                    ):
                        raise AuthorityError(
                            "Target cannot be resynchronized after closeout effects began"
                        )
                    authority.execute(
                        "DELETE FROM closeout_steps WHERE task_id=? AND partition_id=?",
                        (task_id, f"target:{target_id}"),
                    )
                slot_managed_paths = set(planned_paths)
                if existing:
                    slot_managed_paths.update(
                        json.loads(existing["managed_paths_json"])
                    )
                    prior_worktree = Path(existing["worktree_path"])
                    for source_manifest in sorted(
                        (source / ".trellis/releases").glob("*.json")
                    ):
                        release_binding = authority.one(
                            "SELECT qualified FROM release_bindings WHERE release_id=?",
                            (source_manifest.stem,),
                        )
                        relative = source_manifest.relative_to(source).as_posix()
                        candidate_manifest = _safe_relative(prior_worktree, relative)
                        if (
                            release_binding
                            and release_binding["qualified"]
                            and not candidate_manifest.is_symlink()
                            and candidate_manifest.is_file()
                            and candidate_manifest.read_bytes()
                            == source_manifest.read_bytes()
                        ):
                            slot_managed_paths.add(relative)
                prior_receipt = (
                    json.loads(existing["receipt_json"])
                    if existing and existing["receipt_json"]
                    else {}
                )
                attempt = int(prior_receipt.get("sync_attempt", 0)) + 1
                branch = existing["branch"] if existing else f"codex/trellis-sync-{run['run_id'][4:12]}-{target_id}"
                worktree = Path(existing["worktree_path"]) if existing and existing["worktree_path"] else _slot_worktree(target, run["run_id"], target_id)
                authority.execute(
                    """INSERT INTO target_slots VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(run_id,target_id) DO UPDATE SET
                         target_root=excluded.target_root,
                         state=excluded.state,
                         branch=excluded.branch,
                         worktree_path=excluded.worktree_path,
                         managed_paths_json=excluded.managed_paths_json,
                         receipt_json=excluded.receipt_json,
                         error=NULL,
                         updated_at=excluded.updated_at""",
                    (
                        run["run_id"], target_id, str(target), "planned", branch, str(worktree),
                        canonical_json(sorted(slot_managed_paths)),
                        canonical_json({"sync_attempt": attempt}),
                        None,
                        utc_now(),
                    ),
                )
                authority.record_event(
                    f"sync:{run['run_id']}:{target_id}:{release_id}:{attempt}:plan",
                    "target_slot_planned",
                    {"release_id": release_id, "sync_attempt": attempt, "target_id": target_id},
                    task_id=task_id,
                    run_id=run["run_id"],
                    operation_input={
                        "release_id": release_id,
                        "sync_attempt": attempt,
                        "target_base": git(target, "rev-parse", "HEAD^{commit}").stdout.strip(),
                        "target_id": target_id,
                    },
                )
            try:
                if not worktree.exists():
                    branch_exists = git(
                        target,
                        "show-ref",
                        "--verify",
                        f"refs/heads/{branch}",
                        check=False,
                    ).returncode == 0
                    if branch_exists:
                        git(target, "worktree", "add", str(worktree), branch)
                    else:
                        git(target, "worktree", "add", "-b", branch, str(worktree), "HEAD")
                for item in base_payload:
                    source_path = _artifact_path(
                        source_common,
                        locator_root,
                        release_id,
                        "base",
                        item["path"],
                    )
                    target_path = _safe_relative(worktree, item["path"])
                    if target_path.is_symlink():
                        raise AuthorityError(
                            f"Target managed path is a symlink: {item['path']}"
                        )
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)
                for raw in release.get("intentional_deletions", []):
                    path = _safe_relative(worktree, raw)
                    if path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                for item in payload:
                    source_path = _artifact_path(
                        source_common,
                        locator_root,
                        release_id,
                        "overlay",
                        item["path"],
                    )
                    target_path = _safe_relative(worktree, item["path"])
                    if target_path.is_symlink():
                        raise AuthorityError(
                            f"Target managed path is a symlink: {item['path']}"
                        )
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)
                release_material = _install_release_material(
                    source_common,
                    locator_root,
                    target,
                    worktree,
                    release,
                    base_payload,
                    payload,
                )
                version = _target_version(worktree)
                check_results = []
                for check_id in check_ids:
                    resolved = resolve_check(
                        catalog, check_id, version=version,
                        phase="post_materialization", role="target",
                    )
                    check_results.append(run_check(worktree, resolved))
                if not all(item["passed"] for item in check_results):
                    raise AuthorityError("Target logical checks failed")
                candidate_tree = digest(
                    {
                        "repository": repository_snapshot(
                            worktree, exclude=(".trellis/deploy/adoption.json",)
                        ),
                        "trellis_base_version": release["trellis_base_version"],
                    }
                )
                receipt = {
                    "candidate_tree": candidate_tree,
                    "check_results_digest": digest(check_results),
                    "check_ids": list(check_ids),
                    "dirty_preimage_digest": digest(dirty),
                    "index_preimage_digest": _index_digest(target),
                    "managed_preimage_digest": _managed_preimage_digest(
                        target, planned_paths
                    ),
                    "managed_payload_digest": release["managed_payload_digest"],
                    "release_id": release_id,
                    "release_material": release_material,
                    "sync_attempt": attempt,
                    "target_base": git(target, "rev-parse", "HEAD^{commit}").stdout.strip(),
                    "target_branch": _target_branch(target),
                    "target_id": target_id,
                    "trellis_base_payload_digest": base_artifact[
                        "payload_digest"
                    ],
                }
                adoption = worktree / ".trellis/deploy/adoption.json"
                receipt["working_candidate_digest"] = _working_candidate_digest(
                    worktree,
                    exclude=(".trellis/deploy/adoption.json",),
                )
                _write_if_changed(adoption, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
                state, error = "verified", None
            except Exception as exc:
                receipt = {"sync_attempt": attempt}
                state, error = "failed", str(exc)
            with authority.transaction():
                authority.execute(
                    """UPDATE target_slots SET state=?,receipt_json=?,error=?,updated_at=?
                       WHERE run_id=? AND target_id=?""",
                    (state, canonical_json(receipt), error, utc_now(), run["run_id"], target_id),
                )
                authority.record_event(
                    f"sync:{run['run_id']}:{target_id}:{release_id}:{attempt}:result",
                    "target_slot_result",
                    {
                        "error_digest": digest(error) if error else None,
                        "state": state,
                        "sync_attempt": attempt,
                        "target_id": target_id,
                    },
                    task_id=task_id,
                    run_id=run["run_id"],
                    operation_input={
                        "receipt": receipt,
                        "state": state,
                        "sync_attempt": attempt,
                        "target_id": target_id,
                    },
                )
            results[target_id] = state
        aggregate = "verified" if all(state == "verified" for state in results.values()) else "partial"
        task = authority.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        task_root = Path(str(task["worktree_path"] or source))
        candidate = (
            _task_authorization_candidate_digest(task_root, task)
            if aggregate == "verified"
            else None
        )
        with authority.transaction():
            authority.execute(
                """UPDATE tasks SET work_state=?,verified_candidate_digest=?,updated_at=?
                   WHERE task_id=?""",
                (
                    "verified" if aggregate == "verified" else "running",
                    candidate,
                    utc_now(),
                    task_id,
                ),
            )
        return {"aggregate": aggregate, "release_id": release_id, "slots": results}
