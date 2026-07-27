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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .handoff import HandoffPackageError, validate_handoff_package
from .profile import load_profile
from .runtime_cdp import resolve_runtime_config

CliRunner = Callable[[Sequence[str], Path, Mapping[str, str], float], tuple[int, bytes, bytes]]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SAFE_LEAF_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9._-]{8,80}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9._:/#-]{1,160}$")
REASON_CODE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
ABSOLUTE_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/._-])(?:/(?:tmp|var/tmp|home|mnt|Users)/|[A-Za-z]:\\\\)")
LOCAL_ENDPOINT_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:authorization|bearer|cookie|password|secret|session|token)\b\s*[:=]\s*\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}))"
)

JSON_OBJECT_TYPE = {"type": "object", "additionalProperties": True}
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

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(_safe_message(message))


@dataclass(frozen=True)
class StockRuntimeSettings:
    executable: tuple[str, ...] = ("hermes-benchmark",)
    cwd: Path = Path(".")
    profile_ref: Path = Path("profiles/local/hermes.v1.4.douyin.local.json")
    storage_root: Path | None = None
    timeout_seconds: float = 60.0
    stdout_cap_bytes: int = 64 * 1024
    stderr_cap_bytes: int = 16 * 1024
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
    storage_value = raw.get("storage_root")
    return StockRuntimeSettings(
        executable=executable,
        cwd=cwd,
        profile_ref=Path(str(profile_value)).expanduser(),
        storage_root=Path(str(storage_value)).expanduser() if storage_value else None,
        timeout_seconds=float(raw.get("timeout_seconds") or 60),
        stdout_cap_bytes=int(raw.get("stdout_cap_bytes") or 64 * 1024),
        stderr_cap_bytes=int(raw.get("stderr_cap_bytes") or 16 * 1024),
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
        return _call_cli(settings, "run-daily", ("--date", run_date, "--analysis-mode", "hermes-handoff"))

    return _run_tool("stock_run_daily", action)


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


def _run_tool(tool: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        data = action()
        _assert_no_forbidden_values(data)
        return {"ok": True, "tool": tool, "data": data, "error": None, "retryable": False}
    except StockRuntimeError as exc:
        return {"ok": False, "tool": tool, "data": None, "error": {"code": exc.code, "message": str(exc)}, "retryable": exc.retryable}


def _call_cli(settings: StockRuntimeSettings, command: str, extra_args: Sequence[str]) -> dict[str, Any]:
    if not settings.executable:
        raise StockRuntimeError("stock_runtime_config_invalid", "stock runtime executable is not configured")
    cwd = settings.cwd.resolve()
    if not cwd.exists() or not cwd.is_dir():
        raise StockRuntimeError("stock_runtime_config_invalid", "stock runtime cwd is not available")
    argv = [*settings.executable, command, "--profile", str(settings.profile_ref), *extra_args, "--json"]
    env = {name: os.environ[name] for name in settings.env_allowlist if name in os.environ}
    runner = settings.runner or _subprocess_runner
    try:
        returncode, stdout, stderr = runner(tuple(str(item) for item in argv), cwd, env, settings.timeout_seconds)
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
        raise StockRuntimeError(code, message, retryable=bool(envelope.get("retryable")))
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise StockRuntimeError("contract_mismatch", "Hermes stock command data must be an object")
    return _allowlisted_data(command, data)


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
    if envelope.get("command") != command:
        raise StockRuntimeError("contract_mismatch", "Hermes stock envelope command did not match the requested command")
    if not isinstance(envelope.get("exit_code"), int) or envelope["exit_code"] != returncode:
        raise StockRuntimeError("contract_mismatch", "Hermes stock process exit code did not match the JSON envelope")
    ok = envelope.get("ok")
    if returncode == 0 and ok is not True:
        raise StockRuntimeError("contract_mismatch", "Hermes stock success exit returned a failing envelope")
    if returncode != 0 and ok is not False:
        raise StockRuntimeError("contract_mismatch", "Hermes stock failure exit returned a successful envelope")


def _allowlisted_data(command: str, data: Mapping[str, Any]) -> dict[str, Any]:
    fields = COMMAND_DATA_ALLOWLIST.get(command)
    if fields is None:
        raise StockRuntimeError("contract_mismatch", "Hermes stock command is not allowlisted")
    return {field: data[field] for field in fields if field in data}


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
    if ABSOLUTE_LOCAL_PATH_RE.search(value) or LOCAL_ENDPOINT_RE.search(value) or SECRET_VALUE_RE.search(value):
        raise StockRuntimeError("secret_like_output", "adapter output contained a forbidden local path, endpoint, or secret-like value")
    if len(value) > 32_000:
        raise StockRuntimeError("field_too_large", "adapter output field exceeded the safety cap")


def _safe_message(message: str) -> str:
    if not message or ABSOLUTE_LOCAL_PATH_RE.search(message) or LOCAL_ENDPOINT_RE.search(message) or SECRET_VALUE_RE.search(message):
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
