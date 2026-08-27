"""Collector profile stock_runtime adapter for Hermes stock CLI contracts.

This module is intentionally a narrow adapter: it invokes the existing
``hermes-benchmark`` CLI with fixed argv templates, validates the CLI JSON
envelope, constrains storage refs to the configured Hermes stock storage root,
and returns only allowlisted fields to the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .content_pipeline import COPY_MODES, PIPELINE_SCHEMA_VERSION, REQUEST_SCHEMA_VERSION, ContentPipelineError, validate_request
from .handoff import HandoffPackageError, validate_handoff_package
from .profile import load_profile
from .runtime_cdp import resolve_runtime_config

CliRunner = Callable[[Sequence[str], Path, Mapping[str, str], float], tuple[int, bytes, bytes]]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXPECTED_CONTRACT_VERSION = "2.0"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SAFE_LEAF_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9._-]{8,80}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9._:/#-]{1,160}$")
REASON_CODE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
CONTENT_RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9._-]{8,80}$")
RAW_DOUYIN_AWEME_ID_RE = re.compile(r"^[0-9]{1,64}$")
CANONICAL_DOUYIN_CONTENT_ID_RE = re.compile(r"^content-douyin-[A-Za-z0-9][A-Za-z0-9_.:-]{0,128}$")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
CONTENT_FILE_REF_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,240}$")
CONTENT_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
ABSOLUTE_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/._-])(?:/(?:tmp|var/tmp|home|mnt|Users)/|[A-Za-z]:\\\\)")
FILE_ABSOLUTE_LOCAL_PATH_RE = re.compile(r"(?i)file:(?:/(?:tmp|var/tmp|home|mnt|Users)/|[A-Za-z]:\\\\)")
LOCAL_ENDPOINT_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:authorization|bearer|cookie|password|secret|session|token)\b\s*[:=]\s*\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}))"
)
CONTENT_PIPELINE_MAX_SCHEMA_ITEMS = 50

JSON_OBJECT_TYPE = {"type": "object", "additionalProperties": True}
SAFE_ID_SCHEMA = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"}
CONTENT_ID_SCHEMA = {"type": "string", "pattern": r"^(?:[0-9]{1,64}|content-douyin-[A-Za-z0-9][A-Za-z0-9_.:-]{0,128})$"}
STOCK_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "stock_validate_config": {
        "name": "stock_validate_config",
        "description": "Validate the fixed Hermes stock profile config. No arguments accepted.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "stock_healthcheck": {
        "name": "stock_healthcheck",
        "description": "Run the fixed Hermes stock runtime healthcheck. No arguments accepted.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "stock_run_daily": {
        "name": "stock_run_daily",
        "description": "Run the fixed Hermes stock daily handoff for a YYYY-MM-DD date.",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "Run date in YYYY-MM-DD format."}},
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    "stock_run_content_pipeline": {
        "name": "stock_run_content_pipeline",
        "description": "Run the fixed local TrendRadar content pipeline for a configured Douyin account with one scope selector.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": SAFE_ID_SCHEMA,
                "content_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": CONTENT_PIPELINE_MAX_SCHEMA_ITEMS,
                    "uniqueItems": True,
                    "items": CONTENT_ID_SCHEMA,
                },
                "published_since": {"type": "string", "minLength": 1, "maxLength": 64},
                "max_items": {"type": "integer", "minimum": 1, "maximum": CONTENT_PIPELINE_MAX_SCHEMA_ITEMS},
                "all_visible": {"type": "boolean", "const": True},
                "copy_mode": {"type": "string", "enum": sorted(COPY_MODES)},
            },
            "required": ["account_id", "copy_mode"],
            "oneOf": [
                {"required": ["content_ids"], "not": {"anyOf": [{"required": ["published_since"]}, {"required": ["max_items"]}, {"required": ["all_visible"]}]}},
                {"required": ["published_since"], "not": {"anyOf": [{"required": ["content_ids"]}, {"required": ["max_items"]}, {"required": ["all_visible"]}]}},
                {"required": ["max_items"], "not": {"anyOf": [{"required": ["content_ids"]}, {"required": ["published_since"]}, {"required": ["all_visible"]}]}},
                {"required": ["all_visible"], "not": {"anyOf": [{"required": ["content_ids"]}, {"required": ["published_since"]}, {"required": ["max_items"]}]}},
            ],
            "additionalProperties": False,
        },
    },
    "stock_read_analysis_package": {
        "name": "stock_read_analysis_package",
        "description": "Read a run-scoped Hermes stock analysis package ref returned by stock_run_daily.",
        "parameters": {
            "type": "object",
            "properties": {"package_ref": {"type": "string", "description": "file:<run_id>/artifacts/analysis_package.json"}},
            "required": ["package_ref"],
            "additionalProperties": False,
        },
    },
    "stock_record_analysis_result": {
        "name": "stock_record_analysis_result",
        "description": "Write a controlled run-scoped analysis artifact and record its ref through Hermes stock.",
        "parameters": {
            "type": "object",
            "properties": {
                "package_ref": {"type": "string"},
                "content_id": {"type": "string"},
                "status": {"type": "string", "enum": ["succeeded", "partial_failed", "failed"]},
                "analysis": JSON_OBJECT_TYPE,
            },
            "required": ["package_ref", "content_id", "status", "analysis"],
            "additionalProperties": False,
        },
    },
    "stock_build_internal_digest": {
        "name": "stock_build_internal_digest",
        "description": "Build a Hermes stock internal digest payload for a run_id.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    "stock_record_feedback": {
        "name": "stock_record_feedback",
        "description": "Record adopt/reject feedback through the fixed Hermes stock contract.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "content_id": {"type": "string"},
                "analysis_result_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["adopt", "reject"]},
                "actor_ref": {"type": "string"},
                "source_message_ref": {"type": "string"},
                "reason_code": {"type": "string"},
            },
            "required": ["run_id", "content_id", "analysis_result_id", "decision", "actor_ref", "source_message_ref"],
            "additionalProperties": False,
        },
    },
}

COMMAND_DATA_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "validate-config": (
        "schema_version",
        "ok",
        "profile_id",
        "profile_mode",
        "profile_hash",
        "platforms",
        "enabled_account_count",
        "required_enabled_accounts",
        "schedule_configured",
        "validation",
        "warnings",
        "errors",
    ),
    "healthcheck": ("schema_version", "profile_id", "profile_hash", "run_eligible", "runtime_effective_status", "checks", "errors", "version"),
    "run-daily": (
        "schema_version",
        "run_id",
        "date",
        "profile_id",
        "profile_hash",
        "status",
        "analysis_mode",
        "feishu_mode",
        "account_summary",
        "content_summary",
        "transcript_summary",
        "analysis_package_id",
        "analysis_package_ref",
        "artifact_refs",
        "errors",
    ),
    "content-pipeline": (
        "schema_version",
        "run_id",
        "request_digest",
        "status",
        "copy_mode",
        "summary",
        "items",
        "error_code",
        "artifact_refs",
        "replayed",
    ),
    "record-analysis-result": (
        "schema_version",
        "analysis_result_id",
        "package_id",
        "run_id",
        "content_id",
        "transcript_artifact_ref",
        "result_ref",
        "result_hash",
        "status",
    ),
    "build-internal-digest": (
        "schema_version",
        "run_id",
        "digest_payload_ref",
        "digest_payload_hash",
        "status",
        "summary",
        "trace",
        "delivery",
    ),
    "record-feedback": (
        "schema_version",
        "feedback_id",
        "run_id",
        "content_id",
        "analysis_result_id",
        "result_ref",
        "result_hash",
        "result_status",
        "decision",
        "actor_ref",
        "source_message_ref",
        "reason_code",
    ),
}

PACKAGE_CONTENT_FIELDS = (
    "content_id",
    "platform",
    "account_id",
    "account_display_name",
    "source_url",
    "title_or_caption_raw",
    "publish_at",
    "collected_at",
    "transcript_status",
    "transcript_artifact_ref",
    "dedup_key",
)


class StockRuntimeError(ValueError):
    """Fail-closed adapter error with a safe, bounded public message."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, data: Mapping[str, Any] | None = None):
        self.code = code
        self.retryable = retryable
        self.data = dict(data) if data is not None else None
        super().__init__(_safe_message(message))


@dataclass(frozen=True)
class StockRuntimeSettings:
    executable: tuple[str, ...] = ("hermes-benchmark",)
    cwd: Path = Path(".")
    profile_ref: Path = Path("profiles/local/hermes.v1.4.douyin.local.json")
    content_pipeline_profile_ref: Path | None = None
    storage_root: Path | None = None
    timeout_seconds: float = 60.0
    content_pipeline_timeout_seconds: float = 1800.0
    stdout_cap_bytes: int = 64 * 1024
    stderr_cap_bytes: int = 16 * 1024
    content_request_cap_bytes: int = 16 * 1024
    package_cap_bytes: int = 256 * 1024
    result_cap_bytes: int = 64 * 1024
    digest_cap_bytes: int = 256 * 1024
    max_package_contents: int = 50
    max_string_chars: int = 4000
    env_allowlist: tuple[str, ...] = ("PATH", "PYTHONPATH", "VIRTUAL_ENV")
    runner: CliRunner | None = None

    def resolved_storage_root(self) -> Path:
        if self.storage_root is not None:
            return self.storage_root
        profile = load_profile(self.profile_ref)
        return resolve_runtime_config(profile).storage_dir


def settings_from_hermes_config() -> StockRuntimeSettings:
    """Build settings from the active Hermes profile config.

    The model never supplies executable/cwd/profile/storage. Operators pin these
    in ``stock_runtime`` config for the collector profile distribution.
    """
    try:
        from hermes_cli.config import load_config
    except Exception as exc:  # pragma: no cover - only used inside Hermes Agent
        raise StockRuntimeError("stock_runtime_config_unavailable", "Hermes config loader is unavailable") from exc

    cfg = load_config() or {}
    raw = cfg.get("stock_runtime") if isinstance(cfg, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    executable = _config_executable(raw.get("executable", ["hermes-benchmark"]))
    cwd = Path(str(raw.get("cwd") or raw.get("repo_cwd") or ".")).expanduser()
    profile_value = raw.get("profile_ref") or raw.get("profile")
    if not profile_value:
        raise StockRuntimeError("stock_runtime_config_invalid", "stock_runtime.profile_ref is required")
    content_profile_value = raw.get("content_pipeline_profile_ref") or raw.get("content_pipeline_profile")
    storage_value = raw.get("storage_root")
    return StockRuntimeSettings(
        executable=executable,
        cwd=cwd,
        profile_ref=Path(str(profile_value)).expanduser(),
        content_pipeline_profile_ref=Path(str(content_profile_value)).expanduser() if content_profile_value else None,
        storage_root=Path(str(storage_value)).expanduser() if storage_value else None,
        timeout_seconds=float(raw.get("timeout_seconds") or 60),
        content_pipeline_timeout_seconds=float(raw.get("content_pipeline_timeout_seconds") or 1800),
        stdout_cap_bytes=int(raw.get("stdout_cap_bytes") or 64 * 1024),
        stderr_cap_bytes=int(raw.get("stderr_cap_bytes") or 16 * 1024),
        content_request_cap_bytes=int(raw.get("content_request_cap_bytes") or 16 * 1024),
        package_cap_bytes=int(raw.get("package_cap_bytes") or 256 * 1024),
        result_cap_bytes=int(raw.get("result_cap_bytes") or 64 * 1024),
        digest_cap_bytes=int(raw.get("digest_cap_bytes") or 256 * 1024),
        max_package_contents=int(raw.get("max_package_contents") or 50),
        max_string_chars=int(raw.get("max_string_chars") or 4000),
    )


def stock_validate_config(settings: StockRuntimeSettings) -> dict[str, Any]:
    return _run_tool("stock_validate_config", lambda: _call_cli(settings, "validate-config", ()))


def stock_healthcheck(settings: StockRuntimeSettings) -> dict[str, Any]:
    return _run_tool("stock_healthcheck", lambda: _call_cli(settings, "healthcheck", ()))


def stock_run_daily(settings: StockRuntimeSettings, run_date: str) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        if not DATE_RE.fullmatch(run_date):
            raise StockRuntimeError("invalid_date", "date must be YYYY-MM-DD")
        try:
            date.fromisoformat(run_date)
        except ValueError as exc:
            raise StockRuntimeError("invalid_date", "date must be a valid calendar date") from exc
        return _call_cli(settings, "run-daily", ("--date", run_date, "--analysis-mode", "hermes-handoff"))

    return _run_tool("stock_run_daily", action)


def stock_run_content_pipeline(
    settings: StockRuntimeSettings,
    *,
    account_id: str,
    copy_mode: str,
    content_ids: Any = None,
    published_since: Any = None,
    max_items: Any = None,
    all_visible: Any = None,
) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        if settings.content_pipeline_profile_ref is None:
            raise StockRuntimeError("stock_runtime_config_invalid", "stock_runtime.content_pipeline_profile_ref is required")
        request = _content_pipeline_request(
            settings,
            account_id=account_id,
            copy_mode=copy_mode,
            content_ids=content_ids,
            published_since=published_since,
            max_items=max_items,
            all_visible=all_visible,
        )
        payload = _canonical_json_bytes(request)
        if len(payload) > settings.content_request_cap_bytes:
            raise StockRuntimeError("content_request_too_large", "content pipeline request exceeds the adapter size cap")
        with tempfile.TemporaryDirectory(prefix="stock-content-request-") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            descriptor = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            return _call_cli(
                settings,
                "content-pipeline",
                ("--request", str(request_path)),
                profile_ref=settings.content_pipeline_profile_ref,
                include_error_data=True,
                timeout_seconds=settings.content_pipeline_timeout_seconds,
            )

    return _run_tool("stock_run_content_pipeline", action)


def stock_read_analysis_package(settings: StockRuntimeSettings, package_ref: str) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        package = _load_package(settings, package_ref)
        contents = package["contents"]
        if len(contents) > settings.max_package_contents:
            raise StockRuntimeError("package_too_large", "analysis package contains too many content items")
        public_contents = []
        for item in contents:
            projected = {field: _bounded_string(str(item.get(field) or ""), settings.max_string_chars) for field in PACKAGE_CONTENT_FIELDS}
            _assert_no_forbidden_values(projected)
            public_contents.append(projected)
        data = {
            "schema_version": package["schema_version"],
            "package_id": package["package_id"],
            "run_id": package["run_id"],
            "profile_hash": package["profile_hash"],
            "mode": package["mode"],
            "content_count": len(public_contents),
            "contents": public_contents,
        }
        _assert_no_forbidden_values(data)
        return data

    return _run_tool("stock_read_analysis_package", action)


def stock_record_analysis_result(
    settings: StockRuntimeSettings,
    *,
    package_ref: str,
    content_id: str,
    status: str,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        if status not in {"succeeded", "partial_failed", "failed"}:
            raise StockRuntimeError("analysis_result_invalid", "analysis status is unsupported")
        if not isinstance(analysis, Mapping):
            raise StockRuntimeError("analysis_result_invalid", "analysis must be an object")
        _assert_no_forbidden_values(analysis)
        package = _load_package(settings, package_ref)
        content = _package_content(package, content_id)
        run_id = str(package["run_id"])
        leaf = _safe_artifact_leaf(content_id)
        result_ref = f"file:{run_id}/artifacts/analysis_results/{leaf}.json"
        result_bytes = _canonical_json_bytes(dict(analysis))
        if len(result_bytes) > settings.result_cap_bytes:
            raise StockRuntimeError("result_too_large", "analysis result artifact is too large")
        result_hash = _write_once_or_replay(settings, result_ref, result_bytes)
        metadata_ref = f"file:{run_id}/artifacts/analysis_results/{leaf}.metadata.json"
        metadata = {
            "schema_version": "1.4",
            "package_id": str(package["package_id"]),
            "run_id": run_id,
            "content_id": content_id,
            "transcript_artifact_ref": str(content["transcript_artifact_ref"]),
            "result_ref": result_ref,
            "result_hash": result_hash,
            "status": status,
        }
        metadata_bytes = _canonical_json_bytes(metadata)
        _write_once_or_replay(settings, metadata_ref, metadata_bytes)
        metadata_path = _resolve_file_ref(metadata_ref, settings.resolved_storage_root(), run_id=run_id)
        data = _call_cli(settings, "record-analysis-result", ("--package", package_ref, "--result", str(metadata_path)))
        if data.get("result_ref") != result_ref or data.get("result_hash") != result_hash:
            raise StockRuntimeError("analysis_result_contract_mismatch", "recorded result ref/hash did not match the written artifact")
        return data

    return _run_tool("stock_record_analysis_result", action)


def stock_build_internal_digest(settings: StockRuntimeSettings, run_id: str) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        _validate_run_id(run_id)
        data = _call_cli(settings, "build-internal-digest", ("--run-id", run_id))
        digest_ref = str(data.get("digest_payload_ref") or "")
        digest_hash = str(data.get("digest_payload_hash") or "")
        payload_bytes = _read_ref_bytes(settings, digest_ref, settings.digest_cap_bytes, run_id=run_id)
        if _sha256(payload_bytes) != digest_hash:
            raise StockRuntimeError("digest_hash_mismatch", "digest payload hash did not match")
        data["digest_payload_hash_verified"] = True
        _assert_no_forbidden_values(data)
        return data

    return _run_tool("stock_build_internal_digest", action)


def stock_record_feedback(
    settings: StockRuntimeSettings,
    *,
    run_id: str,
    content_id: str,
    analysis_result_id: str,
    decision: str,
    actor_ref: str,
    source_message_ref: str,
    reason_code: str = "",
) -> dict[str, Any]:
    def action() -> dict[str, Any]:
        _validate_run_id(run_id)
        _validate_safe_id(content_id, "content_id")
        _validate_safe_id(analysis_result_id, "analysis_result_id")
        if decision not in {"adopt", "reject"}:
            raise StockRuntimeError("feedback_invalid", "feedback decision must be adopt or reject")
        _validate_opaque_ref(actor_ref, "actor_ref")
        _validate_opaque_ref(source_message_ref, "source_message_ref")
        if reason_code and not REASON_CODE_RE.fullmatch(reason_code):
            raise StockRuntimeError("feedback_invalid", "feedback reason_code must be a bounded slug")
        args = (
            "--run-id",
            run_id,
            "--content-id",
            content_id,
            "--analysis-result-id",
            analysis_result_id,
            "--decision",
            decision,
            "--actor-ref",
            actor_ref,
            "--source-message-ref",
            source_message_ref,
        )
        if reason_code:
            args = (*args, "--reason-code", reason_code)
        return _call_cli(settings, "record-feedback", args)

    return _run_tool("stock_record_feedback", action)


def _normalize_content_pipeline_content_ids(content_ids: Sequence[Any]) -> list[str]:
    normalized: list[str] = []
    for item in content_ids:
        if not isinstance(item, str):
            raise StockRuntimeError("content_request_content_ids", "content_ids must be raw aweme ids or canonical Douyin content ids")
        if RAW_DOUYIN_AWEME_ID_RE.fullmatch(item):
            normalized.append(f"content-douyin-{item}")
        elif CANONICAL_DOUYIN_CONTENT_ID_RE.fullmatch(item):
            normalized.append(item)
        else:
            raise StockRuntimeError("content_request_content_ids", "content_ids must be raw aweme ids or canonical Douyin content ids")
    return normalized


def _content_pipeline_request(
    settings: StockRuntimeSettings,
    *,
    account_id: str,
    copy_mode: str,
    content_ids: Any,
    published_since: Any,
    max_items: Any,
    all_visible: Any,
) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    active: list[str] = []
    if content_ids is not None:
        if not isinstance(content_ids, list) or not content_ids:
            raise StockRuntimeError("content_request_content_ids", "content_ids must be a non-empty list of opaque ids")
        if len(content_ids) > settings.max_package_contents:
            raise StockRuntimeError("content_request_too_large", "content_ids exceeds the adapter item cap")
        scope["content_ids"] = _normalize_content_pipeline_content_ids(content_ids)
        active.append("content_ids")
    if published_since is not None:
        if not isinstance(published_since, str) or not published_since:
            raise StockRuntimeError("content_request_published_since", "published_since must be a timestamp string")
        scope["published_since"] = published_since
        active.append("published_since")
    if max_items is not None:
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
            raise StockRuntimeError("content_request_max_items", "max_items must be a positive integer")
        if max_items > settings.max_package_contents:
            raise StockRuntimeError("content_request_too_large", "max_items exceeds the adapter item cap")
        scope["max_items"] = max_items
        active.append("max_items")
    if all_visible is not None:
        if all_visible is not True:
            raise StockRuntimeError("content_request_scope", "all_visible must be true when supplied")
        scope["all_visible"] = True
        active.append("all_visible")
    if not active:
        raise StockRuntimeError("content_pipeline_scope_selector_required", "exactly one content pipeline scope selector is required")
    if len(active) != 1:
        raise StockRuntimeError("content_pipeline_scope_selector_conflict", "exactly one content pipeline scope selector is allowed")

    try:
        return validate_request(
            {
                "schema_version": REQUEST_SCHEMA_VERSION,
                "target": {"platform": "douyin", "account_id": account_id},
                "scope": scope,
                "copy_mode": copy_mode,
            }
        )
    except ContentPipelineError as exc:
        raise StockRuntimeError(exc.code, str(exc)) from exc


def _run_tool(tool: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        data = action()
        _assert_no_forbidden_values(data)
        return {"ok": True, "tool": tool, "data": data, "error": None, "retryable": False}
    except StockRuntimeError as exc:
        if exc.data is not None:
            _assert_no_forbidden_values(exc.data)
        return {"ok": False, "tool": tool, "data": exc.data, "error": {"code": exc.code, "message": str(exc)}, "retryable": exc.retryable}


def _call_cli(
    settings: StockRuntimeSettings,
    command: str,
    extra_args: Sequence[str],
    *,
    profile_ref: Path | None = None,
    include_error_data: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    if not settings.executable:
        raise StockRuntimeError("stock_runtime_config_invalid", "stock runtime executable is not configured")
    cwd = settings.cwd.resolve()
    if not cwd.exists() or not cwd.is_dir():
        raise StockRuntimeError("stock_runtime_config_invalid", "stock runtime cwd is not available")
    argv = [*settings.executable, command, "--profile", str(profile_ref or settings.profile_ref), *extra_args, "--json"]
    env = {name: os.environ[name] for name in settings.env_allowlist if name in os.environ}
    runner = settings.runner or _subprocess_runner
    try:
        returncode, stdout, stderr = runner(tuple(str(item) for item in argv), cwd, env, settings.timeout_seconds if timeout_seconds is None else timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise StockRuntimeError("stock_runtime_timeout", "Hermes stock command timed out", retryable=True) from exc
    except OSError as exc:
        raise StockRuntimeError("stock_runtime_unavailable", "Hermes stock executable is unavailable", retryable=True) from exc
    if len(stdout) > settings.stdout_cap_bytes or len(stderr) > settings.stderr_cap_bytes:
        raise StockRuntimeError("output_too_large", "Hermes stock command output exceeded the adapter cap")
    if stderr.strip():
        _assert_text_safe(stderr.decode("utf-8", errors="replace"))
        raise StockRuntimeError("unexpected_stderr", "Hermes stock command wrote to stderr")
    try:
        envelope = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StockRuntimeError("invalid_json", "Hermes stock command did not return a valid JSON envelope") from exc
    if not isinstance(envelope, dict):
        raise StockRuntimeError("invalid_json", "Hermes stock command JSON envelope must be an object")
    _validate_envelope(envelope, command, returncode)
    _assert_no_forbidden_values(envelope)
    if envelope.get("ok") is not True:
        raw_error = envelope.get("error")
        error: Mapping[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        code = str(error.get("code") or "cli_error")
        message = str(error.get("message") or "Hermes stock command failed")
        error_data = None
        raw_data = envelope.get("data")
        if include_error_data and isinstance(raw_data, dict):
            error_data = _allowlisted_data(command, raw_data, settings=settings)
        raise StockRuntimeError(code, message, retryable=bool(envelope.get("retryable")), data=error_data)
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise StockRuntimeError("contract_mismatch", "Hermes stock command data must be an object")
    return _allowlisted_data(command, data, settings=settings)


def _subprocess_runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_seconds: float) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _validate_envelope(envelope: Mapping[str, Any], command: str, returncode: int) -> None:
    if envelope.get("contract_version") != EXPECTED_CONTRACT_VERSION:
        raise StockRuntimeError("contract_mismatch", "Hermes stock contract version did not match the adapter")
    if envelope.get("command") != command:
        raise StockRuntimeError("contract_mismatch", "Hermes stock envelope command did not match the requested command")
    if not isinstance(envelope.get("exit_code"), int) or envelope["exit_code"] != returncode:
        raise StockRuntimeError("contract_mismatch", "Hermes stock process exit code did not match the JSON envelope")
    ok = envelope.get("ok")
    if returncode == 0 and ok is not True:
        raise StockRuntimeError("contract_mismatch", "Hermes stock success exit returned a failing envelope")
    if returncode != 0 and ok is not False:
        raise StockRuntimeError("contract_mismatch", "Hermes stock failure exit returned a successful envelope")


def _allowlisted_data(command: str, data: Mapping[str, Any], *, settings: StockRuntimeSettings | None = None) -> dict[str, Any]:
    if command == "content-pipeline":
        return _allowlisted_content_pipeline_data(data, settings=settings)
    fields = COMMAND_DATA_ALLOWLIST.get(command)
    if fields is None:
        raise StockRuntimeError("contract_mismatch", "Hermes stock command is not allowlisted")
    return {field: data[field] for field in fields if field in data}


def _allowlisted_content_pipeline_data(data: Mapping[str, Any], *, settings: StockRuntimeSettings | None = None) -> dict[str, Any]:
    if data.get("schema_version") != PIPELINE_SCHEMA_VERSION:
        raise StockRuntimeError("contract_mismatch", "content pipeline receipt schema did not match")
    run_id = _content_run_id(data.get("run_id"))
    request_digest = data.get("request_digest")
    if not isinstance(request_digest, str) or not HEX_DIGEST_RE.fullmatch(request_digest):
        raise StockRuntimeError("contract_mismatch", "content pipeline receipt digest is invalid")
    status = data.get("status")
    if status not in {"success", "partial", "no_op", "blocked"}:
        raise StockRuntimeError("contract_mismatch", "content pipeline receipt status is invalid")
    copy_mode = data.get("copy_mode")
    if copy_mode not in COPY_MODES:
        raise StockRuntimeError("contract_mismatch", "content pipeline receipt copy_mode is invalid")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise StockRuntimeError("contract_mismatch", "content pipeline receipt items must be a list")
    max_items = settings.max_package_contents if settings is not None else CONTENT_PIPELINE_MAX_SCHEMA_ITEMS
    if len(raw_items) > max_items:
        raise StockRuntimeError("content_receipt_too_large", "content pipeline receipt contains too many items")
    items = [_allowlisted_content_pipeline_item(item, run_id) for item in raw_items]
    error_code = _optional_content_error_code(data.get("error_code"))
    projected = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "request_digest": request_digest,
        "status": status,
        "copy_mode": copy_mode,
        "summary": _content_pipeline_summary(data.get("summary")),
        "items": items,
        "error_code": error_code,
        "artifact_refs": _content_artifact_refs(data.get("artifact_refs"), run_id, item_prefix=None),
        "replayed": _content_replayed(data.get("replayed")),
    }
    _assert_no_forbidden_values(projected)
    return projected


def _allowlisted_content_pipeline_item(value: Any, run_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StockRuntimeError("contract_mismatch", "content pipeline item must be an object")
    content_id = value.get("content_id")
    if not isinstance(content_id, str) or not SAFE_ID_RE.fullmatch(content_id):
        raise StockRuntimeError("contract_mismatch", "content pipeline item content_id is invalid")
    stage = value.get("stage")
    status = value.get("status")
    if stage not in {"completed", "blocked"} or status not in {"completed", "blocked"} or stage != status:
        raise StockRuntimeError("contract_mismatch", "content pipeline item status is invalid")
    return {
        "content_id": content_id,
        "stage": stage,
        "status": status,
        "artifact_refs": _content_artifact_refs(value.get("artifact_refs"), run_id, item_prefix=content_id),
        "media_sha256": _optional_hash(value.get("media_sha256"), "media_sha256"),
        "transcript_original_sha256": _optional_hash(value.get("transcript_original_sha256"), "transcript_original_sha256"),
        "error_code": _optional_content_error_code(value.get("error_code")),
    }


def _content_pipeline_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise StockRuntimeError("contract_mismatch", "content pipeline summary must be an object")
    projected: dict[str, int] = {}
    for field in ("selected", "completed", "blocked"):
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > 1_000_000:
            raise StockRuntimeError("contract_mismatch", "content pipeline summary count is invalid")
        projected[field] = raw
    return projected


def _content_artifact_refs(value: Any, run_id: str, *, item_prefix: str | None) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise StockRuntimeError("contract_mismatch", "content pipeline artifact_refs are invalid")
    refs: list[str] = []
    for ref in value:
        if not isinstance(ref, str) or not CONTENT_FILE_REF_RE.fullmatch(ref):
            raise StockRuntimeError("contract_mismatch", "content pipeline artifact ref is invalid")
        rel = _relative_file_ref(ref, "content pipeline artifact ref")
        if not rel.parts or rel.parts[0] != run_id:
            raise StockRuntimeError("ref_out_of_bounds", "content pipeline artifact ref must be scoped to the content run")
        if item_prefix is not None and (len(rel.parts) < 2 or rel.parts[1] != item_prefix):
            raise StockRuntimeError("ref_out_of_bounds", "content pipeline item artifact ref must be scoped to the content item")
        refs.append(ref)
    if len(set(refs)) != len(refs):
        raise StockRuntimeError("contract_mismatch", "content pipeline artifact refs must be unique")
    return refs


def _content_run_id(value: Any) -> str:
    if not isinstance(value, str) or not CONTENT_RUN_ID_RE.fullmatch(value):
        raise StockRuntimeError("contract_mismatch", "content pipeline run_id is invalid")
    return value


def _optional_hash(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise StockRuntimeError("contract_mismatch", f"content pipeline {field} is invalid")
    return value


def _optional_content_error_code(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not CONTENT_ERROR_CODE_RE.fullmatch(value):
        raise StockRuntimeError("contract_mismatch", "content pipeline error_code is invalid")
    return value


def _content_replayed(value: Any) -> bool:
    if not isinstance(value, bool):
        raise StockRuntimeError("contract_mismatch", "content pipeline replay flag is invalid")
    return value


def _load_package(settings: StockRuntimeSettings, package_ref: str) -> dict[str, Any]:
    run_id = _run_id_from_ref(package_ref)
    payload = _read_ref_bytes(settings, package_ref, settings.package_cap_bytes, run_id=run_id)
    try:
        package = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StockRuntimeError("invalid_json", "analysis package must be valid JSON") from exc
    if not isinstance(package, dict):
        raise StockRuntimeError("package_invalid", "analysis package must be an object")
    try:
        validate_handoff_package(package)
    except HandoffPackageError as exc:
        raise StockRuntimeError("package_invalid", str(exc)) from exc
    if str(package["run_id"]) != run_id:
        raise StockRuntimeError("package_invalid", "analysis package run_id must match its run-scoped ref")
    _validate_package_content_refs(package, settings.resolved_storage_root(), run_id)
    return package


def _read_ref_bytes(settings: StockRuntimeSettings, ref: str, cap: int, *, run_id: str | None = None) -> bytes:
    path = _resolve_file_ref(ref, settings.resolved_storage_root(), run_id=run_id)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StockRuntimeError("ref_not_readable", "storage ref is not readable") from exc
    if size > cap:
        raise StockRuntimeError("ref_too_large", "storage ref exceeds the adapter size cap")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StockRuntimeError("ref_not_readable", "storage ref is not readable") from exc


def _resolve_file_ref(ref: str, storage_root: Path, *, run_id: str | None = None) -> Path:
    if not ref.startswith("file:"):
        raise StockRuntimeError("ref_invalid", "storage ref must be a file: ref")
    rel = Path(ref.removeprefix("file:"))
    if rel.is_absolute() or ".." in rel.parts:
        raise StockRuntimeError("ref_out_of_bounds", "storage ref must stay under the storage root")
    if run_id is not None:
        _validate_run_id(run_id)
        if not rel.parts or rel.parts[0] != run_id:
            raise StockRuntimeError("ref_out_of_bounds", "storage ref must be scoped to the requested run")
    root = storage_root.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise StockRuntimeError("ref_out_of_bounds", "storage ref must stay under the storage root")
    return path


def _validate_package_content_refs(package: Mapping[str, Any], storage_root: Path, run_id: str) -> None:
    root = storage_root.resolve()
    for item in package["contents"]:
        transcript_ref = str(item.get("transcript_artifact_ref") or "")
        if not transcript_ref:
            continue
        rel = _relative_file_ref(transcript_ref, "transcript artifact ref")
        if run_id not in rel.parts:
            raise StockRuntimeError("ref_out_of_bounds", "transcript artifact ref must be scoped to the package run")
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise StockRuntimeError("ref_out_of_bounds", "transcript artifact ref must stay under the storage root")


def _relative_file_ref(ref: str, label: str) -> Path:
    if not ref.startswith("file:"):
        raise StockRuntimeError("ref_invalid", f"{label} must be a file: ref")
    rel = Path(ref.removeprefix("file:"))
    if rel.is_absolute() or ".." in rel.parts:
        raise StockRuntimeError("ref_out_of_bounds", f"{label} must stay under the storage root")
    return rel


def _run_id_from_ref(ref: str) -> str:
    if not ref.startswith("file:"):
        raise StockRuntimeError("ref_invalid", "analysis package ref must be a file: ref")
    rel = Path(ref.removeprefix("file:"))
    parts = rel.parts
    if len(parts) != 3 or parts[1] != "artifacts" or parts[2] != "analysis_package.json":
        raise StockRuntimeError("ref_invalid", "analysis package ref must be run-scoped")
    run_id = parts[0]
    _validate_run_id(run_id)
    return run_id


def _package_content(package: Mapping[str, Any], content_id: str) -> Mapping[str, Any]:
    _validate_safe_id(content_id, "content_id")
    for item in package["contents"]:
        if item["content_id"] == content_id:
            return item
    raise StockRuntimeError("analysis_result_invalid", "content_id is not present in the analysis package")


def _write_once_or_replay(settings: StockRuntimeSettings, ref: str, payload: bytes) -> str:
    run_id = _run_id_from_any_file_ref(ref)
    path = _resolve_file_ref(ref, settings.resolved_storage_root(), run_id=run_id)
    digest = _sha256(payload)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise StockRuntimeError("result_ref_conflict", "existing artifact is not readable") from exc
        if existing != payload:
            raise StockRuntimeError("result_ref_conflict", "existing artifact differs from the exact replay payload")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise StockRuntimeError("artifact_write_failed", "analysis artifact could not be written") from exc
    return digest


def _run_id_from_any_file_ref(ref: str) -> str:
    if not ref.startswith("file:"):
        raise StockRuntimeError("ref_invalid", "storage ref must be a file: ref")
    rel = Path(ref.removeprefix("file:"))
    if not rel.parts:
        raise StockRuntimeError("ref_invalid", "storage ref must be run-scoped")
    run_id = rel.parts[0]
    _validate_run_id(run_id)
    return run_id


def _canonical_json_bytes(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_artifact_leaf(content_id: str) -> str:
    if SAFE_LEAF_RE.fullmatch(content_id):
        return content_id
    return "content_" + hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:24]


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_RE.fullmatch(run_id):
        raise StockRuntimeError("run_id_invalid", "run_id must be a Hermes stock run ref")


def _validate_safe_id(value: str, label: str) -> None:
    if not SAFE_ID_RE.fullmatch(value):
        raise StockRuntimeError("ref_invalid", f"{label} must be a bounded opaque id")


def _validate_opaque_ref(value: str, label: str) -> None:
    if ":" not in value or not OPAQUE_REF_RE.fullmatch(value):
        raise StockRuntimeError("feedback_invalid", f"feedback {label} must be an opaque ref")


def _bounded_string(value: str, cap: int) -> str:
    if len(value) > cap:
        raise StockRuntimeError("field_too_large", "analysis package field exceeds the adapter cap")
    return value


def _assert_no_forbidden_values(value: Any) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _assert_no_forbidden_values(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_values(child)
    elif isinstance(value, str):
        _assert_text_safe(value)


def _assert_text_safe(value: str) -> None:
    if ABSOLUTE_LOCAL_PATH_RE.search(value) or FILE_ABSOLUTE_LOCAL_PATH_RE.search(value) or LOCAL_ENDPOINT_RE.search(value) or SECRET_VALUE_RE.search(value):
        raise StockRuntimeError("secret_like_output", "adapter output contained a forbidden local path, endpoint, or secret-like value")
    if len(value) > 32_000:
        raise StockRuntimeError("field_too_large", "adapter output field exceeded the safety cap")


def _safe_message(message: str) -> str:
    if not message or ABSOLUTE_LOCAL_PATH_RE.search(message) or FILE_ABSOLUTE_LOCAL_PATH_RE.search(message) or LOCAL_ENDPOINT_RE.search(message) or SECRET_VALUE_RE.search(message):
        return "stock_runtime failed closed"
    return message[:240]


def _config_executable(value: Any) -> tuple[str, ...]:
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    if isinstance(value, str) and value and not any(ch.isspace() for ch in value):
        return (value,)
    raise StockRuntimeError("stock_runtime_config_invalid", "stock_runtime.executable must be a non-empty argv list or single executable")


def assert_tool_result_is_safe(result: Mapping[str, Any]) -> None:
    """Testing helper used by the distribution self-check."""
    _assert_no_forbidden_values(result)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if "stdout" in encoded or "stderr" in encoded or "command" in encoded:
        raise StockRuntimeError("secret_like_output", "tool result exposed raw process details")
    if HASH_RE.search(encoded) is None and result.get("ok") is True:
        # Most successful stock_runtime paths carry an audit hash. This is not
        # a security gate for validate/health, so keep it diagnostic only.
        return
