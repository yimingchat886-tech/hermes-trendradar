from __future__ import annotations

import contextlib
import fcntl
import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_benchmark.collector_distribution.scripts import hermes_benchmark_handoff as runner

PROFILE_HASH = "sha256:" + "a" * 64
RUN_ID = "run_20260825_success"


def _envelope(command: str, *, ok: bool = True, exit_code: int = 0, data: dict[str, Any] | None = None, error_code: str | None = None, retryable: bool = False) -> bytes:
    return json.dumps(
        {
            "contract_version": "2.0",
            "ok": ok,
            "command": command,
            "mode": "runtime",
            "data": data if data is not None else {},
            "error": None if error_code is None else {"code": error_code, "message": "raw details must stay hidden"},
            "exit_code": exit_code,
            "retryable": retryable,
            "unknown_top_level": "drop-me",
        },
        sort_keys=True,
    ).encode()


def _ok_validate(extra: dict[str, Any] | None = None) -> bytes:
    data = {"profile_hash": PROFILE_HASH, "schema_version": "1.4", "unknown": "drop-me"}
    if extra:
        data.update(extra)
    return _envelope("validate-config", data=data)


def _ok_health(extra: dict[str, Any] | None = None) -> bytes:
    data = {"run_eligible": True, "runtime_effective_status": "ready", "unknown": "drop-me"}
    if extra:
        data.update(extra)
    return _envelope("healthcheck", data=data)


def _ok_run(status: str = "success", extra: dict[str, Any] | None = None) -> bytes:
    data: dict[str, Any] = {
        "run_id": RUN_ID,
        "status": status,
        "analysis_mode": "hermes-handoff",
        "account_summary": {"attempted": 10, "unknown": "drop-me"},
        "content_summary": {"collected": 3},
        "transcript_summary": {"done": 3},
        "analysis_package_ref": f"file:{RUN_ID}/artifacts/analysis_package.json",
        "unknown": "drop-me",
    }
    if extra:
        data.update(extra)
    return _envelope("run-daily", data=data)


def _invoke(argv: list[str], command_runner, tmp_path: Path, clock=None) -> tuple[int, dict[str, Any], str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = runner.main(
            argv,
            command_runner=command_runner,
            clock=clock or (lambda: datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)),
            sleeper=lambda _seconds: None,
            lock_dir=tmp_path,
            stdout=stdout,
        )
    raw = stdout.getvalue()
    return code, json.loads(raw), stderr.getvalue()


def test_success_receipt_uses_fixed_sequence_denver_date_and_allowlist(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path, float]] = []

    def fake(argv: list[str], cwd: Path, timeout_seconds: float) -> tuple[int, bytes, bytes]:
        calls.append((argv, cwd, timeout_seconds))
        command = argv[1]
        if command == "validate-config":
            return 0, _ok_validate(), b""
        if command == "healthcheck":
            return 0, _ok_health(), b""
        if command == "run-daily":
            return 0, _ok_run(extra={"secret_extra": "token=hidden", "path_extra": "/home/jym/hidden"}), b""
        raise AssertionError(command)

    code, receipt, stderr = _invoke([], fake, tmp_path, clock=lambda: datetime(2026, 8, 26, 5, 30, tzinfo=timezone.utc))

    assert code == 0
    assert stderr == ""
    assert [call[0] for call in calls] == [
        [runner.EXECUTABLE, "validate-config", "--profile", runner.PROFILE, "--json"],
        [runner.EXECUTABLE, "healthcheck", "--profile", runner.PROFILE, "--json"],
        [runner.EXECUTABLE, "run-daily", "--profile", runner.PROFILE, "--date", "2026-08-25", "--analysis-mode", "hermes-handoff", "--json"],
    ]
    assert {call[1] for call in calls} == {runner.CWD}
    assert all(call[2] == runner.TIMEOUT_SECONDS for call in calls)
    assert receipt == {
        "contract_version": "2.0",
        "command": "run-daily",
        "ok": True,
        "exit_code": 0,
        "retryable": False,
        "data": {
            "run_id": RUN_ID,
            "status": "success",
            "analysis_mode": "hermes-handoff",
            "account_summary": {"attempted": 10},
            "content_summary": {"collected": 3},
            "transcript_summary": {"done": 3},
            "analysis_package_ref": f"file:{RUN_ID}/artifacts/analysis_package.json",
        },
        "tracking_status": "blocked",
        "blocking_reason": "multi_account_collection_not_wired",
        "cron_status": "prepared_not_activated",
    }
    encoded = json.dumps(receipt, sort_keys=True)
    assert "unknown" not in encoded
    assert "secret_extra" not in encoded
    assert "path_extra" not in encoded
    assert runner.PROFILE not in encoded
    assert str(runner.CWD) not in encoded


def test_noop_status_is_projected_without_artifact_creation(tmp_path: Path) -> None:
    commands: list[str] = []

    def fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        commands.append(argv[1])
        if argv[1] == "validate-config":
            return 0, _ok_validate(), b""
        if argv[1] == "healthcheck":
            return 0, _ok_health(), b""
        if argv[1] == "run-daily":
            return 0, _ok_run(status="noop", extra={"analysis_package_ref": ""}), b""
        raise AssertionError(argv)

    code, receipt, _stderr = _invoke([], fake, tmp_path)

    assert code == 0
    assert commands == ["validate-config", "healthcheck", "run-daily"]
    assert receipt["data"]["status"] == "noop"
    assert "analysis_package_ref" not in receipt["data"]


def test_check_only_runs_validate_and_health_never_run_daily_or_lock(tmp_path: Path) -> None:
    commands: list[str] = []

    def fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        commands.append(argv[1])
        if argv[1] == "validate-config":
            return 0, _ok_validate(), b""
        if argv[1] == "healthcheck":
            return 0, _ok_health(), b""
        raise AssertionError("check-only must not call run-daily")

    code, receipt, stderr = _invoke(["--check-only"], fake, tmp_path)

    assert code == 0
    assert stderr == ""
    assert commands == ["validate-config", "healthcheck"]
    assert receipt["command"] == "healthcheck"
    assert receipt["data"] == {}
    assert not any(tmp_path.iterdir())


def test_retry_is_fixed_300_max2_and_only_for_retryable_envelope(tmp_path: Path) -> None:
    attempts: list[str] = []
    sleeps: list[int] = []
    stdout = io.StringIO()

    def fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        command = argv[1]
        attempts.append(command)
        if command == "validate-config":
            return 0, _ok_validate(), b""
        if command == "healthcheck":
            return 0, _ok_health(), b""
        if command == "run-daily" and attempts.count("run-daily") == 1:
            return 4, _envelope("run-daily", ok=False, exit_code=4, error_code="collection_failed", retryable=True), b""
        return 0, _ok_run(), b""

    code = runner.main(
        [],
        command_runner=fake,
        clock=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        sleeper=lambda seconds: sleeps.append(seconds),
        lock_dir=tmp_path,
        stdout=stdout,
    )
    receipt = json.loads(stdout.getvalue())

    assert code == 0
    assert attempts == ["validate-config", "healthcheck", "run-daily", "run-daily"]
    assert sleeps == [300]
    assert receipt["ok"] is True


def test_nonretryable_failure_and_run_lock_conflict_do_not_retry_or_run_daily(tmp_path: Path) -> None:
    commands: list[str] = []

    def nonretry_fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        commands.append(argv[1])
        if argv[1] == "validate-config":
            return 0, _ok_validate(), b""
        if argv[1] == "healthcheck":
            return 0, _ok_health(), b""
        return 4, _envelope("run-daily", ok=False, exit_code=4, error_code="collection_failed", retryable=False), b""

    code, receipt, _stderr = _invoke([], nonretry_fake, tmp_path)
    assert code == 4
    assert commands == ["validate-config", "healthcheck", "run-daily"]
    assert receipt["error"] == {"code": "collection_failed"}
    assert receipt["retryable"] is False

    run_date = "2026-08-25"
    lock_path = runner._lock_path(tmp_path, run_date, PROFILE_HASH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_commands: list[str] = []

        def lock_fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
            lock_commands.append(argv[1])
            if argv[1] == "validate-config":
                return 0, _ok_validate(), b""
            raise AssertionError("lock conflict must stop before healthcheck and run-daily")

        code, receipt, _stderr = _invoke([], lock_fake, tmp_path)
        assert code == 9
        assert lock_commands == ["validate-config"]
        assert receipt["error"] == {"code": "run_lock_conflict"}
        assert receipt["retryable"] is False


def test_invalid_json_oversize_timeout_and_exit_mismatch_fail_closed(tmp_path: Path) -> None:
    cases = [
        (lambda _argv, _cwd, _timeout: (0, b"not-json", b""), "invalid_json", 2),
        (lambda _argv, _cwd, _timeout: (0, b"{" + b"x" * (runner.STDOUT_CAP_BYTES + 1), b""), "output_too_large", 2),
        (lambda _argv, _cwd, _timeout: (_raise_timeout(), b"", b""), "command_timeout", 3),
        (lambda _argv, _cwd, _timeout: (1, _envelope("validate-config", ok=True, exit_code=0), b""), "contract_mismatch", 2),
    ]
    for fake, code_name, exit_code in cases:
        code, receipt, stderr = _invoke([], fake, tmp_path)
        assert code == exit_code
        assert stderr == ""
        assert receipt["ok"] is False
        assert receipt["error"] == {"code": code_name}
        encoded = json.dumps(receipt, sort_keys=True)
        assert "not-json" not in encoded
        assert "stdout" not in encoded
        assert "stderr" not in encoded


def _raise_timeout() -> tuple[int, bytes, bytes]:
    raise subprocess.TimeoutExpired(cmd=[runner.EXECUTABLE], timeout=runner.TIMEOUT_SECONDS)


def test_allowed_field_secret_or_path_leaks_fail_closed_without_leaking_value(tmp_path: Path) -> None:
    bad_payloads = [
        _ok_run(extra={"analysis_package_ref": "/home/jym/secret-package.json"}),
        _ok_run(extra={"account_summary": {"source_url": "https://www.douyin.com/video/123"}}),
        _ok_run(extra={"content_summary": {"cookie": "token=abcdef123456"}}),
    ]
    for payload in bad_payloads:
        commands: list[str] = []

        def fake(argv: list[str], _cwd: Path, _timeout_seconds: float, payload: bytes = payload) -> tuple[int, bytes, bytes]:
            commands.append(argv[1])
            if argv[1] == "validate-config":
                return 0, _ok_validate(), b""
            if argv[1] == "healthcheck":
                return 0, _ok_health(), b""
            return 0, payload, b""

        code, receipt, _stderr = _invoke([], fake, tmp_path)
        assert code == 2
        assert receipt["error"] == {"code": "secret_like_output"}
        encoded = json.dumps(receipt, sort_keys=True).lower()
        assert "/home/" not in encoded
        assert "douyin.com" not in encoded
        assert "token" not in encoded


def test_manifest_policy_and_max_attempts_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake(argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        calls.append(argv)
        return 0, _ok_validate(), b""

    invalid_manifests = [
        {**runner.MANIFEST, "retry_policy": "exponential:300"},
        {**runner.MANIFEST, "retry_policy": "fixed:0"},
        {**runner.MANIFEST, "max_attempts": 3},
        {**runner.MANIFEST, "retryable_exit_codes": ["4"]},
    ]
    for manifest in invalid_manifests:
        monkeypatch.setattr(runner, "MANIFEST", manifest)
        code, receipt, _stderr = _invoke([], fake, tmp_path)
        assert code == 2
        assert receipt["error"] == {"code": "manifest_invalid"}
    assert calls == []


def test_default_subprocess_runner_uses_shell_false_and_rejects_runtime_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        seen["argv"] = argv
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=_ok_validate(), stderr=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    code, receipt, _stderr = _invoke(["--profile", "/tmp/evil.json"], lambda *_args: (_ for _ in ()).throw(AssertionError("must reject overrides")), tmp_path)
    assert code == 2
    assert receipt["error"] == {"code": "invalid_arguments"}

    result = runner._run_subprocess([runner.EXECUTABLE, "validate-config", "--profile", runner.PROFILE, "--json"], runner.CWD, runner.TIMEOUT_SECONDS)
    assert result == (0, _ok_validate(), b"")
    assert seen["argv"] == [runner.EXECUTABLE, "validate-config", "--profile", runner.PROFILE, "--json"]
    assert seen["cwd"] == runner.CWD
    assert seen["capture_output"] is True
    assert seen["timeout"] == runner.TIMEOUT_SECONDS
    assert seen["shell"] is False
    assert seen["check"] is False
    assert "env" not in seen


def test_wrapper_exception_returns_fixed_code_only(tmp_path: Path) -> None:
    def fake(_argv: list[str], _cwd: Path, _timeout_seconds: float) -> tuple[int, bytes, bytes]:
        raise RuntimeError("/home/jym/path Cookie=secret")

    code, receipt, _stderr = _invoke([], fake, tmp_path)

    assert code == 2
    assert receipt["error"] == {"code": "runner_internal_error"}
    encoded = json.dumps(receipt, sort_keys=True).lower()
    assert "/home/" not in encoded
    assert "cookie" not in encoded
