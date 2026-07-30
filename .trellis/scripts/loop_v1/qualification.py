"""Role-aware local Loop v1 qualification controls.

Harness source_development validates configured receipt identity, source_release
remains strict, and downstream installed_runtime verifies target-local managed
bytes without consulting the canonical source checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from common.io import write_bytes_atomic
from common.trellis_config import read_trellis_config


# Python may inherit the Windows temp directory in WSL, where qualification's
# clone-heavy fixtures are prohibitively slow. Keep Linux-local temp I/O local.
if platform.system() == "Linux" and tempfile.gettempdir().startswith("/mnt/"):
    tempfile.tempdir = "/tmp"


# Keep package imports and the -m entry point on one exception identity.
if __name__ == "__main__" and __spec__ is not None:
    sys.modules[__spec__.name] = sys.modules[__name__]


RECEIPT_SCHEMA_VERSION = 4
LAYER_RECEIPT_SCHEMA_VERSION = 1
HARNESS_SOURCE_ROLE = "harness_source"
DOWNSTREAM_PROJECT_ROLE = "downstream_project"
SOURCE_DEVELOPMENT_PURPOSE = "source_development"
SOURCE_RELEASE_PURPOSE = "source_release"
INSTALLED_RUNTIME_PURPOSE = "installed_runtime"
_PURPOSE_ROLES = {
    SOURCE_DEVELOPMENT_PURPOSE: HARNESS_SOURCE_ROLE,
    SOURCE_RELEASE_PURPOSE: HARNESS_SOURCE_ROLE,
    INSTALLED_RUNTIME_PURPOSE: DOWNSTREAM_PROJECT_ROLE,
}
LAYER_RECEIPT_TYPE = "trellis_qualification_layer"
ARTIFACT_QUALIFICATION_LAYER = "artifact"
LOCAL_RUNTIME_QUALIFICATION_LAYER = "local_runtime"
TARGET_VERIFICATION_QUALIFICATION_LAYER = "target"
QUALIFICATION_LAYERS = frozenset(
    {
        ARTIFACT_QUALIFICATION_LAYER,
        LOCAL_RUNTIME_QUALIFICATION_LAYER,
        TARGET_VERIFICATION_QUALIFICATION_LAYER,
    }
)
MINIMUM_TOOL_CAPABILITIES = {
    "git": "available",
    "python": ">=3.11",
    "trellis": "available",
}
OVERLAY_MANIFEST_SCHEMA_VERSION = 2
LEDGER_MIGRATION_GENERATION = 1
QUALIFICATION_IDS = tuple(f"Q{number:02d}" for number in range(1, 13))
OWNER_CLASSES = frozenset({"official", "overlay", "project", "generated"})
MATERIALIZATION_METADATA_PATH = Path(".trellis/.template-hashes.json")
IGNORED_OFFICIAL_PATHS = frozenset(
    {Path(".trellis/.version"), MATERIALIZATION_METADATA_PATH}
)
DEPLOYMENT_TARGETS = (
    "RAG v2",
    "FamiliOS",
    "Hermes stock",
    "qivance-music",
    "xhs-qn-pipeline",
    "AH-map",
)
DEPLOYER_TEST_IDS = (
    "test_downstream_deployer_authority.DownstreamAuthorityTests."
    "test_ah_map_accepts_one_off_authority_but_not_grants",
    "test_downstream_deployer_plan.DownstreamDeployerPlanTests."
    "test_private_candidate_is_deterministic_and_read_only",
    "test_downstream_deployer_plan.DownstreamDeployerPlanTests."
    "test_ah_map_is_one_off_plan_eligible",
    "test_downstream_deployer_plan.DownstreamDeployerPlanTests."
    "test_candidate_checks_gate_artifact_persistence",
    "test_downstream_deployer_plan.DownstreamDeployerPlanTests."
    "test_planning_rejects_drift_and_ownership_conflicts",
    "test_downstream_deployer_cli.DownstreamDeployerCliTests."
    "test_failed_plan_check_persists_no_candidate_plan_or_authority",
    "test_downstream_deployer_transaction.DownstreamDeployerTransactionTests."
    "test_exact_promotion_receipt_and_local_commit",
    "test_loop_v1_downstream_runtime.DownstreamInstalledRuntimeTests."
    "test_source_offline_runtime_advances_integrates_and_cancels",
    "test_single_target.SingleTargetTests."
    "test_single_target_plan_is_source_and_target_read_only",
)
MANIFEST_PATH = Path(".trellis/spec/project/loop-v1-overlay-manifest.json")
OLDEST_DOWNSTREAM_FIXTURE_PATH = Path(
    ".trellis/scripts/tests/fixtures/rag-v2-0.6.5"
)
OLDEST_DOWNSTREAM_SMOKE_COMMANDS = (
    (sys.executable, ".trellis/scripts/task.py", "--help"),
    (
        sys.executable,
        "-c",
        (
            "import sys; sys.path.insert(0, '.trellis/scripts'); "
            "from state_machine import cancel_task"
        ),
    ),
)
DISABLE_MARKER = Path(".trellis/.runtime/loop-v1/admission-disabled.json")
_INSTALLED_ROOT_ENV = "TRELLIS_LOOP_V1_INSTALLED_ROOT"
RUNTIME_PATHS = (
    Path(".trellis/scripts/loop_v1"),
    Path(".trellis/scripts/common"),
    Path(".trellis/scripts/downstream_deployer"),
    OLDEST_DOWNSTREAM_FIXTURE_PATH,
    Path(".trellis/scripts/state_machine.py"),
    Path(".trellis/scripts/task.py"),
)
TEST_PATH_GLOB = ".trellis/scripts/tests/test_loop_v1_*.py"
DEPLOYER_TEST_PATH_GLOB = ".trellis/scripts/tests/test_downstream_deployer_*.py"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_EFFECTS = frozenset(
    {
        "git_read",
        "local_process",
        "local_read",
        "local_runtime_write",
        "qualification_receipt_write",
    }
)


class QualificationError(RuntimeError):
    """Raised when qualification or activation cannot fail safely."""


@dataclass(frozen=True)
class QualificationStatus:
    """Current configured receipt state used by admission and ledger guards."""

    enforced: bool
    valid: bool
    receipt_id: str | None
    issues: tuple[str, ...]


_QUALIFICATION_MATRIX: dict[str, tuple[str, str]] = {
    "Q01": (
        "test_loop_v1_acceptance.LoopV1AcceptanceTests."
        "test_two_child_happy_path_qualifies_end_to_end",
        "test_loop_v1_acceptance.LoopV1AcceptanceTests."
        "test_two_child_path_rejects_incomplete_required_child",
    ),
    "Q02": (
        "test_loop_v1_admission.LoopV1AdmissionTests."
        "test_no_selector_keeps_current_trellis_modes",
        "test_loop_v1_admission.LoopV1AdmissionTests."
        "test_historical_and_unknown_selectors_fail_closed",
    ),
    "Q03": (
        "test_loop_v1_scheduler.LoopV1SchedulerTests."
        "test_same_child_requirement_dependencies_are_co_delivered",
        "test_loop_v1_scheduler.LoopV1SchedulerTests."
        "test_graph_cycles_pause_and_satisfied_edges_repair",
    ),
    "Q04": (
        "test_loop_v1_scheduler.LoopV1SchedulerTests."
        "test_ready_order_cap_three_and_replay_are_deterministic",
        "test_loop_v1_scheduler.LoopV1SchedulerTests."
        "test_alias_path_exclusive_and_shared_capacity_conflicts",
    ),
    "Q05": (
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_explicit_pause_resume_and_cancel_compose_existing_controls",
        "test_loop_v1_context.LoopV1ContextTests."
        "test_every_boundary_rejects_stale_context",
    ),
    "Q06": (
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_guided_replacement_reopens_exact_path_closed_prerequisite_slice",
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_guided_replacement_rejects_missing_path_and_passing_branch",
    ),
    "Q07": (
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_q07_integration_and_final_check_positive_contract",
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_q07_integration_and_final_check_negative_contract",
    ),
    "Q08": (
        "test_loop_v1_acceptance.LoopV1AcceptanceTests."
        "test_crash_after_merge_recovers_without_second_merge",
        "test_loop_v1_recovery.LoopV1RecoveryTests."
        "test_prepared_candidate_recovers_as_no_effect_under_new_fence",
    ),
    "Q09": (
        "test_loop_v1_qualification.LoopV1QualificationFixtureTests."
        "test_valid_receipt_allows_ledger_write",
        "test_loop_v1_qualification.LoopV1QualificationFixtureTests."
        "test_disable_marker_blocks_new_admission_without_pausing_active_run",
    ),
    "Q10": (
        "test_loop_v1_recovery.LoopV1RecoveryTests."
        "test_cancel_is_human_only_terminal_and_preserves_integrated_evidence",
        "test_loop_v1_context.LoopV1ContextTests."
        "test_start_request_is_immutable_and_direct_response_bound",
    ),
    "Q11": (
        "test_loop_v1_orchestrator.LoopV1OrchestratorTests."
        "test_final_review_subset_replaces_complete_multi_requirement_child",
        "test_loop_v1_acceptance.LoopV1AcceptanceTests."
        "test_drift_after_approval_invalidates_gate_without_merging",
    ),
    "Q12": (
        "test_loop_v1_qualification.LoopV1QualificationFixtureTests."
        "test_local_qualification_requires_no_remote_ci_hooks_or_network",
        "test_loop_v1_qualification.LoopV1QualificationFixtureTests."
        "test_unknown_and_prohibited_effects_fail_closed",
    ),
}


def canonical_json(value: object) -> str:
    """Return the canonical JSON representation used by all digests."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest_json(value: object) -> str:
    """Hash a canonical JSON value."""
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest_id(value: object) -> str:
    return "sha256:" + digest_json(value)


def _receipt_path(repo_root: Path, receipt_path: Path) -> Path:
    path = Path(receipt_path)
    return path if path.is_absolute() else Path(repo_root).resolve() / path


def _hash_addressed_identity(
    receipt: Mapping[str, object],
    path: Path,
) -> tuple[dict[str, object], str | None, list[str]]:
    payload = dict(receipt)
    digest = str(payload.pop("receipt_digest", ""))
    receipt_id = str(payload.pop("receipt_id", "")) or None
    issues: list[str] = []
    if not _DIGEST_RE.fullmatch(digest) or digest_json(payload) != digest:
        issues.append("receipt payload digest does not match")
    if path.stem != digest:
        issues.append("receipt path is not hash-addressed by its payload")
    if receipt_id != f"sha256:{digest}":
        issues.append("receipt identity does not match its digest")
    return payload, receipt_id, issues


def _write_layer_receipt(output_dir: Path, payload: Mapping[str, object]) -> Path:
    require_local_effect("qualification_receipt_write")
    body = dict(payload)
    if "receipt_digest" in body or "receipt_id" in body:
        raise QualificationError("layer receipt payload reserves receipt identity fields")
    digest = digest_json(body)
    receipt = {**body, "receipt_digest": digest, "receipt_id": f"sha256:{digest}"}
    destination = Path(output_dir).resolve() / f"{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    if destination.exists() and destination.read_bytes() != encoded:
        raise QualificationError("hash-addressed receipt path contains different bytes")
    write_bytes_atomic(destination, encoded)
    return destination


def _read_layer_receipt(
    repo_root: Path,
    receipt_path: Path,
    layer: str,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    path = _receipt_path(repo_root, receipt_path)
    try:
        receipt = _read_json_object(path, "qualification layer receipt")
    except QualificationError as exc:
        return None, None, [str(exc)]
    _payload, receipt_id, issues = _hash_addressed_identity(receipt, path)
    if receipt.get("receipt_type") != LAYER_RECEIPT_TYPE:
        issues.append("receipt type is not a qualification layer")
    if receipt.get("receipt_schema_version") != LAYER_RECEIPT_SCHEMA_VERSION:
        issues.append("qualification layer schema version is unsupported")
    if receipt.get("layer") != layer:
        issues.append(f"receipt layer is not {layer}")
    return receipt, receipt_id, issues


def _layer_verification_result(
    layer: str,
    receipt_id: str | None,
    issues: Sequence[str],
) -> dict[str, Any]:
    return {
        "issues": list(issues),
        "layer": layer,
        "receipt_id": receipt_id,
        "valid": not issues,
    }


def require_local_effect(effect: str) -> None:
    """Reject effects outside B7's local qualification boundary."""
    if effect not in _SAFE_EFFECTS:
        raise QualificationError(f"effect is not allowed during qualification: {effect}")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must contain a JSON object")
    return value


def _safe_relative_path(raw: object, label: str) -> Path:
    value = str(raw).strip()
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise QualificationError(f"{label} must be a canonical repository-relative path")
    normalized = pure.as_posix()
    if normalized != value or normalized in {".", ""}:
        raise QualificationError(f"{label} is not canonical: {value!r}")
    return Path(*pure.parts)


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _resolve_installed_root(repo_root: Path, installed_root: Path | None) -> Path:
    root = Path(repo_root).resolve()
    if installed_root is None:
        return root
    candidate = Path(installed_root).absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if current.is_symlink():
            raise QualificationError(
                "installed root must not be a symlink or contain symlink components"
            )
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise QualificationError("installed root is not a directory")
    if (
        resolved == root
        or resolved.is_relative_to(root)
        or root.is_relative_to(resolved)
    ):
        raise QualificationError(
            "explicit installed root must be disjoint from runtime root"
        )
    return resolved


def _require_write_outside_root(path: Path, root: Path, label: str) -> None:
    destination = Path(path).resolve()
    if destination == root or destination.is_relative_to(root):
        raise QualificationError(f"{label} must not write inside installed root")


def _path_files(root: Path, relative: Path, scope: str) -> list[Path]:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise QualificationError(
                f"manifest path contains a symlink component: {relative.as_posix()}"
            )
    target = root / relative
    if scope == "file":
        if not target.is_file():
            raise QualificationError(f"manifest file is missing: {relative.as_posix()}")
        return [relative]
    if scope != "tree":
        raise QualificationError(f"unknown manifest scope: {scope}")
    if not target.is_dir():
        raise QualificationError(f"manifest tree is missing: {relative.as_posix()}")
    files = []
    for path in target.rglob("*"):
        if path.is_symlink():
            raise QualificationError(
                f"manifest tree contains a symlink: {path.relative_to(root).as_posix()}"
            )
        if path.is_file() and "__pycache__" not in path.parts:
            files.append(path.relative_to(root))
    return sorted(files)


def _path_set_digest(root: Path, entries: Sequence[Mapping[str, object]]) -> tuple[str, str]:
    leaves: dict[str, str] = {}
    for entry in entries:
        relative = _safe_relative_path(entry.get("path"), "manifest entry path")
        for leaf in _path_files(root, relative, str(entry.get("scope"))):
            leaves[leaf.as_posix()] = _file_digest(root / leaf)
    return digest_json(sorted(leaves)), digest_json(leaves)


def _materialization_metadata_entries(
    manifest: Mapping[str, object],
) -> list[Mapping[str, object]]:
    entries = [
        entry
        for entry in manifest["entries"]
        if Path(str(entry["path"])) == MATERIALIZATION_METADATA_PATH
    ]
    for entry in entries:
        if entry["owner"] != "official" or entry["scope"] != "file":
            raise QualificationError(
                "materialization metadata must be an official file: "
                f"{entry['path']}"
            )
    return entries


def validate_materialization_metadata(
    root: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Validate target-local updater metadata without binding target-specific bytes."""
    evidence: dict[str, object] = {}
    for entry in _materialization_metadata_entries(manifest):
        relative = _safe_relative_path(entry["path"], "materialization metadata path")
        _path_files(root, relative, "file")
        payload = _read_json_object(root / relative, "materialization metadata")
        hashes = payload.get("hashes")
        if (
            set(payload) != {"__version", "hashes"}
            or payload.get("__version") != 2
            or not isinstance(hashes, dict)
        ):
            raise QualificationError(
                f"materialization metadata schema is invalid: {relative.as_posix()}"
            )
        for raw_path, raw_digest in hashes.items():
            tracked = _safe_relative_path(raw_path, "template hash path")
            if (
                tracked in IGNORED_OFFICIAL_PATHS
                or not isinstance(raw_digest, str)
                or not _DIGEST_RE.fullmatch(str(raw_digest))
            ):
                raise QualificationError(
                    f"materialization metadata entry is invalid: {tracked.as_posix()}"
                )
        evidence[relative.as_posix()] = {
            "format": "trellis-template-hashes-v2",
            "schema_version": 2,
        }
    return evidence


def _manifest_owner(
    entries: Sequence[Mapping[str, object]],
    relative: Path,
) -> str | None:
    matches = [
        str(entry.get("owner"))
        for entry in entries
        if _safe_relative_path(entry.get("path"), "manifest entry path") == relative
        or (
            entry.get("scope") == "tree"
            and _safe_relative_path(entry.get("path"), "manifest entry path")
            in relative.parents
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_deployment_policy(manifest: Mapping[str, object]) -> None:
    policy = manifest.get("deployment")
    if not isinstance(policy, Mapping) or policy.get("policy_schema_version") != 1:
        raise QualificationError("overlay manifest requires deployment policy v1")
    if policy.get("source_id") != "trellis-harness":
        raise QualificationError("deployment policy has an unknown source")
    if policy.get("targets") != list(DEPLOYMENT_TARGETS):
        raise QualificationError("deployment policy target enrollment differs")
    if policy.get("target_only_owner") != "project":
        raise QualificationError("target-only paths must be project owned")

    entries = manifest["entries"]
    projection = policy.get("adoption_projection")
    expected_projection = {
        "delete": "never",
        "owner": "project",
        "path": ".trellis/deploy/adoption.json",
        "source_disposition": "preserve",
        "write": "deployer-replace-only",
    }
    if projection != expected_projection or _manifest_owner(
        entries, Path(expected_projection["path"])
    ) != "project":
        raise QualificationError("deployment adoption projection policy differs")

    preserve = policy.get("preserve")
    if not isinstance(preserve, list) or not preserve:
        raise QualificationError("deployment preservation set is missing")
    for raw in preserve:
        relative = _safe_relative_path(raw, "preservation path")
        if _manifest_owner(entries, relative) not in {"project", "generated"}:
            raise QualificationError(
                f"preservation path lacks local ownership: {relative.as_posix()}"
            )

    deletions = policy.get("intentional_deletions")
    if not isinstance(deletions, list):
        raise QualificationError("intentional deletions must be a list")
    seen: set[Path] = set()
    for raw in deletions:
        if not isinstance(raw, Mapping):
            raise QualificationError("deletion disposition must be an object")
        relative = _safe_relative_path(raw.get("path"), "deletion path")
        owner = _manifest_owner(entries, relative)
        if (
            relative in seen
            or owner not in {"official", "overlay"}
            or raw.get("owner") != owner
            or raw.get("scope") != "file"
            or not _DIGEST_RE.fullmatch(str(raw.get("preimage_sha256", "")))
        ):
            raise QualificationError(
                f"invalid intentional deletion: {relative.as_posix()}"
            )
        seen.add(relative)


def load_overlay_manifest(path: Path) -> dict[str, Any]:
    """Load and structurally validate the four-owner overlay manifest."""
    manifest = _read_json_object(path, "overlay manifest")
    schema_version = manifest.get("manifest_schema_version")
    if schema_version not in {1, OVERLAY_MANIFEST_SCHEMA_VERSION}:
        raise QualificationError("unknown overlay manifest schema version")
    if not str(manifest.get("overlay_version", "")).strip():
        raise QualificationError("overlay manifest requires overlay_version")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise QualificationError("overlay manifest requires entries")

    resolved: list[tuple[Path, str, str]] = []
    owners_seen: set[str] = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise QualificationError(f"manifest entry {index} must be an object")
        path_value = _safe_relative_path(raw.get("path"), f"manifest entry {index} path")
        scope = str(raw.get("scope", ""))
        owner = str(raw.get("owner", ""))
        if scope not in {"file", "tree"}:
            raise QualificationError(f"manifest entry {index} has unknown scope")
        if owner not in OWNER_CLASSES:
            raise QualificationError(f"manifest entry {index} has unknown owner")
        owners_seen.add(owner)
        for other_path, other_scope, _ in resolved:
            if path_value == other_path or (
                other_scope == "tree" and other_path in path_value.parents
            ) or (scope == "tree" and path_value in other_path.parents):
                raise QualificationError(
                    f"manifest ownership overlaps: {other_path.as_posix()} and "
                    f"{path_value.as_posix()}"
                )
        resolved.append((path_value, scope, owner))
    if owners_seen != OWNER_CLASSES:
        missing = sorted(OWNER_CLASSES - owners_seen)
        raise QualificationError(f"manifest is missing ownership classes: {missing}")
    if schema_version == OVERLAY_MANIFEST_SCHEMA_VERSION:
        _validate_deployment_policy(manifest)
    return manifest


def check_overlay_conformance(
    repo_root: Path,
    manifest_path: Path | None = None,
    *,
    installed_root: Path | None = None,
    official_root: Path | None = None,
    overlay_root: Path | None = None,
) -> dict[str, Any]:
    """Read-only official-first overlay conformance check."""
    require_local_effect("local_read")
    root = Path(repo_root).resolve()
    if installed_root is not None and official_root is not None:
        raise QualificationError("installed_root and official_root are mutually exclusive")
    installed = _resolve_installed_root(root, installed_root)
    manifest_file = Path(manifest_path or root / MANIFEST_PATH).resolve()
    manifest = load_overlay_manifest(manifest_file)
    entries = manifest["entries"]
    issues: list[str] = []
    owner_evidence: dict[str, dict[str, str]] = {}
    metadata_root = installed if installed_root is not None else root
    try:
        materialization_metadata = validate_materialization_metadata(
            metadata_root,
            manifest,
        )
    except QualificationError as exc:
        materialization_metadata = {}
        issues.append(str(exc))

    for owner in sorted(OWNER_CLASSES):
        selected = [entry for entry in entries if entry["owner"] == owner]
        if owner in {"project", "generated"}:
            owner_evidence[owner] = {
                "path_set_digest": digest_json(selected),
                "payload_digest": "not-distributable",
            }
            continue
        if owner == "official":
            selected = [
                entry
                for entry in selected
                if Path(str(entry["path"])) != MATERIALIZATION_METADATA_PATH
            ]
        source_root = root
        compare_with_target = True
        if owner == "official" and installed_root is not None:
            source_root = installed
            compare_with_target = False
        elif owner == "official" and official_root is not None:
            source_root = Path(official_root).resolve()
        elif owner == "overlay" and overlay_root is not None:
            source_root = Path(overlay_root).resolve()
        try:
            path_digest, payload_digest = _path_set_digest(source_root, selected)
            owner_evidence[owner] = {
                "path_set_digest": path_digest,
                "payload_digest": payload_digest,
            }
            if (
                compare_with_target
                and source_root != root
                and owner in {"official", "overlay"}
            ):
                target_path_digest, target_payload_digest = _path_set_digest(root, selected)
                if target_path_digest != path_digest:
                    issues.append(f"{owner} path set differs from its pinned source")
                if target_payload_digest != payload_digest:
                    issues.append(f"{owner} payload differs from its pinned source")
        except QualificationError as exc:
            issues.append(str(exc))

    result = {
        "manifest_digest": _file_digest(manifest_file),
        "manifest_schema_version": manifest["manifest_schema_version"],
        "materialization_metadata": materialization_metadata,
        "overlay_version": manifest["overlay_version"],
        "owners": owner_evidence,
        "issues": issues,
        "status": "passed" if not issues else "failed",
    }
    result["evidence_digest"] = digest_json(result)
    return result


def _copy_manifest_entries(
    source_root: Path,
    target_root: Path,
    entries: Sequence[Mapping[str, object]],
) -> None:
    for entry in entries:
        relative = _safe_relative_path(entry.get("path"), "manifest entry path")
        for leaf in _path_files(source_root, relative, str(entry.get("scope"))):
            destination = target_root / leaf
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / leaf, destination)


def _assert_no_symlink_ancestors(root: Path, relative: Path, label: str) -> None:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise QualificationError(f"{label} contains a symlink: {relative.as_posix()}")


def _preflight_overlay_application(
    source_root: Path,
    target_root: Path,
    manifest: Mapping[str, object],
) -> list[Path]:
    """Return the complete overlay copy plan or fail before target mutation."""
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    plan: list[Path] = []
    for entry in manifest["entries"]:
        if entry["owner"] != "overlay":
            continue
        relative = _safe_relative_path(entry.get("path"), "manifest entry path")
        scope = str(entry.get("scope"))
        leaves = _path_files(source, relative, scope)
        leaf_set = set(leaves)
        target_entry = target / relative
        _assert_no_symlink_ancestors(target, relative, "overlay target")
        if scope == "tree" and target_entry.exists():
            if not target_entry.is_dir():
                raise QualificationError(
                    f"overlay target collides with a non-tree path: {relative.as_posix()}"
                )
            for existing in target_entry.rglob("*"):
                existing_relative = existing.relative_to(target)
                if existing.is_symlink():
                    raise QualificationError(
                        "overlay target contains a symlink: "
                        f"{existing_relative.as_posix()}"
                    )
                if (
                    existing.is_file()
                    and "__pycache__" not in existing.parts
                    and existing_relative not in leaf_set
                ):
                    raise QualificationError(
                        "overlay target contains an unmanaged path: "
                        f"{existing_relative.as_posix()}"
                    )
        for leaf in leaves:
            _assert_no_symlink_ancestors(source, leaf, "overlay source")
            _assert_no_symlink_ancestors(target, leaf, "overlay target")
            destination = target / leaf
            if destination.exists():
                if not destination.is_file():
                    raise QualificationError(
                        f"overlay target collides with a non-file path: {leaf.as_posix()}"
                    )
                if destination.read_bytes() != (source / leaf).read_bytes():
                    raise QualificationError(
                        f"overlay target collision differs from source: {leaf.as_posix()}"
                    )
            plan.append(leaf)
    return sorted(plan)


def _apply_overlay_payload(
    source_root: Path,
    target_root: Path,
    manifest: Mapping[str, object],
) -> dict[str, str]:
    plan = _preflight_overlay_application(source_root, target_root, manifest)
    for relative in plan:
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    payload = {path.as_posix(): _file_digest(target_root / path) for path in plan}
    return {
        "path_set_digest": digest_json(sorted(payload)),
        "payload_digest": digest_json(payload),
    }


def _run_fixture_git(repo: Path, *args: str) -> str:
    require_local_effect("local_process")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_AUTHOR_EMAIL": "loop-qualification@example.invalid",
            "GIT_AUTHOR_NAME": "Loop Qualification",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_EMAIL": "loop-qualification@example.invalid",
            "GIT_COMMITTER_NAME": "Loop Qualification",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise QualificationError(f"local Git fixture failed: {detail}")
    return completed.stdout.strip()


def run_clean_clone_overlay_conformance(
    repo_root: Path,
    manifest_path: Path | None = None,
    *,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Prove official update then overlay application in one local clean clone."""
    require_local_effect("local_process")
    root = Path(repo_root).resolve()
    installed = _resolve_installed_root(root, installed_root)
    manifest_file = Path(manifest_path or root / MANIFEST_PATH).resolve()
    manifest = load_overlay_manifest(manifest_file)
    metadata_entries = _materialization_metadata_entries(manifest)
    official_entries = [
        entry
        for entry in manifest["entries"]
        if entry["owner"] == "official" and entry not in metadata_entries
    ]
    issues: list[str] = []
    evidence: dict[str, Any] = {}

    try:
        with tempfile.TemporaryDirectory(prefix="loop-v1-overlay-") as tmp:
            fixture_root = Path(tmp)
            official = fixture_root / "official"
            target = fixture_root / "target"
            official.mkdir()
            _run_fixture_git(official, "init", "-q", "-b", "main")
            version = official / ".trellis/.version"
            version.parent.mkdir(parents=True)
            version.write_text("official-base\n", encoding="utf-8")
            _run_fixture_git(official, "add", "--all")
            _run_fixture_git(official, "commit", "-q", "-m", "official base")
            base_tree = _run_fixture_git(official, "rev-parse", "HEAD^{tree}")

            _run_fixture_git(fixture_root, "clone", "-q", str(official), str(target))
            preserved = {
                "generated": (Path("BOARD.md"), b"generated-local\n"),
                "project": (Path("README.md"), b"project-local\n"),
            }
            for relative, payload in preserved.values():
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            _copy_manifest_entries(installed, target, metadata_entries)

            _copy_manifest_entries(installed, official, official_entries)
            _run_fixture_git(official, "add", "--all")
            _run_fixture_git(official, "commit", "-q", "-m", "official update")
            updated_head = _run_fixture_git(official, "rev-parse", "HEAD")
            updated_tree = _run_fixture_git(official, "rev-parse", "HEAD^{tree}")
            _run_fixture_git(target, "fetch", "-q", "origin", "main")
            _run_fixture_git(target, "merge", "-q", "--ff-only", "origin/main")

            applied = _apply_overlay_payload(root, target, manifest)
            target_manifest = target / MANIFEST_PATH
            installed_check = check_overlay_conformance(
                target,
                target_manifest,
                official_root=official,
                overlay_root=root,
            )
            issues.extend(installed_check["issues"])
            preservation: dict[str, dict[str, object]] = {}
            for owner, (relative, payload) in preserved.items():
                unchanged = (target / relative).read_bytes() == payload
                if not unchanged:
                    issues.append(f"{owner} bytes changed during overlay application")
                preservation[owner] = {
                    "path": relative.as_posix(),
                    "preserved": unchanged,
                    "payload_digest": sha256(payload).hexdigest(),
                }
            target_head = _run_fixture_git(target, "rev-parse", "HEAD")
            if target_head != updated_head:
                issues.append("clean clone did not advance to the official update")
            evidence = {
                "application": applied,
                "official_update": {
                    "base_tree": base_tree,
                    "fast_forwarded": target_head == updated_head,
                    "updated_tree": updated_tree,
                },
                "owners": installed_check["owners"],
                "preservation": preservation,
            }
    except QualificationError as exc:
        issues.append(str(exc))

    result = {
        **evidence,
        "issues": issues,
        "manifest_digest": _file_digest(manifest_file),
        "manifest_schema_version": manifest["manifest_schema_version"],
        "overlay_version": manifest["overlay_version"],
        "status": "passed" if not issues else "failed",
    }
    result["evidence_digest"] = digest_json(result)
    return result


def run_oldest_downstream_candidate_conformance(
    repo_root: Path,
    manifest_path: Path | None = None,
    *,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Materialize and smoke-check the pinned oldest downstream candidate."""
    require_local_effect("local_process")
    from downstream_deployer import planning as deployer

    root = Path(repo_root).resolve()
    installed = _resolve_installed_root(root, installed_root)
    fixture_root = root / OLDEST_DOWNSTREAM_FIXTURE_PATH
    fixture = _read_json_object(fixture_root / "fixture.json", "downstream fixture")
    historical_helper = fixture.get("historical_helper")
    if (
        fixture.get("fixture_schema_version") != 1
        or fixture.get("target_id") != DEPLOYMENT_TARGETS[0]
        or fixture.get("official_release") != "0.6.5"
        or not isinstance(historical_helper, Mapping)
    ):
        raise QualificationError("oldest downstream fixture identity differs")
    helper_relative = _safe_relative_path(
        historical_helper.get("path"),
        "historical helper path",
    )
    helper_source = fixture_root / "task_utils.py"
    helper_digest = _file_digest(helper_source)
    if (
        helper_relative != Path(".trellis/scripts/common/task_utils.py")
        or historical_helper.get("sha256") != helper_digest
    ):
        raise QualificationError("oldest downstream helper fixture differs")

    manifest_file = Path(manifest_path or root / MANIFEST_PATH).resolve()
    manifest = load_overlay_manifest(manifest_file)
    issues: list[str] = []
    evidence: dict[str, object] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="loop-v1-downstream-") as tmp:
            target = Path(tmp) / str(fixture["target_id"])
            candidate = Path(tmp) / "candidate"
            helper_target = target / helper_relative
            helper_target.parent.mkdir(parents=True)
            helper_target.write_bytes(helper_source.read_bytes())
            version = target / ".trellis/.version"
            version.parent.mkdir(parents=True, exist_ok=True)
            version.write_text(str(fixture["official_release"]), encoding="utf-8")
            (target / MATERIALIZATION_METADATA_PATH).write_text(
                '{"__version":2,"hashes":{}}\n',
                encoding="utf-8",
            )
            baseline_materialization = deployer._materialize_official(target)
            for backup in (target / ".trellis").glob(".backup-*"):
                shutil.rmtree(backup)
            helper_target.write_bytes(helper_source.read_bytes())
            version.write_text(str(fixture["official_release"]), encoding="utf-8")
            target_snapshot = deployer._snapshot(target)
            deployer._copy_target(target, candidate)
            materialization = deployer._materialize_official(candidate)
            restored = deployer._restore_preserved_paths(
                target,
                candidate,
                target_snapshot,
                manifest,
            )
            metadata = validate_materialization_metadata(candidate, manifest)
            official = deployer._owner_payload(candidate, manifest, "official")
            expected_official = deployer._owner_payload(installed, manifest, "official")
            if official != expected_official:
                raise QualificationError(
                    "oldest downstream official materialization differs"
                )
            overlay = deployer._apply_overlay(root, candidate, manifest)
            checks = deployer._run_checks(
                candidate,
                OLDEST_DOWNSTREAM_SMOKE_COMMANDS,
                label="oldest-downstream-candidate",
                failure=lambda code, detail: QualificationError(f"{code}:{detail}"),
            )
            evidence = {
                "candidate_payload_digest": digest_json(deployer._snapshot(candidate)),
                "fixture": {
                    "helper_sha256": helper_digest,
                    "official_release": fixture["official_release"],
                    "target_id": fixture["target_id"],
                },
                "materialization": {
                    "command": materialization["command"],
                    "returncode": materialization["returncode"],
                },
                "baseline_materialization": {
                    "command": baseline_materialization["command"],
                    "returncode": baseline_materialization["returncode"],
                },
                "materialization_metadata": metadata,
                "overlay": overlay,
                "restored_paths_digest": digest_json(restored),
                "smoke_checks": checks,
            }
    except (QualificationError, deployer.PlanError) as exc:
        issues.append(str(exc))

    result = {
        **evidence,
        "fixture_digest": _file_digest(fixture_root / "fixture.json"),
        "issues": issues,
        "manifest_digest": _file_digest(manifest_file),
        "status": "passed" if not issues else "failed",
    }
    result["evidence_digest"] = digest_json(result)
    return result


def _normalize_test_output(value: str) -> str:
    value = re.sub(r" in [0-9.]+s", " in <duration>s", value)
    return value.replace("\\", "/")


def _test_source_path(repo_root: Path, test_id: str) -> Path:
    module = test_id.split(".", 1)[0]
    return repo_root / ".trellis/scripts/tests" / f"{module}.py"


def _run_test_target(
    repo_root: Path,
    test_id: str,
    *,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    require_local_effect("local_process")
    test_dir = repo_root / ".trellis/scripts/tests"
    env = os.environ.copy()
    python_path = str(repo_root / ".trellis/scripts")
    env["PYTHONPATH"] = python_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    env["GIT_CONFIG_VALUE_0"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("CI", None)
    if installed_root is None:
        env.pop(_INSTALLED_ROOT_ENV, None)
    else:
        env[_INSTALLED_ROOT_ENV] = str(installed_root)
    command = [sys.executable, "-m", "unittest", "-q", test_id]
    try:
        completed = subprocess.run(
            command,
            cwd=test_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        returncode = completed.returncode
        stdout = _normalize_test_output(completed.stdout)
        stderr = _normalize_test_output(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = _normalize_test_output(str(exc.stdout or ""))
        stderr = _normalize_test_output(str(exc.stderr or "") + "\nqualification timeout")
    source = _test_source_path(repo_root, test_id)
    return {
        "command": command,
        "returncode": returncode,
        "source_digest": _file_digest(source),
        "stderr_digest": sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_digest": sha256(stdout.encode("utf-8")).hexdigest(),
        "test_id": test_id,
    }


def _stable_test_run(raw: object) -> dict[str, object]:
    run = dict(raw) if isinstance(raw, Mapping) else {}
    run.pop("duration_ms", None)
    return run


def _stable_scenario(scenario: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": scenario.get("id"),
        "negative": _stable_test_run(scenario.get("negative")),
        "positive": _stable_test_run(scenario.get("positive")),
        "status": scenario.get("status"),
    }


def _qualification_test_files(repo_root: Path) -> list[Path]:
    files = set(repo_root.glob(TEST_PATH_GLOB))
    files.update(repo_root.glob(DEPLOYER_TEST_PATH_GLOB))
    files.update(
        source
        for test_id in DEPLOYER_TEST_IDS
        if (source := _test_source_path(repo_root, test_id)).is_file()
    )
    return sorted(files)


def run_qualification_matrix(
    repo_root: Path,
    *,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Run Q01-Q12 as isolated positive and negative subprocesses."""
    root = Path(repo_root).resolve()
    root_conformance = check_overlay_conformance(
        root,
        installed_root=installed_root,
    )
    if root_conformance["status"] != "passed":
        raise QualificationError(
            f"qualification root conformance failed: {root_conformance['issues']}"
        )
    installed = (
        _resolve_installed_root(root, installed_root)
        if installed_root is not None
        else None
    )
    scenarios: list[dict[str, Any]] = []
    for qualification_id in QUALIFICATION_IDS:
        positive_id, negative_id = _QUALIFICATION_MATRIX[qualification_id]
        positive = _run_test_target(root, positive_id, installed_root=installed)
        negative = _run_test_target(root, negative_id, installed_root=installed)
        passed = positive["returncode"] == 0 and negative["returncode"] == 0
        scenarios.append(
            {
                "id": qualification_id,
                "positive": positive,
                "negative": negative,
                "status": "passed" if passed else "failed",
            }
        )
    stable = [_stable_scenario(scenario) for scenario in scenarios]
    deployer_tests = [
        _run_test_target(root, test_id, installed_root=installed)
        for test_id in DEPLOYER_TEST_IDS
    ]
    stable_deployer = [_stable_test_run(result) for result in deployer_tests]
    candidate_compatibility = run_oldest_downstream_candidate_conformance(
        root,
        installed_root=installed,
    )
    return {
        "candidate_compatibility": candidate_compatibility,
        "deployer_digest": digest_json(stable_deployer),
        "deployer_tests": deployer_tests,
        "external_requirements": {
            "ci": False,
            "hooks": False,
            "network": False,
            "remote": False,
        },
        "qualification_digest": digest_json(stable),
        "scenarios": scenarios,
        "status": "passed"
        if all(scenario["status"] == "passed" for scenario in scenarios)
        and all(result["returncode"] == 0 for result in deployer_tests)
        and candidate_compatibility["status"] == "passed"
        else "failed",
    }


def _validated_scenarios(result: Mapping[str, object]) -> list[dict[str, object]]:
    raw = result.get("scenarios")
    if not isinstance(raw, list):
        raise QualificationError("qualification result requires scenarios")
    scenarios = [_stable_scenario(item) for item in raw if isinstance(item, Mapping)]
    ids = tuple(str(item.get("id")) for item in scenarios)
    if ids != QUALIFICATION_IDS:
        raise QualificationError("qualification result must contain ordered Q01-Q12")
    for scenario in scenarios:
        if scenario.get("status") != "passed":
            raise QualificationError(f"required qualification failed: {scenario.get('id')}")
        expected_tests = _QUALIFICATION_MATRIX[str(scenario.get("id"))]
        for index, polarity in enumerate(("positive", "negative")):
            evidence = scenario.get(polarity)
            if not isinstance(evidence, dict) or evidence.get("returncode") != 0:
                raise QualificationError(
                    f"{scenario.get('id')} lacks passing {polarity} evidence"
                )
            expected_test = expected_tests[index]
            command = evidence.get("command")
            if evidence.get("test_id") != expected_test or not isinstance(command, list) or command[-1:] != [expected_test]:
                raise QualificationError(
                    f"{scenario.get('id')} has unexpected {polarity} test identity"
                )
            for field in ("source_digest", "stderr_digest", "stdout_digest"):
                if not _DIGEST_RE.fullmatch(str(evidence.get(field, ""))):
                    raise QualificationError(
                        f"{scenario.get('id')} has invalid {polarity} {field}"
                    )
    expected = digest_json(scenarios)
    if result.get("qualification_digest") != expected:
        raise QualificationError("qualification result digest is inconsistent")
    requirements = result.get("external_requirements")
    expected_requirements = {"ci": False, "hooks": False, "network": False, "remote": False}
    if requirements != expected_requirements:
        raise QualificationError("qualification must not require remote, CI, hooks, or network")
    return scenarios


def _validated_deployer_tests(
    result: Mapping[str, object],
) -> list[dict[str, object]]:
    raw = result.get("deployer_tests")
    if not isinstance(raw, list):
        raise QualificationError("qualification result requires deployer tests")
    tests = [_stable_test_run(item) for item in raw]
    if tuple(str(item.get("test_id")) for item in tests) != DEPLOYER_TEST_IDS:
        raise QualificationError("qualification used unexpected deployer tests")
    for expected_test, evidence in zip(DEPLOYER_TEST_IDS, tests, strict=True):
        command = evidence.get("command")
        if (
            evidence.get("returncode") != 0
            or not isinstance(command, list)
            or command[-1:] != [expected_test]
        ):
            raise QualificationError(
                f"deployer test lacks passing exact evidence: {expected_test}"
            )
        for field in ("source_digest", "stderr_digest", "stdout_digest"):
            if not _DIGEST_RE.fullmatch(str(evidence.get(field, ""))):
                raise QualificationError(
                    f"deployer test has invalid {field}: {expected_test}"
                )
    if result.get("deployer_digest") != digest_json(tests):
        raise QualificationError("deployer qualification digest is inconsistent")
    return tests


def _validated_candidate_compatibility(
    result: Mapping[str, object],
) -> dict[str, object]:
    raw = result.get("candidate_compatibility")
    if not isinstance(raw, Mapping):
        raise QualificationError(
            "qualification result requires candidate compatibility evidence"
        )
    evidence = dict(raw)
    digest = evidence.pop("evidence_digest", None)
    if (
        raw.get("status") != "passed"
        or raw.get("issues") != []
        or digest != digest_json(evidence)
    ):
        raise QualificationError("candidate compatibility evidence did not pass")
    fixture = raw.get("fixture")
    baseline_materialization = raw.get("baseline_materialization")
    materialization = raw.get("materialization")
    smoke_checks = raw.get("smoke_checks")
    if (
        fixture
        != {
            "helper_sha256": (
                "f5ef4af87ba3e11d8b19630c0c96d009de1811fc9be56c2027a9c96e21ed103e"
            ),
            "official_release": "0.6.5",
            "target_id": DEPLOYMENT_TARGETS[0],
        }
        or not isinstance(baseline_materialization, Mapping)
        or baseline_materialization.get("command")
        != ["trellis", "update", "--force", "--migrate"]
        or baseline_materialization.get("returncode") != 0
        or not isinstance(materialization, Mapping)
        or materialization.get("command")
        != ["trellis", "update", "--force", "--migrate"]
        or materialization.get("returncode") != 0
        or not isinstance(smoke_checks, list)
        or len(smoke_checks) != len(OLDEST_DOWNSTREAM_SMOKE_COMMANDS)
        or any(
            not isinstance(smoke, Mapping)
            or smoke.get("index") != index
            or smoke.get("returncode") != 0
            or smoke.get("command_digest") != digest_json(list(command))
            for index, (smoke, command) in enumerate(
                zip(smoke_checks, OLDEST_DOWNSTREAM_SMOKE_COMMANDS)
            )
        )
    ):
        raise QualificationError("candidate compatibility evidence differs")
    for field in (
        "candidate_payload_digest",
        "fixture_digest",
        "manifest_digest",
        "restored_paths_digest",
    ):
        if not _DIGEST_RE.fullmatch(str(raw.get(field, ""))):
            raise QualificationError(
                f"candidate compatibility has invalid {field}"
            )
    return dict(raw)


def _run_git(repo_root: Path, *args: str) -> str:
    require_local_effect("git_read")
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise QualificationError(completed.stderr.strip() or "git identity read failed")
    return completed.stdout.strip()


def _expanded_files(repo_root: Path, roots: Sequence[Path]) -> list[Path]:
    files: set[Path] = set()
    for relative in roots:
        target = repo_root / relative
        if target.is_file():
            files.add(relative)
        elif target.is_dir():
            for path in target.rglob("*"):
                if path.is_symlink():
                    raise QualificationError(
                        f"runtime tree contains a symlink: "
                        f"{path.relative_to(repo_root).as_posix()}"
                    )
                if path.is_file() and "__pycache__" not in path.parts:
                    files.add(path.relative_to(repo_root))
        else:
            raise QualificationError(f"runtime path is missing: {relative.as_posix()}")
    return sorted(files)


def _file_map(repo_root: Path, files: Sequence[Path]) -> dict[str, str]:
    return {path.as_posix(): _file_digest(repo_root / path) for path in files}


def _tool_environment(
    repo_root: Path,
    *,
    installed_root: Path | None = None,
) -> dict[str, object]:
    installed = _resolve_installed_root(repo_root, installed_root)
    version_file = installed / ".trellis/.version"
    node_version = None
    if shutil.which("node"):
        node_version = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()
    return {
        "filesystem_case_sensitive": os.path.normcase("A") != os.path.normcase("a"),
        "git": _run_git(repo_root, "--version"),
        "node": node_version,
        "os_release": platform.release(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "trellis": version_file.read_text(encoding="utf-8").strip()
        if version_file.is_file()
        else None,
    }


def generate_conformance_receipt(
    repo_root: Path,
    qualification_result: Mapping[str, object],
    output_dir: Path,
    *,
    manifest_path: Path | None = None,
    runtime_commit: str = "HEAD",
    installed_root: Path | None = None,
) -> Path:
    """Generate a deterministic hash-addressed receipt for a committed runtime."""
    require_local_effect("qualification_receipt_write")
    root = Path(repo_root).resolve()
    destination_root = Path(output_dir).resolve()
    if installed_root is not None:
        installed = _resolve_installed_root(root, installed_root)
        _require_write_outside_root(
            destination_root,
            installed,
            "qualification receipt output",
        )
    scenarios = _validated_scenarios(qualification_result)
    deployer_tests = _validated_deployer_tests(qualification_result)
    candidate_compatibility = _validated_candidate_compatibility(
        qualification_result
    )
    evidence_rows = [
        (f"{scenario['id']} {polarity}", scenario[polarity])
        for scenario in scenarios
        for polarity in ("positive", "negative")
    ]
    evidence_rows.extend(
        (str(evidence["test_id"]), evidence) for evidence in deployer_tests
    )
    for label, evidence in evidence_rows:
        source = _test_source_path(root, str(evidence["test_id"]))
        if not source.is_file() or evidence["source_digest"] != _file_digest(source):
            raise QualificationError(f"{label} evidence does not bind its test source")
    commit = _run_git(root, "rev-parse", f"{runtime_commit}^{{commit}}")
    tree = _run_git(root, "rev-parse", f"{commit}^{{tree}}")
    runtime_files = _expanded_files(root, RUNTIME_PATHS)
    runtime_map = _file_map(root, runtime_files)
    for relative, digest in runtime_map.items():
        committed = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=root,
            capture_output=True,
        )
        if committed.returncode != 0 or sha256(committed.stdout).hexdigest() != digest:
            raise QualificationError(
                f"runtime file is not exact in qualifying commit: {relative}"
            )

    manifest_file = Path(manifest_path or root / MANIFEST_PATH).resolve()
    overlay = check_overlay_conformance(
        root,
        manifest_file,
        installed_root=installed_root,
    )
    if overlay["status"] != "passed":
        raise QualificationError(f"overlay conformance failed: {overlay['issues']}")
    overlay_application = run_clean_clone_overlay_conformance(
        root,
        manifest_file,
        installed_root=installed_root,
    )
    if overlay_application["status"] != "passed":
        raise QualificationError(
            f"overlay application conformance failed: {overlay_application['issues']}"
        )
    test_files = _qualification_test_files(root)
    if not test_files:
        raise QualificationError("qualification test sources are missing")
    test_map = {
        path.relative_to(root).as_posix(): _file_digest(path) for path in test_files
    }
    issues: list[dict[str, object]] = []
    payload: dict[str, object] = {
        "issues": issues,
        "ledger": {
            "migration_generation": LEDGER_MIGRATION_GENERATION,
            "schema_version": _ledger_schema_version(),
        },
        "local_only": {
            "ci_required": False,
            "hooks_required": False,
            "network_required": False,
            "remote_required": False,
        },
        "overlay": overlay,
        "overlay_application": overlay_application,
        "qualification": {
            "candidate_compatibility": candidate_compatibility,
            "deployer_digest": qualification_result["deployer_digest"],
            "deployer_tests": deployer_tests,
            "matrix_digest": qualification_result["qualification_digest"],
            "scenarios": scenarios,
            "test_source_digests": test_map,
        },
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "runtime": {
            "bundle_digest": digest_json(runtime_map),
            "files": runtime_map,
            "git_commit": commit,
            "git_tree": tree,
        },
        "status": "qualified",
        "tools": _tool_environment(root, installed_root=installed_root),
    }
    receipt_digest = digest_json(payload)
    receipt = {**payload, "receipt_digest": receipt_digest, "receipt_id": f"sha256:{receipt_digest}"}
    destination = destination_root / f"{receipt_digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if destination.exists() and destination.read_bytes() != encoded:
        raise QualificationError("hash-addressed receipt path contains different bytes")
    write_bytes_atomic(destination, encoded)
    return destination


def _ledger_schema_version() -> int:
    from .ledger import SCHEMA_VERSION

    return SCHEMA_VERSION


def verify_conformance_receipt(
    repo_root: Path,
    receipt_path: Path,
    *,
    expected_digest: str | None = None,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Verify every identity bound by one receipt against installed bytes."""
    root = Path(repo_root).resolve()
    try:
        _resolve_installed_root(root, installed_root)
    except QualificationError as exc:
        return {"issues": [str(exc)], "receipt_id": None, "valid": False}
    path = Path(receipt_path)
    if not path.is_absolute():
        path = root / path
    issues: list[str] = []
    try:
        receipt = _read_json_object(path, "qualification receipt")
    except QualificationError as exc:
        return {"issues": [str(exc)], "receipt_id": None, "valid": False}

    digest = str(receipt.get("receipt_digest", ""))
    receipt_id = str(receipt.get("receipt_id", "")) or None
    payload = dict(receipt)
    payload.pop("receipt_digest", None)
    payload.pop("receipt_id", None)
    if receipt.get("receipt_schema_version") != RECEIPT_SCHEMA_VERSION:
        issues.append("receipt schema version is unsupported")
    if not _DIGEST_RE.fullmatch(digest) or digest_json(payload) != digest:
        issues.append("receipt payload digest does not match")
    if path.stem != digest:
        issues.append("receipt path is not hash-addressed by its payload")
    if expected_digest and expected_digest not in {digest, receipt_id}:
        issues.append("configured receipt digest does not match")
    if receipt_id != f"sha256:{digest}":
        issues.append("receipt identity does not match its digest")
    if receipt.get("status") != "qualified":
        issues.append("receipt status is not qualified")
    raw_issues = receipt.get("issues")
    if not isinstance(raw_issues, list) or any(
        isinstance(issue, dict) and issue.get("required") is True for issue in raw_issues
    ):
        issues.append("receipt contains a required issue or invalid issue list")

    runtime = receipt.get("runtime")
    if not isinstance(runtime, dict):
        issues.append("receipt runtime identity is missing")
    else:
        files = runtime.get("files")
        try:
            current_files = _expanded_files(root, RUNTIME_PATHS)
            current_map = _file_map(root, current_files)
            if files != current_map or runtime.get("bundle_digest") != digest_json(current_map):
                issues.append("installed runtime bundle differs from receipt")
            commit = str(runtime.get("git_commit", ""))
            tree = _run_git(root, "rev-parse", f"{commit}^{{tree}}")
            if tree != runtime.get("git_tree"):
                issues.append("runtime Git commit/tree identity differs")
            for relative, file_digest in current_map.items():
                committed = subprocess.run(
                    ["git", "show", f"{commit}:{relative}"], cwd=root, capture_output=True
                )
                if committed.returncode != 0 or sha256(committed.stdout).hexdigest() != file_digest:
                    issues.append(f"runtime commit does not contain exact file: {relative}")
                    break
        except QualificationError as exc:
            issues.append(str(exc))

    ledger = receipt.get("ledger")
    if ledger != {
        "migration_generation": LEDGER_MIGRATION_GENERATION,
        "schema_version": _ledger_schema_version(),
    }:
        issues.append("ledger schema or migration generation differs")
    if receipt.get("tools") != _tool_environment(
        root,
        installed_root=installed_root,
    ):
        issues.append("tool environment differs from qualification")
    if receipt.get("local_only") != {
        "ci_required": False,
        "hooks_required": False,
        "network_required": False,
        "remote_required": False,
    }:
        issues.append("receipt requires a prohibited external dependency")

    qualification = receipt.get("qualification")
    if not isinstance(qualification, dict):
        issues.append("qualification evidence is missing")
    else:
        try:
            _validated_candidate_compatibility(
                {
                    "candidate_compatibility": qualification.get(
                        "candidate_compatibility"
                    )
                }
            )
        except QualificationError as exc:
            issues.append(str(exc))
        test_files = _qualification_test_files(root)
        current_tests = {
            test.relative_to(root).as_posix(): _file_digest(test) for test in test_files
        }
        if qualification.get("test_source_digests") != current_tests:
            issues.append("qualification test sources differ")
        scenarios = qualification.get("scenarios")
        if not isinstance(scenarios, list) or tuple(
            str(item.get("id")) for item in scenarios if isinstance(item, dict)
        ) != QUALIFICATION_IDS:
            issues.append("qualification Q01-Q12 evidence differs")
        elif qualification.get("matrix_digest") != digest_json(scenarios):
            issues.append("qualification matrix digest differs")
        try:
            _validated_deployer_tests(
                {
                    "deployer_digest": qualification.get("deployer_digest"),
                    "deployer_tests": qualification.get("deployer_tests"),
                }
            )
        except QualificationError as exc:
            issues.append(str(exc))

    overlay = receipt.get("overlay")
    current_overlay: dict[str, Any] | None = None
    if not isinstance(overlay, dict):
        issues.append("overlay evidence is missing")
    else:
        try:
            current_overlay = check_overlay_conformance(
                root,
                installed_root=installed_root,
            )
            issues.extend(current_overlay["issues"])
            if current_overlay != overlay:
                issues.append("overlay manifest or payload differs")
        except QualificationError as exc:
            issues.append(str(exc))
    overlay_application = receipt.get("overlay_application")
    if not isinstance(overlay_application, dict):
        issues.append("overlay application evidence is missing")
    elif current_overlay is not None:
        if overlay_application.get("status") != "passed":
            issues.append("overlay application evidence did not pass")
        if (
            overlay_application.get("manifest_digest")
            != current_overlay.get("manifest_digest")
            or overlay_application.get("overlay_version")
            != current_overlay.get("overlay_version")
            or overlay_application.get("owners") != current_overlay.get("owners")
        ):
            issues.append("overlay application evidence differs from installed payload")
        if overlay_application.get("application") != current_overlay["owners"]["overlay"]:
            issues.append("overlay application copy evidence differs from installed payload")
        official_update = overlay_application.get("official_update")
        if (
            not isinstance(official_update, dict)
            or official_update.get("fast_forwarded") is not True
            or official_update.get("base_tree") == official_update.get("updated_tree")
        ):
            issues.append("overlay application lacks a distinct official fast-forward")
        preservation = overlay_application.get("preservation")
        if (
            not isinstance(preservation, dict)
            or set(preservation) != {"generated", "project"}
            or any(
                not isinstance(item, dict) or item.get("preserved") is not True
                for item in preservation.values()
            )
        ):
            issues.append("overlay application did not preserve local owner bytes")
    return {"issues": issues, "receipt_id": receipt_id, "valid": not issues}


def _manifest_file(repo_root: Path, manifest_path: Path | None) -> Path:
    root = Path(repo_root).resolve()
    raw = Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
    candidate = raw if raw.is_absolute() else root / raw
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise QualificationError("artifact manifest must be source-owned") from exc
    if ".." in relative.parts:
        raise QualificationError("artifact manifest must be source-owned")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise QualificationError("artifact manifest cannot contain a symlink")
    resolved = candidate.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise QualificationError("artifact manifest must be a source-owned file")
    return resolved


def _portable_release_identity(
    repo_root: Path,
    manifest_file: Path,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    manifest = load_overlay_manifest(manifest_file)
    owners: dict[str, dict[str, str]] = {}
    for owner in ("official", "overlay"):
        entries = [entry for entry in manifest["entries"] if entry["owner"] == owner]
        if owner == "official":
            entries = [
                entry
                for entry in entries
                if Path(str(entry["path"])) != MATERIALIZATION_METADATA_PATH
            ]
        path_set_digest, payload_digest = _path_set_digest(root, entries)
        owners[owner] = {
            "path_set_digest": path_set_digest,
            "payload_digest": payload_digest,
        }
    official_core = {
        "payload": owners["official"],
        "release": manifest.get("official_release"),
    }
    overlay_core = {
        "payload": owners["overlay"],
        "version": manifest["overlay_version"],
    }
    release_core = {
        "migrations": {
            "generation": LEDGER_MIGRATION_GENERATION,
            "ledger_schema_version": _ledger_schema_version(),
        },
        "official": {**official_core, "id": _digest_id(official_core)},
        "overlay": {**overlay_core, "id": _digest_id(overlay_core)},
        "ownership_manifest": {
            "digest": _file_digest(manifest_file),
            "schema_version": manifest["manifest_schema_version"],
        },
    }
    return {**release_core, "release_id": _digest_id(release_core)}


def _assert_test_evidence_binds_source(
    repo_root: Path,
    evidence: Mapping[str, object],
) -> None:
    test_id = str(evidence.get("test_id", ""))
    source = _test_source_path(repo_root, test_id)
    if not source.is_file() or evidence.get("source_digest") != _file_digest(source):
        raise QualificationError(f"qualification evidence does not bind: {test_id}")


def _compact_test_evidence(evidence: Mapping[str, object]) -> dict[str, str]:
    return {
        "result": "passed",
        "result_digest": digest_json(_stable_test_run(evidence)),
        "source_digest": str(evidence["source_digest"]),
        "test_id": str(evidence["test_id"]),
    }


def _portable_qualification_evidence(
    repo_root: Path,
    qualification_result: Mapping[str, object],
    manifest_file: Path,
) -> dict[str, object]:
    scenarios = _validated_scenarios(qualification_result)
    deployer_tests = _validated_deployer_tests(qualification_result)
    candidate = _validated_candidate_compatibility(qualification_result)
    for scenario in scenarios:
        for polarity in ("positive", "negative"):
            evidence = scenario[polarity]
            assert isinstance(evidence, Mapping)
            _assert_test_evidence_binds_source(repo_root, evidence)
    for evidence in deployer_tests:
        _assert_test_evidence_binds_source(repo_root, evidence)
    fixture = repo_root / OLDEST_DOWNSTREAM_FIXTURE_PATH / "fixture.json"
    if (
        candidate["fixture_digest"] != _file_digest(fixture)
        or candidate["manifest_digest"] != _file_digest(manifest_file)
    ):
        raise QualificationError("candidate compatibility does not bind fixture or manifest")
    return {
        "candidate": {
            "evidence_digest": str(candidate["evidence_digest"]),
            "fixture_digest": str(candidate["fixture_digest"]),
            "manifest_digest": str(candidate["manifest_digest"]),
            "status": "passed",
        },
        "deployer": [_compact_test_evidence(evidence) for evidence in deployer_tests],
        "matrix": [
            {
                "id": str(scenario["id"]),
                "negative": _compact_test_evidence(scenario["negative"]),
                "positive": _compact_test_evidence(scenario["positive"]),
                "status": "passed",
            }
            for scenario in scenarios
        ],
    }


def _portable_qualification_issues(
    repo_root: Path,
    qualification: object,
    manifest_file: Path,
) -> list[str]:
    issues: list[str] = []
    if not isinstance(qualification, Mapping) or set(qualification) != {
        "candidate",
        "deployer",
        "matrix",
    }:
        return ["artifact qualification evidence is invalid"]
    candidate = qualification["candidate"]
    if (
        not isinstance(candidate, Mapping)
        or set(candidate)
        != {"evidence_digest", "fixture_digest", "manifest_digest", "status"}
        or candidate.get("status") != "passed"
        or any(
            not _DIGEST_RE.fullmatch(str(candidate.get(field, "")))
            for field in ("evidence_digest", "fixture_digest", "manifest_digest")
        )
    ):
        issues.append("artifact candidate qualification evidence is invalid")
    else:
        try:
            fixture = repo_root / OLDEST_DOWNSTREAM_FIXTURE_PATH / "fixture.json"
            if candidate["fixture_digest"] != _file_digest(fixture):
                issues.append("artifact candidate fixture differs")
            if candidate["manifest_digest"] != _file_digest(manifest_file):
                issues.append("artifact candidate manifest differs")
        except OSError as exc:
            issues.append(f"artifact candidate evidence is unreadable: {exc}")

    def check_test(
        evidence: object,
        expected_test: str,
        label: str,
    ) -> None:
        if (
            not isinstance(evidence, Mapping)
            or set(evidence) != {"result", "result_digest", "source_digest", "test_id"}
            or evidence.get("result") != "passed"
            or evidence.get("test_id") != expected_test
            or not _DIGEST_RE.fullmatch(str(evidence.get("result_digest", "")))
            or not _DIGEST_RE.fullmatch(str(evidence.get("source_digest", "")))
        ):
            issues.append(f"artifact {label} evidence is invalid")
            return
        source = _test_source_path(repo_root, expected_test)
        if not source.is_file() or evidence["source_digest"] != _file_digest(source):
            issues.append(f"artifact {label} source differs")

    matrix = qualification["matrix"]
    if not isinstance(matrix, list) or tuple(
        str(row.get("id")) for row in matrix if isinstance(row, Mapping)
    ) != QUALIFICATION_IDS:
        issues.append("artifact qualification matrix IDs differ")
    else:
        for row, qualification_id in zip(matrix, QUALIFICATION_IDS, strict=True):
            if (
                not isinstance(row, Mapping)
                or set(row) != {"id", "negative", "positive", "status"}
                or row.get("status") != "passed"
            ):
                issues.append(f"artifact qualification row is invalid: {qualification_id}")
                continue
            positive, negative = _QUALIFICATION_MATRIX[qualification_id]
            check_test(row["positive"], positive, f"{qualification_id} positive")
            check_test(row["negative"], negative, f"{qualification_id} negative")

    deployer = qualification["deployer"]
    if not isinstance(deployer, list) or len(deployer) != len(DEPLOYER_TEST_IDS):
        issues.append("artifact deployer qualification evidence is invalid")
    else:
        for evidence, test_id in zip(deployer, DEPLOYER_TEST_IDS, strict=True):
            check_test(evidence, test_id, "deployer")
    return issues


def _assert_output_outside_installed(
    repo_root: Path,
    output_dir: Path,
    installed_root: Path | None,
    label: str,
) -> None:
    if installed_root is not None:
        installed = _resolve_installed_root(repo_root, installed_root)
        _require_write_outside_root(Path(output_dir).resolve(), installed, label)


def generate_artifact_qualification_receipt(
    repo_root: Path,
    qualification_result: Mapping[str, object],
    output_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> Path:
    """Generate portable release qualification without local runtime claims."""
    root = Path(repo_root).resolve()
    manifest_file = _manifest_file(root, manifest_path)
    payload = {
        "layer": ARTIFACT_QUALIFICATION_LAYER,
        "minimum_tool_capabilities": MINIMUM_TOOL_CAPABILITIES,
        "qualification": _portable_qualification_evidence(
            root,
            qualification_result,
            manifest_file,
        ),
        "receipt_schema_version": LAYER_RECEIPT_SCHEMA_VERSION,
        "receipt_type": LAYER_RECEIPT_TYPE,
        "release": _portable_release_identity(
            root,
            manifest_file,
        ),
        "status": "qualified",
    }
    return _write_layer_receipt(output_dir, payload)


def _expected_digest_matches(
    expected_digest: str | None,
    receipt_id: str | None,
) -> bool:
    if expected_digest is None:
        return True
    if receipt_id is None:
        return False
    return expected_digest in {receipt_id, receipt_id.removeprefix("sha256:")}


def verify_artifact_qualification_receipt(
    repo_root: Path,
    receipt_path: Path,
    *,
    expected_digest: str | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Verify portable release qualification against current portable bytes."""
    root = Path(repo_root).resolve()
    receipt, receipt_id, issues = _read_layer_receipt(
        root,
        receipt_path,
        ARTIFACT_QUALIFICATION_LAYER,
    )
    if not _expected_digest_matches(expected_digest, receipt_id):
        issues.append("configured receipt digest does not match")
    if receipt is None or issues:
        return _layer_verification_result(
            ARTIFACT_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    expected_keys = {
        "layer",
        "minimum_tool_capabilities",
        "qualification",
        "receipt_digest",
        "receipt_id",
        "receipt_schema_version",
        "receipt_type",
        "release",
        "status",
    }
    if set(receipt) != expected_keys:
        issues.append("artifact receipt fields are unsupported")
        return _layer_verification_result(
            ARTIFACT_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    try:
        manifest_file = _manifest_file(root, manifest_path)
        if receipt.get("status") != "qualified":
            issues.append("artifact receipt status is not qualified")
        if receipt.get("minimum_tool_capabilities") != MINIMUM_TOOL_CAPABILITIES:
            issues.append("artifact minimum tool capability contract differs")
        if receipt.get("release") != _portable_release_identity(
            root,
            manifest_file,
        ):
            issues.append("artifact release identity differs")
        issues.extend(
            _portable_qualification_issues(
                root,
                receipt.get("qualification"),
                manifest_file,
            )
        )
    except (OSError, QualificationError) as exc:
        issues.append(str(exc))
    return _layer_verification_result(
        ARTIFACT_QUALIFICATION_LAYER,
        receipt_id,
        issues,
    )


def _runtime_identity(repo_root: Path) -> dict[str, object]:
    files = _expanded_files(repo_root, RUNTIME_PATHS)
    payload = _file_map(repo_root, files)
    return {"bundle_digest": digest_json(payload), "files": payload}


def _local_tool_capabilities(repo_root: Path, installed_root: Path) -> dict[str, str]:
    version_file = installed_root / ".trellis/.version"
    if not version_file.is_file():
        raise QualificationError("installed Trellis version is missing")
    tools = {
        "git": _run_git(repo_root, "--version"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "trellis": version_file.read_text(encoding="utf-8").strip(),
    }
    if (
        not tools["git"]
        or sys.version_info < (3, 11)
        or not tools["trellis"]
    ):
        raise QualificationError("local runtime does not satisfy minimum tool capability")
    return tools


def _local_runtime_bindings(
    repo_root: Path,
    artifact_receipt: Mapping[str, object],
    manifest_file: Path,
    *,
    installed_root: Path | None,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    installed = _resolve_installed_root(root, installed_root)
    conformance = check_overlay_conformance(
        root,
        manifest_file,
        installed_root=installed_root,
    )
    if conformance["status"] != "passed":
        raise QualificationError(
            f"local runtime conformance failed: {conformance['issues']}"
        )
    release = artifact_receipt.get("release")
    if not isinstance(release, Mapping) or not isinstance(release.get("release_id"), str):
        raise QualificationError("artifact receipt release identity is missing")
    if (
        not isinstance(release.get("official"), Mapping)
        or not isinstance(release.get("overlay"), Mapping)
        or release["official"].get("payload") != conformance["owners"]["official"]
        or release["overlay"].get("payload") != conformance["owners"]["overlay"]
    ):
        raise QualificationError("installed runtime payload differs from artifact release")
    receipt_id = artifact_receipt.get("receipt_id")
    if not isinstance(receipt_id, str):
        raise QualificationError("artifact receipt identity is missing")
    return {
        "artifact_receipt_id": receipt_id,
        "doctor": {
            "manifest_digest": conformance["manifest_digest"],
            "materialization_metadata": conformance["materialization_metadata"],
            "runtime_schema": {"ledger_schema_version": _ledger_schema_version()},
            "status": "passed",
        },
        "installed": {
            "official": conformance["owners"]["official"],
            "overlay": conformance["owners"]["overlay"],
        },
        "release_id": release["release_id"],
        "runtime": _runtime_identity(root),
        "tools": _local_tool_capabilities(root, installed),
    }


def generate_local_runtime_receipt(
    repo_root: Path,
    artifact_receipt_path: Path,
    output_dir: Path,
    *,
    manifest_path: Path | None = None,
    installed_root: Path | None = None,
) -> Path:
    """Bind one installed runtime to an exact verified artifact receipt."""
    root = Path(repo_root).resolve()
    _assert_output_outside_installed(
        root,
        output_dir,
        installed_root,
        "local runtime receipt output",
    )
    artifact_verification = verify_artifact_qualification_receipt(
        root,
        artifact_receipt_path,
        manifest_path=manifest_path,
    )
    if not artifact_verification["valid"]:
        raise QualificationError(
            f"artifact receipt is invalid: {artifact_verification['issues']}"
        )
    artifact, _receipt_id, issues = _read_layer_receipt(
        root,
        artifact_receipt_path,
        ARTIFACT_QUALIFICATION_LAYER,
    )
    if artifact is None or issues:
        raise QualificationError("artifact receipt became unreadable")
    manifest_file = _manifest_file(root, manifest_path)
    payload = {
        "layer": LOCAL_RUNTIME_QUALIFICATION_LAYER,
        "receipt_schema_version": LAYER_RECEIPT_SCHEMA_VERSION,
        "receipt_type": LAYER_RECEIPT_TYPE,
        "status": "compatible",
        **_local_runtime_bindings(
            root,
            artifact,
            manifest_file,
            installed_root=installed_root,
        ),
    }
    return _write_layer_receipt(output_dir, payload)


def verify_local_runtime_receipt(
    repo_root: Path,
    artifact_receipt_path: Path,
    receipt_path: Path,
    *,
    expected_digest: str | None = None,
    manifest_path: Path | None = None,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Verify an installed runtime only after its artifact dependency passes."""
    root = Path(repo_root).resolve()
    receipt, receipt_id, issues = _read_layer_receipt(
        root,
        receipt_path,
        LOCAL_RUNTIME_QUALIFICATION_LAYER,
    )
    if not _expected_digest_matches(expected_digest, receipt_id):
        issues.append("configured receipt digest does not match")
    artifact_verification = verify_artifact_qualification_receipt(
        root,
        artifact_receipt_path,
        manifest_path=manifest_path,
    )
    if not artifact_verification["valid"]:
        issues.append("artifact receipt dependency is invalid")
        issues.extend(str(issue) for issue in artifact_verification["issues"])
        return _layer_verification_result(
            LOCAL_RUNTIME_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    if receipt is None or issues:
        return _layer_verification_result(
            LOCAL_RUNTIME_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    expected_keys = {
        "artifact_receipt_id",
        "doctor",
        "installed",
        "layer",
        "receipt_digest",
        "receipt_id",
        "receipt_schema_version",
        "receipt_type",
        "release_id",
        "runtime",
        "status",
        "tools",
    }
    if set(receipt) != expected_keys:
        issues.append("local runtime receipt fields are unsupported")
        return _layer_verification_result(
            LOCAL_RUNTIME_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    artifact, _artifact_id, artifact_issues = _read_layer_receipt(
        root,
        artifact_receipt_path,
        ARTIFACT_QUALIFICATION_LAYER,
    )
    if artifact is None or artifact_issues:
        issues.append("artifact receipt became unreadable")
        return _layer_verification_result(
            LOCAL_RUNTIME_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    try:
        expected = _local_runtime_bindings(
            root,
            artifact,
            _manifest_file(root, manifest_path),
            installed_root=installed_root,
        )
        if receipt.get("status") != "compatible":
            issues.append("local runtime receipt status is not compatible")
        for key, value in expected.items():
            if receipt.get(key) != value:
                issues.append(f"local runtime {key} differs")
    except (OSError, QualificationError) as exc:
        issues.append(str(exc))
    return _layer_verification_result(
        LOCAL_RUNTIME_QUALIFICATION_LAYER,
        receipt_id,
        issues,
    )


def _target_receipt_bindings(
    source_root: Path,
    target_root: Path,
    artifact_receipt: Mapping[str, object],
    local_receipt: Mapping[str, object],
    plan: Mapping[str, object],
    final_receipt: Mapping[str, object],
    verification_commands: Sequence[Sequence[str]],
) -> dict[str, object]:
    from downstream_deployer.transaction import verify_transaction

    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    if not target.is_dir():
        raise QualificationError("target verification root is not a directory")
    try:
        verification = verify_transaction(
            source,
            target,
            plan,
            final_receipt,
            verification_commands=verification_commands,
        )
    except Exception as exc:
        raise QualificationError(
            f"target transaction verification failed: {type(exc).__name__}"
        ) from exc
    plan_target = plan.get("target")
    candidate = plan.get("candidate")
    receipt_id = final_receipt.get("receipt_id")
    if (
        not isinstance(plan_target, Mapping)
        or not isinstance(candidate, Mapping)
        or not isinstance(receipt_id, str)
        or verification.get("status") != "verified"
        or verification.get("plan_digest") != plan.get("plan_digest")
        or verification.get("target_id") != plan_target.get("id")
        or verification.get("receipt_id") != receipt_id
        or target.name != plan_target.get("id")
    ):
        raise QualificationError("target transaction result does not bind the exact plan")
    required_target = {
        "branch",
        "checks_digest",
        "git_policy_digest",
        "head",
        "id",
        "instructions",
        "preimage_digest",
        "tree",
        "verification_policy_digest",
    }
    if not required_target.issubset(plan_target):
        raise QualificationError("target plan identity is incomplete")
    if not isinstance(plan.get("plan_digest"), str) or not isinstance(
        candidate.get("payload_digest"), str
    ):
        raise QualificationError("target plan digest is incomplete")
    artifact_id = artifact_receipt.get("receipt_id")
    local_id = local_receipt.get("receipt_id")
    release = artifact_receipt.get("release")
    if (
        not isinstance(artifact_id, str)
        or not isinstance(local_id, str)
        or not isinstance(release, Mapping)
        or not isinstance(release.get("release_id"), str)
    ):
        raise QualificationError("target receipt dependencies are incomplete")
    policy = {
        "checks": plan_target["checks_digest"],
        "git": plan_target["git_policy_digest"],
        "instructions": digest_json(plan_target["instructions"]),
        "verification": plan_target["verification_policy_digest"],
    }
    return {
        "dependencies": {
            "artifact_receipt_id": artifact_id,
            "local_runtime_receipt_id": local_id,
            "release_id": release["release_id"],
        },
        "final_receipt": {
            "id": receipt_id,
            "payload_digest": digest_json(final_receipt),
        },
        "plan": {
            "candidate_payload_digest": candidate["payload_digest"],
            "digest": plan["plan_digest"],
        },
        "target": {
            "base_head": plan_target["head"],
            "base_tree": plan_target["tree"],
            "branch": plan_target["branch"],
            "id": plan_target["id"],
            "policy_digest": digest_json(policy),
            "preimage_digest": plan_target["preimage_digest"],
        },
        "verification": verification,
    }


def _assert_target_receipt_output(
    output_dir: Path,
    source_root: Path,
    target_root: Path,
) -> None:
    destination = Path(output_dir).resolve()
    for root in (Path(source_root).resolve(), Path(target_root).resolve()):
        if destination == root or destination.is_relative_to(root):
            raise QualificationError("target verification receipt output overlaps a protected root")


def generate_target_verification_receipt(
    source_root: Path,
    target_root: Path,
    artifact_receipt_path: Path,
    local_runtime_receipt_path: Path,
    plan: Mapping[str, object],
    final_receipt: Mapping[str, object],
    verification_commands: Sequence[Sequence[str]],
    output_dir: Path,
    *,
    manifest_path: Path | None = None,
    installed_root: Path | None = None,
) -> Path:
    """Record one exact, already-verified target observation."""
    source = Path(source_root).resolve()
    _assert_target_receipt_output(output_dir, source, target_root)
    artifact_verification = verify_artifact_qualification_receipt(
        source,
        artifact_receipt_path,
        manifest_path=manifest_path,
    )
    if not artifact_verification["valid"]:
        raise QualificationError("artifact receipt dependency is invalid")
    local_verification = verify_local_runtime_receipt(
        source,
        artifact_receipt_path,
        local_runtime_receipt_path,
        manifest_path=manifest_path,
        installed_root=installed_root,
    )
    if not local_verification["valid"]:
        raise QualificationError("local runtime receipt dependency is invalid")
    artifact, _artifact_id, artifact_issues = _read_layer_receipt(
        source,
        artifact_receipt_path,
        ARTIFACT_QUALIFICATION_LAYER,
    )
    local, _local_id, local_issues = _read_layer_receipt(
        source,
        local_runtime_receipt_path,
        LOCAL_RUNTIME_QUALIFICATION_LAYER,
    )
    if artifact is None or local is None or artifact_issues or local_issues:
        raise QualificationError("layer receipt became unreadable")
    payload = {
        "layer": TARGET_VERIFICATION_QUALIFICATION_LAYER,
        "receipt_schema_version": LAYER_RECEIPT_SCHEMA_VERSION,
        "receipt_type": LAYER_RECEIPT_TYPE,
        "status": "verified",
        **_target_receipt_bindings(
            source,
            target_root,
            artifact,
            local,
            plan,
            final_receipt,
            verification_commands,
        ),
    }
    return _write_layer_receipt(output_dir, payload)


def verify_target_verification_receipt(
    source_root: Path,
    target_root: Path,
    artifact_receipt_path: Path,
    local_runtime_receipt_path: Path,
    receipt_path: Path,
    plan: Mapping[str, object],
    final_receipt: Mapping[str, object],
    verification_commands: Sequence[Sequence[str]],
    *,
    expected_digest: str | None = None,
    manifest_path: Path | None = None,
    installed_root: Path | None = None,
) -> dict[str, Any]:
    """Verify one target receipt after artifact and local dependencies pass."""
    source = Path(source_root).resolve()
    receipt, receipt_id, issues = _read_layer_receipt(
        source,
        receipt_path,
        TARGET_VERIFICATION_QUALIFICATION_LAYER,
    )
    if not _expected_digest_matches(expected_digest, receipt_id):
        issues.append("configured receipt digest does not match")
    artifact_verification = verify_artifact_qualification_receipt(
        source,
        artifact_receipt_path,
        manifest_path=manifest_path,
    )
    if not artifact_verification["valid"]:
        issues.append("artifact receipt dependency is invalid")
        return _layer_verification_result(
            TARGET_VERIFICATION_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    local_verification = verify_local_runtime_receipt(
        source,
        artifact_receipt_path,
        local_runtime_receipt_path,
        manifest_path=manifest_path,
        installed_root=installed_root,
    )
    if not local_verification["valid"]:
        issues.append("local runtime receipt dependency is invalid")
        return _layer_verification_result(
            TARGET_VERIFICATION_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    if receipt is None or issues:
        return _layer_verification_result(
            TARGET_VERIFICATION_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    expected_keys = {
        "dependencies",
        "final_receipt",
        "layer",
        "plan",
        "receipt_digest",
        "receipt_id",
        "receipt_schema_version",
        "receipt_type",
        "status",
        "target",
        "verification",
    }
    if set(receipt) != expected_keys:
        issues.append("target verification receipt fields are unsupported")
        return _layer_verification_result(
            TARGET_VERIFICATION_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    artifact, _artifact_id, artifact_issues = _read_layer_receipt(
        source,
        artifact_receipt_path,
        ARTIFACT_QUALIFICATION_LAYER,
    )
    local, _local_id, local_issues = _read_layer_receipt(
        source,
        local_runtime_receipt_path,
        LOCAL_RUNTIME_QUALIFICATION_LAYER,
    )
    if artifact is None or local is None or artifact_issues or local_issues:
        issues.append("layer receipt became unreadable")
        return _layer_verification_result(
            TARGET_VERIFICATION_QUALIFICATION_LAYER,
            receipt_id,
            issues,
        )
    try:
        expected = _target_receipt_bindings(
            source,
            target_root,
            artifact,
            local,
            plan,
            final_receipt,
            verification_commands,
        )
        if receipt.get("status") != "verified":
            issues.append("target verification receipt status is not verified")
        for key, value in expected.items():
            if receipt.get(key) != value:
                issues.append(f"target verification {key} differs")
    except QualificationError as exc:
        issues.append(str(exc))
    return _layer_verification_result(
        TARGET_VERIFICATION_QUALIFICATION_LAYER,
        receipt_id,
        issues,
    )


def read_qualification_receipt(receipt_path: Path) -> dict[str, Any]:
    """Describe one legacy or layered receipt without inferring new evidence."""
    path = Path(receipt_path)
    try:
        receipt = _read_json_object(path, "qualification receipt")
    except QualificationError as exc:
        return {"issues": [str(exc)], "kind": "unknown", "readable": False}
    _payload, receipt_id, issues = _hash_addressed_identity(receipt, path)
    if receipt.get("receipt_type") == LAYER_RECEIPT_TYPE:
        layer = receipt.get("layer")
        if layer not in QUALIFICATION_LAYERS:
            issues.append("qualification layer is unknown")
        if receipt.get("receipt_schema_version") != LAYER_RECEIPT_SCHEMA_VERSION:
            issues.append("qualification layer schema version is unsupported")
        return {
            "issues": issues,
            "kind": layer if not issues else "unknown",
            "layers": {
                "artifact": "present" if layer == ARTIFACT_QUALIFICATION_LAYER else "absent",
                "local_runtime": "present"
                if layer == LOCAL_RUNTIME_QUALIFICATION_LAYER
                else "absent",
                "target": "present"
                if layer == TARGET_VERIFICATION_QUALIFICATION_LAYER
                else "absent",
            },
            "readable": not issues,
            "receipt_id": receipt_id,
        }
    if receipt.get("receipt_schema_version") == RECEIPT_SCHEMA_VERSION:
        return {
            "issues": issues,
            "kind": "legacy",
            "layers": {
                "artifact": "inseparable",
                "local_runtime": "inseparable",
                "target": "absent",
            },
            "readable": not issues,
            "receipt_id": receipt_id,
        }
    issues.append("qualification receipt schema is unknown")
    return {
        "issues": issues,
        "kind": "unknown",
        "readable": False,
        "receipt_id": receipt_id,
    }


def _configured_receipt_status(
    repo_root: Path,
    *,
    envelope_receipt: str | None = None,
    installed_root: Path | None = None,
    purpose: str = SOURCE_RELEASE_PURPOSE,
) -> QualificationStatus:
    """Verify configured receipt policy without consulting the rollback marker."""
    root = Path(repo_root).resolve()
    config = read_trellis_config(root)
    layers_config = config.get("qualification_layers")
    if layers_config is not None and not isinstance(layers_config, dict):
        return QualificationStatus(
            True,
            False,
            None,
            ("qualification layer configuration is invalid",),
        )
    loop_config = config.get("loop_v1")
    if not isinstance(loop_config, dict):
        return QualificationStatus(False, True, None, ())
    enabled = str(loop_config.get("admission_enabled", "false")).strip().lower() == "true"
    if not enabled:
        return QualificationStatus(True, False, None, ("Loop v1 admission is disabled",))
    expected_role = _PURPOSE_ROLES.get(purpose)
    if expected_role is None:
        return QualificationStatus(
            True,
            False,
            None,
            (f"unknown qualification purpose: {purpose}",),
        )
    runtime_mode = str(loop_config.get("runtime_mode", "")).strip()
    if runtime_mode not in {HARNESS_SOURCE_ROLE, DOWNSTREAM_PROJECT_ROLE}:
        return QualificationStatus(
            True,
            False,
            None,
            (
                "loop_v1.runtime_mode must be 'harness_source' or "
                "'downstream_project'",
            ),
        )
    if runtime_mode != expected_role:
        return QualificationStatus(
            True,
            False,
            None,
            (
                f"qualification purpose '{purpose}' requires "
                f"runtime_mode '{expected_role}'",
            ),
        )
    parent_default = str(loop_config.get("parent_default", "")).strip()
    if parent_default != "loop_v1":
        return QualificationStatus(
            True,
            False,
            None,
            ("enabled Loop v1 admission requires parent_default 'loop_v1'",),
        )
    admission_mode = str(
        (layers_config or {}).get("admission_mode", "legacy")
    ).strip()
    if purpose == INSTALLED_RUNTIME_PURPOSE:
        if admission_mode != INSTALLED_RUNTIME_PURPOSE:
            return QualificationStatus(
                True,
                False,
                None,
                (
                    "downstream installed runtime requires "
                    "qualification_layers.admission_mode 'installed_runtime'",
                ),
            )
        expected_layer_keys = {
            "admission_mode",
            "artifact_receipt",
            "artifact_receipt_digest",
            "local_runtime_receipt",
            "local_runtime_receipt_digest",
        }
        if not isinstance(layers_config, dict) or set(layers_config) != expected_layer_keys:
            return QualificationStatus(
                True,
                False,
                None,
                ("installed runtime qualification layer configuration is invalid",),
            )
        try:
            artifact_relative = _safe_relative_path(
                layers_config["artifact_receipt"],
                "artifact receipt path",
            )
            local_relative = _safe_relative_path(
                layers_config["local_runtime_receipt"],
                "local runtime receipt path",
            )
            artifact_expected = str(
                layers_config["artifact_receipt_digest"]
            ).strip()
            local_expected = str(
                layers_config["local_runtime_receipt_digest"]
            ).strip()
            artifact = verify_artifact_qualification_receipt(
                root,
                artifact_relative,
                expected_digest=artifact_expected,
            )
            local = verify_local_runtime_receipt(
                root,
                artifact_relative,
                local_relative,
                expected_digest=local_expected,
            )
        except (OSError, QualificationError) as exc:
            return QualificationStatus(True, False, None, (str(exc),))
        issues = [
            *(f"artifact: {issue}" for issue in artifact["issues"]),
            *(f"local runtime: {issue}" for issue in local["issues"]),
        ]
        receipt_id = local["receipt_id"]
        if envelope_receipt is not None and envelope_receipt != receipt_id:
            issues.append("parent start envelope names a different qualification receipt")
        return QualificationStatus(
            True,
            not issues,
            receipt_id if isinstance(receipt_id, str) else None,
            tuple(issues),
        )
    if admission_mode != "legacy":
        return QualificationStatus(
            True,
            False,
            None,
            ("qualification layer mode requires explicit migration/cutover",),
        )
    raw_path = str(loop_config.get("qualification_receipt", "")).strip()
    expected = str(loop_config.get("qualification_receipt_digest", "")).strip()
    if not raw_path or not expected:
        return QualificationStatus(True, False, None, ("active qualification receipt is not configured",))
    try:
        relative = _safe_relative_path(raw_path, "qualification receipt path")
    except QualificationError as exc:
        return QualificationStatus(True, False, None, (str(exc),))
    if purpose == SOURCE_DEVELOPMENT_PURPOSE:
        receipt = read_qualification_receipt(root / relative)
        issues = list(receipt.get("issues", ()))
        receipt_id = receipt.get("receipt_id")
        if receipt_id != f"sha256:{expected}":
            issues.append("active qualification receipt identity differs")
        if envelope_receipt is not None and envelope_receipt != receipt_id:
            issues.append("parent start envelope names a different qualification receipt")
        return QualificationStatus(
            True,
            not issues,
            receipt_id if isinstance(receipt_id, str) else None,
            tuple(issues),
        )
    try:
        verification = verify_conformance_receipt(
            root,
            relative,
            expected_digest=expected,
            installed_root=installed_root,
        )
    except (OSError, QualificationError) as exc:
        return QualificationStatus(True, False, None, (str(exc),))
    issues = list(verification["issues"])
    receipt_id = verification["receipt_id"]
    if envelope_receipt is not None and envelope_receipt != receipt_id:
        issues.append("parent start envelope names a different qualification receipt")
    return QualificationStatus(True, not issues, receipt_id, tuple(issues))


def configured_qualification(
    repo_root: Path,
    *,
    envelope_receipt: str | None = None,
    installed_root: Path | None = None,
    purpose: str = SOURCE_RELEASE_PURPOSE,
) -> QualificationStatus:
    """Resolve the fail-closed configured qualification policy."""
    root = Path(repo_root).resolve()
    if installed_root is None and _INSTALLED_ROOT_ENV in os.environ:
        installed_root = Path(os.environ[_INSTALLED_ROOT_ENV])
    if (root / DISABLE_MARKER).exists():
        return QualificationStatus(
            True,
            False,
            None,
            ("local admission disable marker is active",),
        )
    return _configured_receipt_status(
        root,
        envelope_receipt=envelope_receipt,
        installed_root=installed_root,
        purpose=purpose,
    )


def operation_qualification(
    repo_root: Path,
    *,
    envelope_receipt: str | None = None,
) -> QualificationStatus:
    """Select the runtime check for one repository-local Loop operation."""
    root = Path(repo_root).resolve()
    config = read_trellis_config(root)
    loop_config = config.get("loop_v1")
    runtime_mode = (
        str(loop_config.get("runtime_mode", "")).strip()
        if isinstance(loop_config, dict)
        else ""
    )
    purpose = (
        INSTALLED_RUNTIME_PURPOSE
        if runtime_mode == DOWNSTREAM_PROJECT_ROLE
        else SOURCE_DEVELOPMENT_PURPOSE
    )
    return configured_qualification(
        root,
        envelope_receipt=envelope_receipt,
        purpose=purpose,
    )


def capture_execution_binding(
    repo_root: Path, qualification: QualificationStatus
) -> dict[str, object]:
    """Snapshot the admission receipt verification for one newly admitted run."""
    if not qualification.enforced:
        return {"mode": "unenforced"}
    if not qualification.valid or qualification.receipt_id is None:
        raise QualificationError("cannot bind an invalid admission qualification")

    root = Path(repo_root).resolve()
    if (root / DISABLE_MARKER).exists():
        raise QualificationError("local admission disable marker is active")
    config = read_trellis_config(root)
    loop_config = config.get("loop_v1")
    if not isinstance(loop_config, dict):
        raise QualificationError("Loop v1 configuration changed during admission")
    if str(loop_config.get("runtime_mode", "")).strip() == DOWNSTREAM_PROJECT_ROLE:
        layers = config.get("qualification_layers")
        expected_keys = {
            "admission_mode",
            "artifact_receipt",
            "artifact_receipt_digest",
            "local_runtime_receipt",
            "local_runtime_receipt_digest",
        }
        if not isinstance(layers, dict) or set(layers) != expected_keys:
            raise QualificationError("installed runtime qualification changed during admission")
        artifact = _safe_relative_path(layers["artifact_receipt"], "artifact receipt path")
        local = _safe_relative_path(
            layers["local_runtime_receipt"], "local runtime receipt path"
        )
        artifact_digest = str(layers["artifact_receipt_digest"]).strip()
        local_digest = str(layers["local_runtime_receipt_digest"]).strip()
        if (
            not _DIGEST_RE.fullmatch(artifact_digest)
            or not _DIGEST_RE.fullmatch(local_digest)
            or qualification.receipt_id != f"sha256:{local_digest}"
        ):
            raise QualificationError("configured admission receipt changed during binding")
        return {
            "artifact_receipt": artifact.as_posix(),
            "artifact_receipt_digest": artifact_digest,
            "local_runtime_receipt": local.as_posix(),
            "local_runtime_receipt_digest": local_digest,
            "mode": INSTALLED_RUNTIME_PURPOSE,
            "receipt_id": qualification.receipt_id,
        }

    receipt = _safe_relative_path(
        loop_config.get("qualification_receipt"), "qualification receipt path"
    )
    digest = str(loop_config.get("qualification_receipt_digest", "")).strip()
    if (
        not _DIGEST_RE.fullmatch(digest)
        or qualification.receipt_id != f"sha256:{digest}"
    ):
        raise QualificationError("configured admission receipt changed during binding")
    return {
        "mode": SOURCE_DEVELOPMENT_PURPOSE,
        "receipt_digest": digest,
        "receipt_id": qualification.receipt_id,
        "receipt_path": receipt.as_posix(),
    }


def legacy_execution_binding(receipt_id: object) -> dict[str, object]:
    """Recover a historical execution binding from its immutable receipt ID."""
    value = str(receipt_id).strip()
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    if not value.startswith(prefix) or not _DIGEST_RE.fullmatch(digest):
        return {"mode": "invalid"}
    return {
        "mode": SOURCE_DEVELOPMENT_PURPOSE,
        "receipt_digest": digest,
        "receipt_id": value,
        "receipt_path": (
            Path(".trellis/spec/project/receipts/loop-v1") / f"{digest}.json"
        ).as_posix(),
    }


def execution_qualification(
    repo_root: Path, binding: object
) -> QualificationStatus:
    """Validate a run's persisted execution binding without reading admission config."""
    if not isinstance(binding, Mapping):
        return QualificationStatus(
            True, False, None, ("execution qualification binding is invalid",)
        )
    mode = binding.get("mode")
    if mode == "unenforced":
        if set(binding) == {"mode"}:
            return QualificationStatus(False, True, None, ())
        return QualificationStatus(
            True, False, None, ("unenforced execution binding is malformed",)
        )
    root = Path(repo_root).resolve()
    if mode == SOURCE_DEVELOPMENT_PURPOSE:
        expected_keys = {"mode", "receipt_digest", "receipt_id", "receipt_path"}
        if set(binding) != expected_keys:
            return QualificationStatus(
                True, False, None, ("source execution binding is malformed",)
            )
        try:
            digest = str(binding["receipt_digest"]).strip()
            receipt_id = str(binding["receipt_id"]).strip()
            receipt_path = _safe_relative_path(
                binding["receipt_path"], "execution receipt path"
            )
        except QualificationError as exc:
            return QualificationStatus(True, False, None, (str(exc),))
        if (
            not _DIGEST_RE.fullmatch(digest)
            or receipt_id != f"sha256:{digest}"
        ):
            return QualificationStatus(
                True, False, None, ("execution receipt identity is malformed",)
            )
        receipt = read_qualification_receipt(root / receipt_path)
        issues = list(receipt.get("issues", ()))
        if receipt.get("receipt_id") != receipt_id:
            issues.append("execution receipt identity differs")
        return QualificationStatus(True, not issues, receipt_id, tuple(issues))
    if mode == INSTALLED_RUNTIME_PURPOSE:
        expected_keys = {
            "artifact_receipt",
            "artifact_receipt_digest",
            "local_runtime_receipt",
            "local_runtime_receipt_digest",
            "mode",
            "receipt_id",
        }
        if set(binding) != expected_keys:
            return QualificationStatus(
                True, False, None, ("installed execution binding is malformed",)
            )
        try:
            artifact_path = _safe_relative_path(
                binding["artifact_receipt"], "execution artifact receipt path"
            )
            local_path = _safe_relative_path(
                binding["local_runtime_receipt"],
                "execution local runtime receipt path",
            )
            artifact_digest = str(binding["artifact_receipt_digest"]).strip()
            local_digest = str(binding["local_runtime_receipt_digest"]).strip()
            receipt_id = str(binding["receipt_id"]).strip()
        except QualificationError as exc:
            return QualificationStatus(True, False, None, (str(exc),))
        if (
            not _DIGEST_RE.fullmatch(artifact_digest)
            or not _DIGEST_RE.fullmatch(local_digest)
            or receipt_id != f"sha256:{local_digest}"
        ):
            return QualificationStatus(
                True, False, None, ("execution receipt identity is malformed",)
            )
        artifact = verify_artifact_qualification_receipt(
            root, artifact_path, expected_digest=artifact_digest
        )
        local = verify_local_runtime_receipt(
            root,
            artifact_path,
            local_path,
            expected_digest=local_digest,
        )
        issues = [
            *(f"artifact: {issue}" for issue in artifact["issues"]),
            *(f"local runtime: {issue}" for issue in local["issues"]),
        ]
        if local["receipt_id"] != receipt_id:
            issues.append("execution receipt identity differs")
        return QualificationStatus(True, not issues, receipt_id, tuple(issues))
    return QualificationStatus(
        True, False, None, ("execution qualification binding mode is unsupported",)
    )


def disable_loop_v1_admission(repo_root: Path, reason: str) -> dict[str, object]:
    """Disable local admission without changing already admitted runs."""
    require_local_effect("local_runtime_write")
    reason = reason.strip()
    if not reason:
        raise QualificationError("rollback reason is required")
    root = Path(repo_root).resolve()
    marker = root / DISABLE_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "disabled": True,
        "reason": reason,
        "reason_digest": sha256(reason.encode("utf-8")).hexdigest(),
    }
    write_bytes_atomic(
        marker,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "failures": [],
        "marker": str(marker),
        "paused_parents": [],
        "preserved": True,
        "status": "disabled",
    }


def enable_loop_v1_admission(
    repo_root: Path,
    *,
    installed_root: Path | None = None,
) -> dict[str, object]:
    """Remove the local rollback marker only after the configured receipt verifies."""
    require_local_effect("local_runtime_write")
    root = Path(repo_root).resolve()
    marker = root / DISABLE_MARKER
    marker_bytes = marker.read_bytes() if marker.is_file() else None
    status = _configured_receipt_status(root, installed_root=installed_root)
    if not status.enforced:
        raise QualificationError("Loop v1 qualification config is missing")
    if not status.valid:
        raise QualificationError("; ".join(status.issues))
    if marker_bytes is not None:
        if not marker.is_file() or marker.read_bytes() != marker_bytes:
            raise QualificationError("local admission disable marker changed during verification")
        marker.unlink()
    return {"receipt_id": status.receipt_id, "status": "enabled"}


def _write_json_output(value: object, output: str | None) -> None:
    encoded = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(path, encoded.encode("utf-8"))
    else:
        print(encoded, end="")


def _read_verification_commands(path: str) -> tuple[tuple[str, ...], ...]:
    value = _read_json_object(Path(path), "verification command policy")
    commands = value.get("commands")
    if set(value) != {"commands"} or not isinstance(commands, list) or not commands:
        raise QualificationError("verification command policy requires commands")
    normalized: list[tuple[str, ...]] = []
    for command in commands:
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise QualificationError("verification commands must be non-empty string arrays")
        normalized.append(tuple(command))
    return tuple(normalized)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--installed-root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--output")
    receipt_parser = subparsers.add_parser("receipt")
    receipt_parser.add_argument("--qualification", required=True)
    receipt_parser.add_argument("--output-dir", required=True)
    receipt_parser.add_argument("--runtime-commit", default="HEAD")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.add_argument("--expected-digest")
    disable_parser = subparsers.add_parser("disable")
    disable_parser.add_argument("--reason", required=True)
    subparsers.add_parser("enable")
    overlay_parser = subparsers.add_parser("overlay-check")
    overlay_parser.add_argument("--manifest")
    artifact_receipt_parser = subparsers.add_parser("artifact-receipt")
    artifact_receipt_parser.add_argument("--qualification", required=True)
    artifact_receipt_parser.add_argument("--output-dir", required=True)
    artifact_receipt_parser.add_argument("--manifest")
    artifact_verify_parser = subparsers.add_parser("artifact-verify")
    artifact_verify_parser.add_argument("--receipt", required=True)
    artifact_verify_parser.add_argument("--expected-digest")
    artifact_verify_parser.add_argument("--manifest")
    local_receipt_parser = subparsers.add_parser("local-receipt")
    local_receipt_parser.add_argument("--artifact", required=True)
    local_receipt_parser.add_argument("--output-dir", required=True)
    local_receipt_parser.add_argument("--manifest")
    local_verify_parser = subparsers.add_parser("local-verify")
    local_verify_parser.add_argument("--artifact", required=True)
    local_verify_parser.add_argument("--receipt", required=True)
    local_verify_parser.add_argument("--expected-digest")
    local_verify_parser.add_argument("--manifest")
    target_receipt_parser = subparsers.add_parser("target-receipt")
    target_receipt_parser.add_argument("--artifact", required=True)
    target_receipt_parser.add_argument("--local-receipt", required=True)
    target_receipt_parser.add_argument("--target-root", required=True)
    target_receipt_parser.add_argument("--plan", required=True)
    target_receipt_parser.add_argument("--final-receipt", required=True)
    target_receipt_parser.add_argument("--verification-commands", required=True)
    target_receipt_parser.add_argument("--output-dir", required=True)
    target_receipt_parser.add_argument("--manifest")
    target_verify_parser = subparsers.add_parser("target-verify")
    target_verify_parser.add_argument("--artifact", required=True)
    target_verify_parser.add_argument("--local-receipt", required=True)
    target_verify_parser.add_argument("--receipt", required=True)
    target_verify_parser.add_argument("--target-root", required=True)
    target_verify_parser.add_argument("--plan", required=True)
    target_verify_parser.add_argument("--final-receipt", required=True)
    target_verify_parser.add_argument("--verification-commands", required=True)
    target_verify_parser.add_argument("--expected-digest")
    target_verify_parser.add_argument("--manifest")
    read_parser = subparsers.add_parser("receipt-read")
    read_parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    installed_root = Path(args.installed_root) if args.installed_root else None
    try:
        installed = (
            _resolve_installed_root(root, installed_root)
            if installed_root is not None
            else None
        )
        if args.command in {"artifact-receipt", "artifact-verify"} and installed_root:
            raise QualificationError(
                "artifact receipts cannot use --installed-root; use a portable release root"
            )
        if args.command == "run":
            if args.output and installed is not None:
                _require_write_outside_root(
                    Path(args.output),
                    installed,
                    "qualification output",
                )
            result = run_qualification_matrix(root, installed_root=installed_root)
            _write_json_output(result, args.output)
            return 0 if result["status"] == "passed" else 1
        if args.command == "receipt":
            qualification = _read_json_object(Path(args.qualification), "qualification result")
            path = generate_conformance_receipt(
                root,
                qualification,
                Path(args.output_dir),
                runtime_commit=args.runtime_commit,
                installed_root=installed_root,
            )
            _write_json_output({"path": str(path), "status": "generated"}, None)
            return 0
        if args.command == "verify":
            result = verify_conformance_receipt(
                root,
                Path(args.receipt),
                expected_digest=args.expected_digest,
                installed_root=installed_root,
            )
            _write_json_output(result, None)
            return 0 if result["valid"] else 1
        if args.command == "artifact-receipt":
            qualification = _read_json_object(
                Path(args.qualification),
                "qualification result",
            )
            path = generate_artifact_qualification_receipt(
                root,
                qualification,
                Path(args.output_dir),
                manifest_path=Path(args.manifest) if args.manifest else None,
            )
            _write_json_output(
                {"receipt_id": f"sha256:{path.stem}", "status": "generated"},
                None,
            )
            return 0
        if args.command == "artifact-verify":
            result = verify_artifact_qualification_receipt(
                root,
                Path(args.receipt),
                expected_digest=args.expected_digest,
                manifest_path=Path(args.manifest) if args.manifest else None,
            )
            _write_json_output(result, None)
            return 0 if result["valid"] else 1
        if args.command == "local-receipt":
            path = generate_local_runtime_receipt(
                root,
                Path(args.artifact),
                Path(args.output_dir),
                manifest_path=Path(args.manifest) if args.manifest else None,
                installed_root=installed_root,
            )
            _write_json_output(
                {"receipt_id": f"sha256:{path.stem}", "status": "generated"},
                None,
            )
            return 0
        if args.command == "local-verify":
            result = verify_local_runtime_receipt(
                root,
                Path(args.artifact),
                Path(args.receipt),
                expected_digest=args.expected_digest,
                manifest_path=Path(args.manifest) if args.manifest else None,
                installed_root=installed_root,
            )
            _write_json_output(result, None)
            return 0 if result["valid"] else 1
        if args.command == "target-receipt":
            path = generate_target_verification_receipt(
                root,
                Path(args.target_root),
                Path(args.artifact),
                Path(args.local_receipt),
                _read_json_object(Path(args.plan), "target plan"),
                _read_json_object(Path(args.final_receipt), "target final receipt"),
                _read_verification_commands(args.verification_commands),
                Path(args.output_dir),
                manifest_path=Path(args.manifest) if args.manifest else None,
                installed_root=installed_root,
            )
            _write_json_output(
                {"receipt_id": f"sha256:{path.stem}", "status": "generated"},
                None,
            )
            return 0
        if args.command == "target-verify":
            result = verify_target_verification_receipt(
                root,
                Path(args.target_root),
                Path(args.artifact),
                Path(args.local_receipt),
                Path(args.receipt),
                _read_json_object(Path(args.plan), "target plan"),
                _read_json_object(Path(args.final_receipt), "target final receipt"),
                _read_verification_commands(args.verification_commands),
                expected_digest=args.expected_digest,
                manifest_path=Path(args.manifest) if args.manifest else None,
                installed_root=installed_root,
            )
            _write_json_output(result, None)
            return 0 if result["valid"] else 1
        if args.command == "receipt-read":
            _write_json_output(
                read_qualification_receipt(_receipt_path(root, Path(args.receipt))),
                None,
            )
            return 0
        if args.command == "disable":
            _write_json_output(disable_loop_v1_admission(root, args.reason), None)
            return 0
        if args.command == "enable":
            _write_json_output(
                enable_loop_v1_admission(root, installed_root=installed_root),
                None,
            )
            return 0
        manifest = Path(args.manifest).resolve() if args.manifest else None
        installed = check_overlay_conformance(
            root,
            manifest,
            installed_root=installed_root,
        )
        application = run_clean_clone_overlay_conformance(
            root,
            manifest,
            installed_root=installed_root,
        )
        issues = [*installed["issues"], *application["issues"]]
        result = {
            "application": application,
            "installed": installed,
            "issues": issues,
            "status": "passed" if not issues else "failed",
        }
        result["evidence_digest"] = digest_json(result)
        _write_json_output(result, None)
        return 0 if result["status"] == "passed" else 1
    except QualificationError as exc:
        _write_json_output({"error": str(exc), "status": "failed"}, None)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
