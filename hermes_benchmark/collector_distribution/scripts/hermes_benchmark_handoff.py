#!/usr/bin/env python3
"""Deterministic no-agent Hermes benchmark cron handoff runner.

Cron owns only the schedule. This wrapper owns the fixed command sequence,
fail-closed retry rules, and the compact public receipt printed to stdout.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import fcntl

CONTRACT_VERSION = "2.0"
EXECUTABLE = "/home/jym/.local/bin/hermes-benchmark"
PROFILE = "/home/jym/workspace/Hermes trendradar/profiles/local/hermes.v1.4.douyin.local.json"
CWD = Path("/home/jym/workspace/Hermes trendradar")
TIMEZONE = "America/Denver"
TIMEOUT_SECONDS = 60.0
STDOUT_CAP_BYTES = 64 * 1024
STDERR_CAP_BYTES = 16 * 1024
PROFILE_CAP_BYTES = 256 * 1024
EXIT_CONTRACT = 2
EXIT_RUNTIME = 3
EXIT_RUN_LOCK_CONFLICT = 9
AUTHORIZED_RETRY_POLICY = "fixed:300"
RECEIPT_STATUS = {
    "tracking_status": "blocked",
    "blocking_reason": "multi_account_collection_not_wired",
    "cron_status": "prepared_not_activated",
}
RUN_DATA_ALLOWLIST = (
    "run_id",
    "status",
    "analysis_mode",
    "account_summary",
    "content_summary",
    "transcript_summary",
    "analysis_package_ref",
)
SUMMARY_ALLOWLIST = {
    "account_summary": {"attempted", "succeeded", "failed", "skipped", "total"},
    "content_summary": {"collected", "upserted", "deduped", "failed", "total"},
    "transcript_summary": {"queued", "done", "failed", "blocked", "total"},
}
PROFILE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ABSOLUTE_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/._-])(?:/(?:tmp|var/tmp|home|mnt|Users)/|[A-Za-z]:\\)")
URL_OR_ENDPOINT_RE = re.compile(
    r"(?i)(?:https?://|\b(?:localhost|127\.0\.0\.1)\b|\b(?:douyin|xiaohongshu|twitter|x)\.com\b|\bproxy\b|\bcdp\b)"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:authorization|bearer|cookie|password|secret|session|token)\b\s*[:=]?\s*\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}))"
)
SAFE_BACKOFF_REF_RE = re.compile(r"^env:[A-Z0-9_]{1,128}$")
ERROR_CODE_ALLOWLIST = {
    "collection_failed",
    "command_failed",
    "command_timeout",
    "config_invalid",
    "contract_mismatch",
    "field_too_large",
    "handoff_package_invalid",
    "invalid_arguments",
    "invalid_json",
    "manifest_invalid",
    "manifest_oversize",
    "manifest_unavailable",
    "output_too_large",
    "run_lock_conflict",
    "runner_internal_error",
    "secret_like_output",
    "unexpected_stderr",
    "upstream_error_code_rejected",
}
RECEIPT_COMMAND_ALLOWLIST = {"runner", "validate-config", "healthcheck", "run-daily"}
CommandRunner = Callable[[list[str], Path, float], tuple[int, bytes, bytes]]
Clock = Callable[[], datetime]
Sleeper = Callable[[int], None]
ManifestLoader = Callable[[], Mapping[str, Any]]


class RunnerError(Exception):
    """Fail-closed public error. The message is intentionally never printed."""

    def __init__(self, code: str, *, exit_code: int = EXIT_CONTRACT, command: str = "runner", retryable: bool = False):
        self.code = code
        self.exit_code = exit_code
        self.command = command
        self.retryable = retryable
        super().__init__(code)


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RunnerError("run_lock_conflict", exit_code=EXIT_RUN_LOCK_CONFLICT, command="run-daily") from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()


def main(
    argv: Sequence[str] | None = None,
    *,
    command_runner: CommandRunner | None = None,
    manifest_loader: ManifestLoader | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    lock_dir: Path | None = None,
    stdout: Any | None = None,
) -> int:
    """Run the deterministic handoff sequence and print one compact receipt."""
    out = stdout if stdout is not None else sys.stdout
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        check_only = _parse_args(args)
        retry = _validate_manifest((manifest_loader or _load_manifest_retry)())
        run_date = _run_date(clock or _default_clock)
        command_runner = command_runner or _run_subprocess
        sleeper = sleeper or time.sleep
        lock_dir = Path("/tmp/hermes-benchmark-handoff-locks") if lock_dir is None else lock_dir

        validate = _run_command("validate-config", [], command_runner, retry, sleeper)
        profile_hash = _profile_hash(validate)
        if check_only:
            health = _run_command("healthcheck", [], command_runner, retry, sleeper)
            receipt = _receipt_from_envelope(health, data={})
        else:
            lock_path = _lock_path(lock_dir, run_date, profile_hash)
            with RunLock(lock_path):
                _run_command("healthcheck", [], command_runner, retry, sleeper)
                run = _run_command(
                    "run-daily",
                    ["--date", run_date, "--analysis-mode", "hermes-handoff"],
                    command_runner,
                    retry,
                    sleeper,
                )
            receipt = _receipt_from_envelope(run, data=_project_run_data(run.get("data")))
    except RunnerError as exc:
        receipt = _error_receipt(exc.command, exc.code, exc.exit_code, retryable=exc.retryable)
    except Exception:
        receipt = _error_receipt("runner", "runner_internal_error", EXIT_CONTRACT, retryable=False)
    out.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return int(receipt["exit_code"])


def _parse_args(args: Sequence[str]) -> bool:
    if not args:
        return False
    if list(args) == ["--check-only"]:
        return True
    raise RunnerError("invalid_arguments")


def _default_clock() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def _run_date(clock: Clock) -> str:
    now = clock()
    zone = ZoneInfo(TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    return now.astimezone(zone).date().isoformat()


def _load_manifest_retry(profile_path: str | Path = PROFILE) -> dict[str, Any]:
    path = Path(profile_path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(PROFILE_CAP_BYTES + 1)
    except OSError as exc:
        raise RunnerError("manifest_unavailable") from exc
    if len(raw) > PROFILE_CAP_BYTES:
        raise RunnerError("manifest_oversize")
    try:
        profile = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("manifest_invalid") from exc
    if not isinstance(profile, Mapping):
        raise RunnerError("manifest_invalid")
    retry = profile.get("retry")
    if not isinstance(retry, Mapping):
        raise RunnerError("manifest_invalid")
    return {
        "max_attempts": retry.get("max_attempts"),
        "retryable_exit_codes": retry.get("retryable_exit_codes"),
        "non_retryable_exit_codes": retry.get("non_retryable_exit_codes"),
        "backoff_policy_ref": retry.get("backoff_policy_ref"),
    }


def _validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    policy = AUTHORIZED_RETRY_POLICY
    if not isinstance(policy, str) or not policy.startswith("fixed:"):
        raise RunnerError("manifest_invalid")
    raw_delay = policy.removeprefix("fixed:")
    if not raw_delay.isdigit():
        raise RunnerError("manifest_invalid")
    delay = int(raw_delay)
    if not 1 <= delay <= 3600:
        raise RunnerError("manifest_invalid")
    backoff_policy_ref = manifest.get("backoff_policy_ref")
    if not isinstance(backoff_policy_ref, str) or not SAFE_BACKOFF_REF_RE.fullmatch(backoff_policy_ref):
        raise RunnerError("manifest_invalid")
    max_attempts = manifest.get("max_attempts")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
        raise RunnerError("manifest_invalid")
    retryable_exit_codes = _validate_exit_code_list(manifest.get("retryable_exit_codes"))
    non_retryable_exit_codes = _validate_exit_code_list(manifest.get("non_retryable_exit_codes"))
    if retryable_exit_codes & non_retryable_exit_codes:
        raise RunnerError("manifest_invalid")
    return {
        "delay_seconds": delay,
        "max_attempts": max_attempts,
        "retryable_exit_codes": retryable_exit_codes,
        "non_retryable_exit_codes": non_retryable_exit_codes,
        "backoff_policy_ref": backoff_policy_ref,
    }


def _validate_exit_code_list(raw_codes: Any) -> set[int]:
    if not isinstance(raw_codes, list) or not raw_codes:
        raise RunnerError("manifest_invalid")
    codes: list[int] = []
    for code in raw_codes:
        if not isinstance(code, int) or isinstance(code, bool) or not 1 <= code <= 255:
            raise RunnerError("manifest_invalid")
        codes.append(code)
    if len(set(codes)) != len(codes):
        raise RunnerError("manifest_invalid")
    return set(codes)


def _run_command(
    command: str,
    extra_args: Sequence[str],
    command_runner: CommandRunner,
    retry: Mapping[str, Any],
    sleeper: Sleeper,
) -> dict[str, Any]:
    attempt = 1
    while True:
        envelope = _invoke_command(command, extra_args, command_runner)
        if envelope.get("ok") is True:
            return envelope
        error_code = _error_code(envelope)
        retryable = bool(envelope.get("retryable"))
        exit_code = int(envelope["exit_code"])
        should_retry = (
            retryable
            and exit_code in retry["retryable_exit_codes"]
            and exit_code not in retry["non_retryable_exit_codes"]
            and error_code != "run_lock_conflict"
            and attempt < retry["max_attempts"]
        )
        if not should_retry:
            raise RunnerError(error_code, exit_code=exit_code, command=command, retryable=retryable and should_retry)
        sleeper(int(retry["delay_seconds"]))
        attempt += 1


def _invoke_command(command: str, extra_args: Sequence[str], command_runner: CommandRunner) -> dict[str, Any]:
    argv = [EXECUTABLE, command, "--profile", PROFILE, *extra_args, "--json"]
    try:
        returncode, raw_stdout, raw_stderr = command_runner(argv, CWD, TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RunnerError("command_timeout", exit_code=EXIT_RUNTIME, command=command) from exc
    if not isinstance(returncode, int) or not isinstance(raw_stdout, bytes) or not isinstance(raw_stderr, bytes):
        raise RunnerError("contract_mismatch", command=command)
    if len(raw_stdout) > STDOUT_CAP_BYTES or len(raw_stderr) > STDERR_CAP_BYTES:
        raise RunnerError("output_too_large", command=command)
    if raw_stderr.strip():
        raise RunnerError("unexpected_stderr", command=command)
    try:
        envelope = json.loads(raw_stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("invalid_json", command=command) from exc
    if not isinstance(envelope, dict):
        raise RunnerError("invalid_json", command=command)
    _validate_envelope(envelope, command, returncode)
    return envelope


def _run_subprocess(argv: list[str], cwd: Path, timeout_seconds: float) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _validate_envelope(envelope: Mapping[str, Any], command: str, returncode: int) -> None:
    if envelope.get("contract_version") != CONTRACT_VERSION:
        raise RunnerError("contract_mismatch", command=command)
    if envelope.get("command") != command:
        raise RunnerError("contract_mismatch", command=command)
    if not isinstance(envelope.get("ok"), bool):
        raise RunnerError("contract_mismatch", command=command)
    if not isinstance(envelope.get("retryable"), bool):
        raise RunnerError("contract_mismatch", command=command)
    if not isinstance(envelope.get("exit_code"), int) or isinstance(envelope.get("exit_code"), bool):
        raise RunnerError("contract_mismatch", command=command)
    if envelope["exit_code"] != returncode:
        raise RunnerError("contract_mismatch", command=command)
    if returncode == 0 and envelope["ok"] is not True:
        raise RunnerError("contract_mismatch", command=command)
    if returncode != 0 and envelope["ok"] is not False:
        raise RunnerError("contract_mismatch", command=command)
    data = envelope.get("data")
    if data is not None and not isinstance(data, dict):
        raise RunnerError("contract_mismatch", command=command)
    error = envelope.get("error")
    if envelope["ok"] is False:
        if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not error["code"]:
            raise RunnerError("contract_mismatch", command=command)
    elif error is not None:
        raise RunnerError("contract_mismatch", command=command)


def _profile_hash(validate_envelope: Mapping[str, Any]) -> str:
    data = validate_envelope.get("data")
    if not isinstance(data, dict):
        raise RunnerError("contract_mismatch", command="validate-config")
    profile_hash = data.get("profile_hash")
    if not isinstance(profile_hash, str) or not PROFILE_HASH_RE.fullmatch(profile_hash):
        raise RunnerError("contract_mismatch", command="validate-config")
    return profile_hash


def _lock_path(lock_dir: Path, run_date: str, profile_hash: str) -> Path:
    digest = hashlib.sha256(f"{run_date}\n{profile_hash}".encode("utf-8")).hexdigest()
    return lock_dir / f"hermes-benchmark-handoff-{digest}.lock"


def _receipt_from_envelope(envelope: Mapping[str, Any], *, data: Mapping[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "command": str(envelope["command"]),
        "ok": bool(envelope["ok"]),
        "exit_code": int(envelope["exit_code"]),
        "retryable": bool(envelope.get("retryable", False)),
    }
    if data or envelope["command"] == "healthcheck":
        receipt["data"] = dict(data)
    if envelope["ok"] is False:
        receipt["error"] = {"code": _error_code(envelope)}
    receipt.update(RECEIPT_STATUS)
    _assert_no_forbidden_values(receipt)
    return receipt


def _error_receipt(command: str, code: str, exit_code: int, *, retryable: bool) -> dict[str, Any]:
    receipt = {
        "contract_version": CONTRACT_VERSION,
        "command": _receipt_command(command),
        "ok": False,
        "exit_code": exit_code,
        "retryable": retryable,
        "error": {"code": _public_error_code(code)},
    }
    receipt.update(RECEIPT_STATUS)
    try:
        _assert_no_forbidden_values(receipt)
    except RunnerError:
        receipt["command"] = "runner"
        receipt["error"] = {"code": "secret_like_output"}
    except Exception:
        receipt["command"] = "runner"
        receipt["error"] = {"code": "runner_internal_error"}
    return receipt


def _error_code(envelope: Mapping[str, Any]) -> str:
    error = envelope.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str) and error["code"]:
        return _public_error_code(error["code"])
    return "command_failed"


def _public_error_code(code: Any) -> str:
    if not isinstance(code, str) or not code:
        return "command_failed"
    if code in ERROR_CODE_ALLOWLIST:
        return code
    try:
        _assert_text_safe(code)
    except Exception:
        return "secret_like_output"
    return "upstream_error_code_rejected"


def _receipt_command(command: str) -> str:
    if command in RECEIPT_COMMAND_ALLOWLIST:
        return command
    return "runner"


def _project_run_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise RunnerError("contract_mismatch", command="run-daily")
    projected: dict[str, Any] = {}
    for key in RUN_DATA_ALLOWLIST:
        if key not in data or data[key] in (None, ""):
            continue
        value = data[key]
        if key in SUMMARY_ALLOWLIST:
            if not isinstance(value, Mapping):
                raise RunnerError("contract_mismatch", command="run-daily")
            _assert_no_forbidden_values(value)
            value = {child_key: child_value for child_key, child_value in value.items() if child_key in SUMMARY_ALLOWLIST[key]}
            if not value:
                continue
        projected[key] = value
    _assert_no_forbidden_values(projected)
    return projected


def _assert_no_forbidden_values(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                _assert_text_safe(key)
            _assert_no_forbidden_values(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_values(child)
    elif isinstance(value, str):
        _assert_text_safe(value)


def _assert_text_safe(value: str) -> None:
    if ABSOLUTE_LOCAL_PATH_RE.search(value) or URL_OR_ENDPOINT_RE.search(value) or SECRET_VALUE_RE.search(value):
        raise RunnerError("secret_like_output")
    if len(value) > 32_000:
        raise RunnerError("field_too_large")


if __name__ == "__main__":
    raise SystemExit(main())
