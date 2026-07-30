"""Build and verify a local disposable upstream-release candidate."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from loop_v1.qualification import (
    MANIFEST_PATH,
    MATERIALIZATION_METADATA_PATH,
    QualificationError,
    _portable_release_identity,
    check_overlay_conformance,
    digest_json,
    load_overlay_manifest,
)


SCHEMA_VERSION = 1


class UpstreamReleaseError(RuntimeError):
    """Raised when a local candidate cannot be proven safe."""


def _digest_id(value: object) -> str:
    return "sha256:" + digest_json(value)


def _relative_path(value: object, label: str) -> Path:
    raw = str(value).strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "\\" in raw:
        raise UpstreamReleaseError(f"{label} must be a safe relative path")
    return path


def _assert_no_symlinks(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        directories.sort()
        files.sort()
        for name in list(directories):
            candidate = base / name
            if candidate.is_symlink():
                raise UpstreamReleaseError("local input contains a symlink")
            if base == root and name == ".git":
                directories.remove(name)
        for name in files:
            candidate = base / name
            if candidate.is_symlink():
                raise UpstreamReleaseError("local input contains a symlink")


def _local_root(value: os.PathLike[str] | str, label: str, *, scan_tree: bool) -> Path:
    raw = os.fspath(value)
    if "://" in raw:
        raise UpstreamReleaseError(f"{label} must be a local path")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise UpstreamReleaseError(f"{label} must name an existing absolute directory")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise UpstreamReleaseError(f"{label} must not resolve through a symlink")
    if scan_tree:
        _assert_no_symlinks(resolved)
    return resolved


def _new_output_path(
    value: os.PathLike[str] | str | None,
    scratch_root: Path,
    source_root: Path,
    official_root: Path,
) -> Path | None:
    if value is None:
        return None
    raw = os.fspath(value)
    if "://" in raw:
        raise UpstreamReleaseError("candidate output must be a local path")
    output = Path(raw)
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise UpstreamReleaseError("candidate output must be a new absolute directory")
    resolved = output.resolve(strict=False)
    if (
        resolved.parent != scratch_root
        or not resolved.is_relative_to(scratch_root)
        or resolved == source_root
        or resolved == official_root
    ):
        raise UpstreamReleaseError("candidate output must be a new scratch child")
    return resolved


def _assert_disjoint(*roots: Path) -> None:
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if (
                left == right
                or left.is_relative_to(right)
                or right.is_relative_to(left)
            ):
                raise UpstreamReleaseError("source, pin, and scratch roots must be disjoint")


def _tree_file_map(root: Path) -> dict[str, str]:
    _assert_no_symlinks(root)
    files: dict[str, str] = {}
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        directories.sort()
        names.sort()
        if base == root and ".git" in directories:
            directories.remove(".git")
        for name in names:
            if base == root and name == ".git":
                continue
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise UpstreamReleaseError("candidate inputs must contain regular files")
            files[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return files


def _entry_file_map(
    root: Path,
    entry: Mapping[str, object],
) -> dict[str, str]:
    relative = _relative_path(entry.get("path"), "manifest entry")
    scope = str(entry.get("scope", ""))
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise UpstreamReleaseError("manifest-owned path contains a symlink")
    target = root / relative
    if scope == "file":
        if target.is_symlink() or not target.is_file():
            raise UpstreamReleaseError("manifest-owned file is missing")
        return {relative.as_posix(): sha256(target.read_bytes()).hexdigest()}
    if scope != "tree" or target.is_symlink() or not target.is_dir():
        raise UpstreamReleaseError("manifest-owned tree is missing")

    files: dict[str, str] = {}
    for current, directories, names in os.walk(target, topdown=True, followlinks=False):
        base = Path(current)
        directories.sort()
        names.sort()
        for name in [*directories, *names]:
            if (base / name).is_symlink():
                raise UpstreamReleaseError("manifest-owned tree contains a symlink")
        for name in names:
            path = base / name
            if not path.is_file():
                raise UpstreamReleaseError("manifest-owned tree contains a non-file")
            files[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return files


def _entries_file_map(
    root: Path,
    entries: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in entries:
        for relative, digest in _entry_file_map(root, entry).items():
            if relative in files:
                raise UpstreamReleaseError("manifest-owned paths overlap")
            files[relative] = digest
    return files


def _path_identity(files: Mapping[str, str]) -> dict[str, str]:
    return {
        "path_set_digest": digest_json(sorted(files)),
        "payload_digest": digest_json(dict(files)),
    }


def _manifest_file(
    source_root: Path,
    manifest_path: os.PathLike[str] | str | None,
) -> tuple[Path, Path, dict[str, Any]]:
    raw = Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
    candidate = raw if raw.is_absolute() else source_root / raw
    try:
        relative = candidate.relative_to(source_root)
    except ValueError as exc:
        raise UpstreamReleaseError("manifest must be source-owned") from exc
    relative = _relative_path(relative.as_posix(), "manifest")
    current = source_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise UpstreamReleaseError("manifest must not resolve through a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(source_root):
        raise UpstreamReleaseError("manifest must be a source-owned file")
    try:
        manifest = load_overlay_manifest(resolved)
    except QualificationError as exc:
        raise UpstreamReleaseError(str(exc)) from exc
    owned = [
        entry
        for entry in manifest["entries"]
        if _relative_path(entry["path"], "manifest entry") == relative
    ]
    if len(owned) != 1 or owned[0].get("owner") != "overlay":
        raise UpstreamReleaseError("manifest must be one overlay-owned file")
    return resolved, relative, manifest


def _git(source_root: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=source_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=15,
        )
    except OSError as exc:
        raise UpstreamReleaseError("local Git inspection is unavailable") from exc
    if completed.returncode != 0:
        raise UpstreamReleaseError("source must be a symbolic Git checkout")
    return completed.stdout.strip()


def _source_identity(source_root: Path) -> dict[str, str]:
    if _git(source_root, "rev-parse", "--is-inside-work-tree") != "true":
        raise UpstreamReleaseError("source must be a Git checkout")
    status = _git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise UpstreamReleaseError("source must be clean before candidate planning")
    return {
        "branch": _git(source_root, "symbolic-ref", "--short", "HEAD"),
        "commit": _git(source_root, "rev-parse", "HEAD"),
        "snapshot_digest": digest_json(_tree_file_map(source_root)),
        "tree": _git(source_root, "rev-parse", "HEAD^{tree}"),
    }


def _require_conformance(root: Path, manifest_file: Path) -> None:
    try:
        result = check_overlay_conformance(root, manifest_file)
    except QualificationError as exc:
        raise UpstreamReleaseError(str(exc)) from exc
    if result["status"] != "passed":
        raise UpstreamReleaseError("overlay ownership conformance failed")


def _pin_identity(
    official_root: Path,
    official_entries: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, object]]:
    if (official_root / ".git").exists():
        raise UpstreamReleaseError("official pin must not contain Git metadata")
    pin_files = _tree_file_map(official_root)
    owned_files = _entries_file_map(official_root, official_entries)
    if pin_files != owned_files:
        raise UpstreamReleaseError("official pin contains unknown ownership")
    release = str(manifest.get("official_release", "")).strip()
    if not release:
        raise UpstreamReleaseError("manifest must name an official release")
    metadata_path = MATERIALIZATION_METADATA_PATH.as_posix()
    release_files = dict(owned_files)
    metadata_digest = release_files.pop(metadata_path, None)
    if metadata_digest is None:
        raise UpstreamReleaseError("official pin is missing materialization metadata")
    identity = {"payload": _path_identity(release_files), "release": release}
    return owned_files, {
        **identity,
        "id": _digest_id(identity),
        "materialization_metadata_digest": metadata_digest,
    }


def _replace_entry(source_root: Path, candidate_root: Path, entry: Mapping[str, object]) -> None:
    relative = _relative_path(entry.get("path"), "manifest entry")
    scope = str(entry.get("scope", ""))
    source = source_root / relative
    target = candidate_root / relative
    if target.is_symlink():
        raise UpstreamReleaseError("candidate path contains a symlink")
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    if scope == "file":
        if source.is_symlink() or not source.is_file():
            raise UpstreamReleaseError("manifest-owned file is missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    if scope != "tree" or source.is_symlink() or not source.is_dir():
        raise UpstreamReleaseError("manifest-owned tree is missing")
    shutil.copytree(source, target)


def _replace_entries(
    source_root: Path,
    candidate_root: Path,
    entries: Sequence[Mapping[str, object]],
) -> list[str]:
    before = _entries_file_map(candidate_root, entries)
    after = _entries_file_map(source_root, entries)
    for entry in entries:
        _replace_entry(source_root, candidate_root, entry)
    return sorted(set(before) | set(after))


def _candidate_file_map(
    source_root: Path,
    official_files: Mapping[str, str],
    official_entries: Sequence[Mapping[str, object]],
    overlay_entries: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    expected = _tree_file_map(source_root)
    for relative in _entries_file_map(source_root, official_entries):
        expected.pop(relative)
    for relative in _entries_file_map(source_root, overlay_entries):
        expected.pop(relative)
    expected.update(official_files)
    expected.update(_entries_file_map(source_root, overlay_entries))
    return expected


def _mutation_records(
    source_root: Path,
    official_root: Path,
    official_entries: Sequence[Mapping[str, object]],
    overlay_entries: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    official_paths = set(_entries_file_map(source_root, official_entries))
    official_paths.update(_entries_file_map(official_root, official_entries))
    return [
        {"owner": "official", "paths": sorted(official_paths)},
        {
            "owner": "overlay",
            "paths": sorted(_entries_file_map(source_root, overlay_entries)),
        },
    ]


def _preservation(
    source_root: Path,
    candidate_root: Path,
    manifest: Mapping[str, object],
) -> dict[str, dict[str, str]]:
    projection = manifest["deployment"]["adoption_projection"]
    optional_project_path = _relative_path(
        projection["path"],
        "adoption projection",
    )
    result: dict[str, dict[str, str]] = {}
    for owner in ("project", "generated"):
        entries = [entry for entry in manifest["entries"] if entry["owner"] == owner]

        def present_entries(root: Path) -> list[Mapping[str, object]]:
            return [
                entry
                for entry in entries
                if not (
                    owner == "project"
                    and _relative_path(entry["path"], "manifest entry")
                    == optional_project_path
                    and not os.path.lexists(root / optional_project_path)
                )
            ]

        source_files = _entries_file_map(source_root, present_entries(source_root))
        candidate_files = _entries_file_map(
            candidate_root,
            present_entries(candidate_root),
        )
        if source_files != candidate_files:
            raise UpstreamReleaseError(f"candidate did not preserve {owner} bytes")
        result[owner] = _path_identity(source_files)
    return result


def _release_identity(candidate_root: Path, manifest_file: Path) -> dict[str, object]:
    try:
        return _portable_release_identity(candidate_root, manifest_file)
    except QualificationError as exc:
        raise UpstreamReleaseError(str(exc)) from exc


def _candidate_plan(
    *,
    source_root: Path,
    official_root: Path,
    candidate_root: Path,
    manifest_file: Path,
    manifest_relative: Path,
    manifest: Mapping[str, object],
    source_identity: Mapping[str, str],
    official_files: Mapping[str, str],
    pin_identity: Mapping[str, object],
    artifact_persisted: bool,
) -> dict[str, object]:
    official_entries = [
        entry for entry in manifest["entries"] if entry["owner"] == "official"
    ]
    overlay_entries = [
        entry for entry in manifest["entries"] if entry["owner"] == "overlay"
    ]
    official_paths = _replace_entries(official_root, candidate_root, official_entries)
    overlay_paths = _replace_entries(source_root, candidate_root, overlay_entries)
    if os.path.lexists(candidate_root / ".git"):
        raise UpstreamReleaseError("candidate must not contain Git metadata")
    candidate_files = _tree_file_map(candidate_root)
    expected_files = _candidate_file_map(
        source_root,
        official_files,
        official_entries,
        overlay_entries,
    )
    if candidate_files != expected_files:
        raise UpstreamReleaseError("candidate changed an unknown-owned path")
    _require_conformance(candidate_root, candidate_root / manifest_relative)
    preservation = _preservation(source_root, candidate_root, manifest)
    release = _release_identity(candidate_root, candidate_root / manifest_relative)
    if release["official"]["id"] != pin_identity["id"]:
        raise UpstreamReleaseError("candidate official identity does not bind the pin")
    expected_mutations = _mutation_records(
        source_root,
        official_root,
        official_entries,
        overlay_entries,
    )
    actual_mutations = [
        {"owner": "official", "paths": official_paths},
        {"owner": "overlay", "paths": overlay_paths},
    ]
    if actual_mutations != expected_mutations:
        raise UpstreamReleaseError("candidate mutation paths do not match ownership")
    candidate = {
        "artifact_id": _digest_id({"snapshot_digest": digest_json(candidate_files)}),
        "artifact_persisted": artifact_persisted,
        "snapshot_digest": digest_json(candidate_files),
    }
    rollback = {
        "official_component_id": release["official"]["id"],
        "overlay_component_id": release["overlay"]["id"],
        "strategy": "discard_candidate",
    }
    plan = {
        "candidate": candidate,
        "manifest": {
            "digest": sha256(manifest_file.read_bytes()).hexdigest(),
            "relative_path": manifest_relative.as_posix(),
            "schema_version": manifest["manifest_schema_version"],
        },
        "mutations": actual_mutations,
        "pin": dict(pin_identity),
        "preservation": preservation,
        "release": release,
        "rollback": rollback,
        "schema_version": SCHEMA_VERSION,
        "source": dict(source_identity),
    }
    return {**plan, "plan_digest": digest_json(plan)}


def plan_candidate(
    source_root: os.PathLike[str] | str,
    official_root: os.PathLike[str] | str,
    scratch_root: os.PathLike[str] | str,
    *,
    candidate_output: os.PathLike[str] | str | None = None,
    manifest_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Plan one pinned candidate without mutating the canonical source."""
    source = _local_root(source_root, "source", scan_tree=True)
    official = _local_root(official_root, "official pin", scan_tree=True)
    scratch = _local_root(scratch_root, "scratch", scan_tree=False)
    _assert_disjoint(source, official, scratch)
    output = _new_output_path(candidate_output, scratch, source, official)
    manifest_file, manifest_relative, manifest = _manifest_file(source, manifest_path)
    source_before = _source_identity(source)
    _require_conformance(source, manifest_file)
    official_entries = [
        entry for entry in manifest["entries"] if entry["owner"] == "official"
    ]
    official_files, pin_identity = _pin_identity(official, official_entries, manifest)

    with tempfile.TemporaryDirectory(
        prefix="trellis-upstream-candidate-",
        dir=scratch,
    ) as temporary:
        candidate = Path(temporary)
        shutil.copytree(
            source,
            candidate,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git"),
        )
        plan = _candidate_plan(
            source_root=source,
            official_root=official,
            candidate_root=candidate,
            manifest_file=manifest_file,
            manifest_relative=manifest_relative,
            manifest=manifest,
            source_identity=source_before,
            official_files=official_files,
            pin_identity=pin_identity,
            artifact_persisted=output is not None,
        )
        source_after = _source_identity(source)
        if source_after != source_before:
            raise UpstreamReleaseError("candidate planning changed the source")
        if output is not None:
            try:
                shutil.copytree(candidate, output)
            except OSError as exc:
                shutil.rmtree(output, ignore_errors=True)
                raise UpstreamReleaseError("candidate artifact could not be persisted") from exc
    return plan


def _validated_plan(plan: Mapping[str, object]) -> dict[str, object]:
    value = dict(plan)
    digest = value.pop("plan_digest", None)
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(digest, str):
        raise UpstreamReleaseError("candidate plan schema is invalid")
    if digest_json(value) != digest:
        raise UpstreamReleaseError("candidate plan digest does not bind its payload")
    return value


def verify_candidate(
    source_root: os.PathLike[str] | str,
    official_root: os.PathLike[str] | str,
    candidate_root: os.PathLike[str] | str,
    plan: Mapping[str, object],
    *,
    manifest_path: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Verify one persisted candidate against its exact source and pin."""
    value = _validated_plan(plan)
    source = _local_root(source_root, "source", scan_tree=True)
    official = _local_root(official_root, "official pin", scan_tree=True)
    candidate = _local_root(candidate_root, "candidate", scan_tree=True)
    _assert_disjoint(source, official, candidate)
    if os.path.lexists(candidate / ".git"):
        raise UpstreamReleaseError("candidate must not contain Git metadata")
    manifest_file, manifest_relative, manifest = _manifest_file(source, manifest_path)
    planned_manifest = value.get("manifest")
    if (
        not isinstance(planned_manifest, Mapping)
        or planned_manifest.get("relative_path") != manifest_relative.as_posix()
        or planned_manifest.get("digest") != sha256(manifest_file.read_bytes()).hexdigest()
        or planned_manifest.get("schema_version") != manifest["manifest_schema_version"]
    ):
        raise UpstreamReleaseError("candidate plan manifest binding differs")
    source_identity = _source_identity(source)
    if value.get("source") != source_identity:
        raise UpstreamReleaseError("candidate plan source identity drifted")
    _require_conformance(source, manifest_file)
    official_entries = [
        entry for entry in manifest["entries"] if entry["owner"] == "official"
    ]
    overlay_entries = [
        entry for entry in manifest["entries"] if entry["owner"] == "overlay"
    ]
    official_files, pin_identity = _pin_identity(official, official_entries, manifest)
    if value.get("pin") != pin_identity:
        raise UpstreamReleaseError("candidate plan pin identity drifted")
    candidate_files = _tree_file_map(candidate)
    expected_files = _candidate_file_map(
        source,
        official_files,
        official_entries,
        overlay_entries,
    )
    if candidate_files != expected_files:
        raise UpstreamReleaseError("candidate bytes differ from the planned ownership map")
    expected_mutations = _mutation_records(
        source,
        official,
        official_entries,
        overlay_entries,
    )
    if value.get("mutations") != expected_mutations:
        raise UpstreamReleaseError("candidate plan mutation paths drifted")
    planned_candidate = value.get("candidate")
    if (
        not isinstance(planned_candidate, Mapping)
        or planned_candidate.get("snapshot_digest") != digest_json(candidate_files)
        or planned_candidate.get("artifact_id")
        != _digest_id({"snapshot_digest": digest_json(candidate_files)})
    ):
        raise UpstreamReleaseError("candidate snapshot identity drifted")
    candidate_manifest = candidate / manifest_relative
    _require_conformance(candidate, candidate_manifest)
    preservation = _preservation(source, candidate, manifest)
    if value.get("preservation") != preservation:
        raise UpstreamReleaseError("candidate preservation identity drifted")
    release = _release_identity(candidate, candidate_manifest)
    if release["official"]["id"] != pin_identity["id"] or value.get("release") != release:
        raise UpstreamReleaseError("candidate release identity drifted")
    rollback = {
        "official_component_id": release["official"]["id"],
        "overlay_component_id": release["overlay"]["id"],
        "strategy": "discard_candidate",
    }
    if value.get("rollback") != rollback:
        raise UpstreamReleaseError("candidate rollback identity drifted")
    verified = {
        "release": release,
        "schema_version": SCHEMA_VERSION,
        "status": "verified",
    }
    return {**verified, "verification_digest": digest_json(verified)}
