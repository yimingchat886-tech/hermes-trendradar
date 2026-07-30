"""Exact-plan downstream promotion, recovery, receipt, and local commit."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from common.io import write_bytes_atomic
from loop_v1.qualification import (
    IGNORED_OFFICIAL_PATHS,
    digest_json,
    load_overlay_manifest,
)

from .planning import (
    PlanError,
    _assert_no_symlink,
    _git,
    _git_policy_digest,
    _instruction_identity,
    _repo_identity,
    _run_checks,
    _safe_relative,
    _snapshot,
    _source_identity,
    _verification_policy,
)


_ADOPTION_PATH = Path(".trellis/deploy/adoption.json")
_DIGEST_PREFIX = "sha256:"
_IDENTITY = {
    "email": "trellis-loop-updater@localhost",
    "name": "Trellis Loop Updater",
}
_MUTATION_CHANGES = frozenset({"create", "delete", "update"})
_MUTATION_OWNERS = frozenset({"official", "overlay"})
_RECOVERY_BINDING_FIELDS = {
    "adoption_receipt_id",
    "base_head",
    "branch",
    "check_policy_digest",
    "commit_policy_digest",
    "deployment_policy_digest",
    "effect_digest",
    "failed_transaction_id",
    "lineage_anchor",
    "notification_policy_digest",
    "ownership_digest",
    "plan_digest",
    "preimage_digest",
    "preservation_digest",
    "recovery_policy_digest",
    "repository_id",
    "source_receipt_id",
    "target_id",
}


class TransactionError(RuntimeError):
    """Raised when an exact transaction cannot safely begin."""


def _error(code: str, detail: str) -> TransactionError:
    return TransactionError(f"{code}: {detail}")


def _is_digest(value: object, *, prefixed: bool = False) -> bool:
    text = str(value)
    if prefixed:
        if not text.startswith(_DIGEST_PREFIX):
            return False
        text = text.removeprefix(_DIGEST_PREFIX)
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def _receipt(payload: Mapping[str, object]) -> dict[str, object]:
    value = dict(payload)
    value["receipt_id"] = _DIGEST_PREFIX + digest_json(payload)
    return value


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _reason_code(error: Exception) -> str:
    if isinstance(error, (PlanError, TransactionError)):
        return str(error).split(":", 1)[0]
    return type(error).__name__.upper()


def _validate_plan(plan: Mapping[str, object]) -> list[dict[str, str]]:
    value = dict(plan)
    plan_digest = str(value.pop("plan_digest", ""))
    if (
        not _is_digest(plan_digest)
        or digest_json(value) != plan_digest
        or plan.get("status") != "planned"
    ):
        raise _error("PLAN_INVALID", "plan digest or status is invalid")
    candidate = plan.get("candidate")
    target = plan.get("target")
    manifest = plan.get("manifest")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("artifact_persisted") is not True
        or not isinstance(target, Mapping)
        or not isinstance(manifest, Mapping)
    ):
        raise _error("PLAN_INVALID", "required transaction bindings are missing")
    projection = manifest.get("adoption_projection")
    if (
        not isinstance(projection, Mapping)
        or projection.get("path") != _ADOPTION_PATH.as_posix()
        or not _is_digest(projection.get("policy_digest"))
    ):
        raise _error("PLAN_INVALID", "adoption projection binding is invalid")
    raw_mutations = candidate.get("predicted_mutations")
    if not isinstance(raw_mutations, list):
        raise _error("PLAN_INVALID", "predicted mutations must be a list")
    mutations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_mutations:
        if not isinstance(item, Mapping):
            raise _error("PLAN_INVALID", "predicted mutation is not an object")
        change = str(item.get("change", ""))
        owner = str(item.get("owner", ""))
        path = _safe_relative(item.get("path"), "mutation path").as_posix()
        if (
            set(item) != {"change", "owner", "path"}
            or change not in _MUTATION_CHANGES
            or owner not in _MUTATION_OWNERS
            or (
                Path(path) in IGNORED_OFFICIAL_PATHS
                and owner != "official"
            )
            or path in seen
        ):
            raise _error("PLAN_INVALID", f"invalid predicted mutation: {path}")
        seen.add(path)
        mutations.append({"change": change, "owner": owner, "path": path})
    if mutations != sorted(mutations, key=lambda item: item["path"]):
        raise _error("PLAN_INVALID", "predicted mutations are not sorted")
    return mutations


def _git_visible_paths(
    mutations: Sequence[Mapping[str, str]],
) -> set[str]:
    return {
        item["path"]
        for item in mutations
        if Path(item["path"]) not in IGNORED_OFFICIAL_PATHS
    } | {_ADOPTION_PATH.as_posix()}


def _target_preflight(
    target: Path,
    plan: Mapping[str, object],
) -> dict[str, object]:
    expected = plan["target"]
    if not isinstance(expected, Mapping) or target.name != expected.get("id"):
        raise _error("TARGET_BINDING_MISMATCH", target.name)
    actual = _repo_identity(target, "TARGET")
    fields = (
        "branch",
        "checks_digest",
        "git_policy_digest",
        "head",
        "instructions",
        "preimage_digest",
        "tree",
    )
    normalized = {**actual, "preimage_digest": actual["snapshot_digest"]}
    if any(normalized.get(field) != expected.get(field) for field in fields):
        raise _error("TARGET_DRIFT", str(expected.get("id", "")))
    return actual


def _source_preflight(
    source: Path,
    plan: Mapping[str, object],
) -> dict[str, object]:
    expected = plan.get("source")
    manifest_binding = plan.get("manifest")
    if not isinstance(expected, Mapping) or not isinstance(manifest_binding, Mapping):
        raise _error("PLAN_INVALID", "source binding is missing")
    relative = _safe_relative(manifest_binding.get("path"), "manifest path")
    _assert_no_symlink(source, relative, "manifest path")
    manifest_file = source / relative
    if not manifest_file.is_file():
        raise _error("SOURCE_DRIFT", str(expected.get("commit", "")))
    actual = _source_identity(
        source,
        manifest_file,
        load_overlay_manifest(manifest_file),
    )
    if actual != dict(expected):
        raise _error("SOURCE_DRIFT", str(expected.get("commit", "")))
    return actual


def _candidate_preflight(
    source: Path,
    target: Path,
    candidate: Path,
    plan: Mapping[str, object],
    target_before: Mapping[str, object],
    mutations: Sequence[Mapping[str, str]],
) -> None:
    resolved = candidate.resolve()
    if (
        not resolved.is_dir()
        or candidate.is_symlink()
        or any(
            resolved == protected
            or resolved.is_relative_to(protected)
            or protected.is_relative_to(resolved)
            for protected in (source, target)
        )
        or (resolved / ".git").exists()
    ):
        raise _error("CANDIDATE_INVALID", "candidate root is invalid")
    snapshot = _snapshot(resolved)
    if digest_json(snapshot) != plan["candidate"].get("payload_digest"):
        raise _error("CANDIDATE_DRIFT", "candidate payload digest changed")
    expected = {item["path"]: dict(item) for item in mutations}
    before = target_before["snapshot"]
    actual: dict[str, dict[str, str]] = {}
    for path in sorted(set(before) | set(snapshot)):
        old = before.get(path)
        new = snapshot.get(path)
        if old == new:
            continue
        if old is None:
            change = "create"
        elif new is None:
            change = "delete"
        elif old.get("type") != new.get("type"):
            raise _error("CANDIDATE_TYPE_DRIFT", path)
        else:
            change = "update"
        if path not in expected or expected[path]["change"] != change:
            raise _error("CANDIDATE_SCOPE_DRIFT", path)
        actual[path] = expected[path]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        raise _error("CANDIDATE_SCOPE_DRIFT", ",".join(missing))
    for item in mutations:
        relative = Path(item["path"])
        _assert_no_symlink(resolved, relative, "candidate mutation")
        candidate_path = resolved / relative
        if item["change"] == "delete":
            if candidate_path.exists() or candidate_path.is_symlink():
                raise _error("CANDIDATE_SCOPE_DRIFT", item["path"])
        elif not candidate_path.is_file():
            raise _error("CANDIDATE_TYPE_DRIFT", item["path"])


def _candidate_receipt(
    plan: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    target = plan["target"]
    return _receipt(
        {
            "candidate": {
                "payload_digest": plan["candidate"]["payload_digest"],
                "predicted_mutations_digest": digest_json(
                    plan["candidate"]["predicted_mutations"]
                ),
            },
            "checks_result_digest": digest_json(checks),
            "manifest_digest": plan["manifest"]["digest"],
            "plan_digest": plan["plan_digest"],
            "schema_version": 1,
            "source_receipt_id": plan["source"]["qualification_receipt_id"],
            "status": "candidate_verified",
            "target": {
                "base_head": target["head"],
                "branch": target["branch"],
                "id": target["id"],
                "preimage_digest": target["preimage_digest"],
            },
        }
    )


def _projection_bytes(
    plan: Mapping[str, object],
    candidate_receipt: Mapping[str, object],
) -> bytes:
    payload = {
        "manifest_digest": plan["manifest"]["digest"],
        "overlay_version": plan["manifest"]["overlay_version"],
        "plan_digest": plan["plan_digest"],
        "schema_version": 1,
        "source": {
            "commit": plan["source"]["commit"],
            "qualification_receipt_id": plan["source"]["qualification_receipt_id"],
        },
        "target": {
            "base_head": plan["target"]["head"],
            "branch": plan["target"]["branch"],
            "id": plan["target"]["id"],
            "receipt_id": candidate_receipt["receipt_id"],
        },
    }
    return _json_bytes(payload)


def _capture_preimage(
    source: Path,
    target: Path,
    paths: Sequence[str],
    *,
    recovery_root: Path,
    transaction_id: str,
    target_before: Mapping[str, object],
) -> tuple[Path, dict[str, object]]:
    root_input = Path(recovery_root).expanduser().absolute()
    if any(
        candidate.exists() and candidate.is_symlink()
        for candidate in (root_input, *root_input.parents)
    ):
        raise _error("RECOVERY_ROOT_INVALID", "symlink path is forbidden")
    root = root_input.resolve()
    if not root.is_dir() or any(
        root == protected
        or root.is_relative_to(protected)
        or protected.is_relative_to(root)
        for protected in (source, target)
    ):
        raise _error("RECOVERY_ROOT_INVALID", "recovery root must be disjoint")
    transaction_root = root / transaction_id.removeprefix(_DIGEST_PREFIX)
    try:
        transaction_root.mkdir()
    except FileExistsError as exc:
        raise _error("TRANSACTION_EXISTS", transaction_id) from exc
    entries: dict[str, dict[str, object]] = {}
    try:
        for name in paths:
            relative = Path(name)
            _assert_no_symlink(target, relative, "target preimage")
            path = target / relative
            if path.exists():
                if not path.is_file():
                    raise _error("TARGET_TYPE_DRIFT", name)
                payload = path.read_bytes()
                entries[name] = {
                    "content": base64.b64encode(payload).decode("ascii"),
                    "exists": True,
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "sha256": sha256(payload).hexdigest(),
                }
            else:
                entries[name] = {"exists": False}
        preimage = {
            "entries": entries,
            "schema_version": 1,
            "target": {
                "branch": target_before["branch"],
                "head": target_before["head"],
                "identity_digest": digest_json(target_before),
            },
            "transaction_id": transaction_id,
        }
        write_bytes_atomic(
            transaction_root / "preimage.json",
            _json_bytes(preimage),
        )
        return transaction_root, preimage
    except BaseException:
        shutil.rmtree(transaction_root, ignore_errors=True)
        raise


def _write_file(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink(path.parent, Path(path.name), "transaction write")
    write_bytes_atomic(path, payload)
    path.chmod(mode)


def _promote_path(target: Path, candidate: Path, mutation: Mapping[str, str]) -> None:
    relative = Path(mutation["path"])
    _assert_no_symlink(target, relative, "target mutation")
    destination = target / relative
    if mutation["change"] == "delete":
        if not destination.is_file():
            raise _error("TARGET_TYPE_DRIFT", mutation["path"])
        destination.unlink()
        return
    source = candidate / relative
    if not source.is_file() or source.is_symlink():
        raise _error("CANDIDATE_TYPE_DRIFT", mutation["path"])
    if destination.exists() and not destination.is_file():
        raise _error("TARGET_TYPE_DRIFT", mutation["path"])
    _write_file(
        destination,
        source.read_bytes(),
        stat.S_IMODE(source.stat().st_mode),
    )


def _write_projection(target: Path, payload: bytes) -> None:
    _assert_no_symlink(target, _ADOPTION_PATH, "adoption projection")
    path = target / _ADOPTION_PATH
    if path.exists() and not path.is_file():
        raise _error("TARGET_TYPE_DRIFT", _ADOPTION_PATH.as_posix())
    _write_file(path, payload, 0o644)


def _changed_paths(root: Path) -> set[str]:
    tracked = _git_bytes(root, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = _git_bytes(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in (tracked + untracked).split(b"\0")
        if item
    }


def _git_bytes(
    root: Path,
    *args: str,
) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if completed.returncode != 0:
        raise _error(
            "GIT_TRANSACTION_FAILED",
            _DIGEST_PREFIX
            + sha256(completed.stdout + b"\0" + completed.stderr).hexdigest(),
        )
    return completed.stdout


def _validate_commit_authority(
    authority: Mapping[str, object] | None,
    plan: Mapping[str, object],
    target: Path,
) -> Mapping[str, object] | None:
    if authority is None:
        return None
    required = {
        "authority_id",
        "authority_kind",
        "base_head",
        "branch",
        "enabled",
        "lineage_anchor",
        "plan_digest",
        "source_receipt_id",
    }
    if (
        set(authority) != required
        or authority.get("enabled") is not True
        or authority.get("authority_kind") not in {"grant", "one_off"}
        or authority.get("base_head") != plan["target"]["head"]
        or authority.get("branch") != plan["target"]["branch"]
        or authority.get("plan_digest") != plan["plan_digest"]
        or authority.get("source_receipt_id")
        != plan["source"]["qualification_receipt_id"]
        or not _is_digest(authority.get("authority_id"), prefixed=True)
    ):
        raise _error("COMMIT_AUTHORITY_INVALID", "commit authority does not match plan")
    anchor = str(authority.get("lineage_anchor", ""))
    try:
        _git_bytes(target, "merge-base", "--is-ancestor", anchor, plan["target"]["head"])
    except TransactionError as exc:
        raise _error("LINEAGE_INVALID", "accepted anchor is not an ancestor") from exc
    return authority


def _assert_commit_fresh(target: Path, plan: Mapping[str, object]) -> None:
    expected = plan["target"]
    if (
        _git(target, "symbolic-ref", "--quiet", "--short", "HEAD")
        != expected["branch"]
        or _git(target, "rev-parse", "HEAD^{commit}") != expected["head"]
        or _git_policy_digest(target) != expected["git_policy_digest"]
        or _instruction_identity(target) != expected["instructions"]
    ):
        raise _error("COMMIT_POLICY_DRIFT", str(expected["id"]))


def _commit_message(
    plan: Mapping[str, object],
    authority: Mapping[str, object],
    target_receipt_id: str,
) -> str:
    return "\n".join(
        [
            f"chore(trellis): apply {plan['manifest']['overlay_version']}",
            "",
            f"Trellis-Grant: {authority['authority_id']}",
            f"Trellis-Plan: sha256:{plan['plan_digest']}",
            f"Trellis-Source-Receipt: {plan['source']['qualification_receipt_id']}",
            f"Trellis-Target-Receipt: {target_receipt_id}",
        ]
    )


def _local_commit(
    target: Path,
    paths: Sequence[str],
    plan: Mapping[str, object],
    authority: Mapping[str, object],
    target_receipt_id: str,
) -> dict[str, object]:
    expected = set(paths)
    _git_bytes(target, "add", "--", *paths)
    staged = {
        item.decode("utf-8", errors="surrogateescape")
        for item in _git_bytes(
            target,
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        ).split(b"\0")
        if item
    }
    if staged != expected or _git_bytes(target, "diff", "--name-only", "-z", "--"):
        raise _error("STAGED_SCOPE_MISMATCH", digest_json(sorted(staged)))
    if _git_bytes(
        target,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    ):
        raise _error("STAGED_SCOPE_MISMATCH", "untracked path escaped staging")
    staged_tree = _git(target, "write-tree")
    message = _commit_message(plan, authority, target_receipt_id)
    identity_env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": _IDENTITY["email"],
        "GIT_AUTHOR_NAME": _IDENTITY["name"],
        "GIT_COMMITTER_EMAIL": _IDENTITY["email"],
        "GIT_COMMITTER_NAME": _IDENTITY["name"],
    }
    completed = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=target,
        env=identity_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    output_digest = _DIGEST_PREFIX + sha256(
        completed.stdout + b"\0" + completed.stderr
    ).hexdigest()
    if completed.returncode != 0:
        raise _error("GIT_COMMIT_FAILED", output_digest)
    commit_id = _git(target, "rev-parse", "HEAD^{commit}")
    if (
        _git(target, "rev-parse", f"{commit_id}^") != plan["target"]["head"]
        or _git(target, "rev-parse", f"{commit_id}^{{tree}}") != staged_tree
        or _git(
            target,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    ):
        raise _error("GIT_COMMIT_MISMATCH", commit_id)
    return {
        "authority_id": authority["authority_id"],
        "authority_kind": authority["authority_kind"],
        "commit_id": commit_id,
        "output_digest": output_digest,
        "parent": plan["target"]["head"],
        "staged_paths_digest": digest_json(sorted(staged)),
        "tree": staged_tree,
    }


def _prune_empty_parents(path: Path, root: Path) -> None:
    current = path.parent
    while current != root:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _restore_preimage(
    target: Path,
    preimage: Mapping[str, object],
    target_before: Mapping[str, object],
    commit: Mapping[str, object] | None,
) -> None:
    base_head = str(target_before["head"])
    current_head = _git(target, "rev-parse", "HEAD^{commit}")
    if current_head != base_head:
        if (
            commit is None
            or current_head != commit.get("commit_id")
            or _git(target, "rev-parse", f"{current_head}^") != base_head
        ):
            raise _error("RECOVERY_REF_UNKNOWN", current_head)
        ref = f"refs/heads/{target_before['branch']}"
        _git(target, "update-ref", ref, base_head, current_head)
    _git(target, "read-tree", base_head)
    entries = preimage.get("entries")
    if not isinstance(entries, Mapping):
        raise _error("RECOVERY_PREIMAGE_INVALID", "entries are missing")
    for name, raw in entries.items():
        if not isinstance(raw, Mapping):
            raise _error("RECOVERY_PREIMAGE_INVALID", str(name))
        relative = _safe_relative(name, "recovery path")
        _assert_no_symlink(target, relative, "recovery path")
        path = target / relative
        if raw.get("exists") is True:
            try:
                payload = base64.b64decode(str(raw["content"]), validate=True)
                mode = int(raw["mode"])
            except (KeyError, TypeError, ValueError) as exc:
                raise _error("RECOVERY_PREIMAGE_INVALID", str(name)) from exc
            if sha256(payload).hexdigest() != raw.get("sha256"):
                raise _error("RECOVERY_PREIMAGE_INVALID", str(name))
            _write_file(path, payload, mode)
        else:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise _error("RECOVERY_TYPE_UNKNOWN", str(name))
            if path.exists():
                path.unlink()
                _prune_empty_parents(path, target)
    expected_identity = target_before.get("identity_digest")
    if expected_identity is None:
        expected_identity = digest_json(target_before)
    if (
        not _is_digest(expected_identity)
        or digest_json(_repo_identity(target, "TARGET")) != expected_identity
    ):
        raise _error("RECOVERY_MISMATCH", str(target_before["head"]))


def _failure_result(
    *,
    error: Exception,
    plan: Mapping[str, object],
    preimage: Mapping[str, object],
    recovered: bool,
    transaction_id: str,
    recovery_error: Exception | None = None,
) -> dict[str, object]:
    result = {
        "error_digest": _DIGEST_PREFIX + sha256(str(error).encode("utf-8")).hexdigest(),
        "plan_digest": plan["plan_digest"],
        "reason_code": _reason_code(error),
        "recovery": {
            "preimage_digest": digest_json(preimage),
            "verified": recovered,
        },
        "status": "recovered" if recovered else "unknown_outcome",
        "target_id": plan["target"]["id"],
        "transaction_id": transaction_id,
    }
    if recovery_error is not None:
        result["recovery"]["reason_code"] = _reason_code(recovery_error)
        result["recovery"]["error_digest"] = _DIGEST_PREFIX + sha256(
            str(recovery_error).encode("utf-8")
        ).hexdigest()
    return result


def verify_transaction(
    source_root: Path,
    target_root: Path,
    plan: Mapping[str, object],
    final_receipt: Mapping[str, object],
    *,
    verification_commands: Sequence[Sequence[str]],
) -> dict[str, object]:
    """Verify one final receipt against the exact current source and target."""
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    if (
        source == target
        or source.is_relative_to(target)
        or target.is_relative_to(source)
    ):
        raise _error("ROOT_OVERLAP", "source and target must be disjoint")
    mutations = _validate_plan(plan)
    _source_preflight(source, plan)
    commands = _verification_policy(verification_commands)
    if not commands or digest_json(commands) != plan["target"].get(
        "verification_policy_digest"
    ):
        raise _error("CHECK_POLICY_INVALID", "checks do not match the exact plan")

    receipt = dict(final_receipt)
    receipt_id = receipt.pop("receipt_id", "")
    if (
        set(receipt)
        != {
            "candidate_receipt_id",
            "checks_result_digest",
            "commit",
            "final_payload_digest",
            "plan_digest",
            "projection_digest",
            "schema_version",
            "status",
            "target",
        }
        or receipt_id != _receipt(receipt)["receipt_id"]
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "verified"
        or receipt.get("plan_digest") != plan["plan_digest"]
        or not _is_digest(receipt.get("candidate_receipt_id"), prefixed=True)
        or not _is_digest(receipt.get("final_payload_digest"))
        or not _is_digest(receipt.get("projection_digest"))
    ):
        raise _error("FINAL_RECEIPT_INVALID", "receipt binding is invalid")
    target_receipt = receipt.get("target")
    if (
        not isinstance(target_receipt, Mapping)
        or set(target_receipt) != {"branch", "head", "id"}
        or target_receipt.get("branch") != plan["target"]["branch"]
        or target_receipt.get("id") != plan["target"]["id"]
        or target.name != plan["target"]["id"]
    ):
        raise _error("FINAL_RECEIPT_INVALID", "target binding is invalid")
    if (
        _git(target, "symbolic-ref", "--quiet", "--short", "HEAD")
        != plan["target"]["branch"]
        or _git_policy_digest(target) != plan["target"]["git_policy_digest"]
        or _instruction_identity(target) != plan["target"]["instructions"]
    ):
        raise _error("TARGET_DRIFT", str(plan["target"]["id"]))

    projection = target / _ADOPTION_PATH
    expected_projection = _projection_bytes(
        plan,
        {"receipt_id": receipt["candidate_receipt_id"]},
    )
    if (
        not projection.is_file()
        or projection.is_symlink()
        or projection.read_bytes() != expected_projection
        or sha256(expected_projection).hexdigest() != receipt["projection_digest"]
        or digest_json(_snapshot(target)) != receipt["final_payload_digest"]
    ):
        raise _error("FINAL_RECEIPT_DRIFT", str(plan["target"]["id"]))

    git_paths = _git_visible_paths(mutations)
    commit = receipt.get("commit")
    current_head = _git(target, "rev-parse", "HEAD^{commit}")
    if commit is None:
        if (
            current_head != plan["target"]["head"]
            or target_receipt.get("head") != current_head
            or _changed_paths(target) != git_paths
        ):
            raise _error("FINAL_RECEIPT_DRIFT", str(plan["target"]["id"]))
    elif (
        not isinstance(commit, Mapping)
        or set(commit)
        != {
            "authority_id",
            "authority_kind",
            "commit_id",
            "output_digest",
            "parent",
            "staged_paths_digest",
            "tree",
        }
        or current_head != commit.get("commit_id")
        or target_receipt.get("head") != current_head
        or commit.get("parent") != plan["target"]["head"]
        or commit.get("staged_paths_digest") != digest_json(sorted(git_paths))
        or _git(target, "rev-parse", f"{current_head}^") != plan["target"]["head"]
        or _git(target, "rev-parse", f"{current_head}^{{tree}}") != commit.get("tree")
        or _changed_paths(target)
    ):
        raise _error("FINAL_RECEIPT_DRIFT", str(plan["target"]["id"]))

    checks = _run_checks(target, commands, label="verify", failure=_error)
    checks_digest = digest_json(checks)
    if checks_digest != receipt["checks_result_digest"]:
        raise _error("FINAL_CHECK_DRIFT", checks_digest)
    return {
        "checks_result_digest": checks_digest,
        "committed": commit is not None,
        "plan_digest": plan["plan_digest"],
        "receipt_id": receipt_id,
        "status": "verified",
        "target_id": plan["target"]["id"],
    }


def recover_transaction(
    target_root: Path,
    recovery_root: Path,
    transaction_id: str,
    recovery_authority: Mapping[str, object],
    recovery_binding: Mapping[str, object],
) -> dict[str, object]:
    """Restore one retained preimage under consumed recovery authority."""
    target_input = Path(target_root).expanduser().absolute()
    root_input = Path(recovery_root).expanduser().absolute()
    if any(
        candidate.exists() and candidate.is_symlink()
        for path in (target_input, root_input)
        for candidate in (path, *path.parents)
    ):
        raise _error("RECOVERY_ROOT_INVALID", "symlink path is forbidden")
    target = target_input.resolve()
    root = root_input.resolve()
    transaction_id = str(transaction_id)
    authority = dict(recovery_authority)
    binding = dict(recovery_binding)
    if (
        not target.is_dir()
        or not root.is_dir()
        or root == target
        or root.is_relative_to(target)
        or target.is_relative_to(root)
        or not _is_digest(transaction_id, prefixed=True)
        or set(binding) != _RECOVERY_BINDING_FIELDS
        or set(authority)
        != {
            "authority_id",
            "authority_kind",
            "binding_digest",
            "consumption_id",
            "effect",
            "enabled",
        }
        or authority.get("authority_kind") != "one_off"
        or authority.get("effect") != "recover"
        or authority.get("enabled") is not True
        or not _is_digest(authority.get("authority_id"), prefixed=True)
        or authority.get("binding_digest") != digest_json(binding)
        or binding.get("failed_transaction_id") != transaction_id
        or binding.get("target_id") != target.name
    ):
        raise _error("RECOVERY_AUTHORITY_INVALID", "authority or root is invalid")
    transaction_root = root / transaction_id.removeprefix(_DIGEST_PREFIX)
    preimage_path = transaction_root / "preimage.json"
    if (
        transaction_root.is_symlink()
        or preimage_path.is_symlink()
        or not preimage_path.is_file()
    ):
        raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id)
    try:
        preimage = json.loads(preimage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id) from exc
    if (
        not isinstance(preimage, Mapping)
        or set(preimage) != {"entries", "schema_version", "target", "transaction_id"}
        or preimage.get("schema_version") != 1
        or preimage.get("transaction_id") != transaction_id
        or binding.get("preimage_digest") != digest_json(preimage)
    ):
        raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id)
    target_before = preimage.get("target")
    if (
        not isinstance(target_before, Mapping)
        or set(target_before) != {"branch", "head", "identity_digest"}
        or not _is_digest(target_before.get("identity_digest"))
        or binding.get("base_head") != target_before.get("head")
        or binding.get("branch") != target_before.get("branch")
    ):
        raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id)
    commit: Mapping[str, object] | None = None
    commit_path = transaction_root / "commit.json"
    if commit_path.exists():
        if commit_path.is_symlink() or not commit_path.is_file():
            raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id)
        try:
            value = json.loads(commit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id) from exc
        if not isinstance(value, Mapping):
            raise _error("RECOVERY_PREIMAGE_INVALID", transaction_id)
        commit = value
    _restore_preimage(target, preimage, target_before, commit)
    return _receipt(
        {
            "authority_consumption_id": str(authority["consumption_id"]),
            "authority_id": str(authority["authority_id"]),
            "preimage_digest": digest_json(preimage),
            "schema_version": 1,
            "status": "recovered",
            "target_id": target.name,
            "transaction_id": transaction_id,
        }
    )


def apply_transaction(
    source_root: Path,
    target_root: Path,
    candidate_root: Path,
    plan: Mapping[str, object],
    *,
    verification_commands: Sequence[Sequence[str]],
    recovery_root: Path,
    local_commit_authority: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Apply one exact plan to one target, recovering any failed mutation."""
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    candidate = Path(candidate_root).resolve()
    if (
        source == target
        or source.is_relative_to(target)
        or target.is_relative_to(source)
    ):
        raise _error("ROOT_OVERLAP", "source and target must be disjoint")
    mutations = _validate_plan(plan)
    commands = _verification_policy(verification_commands)
    if not commands or digest_json(commands) != plan["target"].get(
        "verification_policy_digest"
    ):
        raise _error("CHECK_POLICY_INVALID", "checks do not match the exact plan")
    try:
        source_before = _source_preflight(source, plan)
        target_before = _target_preflight(target, plan)
        _candidate_preflight(
            source,
            target,
            candidate,
            plan,
            target_before,
            mutations,
        )
    except PlanError as exc:
        raise _error("PREFLIGHT_INVALID", str(exc)) from exc
    candidate_checks = _run_checks(
        candidate,
        commands,
        label="candidate",
        failure=_error,
    )
    candidate_receipt = _candidate_receipt(plan, candidate_checks)
    authority = _validate_commit_authority(
        local_commit_authority,
        plan,
        target,
    )
    projection = _projection_bytes(plan, candidate_receipt)
    paths = [item["path"] for item in mutations] + [_ADOPTION_PATH.as_posix()]
    git_paths = _git_visible_paths(mutations)
    if len(paths) != len(set(paths)):
        raise _error("PLAN_INVALID", "adoption projection overlaps mutations")
    transaction_id = _DIGEST_PREFIX + digest_json(
        {
            "candidate_receipt_id": candidate_receipt["receipt_id"],
            "plan_digest": plan["plan_digest"],
            "target_id": plan["target"]["id"],
        }
    )
    transaction_root, preimage = _capture_preimage(
        source,
        target,
        paths,
        recovery_root=Path(recovery_root),
        transaction_id=transaction_id,
        target_before=target_before,
    )
    commit: dict[str, object] | None = None
    mutation_started = False
    try:
        if (
            _source_preflight(source, plan) != source_before
            or _repo_identity(target, "TARGET") != target_before
            or digest_json(_snapshot(candidate))
            != plan["candidate"]["payload_digest"]
        ):
            raise _error("TRANSACTION_DRIFT", str(plan["target"]["id"]))
        for mutation in mutations:
            mutation_started = True
            _promote_path(target, candidate, mutation)
        mutation_started = True
        _write_projection(target, projection)
        if _changed_paths(target) != git_paths:
            raise _error("PROMOTION_SCOPE_MISMATCH", digest_json(sorted(_changed_paths(target))))
        final_checks = _run_checks(
            target,
            commands,
            label="target",
            failure=_error,
        )
        if authority is not None:
            _assert_commit_fresh(target, plan)
            commit = _local_commit(
                target,
                sorted(git_paths),
                plan,
                authority,
                str(candidate_receipt["receipt_id"]),
            )
            write_bytes_atomic(
                transaction_root / "commit.json",
                _json_bytes(commit),
            )
        final_snapshot = _snapshot(target)
        final_receipt = _receipt(
            {
                "candidate_receipt_id": candidate_receipt["receipt_id"],
                "checks_result_digest": digest_json(final_checks),
                "commit": commit,
                "final_payload_digest": digest_json(final_snapshot),
                "plan_digest": plan["plan_digest"],
                "projection_digest": sha256(projection).hexdigest(),
                "schema_version": 1,
                "status": "verified",
                "target": {
                    "branch": plan["target"]["branch"],
                    "head": commit["commit_id"] if commit else plan["target"]["head"],
                    "id": plan["target"]["id"],
                },
            }
        )
        return {
            "candidate_receipt": candidate_receipt,
            "commit": commit,
            "final_receipt": final_receipt,
            "plan_digest": plan["plan_digest"],
            "recovery": {
                "preimage_digest": digest_json(preimage),
                "retained": True,
            },
            "status": "succeeded",
            "target_id": plan["target"]["id"],
            "transaction_id": transaction_id,
        }
    except Exception as exc:
        if not mutation_started:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        try:
            _restore_preimage(target, preimage, target_before, commit)
        except Exception as recovery_exc:
            return _failure_result(
                error=exc,
                plan=plan,
                preimage=preimage,
                recovered=False,
                transaction_id=transaction_id,
                recovery_error=recovery_exc,
            )
        return _failure_result(
            error=exc,
            plan=plan,
            preimage=preimage,
            recovered=True,
            transaction_id=transaction_id,
        )
