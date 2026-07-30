"""Deterministic, read-only downstream candidate planning."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Mapping

from common.task_activity import TaskStateInvalid, classify_task_activity
from loop_v1.qualification import (
    MATERIALIZATION_METADATA_PATH,
    MANIFEST_PATH,
    SOURCE_RELEASE_PURPOSE,
    QualificationError,
    canonical_json,
    check_overlay_conformance,
    configured_qualification,
    digest_json,
    load_overlay_manifest,
    validate_materialization_metadata,
)


_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMPED_BACKUP_RE = re.compile(
    r"\.backup-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\Z"
)
_MANAGED_OWNERS = frozenset({"official", "overlay"})
_PRESERVED_OWNERS = frozenset({"project", "generated"})
_OFFICIAL_COMMAND = ("trellis", "update", "--force", "--migrate")
_TRELLIS_CLI_ENV = "TRELLIS_CLI"
_COMMIT_HOOKS = (
    "pre-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
)


class PlanError(RuntimeError):
    """Raised when a target cannot be planned without broadening authority."""


def _error(code: str, detail: str) -> PlanError:
    return PlanError(f"{code}: {detail}")


def _safe_relative(raw: object, label: str) -> Path:
    value = str(raw).strip()
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in value
        or pure.as_posix() != value
        or value == "."
    ):
        raise _error("MANIFEST_INVALID", f"{label} is not repository-relative")
    return Path(*pure.parts)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise _error(
            "GIT_IDENTITY_INVALID",
            completed.stderr.strip() or completed.stdout.strip() or "git read failed",
        )
    return completed.stdout.strip()


def _active_tasks(root: Path) -> list[str]:
    tasks_root = root / ".trellis/tasks"
    if not tasks_root.is_dir():
        return []
    active: list[str] = []
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir() or task_dir.name == "archive":
            continue
        task_file = task_dir / "task.json"
        if not task_file.is_file():
            continue
        if task_file.is_symlink():
            raise _error("TASK_STATE_INVALID", f"{task_file.name} is a symlink")
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _error("TASK_STATE_INVALID", f"{task_file.name}: {exc}") from exc
        if not isinstance(task, dict):
            raise _error("TASK_STATE_INVALID", f"{task_file.name} is not an object")
        try:
            activity = classify_task_activity(task_dir, root)
        except TaskStateInvalid as exc:
            raise _error("TASK_STATE_INVALID", str(exc)) from exc
        if activity.active:
            active.append(task_dir.name)
    return active


def _snapshot(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        name = relative.as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[name] = {
                "mode": mode,
                "target": os.readlink(path),
                "type": "symlink",
            }
        elif path.is_file():
            payload = path.read_bytes()
            result[name] = {
                "mode": mode,
                "sha256": sha256(payload).hexdigest(),
                "size": len(payload),
                "type": "file",
            }
    return result


def _instruction_identity(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            ".git" in relative.parts
            or path.name not in {"AGENTS.md", "CLAUDE.md"}
        ):
            continue
        if path.is_symlink():
            raise _error("SYMLINK_REJECTED", relative.as_posix())
        if path.is_file():
            result[relative.as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def _git_policy_digest(root: Path) -> str:
    config_digest = sha256(
        _git(root, "config", "--show-origin", "--null", "--list").encode("utf-8")
    ).hexdigest()
    hooks_value = Path(_git(root, "rev-parse", "--git-path", "hooks"))
    hooks_root = hooks_value if hooks_value.is_absolute() else root / hooks_value
    hooks: dict[str, dict[str, object]] = {}
    if hooks_root.is_symlink():
        raise _error("SYMLINK_REJECTED", "effective Git hooks directory")
    for name in _COMMIT_HOOKS:
        hook = hooks_root / name
        if hook.is_symlink():
            raise _error("SYMLINK_REJECTED", f"Git hook: {name}")
        if hook.is_file():
            hooks[name] = {
                "executable": os.access(hook, os.X_OK),
                "mode": stat.S_IMODE(hook.stat().st_mode),
                "sha256": sha256(hook.read_bytes()).hexdigest(),
            }
    return digest_json({"config": config_digest, "hooks": hooks})


def _repo_identity(root: Path, role: str) -> dict[str, object]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise _error(f"{role}_ROOT_INVALID", "root is not a directory")
    git_root = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if git_root != resolved:
        raise _error(f"{role}_ROOT_INVALID", "root is not the exact Git worktree root")
    active = _active_tasks(resolved)
    if active:
        raise _error(f"{role}_ACTIVE_TASK", canonical_json(active))
    status_value = _git(
        resolved,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status_value:
        raise _error(f"{role}_DIRTY", "Git worktree or index is not clean")
    branch = _git(resolved, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise _error(f"{role}_DETACHED_HEAD", "symbolic branch is required")
    snapshot = _snapshot(resolved)
    instructions = _instruction_identity(resolved)
    return {
        "active_tasks": active,
        "branch": branch,
        "checks_digest": digest_json(
            {
                "config": snapshot.get(".trellis/config.yaml"),
                "instructions": instructions,
            }
        ),
        "git_policy_digest": _git_policy_digest(resolved),
        "head": _git(resolved, "rev-parse", "HEAD^{commit}"),
        "instructions": instructions,
        "snapshot": snapshot,
        "snapshot_digest": digest_json(snapshot),
        "status_digest": sha256(status_value.encode("utf-8")).hexdigest(),
        "tree": _git(resolved, "rev-parse", "HEAD^{tree}"),
    }


def _assert_no_symlink(root: Path, relative: Path, label: str) -> None:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise _error("SYMLINK_REJECTED", f"{label}: {relative.as_posix()}")


def _entry_files(
    root: Path,
    entry: Mapping[str, object],
) -> list[Path]:
    relative = _safe_relative(entry.get("path"), "entry path")
    scope = str(entry.get("scope"))
    _assert_no_symlink(root, relative, "managed path")
    target = root / relative
    if scope == "file":
        if not target.is_file():
            raise _error("MANAGED_PATH_MISSING", relative.as_posix())
        return [relative]
    if scope != "tree":
        raise _error("MANIFEST_INVALID", f"unknown scope for {relative.as_posix()}")
    if not target.is_dir():
        raise _error("MANAGED_PATH_MISSING", relative.as_posix())
    files: list[Path] = []
    for path in target.rglob("*"):
        if path.is_symlink():
            raise _error(
                "SYMLINK_REJECTED",
                path.relative_to(root).as_posix(),
            )
        if path.is_file() and "__pycache__" not in path.parts:
            files.append(path.relative_to(root))
    return sorted(files)


def _owner_payload(
    root: Path,
    manifest: Mapping[str, object],
    owner: str,
) -> dict[str, str]:
    payload: dict[str, str] = {}
    for entry in manifest["entries"]:
        if (
            entry["owner"] != owner
            or (
                owner == "official"
                and Path(str(entry["path"])) == MATERIALIZATION_METADATA_PATH
            )
        ):
            continue
        for relative in _entry_files(root, entry):
            payload[relative.as_posix()] = sha256(
                (root / relative).read_bytes()
            ).hexdigest()
    return payload


def _source_identity(
    root: Path,
    manifest_file: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise _error("SOURCE_ROOT_INVALID", "root is not a directory")
    git_root = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if git_root != resolved:
        raise _error("SOURCE_ROOT_INVALID", "root is not the exact Git worktree root")
    branch = _git(resolved, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise _error("SOURCE_DETACHED_HEAD", "symbolic branch is required")
    qualification = configured_qualification(
        resolved,
        purpose=SOURCE_RELEASE_PURPOSE,
    )
    if (
        not qualification.enforced
        or not qualification.valid
        or not qualification.receipt_id
    ):
        raise _error(
            "SOURCE_QUALIFICATION_INVALID",
            canonical_json(list(qualification.issues)),
        )
    conformance = check_overlay_conformance(resolved, manifest_file)
    if conformance["status"] != "passed":
        raise _error(
            "SOURCE_PAYLOAD_INVALID",
            canonical_json(conformance["issues"]),
        )
    owner_payloads = {
        owner: _owner_payload(resolved, manifest, owner)
        for owner in sorted(_MANAGED_OWNERS)
    }
    if any(
        conformance["owners"][owner]["payload_digest"]
        != digest_json(owner_payloads[owner])
        for owner in _MANAGED_OWNERS
    ):
        raise _error("SOURCE_PAYLOAD_INVALID", "managed payload changed while read")
    return {
        "branch": branch,
        "commit": _git(resolved, "rev-parse", "HEAD^{commit}"),
        "manifest_digest": sha256(manifest_file.read_bytes()).hexdigest(),
        "owner_payloads": owner_payloads,
        "qualification_receipt_id": qualification.receipt_id,
        "tree": _git(resolved, "rev-parse", "HEAD^{tree}"),
    }


def _deployment_policy(manifest: Mapping[str, object]) -> Mapping[str, object]:
    policy = manifest.get("deployment")
    if (
        manifest.get("manifest_schema_version") != 2
        or not isinstance(policy, Mapping)
        or policy.get("policy_schema_version") != 1
    ):
        raise _error("MANIFEST_INVALID", "deployment policy v1 is required")
    return policy


def _adoption_projection(
    manifest: Mapping[str, object],
) -> Mapping[str, object]:
    projection = _deployment_policy(manifest).get("adoption_projection")
    expected = {
        "delete": "never",
        "owner": "project",
        "path": ".trellis/deploy/adoption.json",
        "source_disposition": "preserve",
        "write": "deployer-replace-only",
    }
    if not isinstance(projection, Mapping) or dict(projection) != expected:
        raise _error("MANIFEST_INVALID", "adoption projection policy is invalid")
    return projection


def _verification_policy(
    commands: Sequence[Sequence[str]],
) -> list[list[str]]:
    normalized: list[list[str]] = []
    for command in commands:
        if isinstance(command, (str, bytes)):
            raise _error("CHECK_POLICY_INVALID", "commands must be argv arrays")
        argv = [str(part) for part in command]
        if not argv or any(not part or "\0" in part for part in argv):
            raise _error("CHECK_POLICY_INVALID", "command argv is invalid")
        normalized.append(argv)
    return normalized


def _owner_for_path(path: str, manifest: Mapping[str, object]) -> str:
    relative = Path(path)
    matches = [
        str(entry["owner"])
        for entry in manifest["entries"]
        if Path(str(entry["path"])) == relative
        or (
            entry["scope"] == "tree"
            and Path(str(entry["path"])) in relative.parents
        )
    ]
    if len(matches) > 1:
        raise _error("OWNERSHIP_OVERLAP", path)
    if matches:
        return matches[0]
    policy = _deployment_policy(manifest)
    if policy.get("target_only_owner") != "project":
        raise _error("OWNERSHIP_UNKNOWN", path)
    return "project"


def _deletion_map(
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    policy = _deployment_policy(manifest)
    raw = policy.get("intentional_deletions")
    if not isinstance(raw, list):
        raise _error("MANIFEST_INVALID", "intentional_deletions must be a list")
    result: dict[str, Mapping[str, object]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise _error("MANIFEST_INVALID", "deletion disposition must be an object")
        relative = _safe_relative(item.get("path"), "deletion path").as_posix()
        owner = _owner_for_path(relative, manifest)
        if (
            owner not in _MANAGED_OWNERS
            or item.get("owner") != owner
            or item.get("scope") != "file"
            or not _DIGEST_RE.fullmatch(str(item.get("preimage_sha256", "")))
            or relative in result
        ):
            raise _error("MANIFEST_INVALID", f"invalid deletion: {relative}")
        result[relative] = item
    return result


def _validate_managed_target_types(
    source: Path,
    target: Path,
    manifest: Mapping[str, object],
) -> None:
    for entry in manifest["entries"]:
        if entry["owner"] not in _MANAGED_OWNERS:
            continue
        relative = _safe_relative(entry["path"], "entry path")
        source_path = source / relative
        target_path = target / relative
        _assert_no_symlink(source, relative, "source managed path")
        _assert_no_symlink(target, relative, "target managed path")
        expected_dir = entry["scope"] == "tree"
        if not source_path.exists():
            if relative.as_posix() in _deletion_map(manifest):
                continue
            raise _error("MANAGED_PATH_MISSING", relative.as_posix())
        if source_path.is_dir() != expected_dir:
            raise _error("MANAGED_TYPE_CONFLICT", relative.as_posix())
        if target_path.exists() and target_path.is_dir() != expected_dir:
            raise _error("MANAGED_TYPE_CONFLICT", relative.as_posix())
        if target_path.is_dir():
            for child in target_path.rglob("*"):
                if child.is_symlink():
                    raise _error(
                        "SYMLINK_REJECTED",
                        child.relative_to(target).as_posix(),
                    )


def _assert_private_root(root: Path, source: Path, target: Path) -> Path:
    resolved = root.resolve()
    if not resolved.is_dir() or root.is_symlink():
        raise _error("SCRATCH_INVALID", "scratch root must be an existing real directory")
    for protected in (source.resolve(), target.resolve()):
        if (
            resolved == protected
            or resolved.is_relative_to(protected)
            or protected.is_relative_to(resolved)
        ):
            raise _error("SCRATCH_INVALID", "scratch must be disjoint from source and target")
    return resolved


def _copy_target(target: Path, candidate: Path) -> None:
    shutil.copytree(
        target,
        candidate,
        ignore=shutil.ignore_patterns(".git"),
        symlinks=True,
    )


def _normalized_output(value: str, candidate: Path) -> str:
    return value.replace(str(candidate), "<candidate>").replace("\\", "/")


def _materialize_official(candidate: Path) -> dict[str, object]:
    executable = os.environ.get(_TRELLIS_CLI_ENV)
    command = list(_OFFICIAL_COMMAND)
    if executable is not None:
        path = Path(executable)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise _error(
                "OFFICIAL_MATERIALIZATION_FAILED",
                "TRELLIS_CLI is not an executable file",
            )
        command[0] = str(path.resolve())
    try:
        completed = subprocess.run(
            command,
            cwd=candidate,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _error("OFFICIAL_MATERIALIZATION_FAILED", type(exc).__name__) from exc
    stdout = _normalized_output(completed.stdout, candidate)
    stderr = _normalized_output(completed.stderr, candidate)
    evidence = {
        "command": list(_OFFICIAL_COMMAND),
        "returncode": completed.returncode,
        "stderr_digest": sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_digest": sha256(stdout.encode("utf-8")).hexdigest(),
    }
    if completed.returncode != 0:
        raise _error("OFFICIAL_MATERIALIZATION_FAILED", canonical_json(evidence))
    return evidence


def _run_checks(
    root: Path,
    commands: Sequence[Sequence[str]],
    *,
    label: str,
    failure: Callable[[str, str], Exception] = _error,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for index, command in enumerate(commands):
        try:
            completed = subprocess.run(
                list(command),
                cwd=root,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                timeout=300,
            )
            output = completed.stdout + b"\0" + completed.stderr
            row = {
                "command_digest": digest_json(list(command)),
                "index": index,
                "output_digest": "sha256:" + sha256(output).hexdigest(),
                "returncode": completed.returncode,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            row = {
                "command_digest": digest_json(list(command)),
                "error": type(exc).__name__,
                "index": index,
            }
            raise failure("CHECK_FAILED", f"{label}:{digest_json(row)}") from exc
        results.append(row)
        if completed.returncode != 0:
            raise failure("CHECK_FAILED", f"{label}:{digest_json(row)}")
    return results


def _restore_preserved_paths(
    target: Path,
    candidate: Path,
    target_snapshot: Mapping[str, Mapping[str, object]],
    manifest: Mapping[str, object],
) -> list[str]:
    restored: list[str] = []
    candidate_snapshot = _snapshot(candidate)
    for name in sorted(set(target_snapshot) | set(candidate_snapshot)):
        before = target_snapshot.get(name)
        after = candidate_snapshot.get(name)
        if before == after or _owner_for_path(name, manifest) not in _PRESERVED_OWNERS:
            continue
        relative = Path(name)
        destination = candidate / relative
        if before is None:
            if destination.is_dir() and not destination.is_symlink():
                raise _error("PRESERVATION_VIOLATION", name)
            destination.unlink()
        elif before.get("type") == "file":
            source = target / relative
            if (
                not source.is_file()
                or source.is_symlink()
                or (destination.exists() and not destination.is_file())
                or destination.is_symlink()
            ):
                raise _error("PRESERVATION_VIOLATION", name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        else:
            raise _error("PRESERVATION_VIOLATION", name)
        if (
            len(relative.parts) >= 2
            and relative.parts[0] == ".trellis"
            and _TIMESTAMPED_BACKUP_RE.fullmatch(relative.parts[1])
        ):
            name = Path(
                ".trellis",
                ".backup-TIMESTAMP",
                *relative.parts[2:],
            ).as_posix()
        restored.append(name)
    return sorted(set(restored))


def _apply_overlay(
    source: Path,
    candidate: Path,
    manifest: Mapping[str, object],
) -> dict[str, str]:
    deletions = set(_deletion_map(manifest))
    payload: dict[str, str] = {}
    for entry in manifest["entries"]:
        if entry["owner"] != "overlay":
            continue
        relative = _safe_relative(entry["path"], "overlay path")
        source_files = _entry_files(source, entry)
        source_set = {path.as_posix() for path in source_files}
        candidate_root = candidate / relative
        if entry["scope"] == "tree" and candidate_root.exists():
            if not candidate_root.is_dir():
                raise _error("MANAGED_TYPE_CONFLICT", relative.as_posix())
            for path in candidate_root.rglob("*"):
                if path.is_symlink():
                    raise _error(
                        "SYMLINK_REJECTED",
                        path.relative_to(candidate).as_posix(),
                    )
                if path.is_file():
                    name = path.relative_to(candidate).as_posix()
                    if (
                        "__pycache__" not in path.parts
                        and name not in source_set
                        and name not in deletions
                    ):
                        raise _error("UNDECLARED_OVERLAY_DESCENDANT", name)
        for source_file in source_files:
            _assert_no_symlink(candidate, source_file, "candidate overlay path")
            destination = candidate / source_file
            if destination.exists() and not destination.is_file():
                raise _error("MANAGED_TYPE_CONFLICT", source_file.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / source_file, destination)
            payload[source_file.as_posix()] = sha256(
                destination.read_bytes()
            ).hexdigest()
    return payload


def _apply_deletions(
    candidate: Path,
    target_snapshot: Mapping[str, Mapping[str, object]],
    manifest: Mapping[str, object],
) -> list[str]:
    deleted: list[str] = []
    for relative, disposition in sorted(_deletion_map(manifest).items()):
        before = target_snapshot.get(relative)
        if (
            not isinstance(before, Mapping)
            or before.get("type") != "file"
            or before.get("sha256") != disposition["preimage_sha256"]
        ):
            raise _error("DELETION_PREIMAGE_MISMATCH", relative)
        path = candidate / relative
        _assert_no_symlink(candidate, Path(relative), "deletion path")
        if not path.is_file():
            raise _error("DELETION_CANDIDATE_MISMATCH", relative)
        path.unlink()
        deleted.append(relative)
    return deleted


def _mutations(
    before: Mapping[str, Mapping[str, object]],
    after: Mapping[str, Mapping[str, object]],
    manifest: Mapping[str, object],
) -> list[dict[str, str]]:
    deletions = _deletion_map(manifest)
    result: list[dict[str, str]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        owner = _owner_for_path(path, manifest)
        if owner in _PRESERVED_OWNERS:
            raise _error("PRESERVATION_VIOLATION", path)
        if new is None and path not in deletions:
            raise _error("UNAUTHORIZED_DELETION", path)
        if old is None:
            change = "create"
        elif new is None:
            change = "delete"
        elif old.get("type") != new.get("type"):
            raise _error("MANAGED_TYPE_CONFLICT", path)
        else:
            change = "update"
        result.append({"change": change, "owner": owner, "path": path})
    return result


def plan_target(
    source_root: Path,
    target_root: Path,
    target_id: str,
    *,
    manifest_path: Path | None = None,
    scratch_root: Path | None = None,
    candidate_output: Path | None = None,
    verification_commands: Sequence[Sequence[str]] = (),
) -> dict[str, object]:
    """Build and digest one private candidate while preserving both repositories."""
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    if (
        source == target
        or source.is_relative_to(target)
        or target.is_relative_to(source)
    ):
        raise _error("ROOT_OVERLAP", "source and target must be disjoint")
    if target.name != target_id:
        raise _error("TARGET_BINDING_MISMATCH", target_id)

    manifest_input = Path(manifest_path or source / MANIFEST_PATH).absolute()
    if not manifest_input.is_relative_to(source):
        raise _error("MANIFEST_INVALID", "manifest must be a source-owned file")
    _assert_no_symlink(
        source,
        manifest_input.relative_to(source),
        "manifest path",
    )
    manifest_file = manifest_input.resolve()
    if not manifest_file.is_file():
        raise _error("MANIFEST_INVALID", "manifest must be a source-owned file")
    manifest = load_overlay_manifest(manifest_file)
    source_before = _source_identity(source, manifest_file, manifest)
    policy = _deployment_policy(manifest)
    projection = _adoption_projection(manifest)
    targets = policy.get("targets")
    if not isinstance(targets, list) or target_id not in targets:
        raise _error("TARGET_NOT_ENROLLED", target_id)
    target_before = _repo_identity(target, "TARGET")
    _validate_managed_target_types(source, target, manifest)
    scratch_base = _assert_private_root(
        Path(scratch_root or tempfile.gettempdir()),
        source,
        target,
    )
    candidate_artifact = (
        Path(candidate_output).absolute() if candidate_output is not None else None
    )
    if candidate_artifact is not None:
        _assert_private_root(candidate_artifact.parent, source, target)
        if candidate_artifact.exists() or candidate_artifact.is_symlink():
            raise _error("CANDIDATE_OUTPUT_EXISTS", "candidate output must not exist")
    checks = _verification_policy(verification_commands)
    post_materialization: dict[str, dict[str, object]]
    candidate_snapshot: dict[str, dict[str, object]]
    official_evidence: dict[str, object]
    overlay_payload: dict[str, str]
    deleted: list[str]
    candidate_checks: list[dict[str, object]]

    candidate_artifact_created = False
    try:
        with tempfile.TemporaryDirectory(
            prefix="trellis-deploy-plan-",
            dir=scratch_base,
        ) as tmp:
            candidate = Path(tmp) / "candidate"
            _copy_target(target, candidate)
            official_evidence = _materialize_official(candidate)
            official_evidence["preserved_paths"] = _restore_preserved_paths(
                target,
                candidate,
                target_before["snapshot"],
                manifest,
            )
            try:
                official_evidence["materialization_metadata"] = (
                    validate_materialization_metadata(candidate, manifest)
                )
            except QualificationError as exc:
                raise _error(
                    "MATERIALIZATION_METADATA_INVALID",
                    str(exc),
                ) from exc
            official_candidate = _owner_payload(candidate, manifest, "official")
            if official_candidate != source_before["owner_payloads"]["official"]:
                raise _error(
                    "OFFICIAL_MATERIALIZATION_MISMATCH",
                    digest_json(official_candidate),
                )
            post_materialization = _snapshot(candidate)
            overlay_payload = _apply_overlay(source, candidate, manifest)
            if overlay_payload != source_before["owner_payloads"]["overlay"]:
                raise _error(
                    "SOURCE_MUTATED_DURING_PLAN",
                    str(source_before["commit"]),
                )
            deleted = _apply_deletions(
                candidate,
                target_before["snapshot"],
                manifest,
            )
            candidate_snapshot = _snapshot(candidate)
            candidate_payloads = {
                owner: _owner_payload(candidate, manifest, owner)
                for owner in sorted(_MANAGED_OWNERS)
            }
            if candidate_payloads != source_before["owner_payloads"]:
                raise _error(
                    "SOURCE_MUTATED_DURING_PLAN",
                    str(source_before["commit"]),
                )
            mutations = _mutations(
                target_before["snapshot"],
                candidate_snapshot,
                manifest,
            )
            candidate_checks = _run_checks(
                candidate,
                checks,
                label="candidate",
            )
            checked_snapshot = _snapshot(candidate)
            if checked_snapshot != candidate_snapshot:
                raise _error(
                    "CHECK_MUTATED_CANDIDATE",
                    digest_json(checked_snapshot),
                )
            if candidate_artifact is not None:
                candidate_artifact.mkdir()
                candidate_artifact_created = True
                shutil.copytree(
                    candidate,
                    candidate_artifact,
                    dirs_exist_ok=True,
                    symlinks=True,
                )

        source_after = _source_identity(
            source,
            manifest_file,
            load_overlay_manifest(manifest_file),
        )
        target_after = _repo_identity(target, "TARGET")
        if source_after != source_before:
            raise _error(
                "SOURCE_MUTATED_DURING_PLAN",
                str(source_before["commit"]),
            )
        if target_after != target_before:
            raise _error("TARGET_MUTATED_DURING_PLAN", target_before["head"])
    except BaseException:
        if candidate_artifact_created and candidate_artifact is not None:
            shutil.rmtree(candidate_artifact, ignore_errors=True)
        raise

    plan: dict[str, object] = {
        "candidate": {
            "artifact_persisted": candidate_artifact is not None,
            "checks_result_digest": digest_json(candidate_checks),
            "payload_digest": digest_json(candidate_snapshot),
            "predicted_mutations": mutations,
        },
        "manifest": {
            "adoption_projection": {
                "path": projection["path"],
                "policy_digest": digest_json(projection),
            },
            "deletion_digest": digest_json(policy["intentional_deletions"]),
            "digest": source_before["manifest_digest"],
            "overlay_version": manifest["overlay_version"],
            "path": manifest_file.relative_to(source).as_posix(),
            "preservation_digest": digest_json(policy["preserve"]),
            "schema_version": manifest["manifest_schema_version"],
        },
        "phases": [
            {
                "evidence_digest": target_before["snapshot_digest"],
                "name": "target_preimage",
            },
            {
                "evidence": official_evidence,
                "name": "official_materialization",
            },
            {
                "evidence_digest": digest_json(post_materialization),
                "name": "post_materialization_preimage",
            },
            {
                "deleted": deleted,
                "evidence_digest": digest_json(overlay_payload),
                "name": "overlay_application",
            },
            {
                "evidence_digest": digest_json(candidate_snapshot),
                "name": "candidate_complete",
            },
            {
                "evidence_digest": digest_json(candidate_checks),
                "name": "candidate_verification",
            },
        ],
        "read_only": {
            "source_after": digest_json(source_after),
            "source_before": digest_json(source_before),
            "target_after": target_after["snapshot_digest"],
            "target_before": target_before["snapshot_digest"],
            "verified": True,
        },
        "scratch": {
            "lifecycle": "cleaned",
            "path_persisted": False,
        },
        "source": source_before,
        "status": "planned",
        "target": {
            "branch": target_before["branch"],
            "checks_digest": target_before["checks_digest"],
            "git_policy_digest": target_before["git_policy_digest"],
            "head": target_before["head"],
            "id": target_id,
            "instructions": target_before["instructions"],
            "ownership_history": target_before["snapshot"].get(
                ".trellis/deploy/adoption.json"
            ),
            "preimage_digest": target_before["snapshot_digest"],
            "task_fingerprint": digest_json(target_before["active_tasks"]),
            "tree": target_before["tree"],
            "verification_policy_digest": digest_json(checks),
        },
    }
    plan["plan_digest"] = digest_json(plan)
    return plan
