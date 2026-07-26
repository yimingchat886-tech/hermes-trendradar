"""Hermes-owned analysis result reference contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .handoff import HandoffPackageError, validate_handoff_package

REQUIRED_RESULT_FIELDS = (
    "schema_version",
    "package_id",
    "run_id",
    "content_id",
    "transcript_artifact_ref",
    "result_ref",
    "status",
)
RESULT_STATUSES = {"succeeded", "partial_failed", "failed"}
ERROR_ANALYSIS_RESULT_INVALID = "analysis_result_invalid"


class AnalysisResultError(ValueError):
    pass


def load_handoff_package(path_or_ref: str, storage_dir: Path) -> dict[str, Any]:
    path = _resolve_package_path(path_or_ref, storage_dir)
    package = _read_json_object(path, "handoff package")
    try:
        validate_handoff_package(package)
    except HandoffPackageError as exc:
        raise AnalysisResultError(str(exc)) from exc
    return package


def load_analysis_result(path: Path) -> tuple[dict[str, Any], str]:
    result_path = path.expanduser()
    payload = _read_json_bytes(result_path, "analysis result")
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisResultError("analysis result must be valid JSON") from exc
    if not isinstance(data, dict):
        raise AnalysisResultError("analysis result must be an object")
    return data, digest


def validate_analysis_result(
    handoff_package: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    result_hash: str | None = None,
    storage_dir: Path | None = None,
) -> dict[str, str]:
    try:
        validate_handoff_package(handoff_package)
    except HandoffPackageError as exc:
        raise AnalysisResultError(str(exc)) from exc

    missing = [field for field in REQUIRED_RESULT_FIELDS if field not in result]
    if missing:
        raise AnalysisResultError(f"analysis result missing: {missing}")

    normalized = {field: str(result.get(field) or "") for field in REQUIRED_RESULT_FIELDS}
    if normalized["schema_version"] != "1.4":
        raise AnalysisResultError("analysis result schema_version must be 1.4")
    if normalized["package_id"] != str(handoff_package["package_id"]):
        raise AnalysisResultError("analysis result package_id must match handoff package")
    if normalized["run_id"] != str(handoff_package["run_id"]):
        raise AnalysisResultError("analysis result run_id must match handoff package")
    if normalized["status"] not in RESULT_STATUSES:
        raise AnalysisResultError(f"unsupported analysis result status: {normalized['status']}")
    _validate_file_ref(normalized["result_ref"], "analysis result_ref")
    artifact_hash = _hash_file_ref(normalized["result_ref"], storage_dir) if storage_dir is not None else None

    content = _handoff_content(handoff_package, normalized["content_id"])
    if normalized["transcript_artifact_ref"] != str(content["transcript_artifact_ref"]):
        raise AnalysisResultError("analysis result transcript_artifact_ref must match handoff content")

    declared_hash = str(result.get("result_hash") or "")
    trusted_hash = artifact_hash or declared_hash or result_hash or ""
    if artifact_hash and declared_hash and declared_hash != artifact_hash:
        raise AnalysisResultError("analysis result_hash must match result_ref target")
    if not artifact_hash and result_hash and declared_hash and declared_hash != result_hash:
        raise AnalysisResultError("analysis result_hash must match result file")
    if not trusted_hash.startswith("sha256:"):
        raise AnalysisResultError("analysis result_hash must be sha256")
    normalized["result_hash"] = trusted_hash
    return normalized


def _resolve_package_path(path_or_ref: str, storage_dir: Path) -> Path:
    if not path_or_ref:
        raise AnalysisResultError("handoff package path is required")
    if path_or_ref.startswith("file:"):
        rel = Path(path_or_ref.removeprefix("file:"))
        if rel.is_absolute() or ".." in rel.parts:
            raise AnalysisResultError("handoff package file ref must stay under storage")
        root = storage_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise AnalysisResultError("handoff package file ref must stay under storage")
        return path
    return Path(path_or_ref).expanduser()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json_bytes(path, label)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisResultError(f"{label} must be valid JSON") from exc
    if not isinstance(data, dict):
        raise AnalysisResultError(f"{label} must be an object")
    return data


def _read_json_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AnalysisResultError(f"{label} is not readable") from exc


def _handoff_content(handoff_package: Mapping[str, Any], content_id: str) -> Mapping[str, Any]:
    for item in handoff_package["contents"]:
        if item["content_id"] == content_id:
            return item
    raise AnalysisResultError("analysis result content_id must exist in handoff package")


def _validate_file_ref(value: str, label: str) -> None:
    if not value.startswith("file:"):
        raise AnalysisResultError(f"{label} must be a file: ref")
    rel = Path(value.removeprefix("file:"))
    if rel.is_absolute() or ".." in rel.parts:
        raise AnalysisResultError(f"{label} must be a relative file: ref")


def _hash_file_ref(value: str, storage_dir: Path) -> str:
    rel = Path(value.removeprefix("file:"))
    root = storage_dir.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise AnalysisResultError("analysis result_ref must stay under storage")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AnalysisResultError("analysis result_ref target is not readable") from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()
