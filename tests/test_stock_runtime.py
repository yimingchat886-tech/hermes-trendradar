from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.profile import load_profile
from hermes_benchmark.runtime_cdp import resolve_runtime_config
from hermes_benchmark.stock_runtime import (
    StockRuntimeSettings,
    stock_read_analysis_package,
    stock_record_analysis_result,
    stock_run_daily,
    stock_validate_config,
)
from hermes_benchmark.stock_runtime_self_check import (
    _seed_handoff_state,
    _write_fixture_profile,
    run_self_check,
)


def test_stock_runtime_uses_fixed_argv_and_minimal_env_without_raw_process_details() -> None:
    calls = []

    def runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], _timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((list(argv), cwd, dict(env)))
        envelope = {
            "contract_version": "2.0",
            "ok": True,
            "command": "validate-config",
            "mode": "profile",
            "data": {
                "schema_version": "1.4",
                "ok": True,
                "profile_id": "collector-fixture",
                "profile_hash": "sha256:" + "a" * 64,
                "enabled_account_count": 10,
                "required_enabled_accounts": 10,
                "validation": "ok",
            },
            "error": None,
            "exit_code": 0,
            "retryable": False,
        }
        return 0, json.dumps(envelope).encode(), b""

    with tempfile.TemporaryDirectory() as tmp:
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=Path(tmp),
            profile_ref=Path(tmp) / "profile.json",
            storage_root=Path(tmp) / "storage",
            env_allowlist=("PATH",),
            runner=runner,
        )
        result = stock_validate_config(settings)

    assert result["ok"] is True
    assert calls[0][0] == ["hermes-benchmark", "validate-config", "--profile", str(settings.profile_ref), "--json"]
    assert calls[0][1] == Path(tmp).resolve()
    assert set(calls[0][2]).issubset({"PATH"})
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "command" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded


def test_stock_runtime_fails_closed_on_invalid_json_exit_mismatch_oversize_and_secret_like_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def settings_with(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> StockRuntimeSettings:
            return StockRuntimeSettings(
                executable=("hermes-benchmark",),
                cwd=root,
                profile_ref=root / "profile.json",
                storage_root=root / "storage",
                stdout_cap_bytes=1024,
                stderr_cap_bytes=32,
                runner=lambda _argv, _cwd, _env, _timeout: (returncode, stdout, stderr),
            )

        assert stock_validate_config(settings_with(b"not-json"))["error"]["code"] == "invalid_json"

        mismatch = {
            "contract_version": "2.0",
            "ok": True,
            "command": "validate-config",
            "mode": "profile",
            "data": {},
            "error": None,
            "exit_code": 0,
            "retryable": False,
        }
        assert stock_validate_config(settings_with(json.dumps(mismatch).encode(), returncode=1))["error"]["code"] == "contract_mismatch"
        assert stock_validate_config(settings_with(b"{" + b"x" * 2000))["error"]["code"] == "output_too_large"

        secret = dict(mismatch)
        secret["data"] = {"profile_id": "token=abcd1234", "schema_version": "1.4"}
        assert stock_validate_config(settings_with(json.dumps(secret).encode()))["error"]["code"] == "secret_like_output"


def test_stock_runtime_rejects_out_of_bounds_and_oversized_package_refs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile_path = _write_fixture_profile(root)
        config = resolve_runtime_config(load_profile(profile_path))
        _seed_handoff_state(profile_path, "2026-07-03")
        settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
            package_cap_bytes=32,
        )
        run = stock_run_daily(settings, "2026-07-03")
        package_ref = run["data"]["analysis_package_ref"]

        assert stock_read_analysis_package(settings, "file:../escape.json")["error"]["code"] == "ref_invalid"
        assert stock_read_analysis_package(settings, package_ref)["error"]["code"] == "ref_too_large"

        normal_settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
        )
        package_path = config.storage_dir / package_ref.removeprefix("file:")
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["contents"][0]["transcript_artifact_ref"] = "file:transcripts/wrong-run/1.json"
        package_path.write_text(json.dumps(package, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        assert stock_read_analysis_package(normal_settings, package_ref)["error"]["code"] == "ref_out_of_bounds"


def test_stock_runtime_records_result_exact_replay_and_conflict_safely() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile_path = _write_fixture_profile(root)
        config = resolve_runtime_config(load_profile(profile_path))
        _seed_handoff_state(profile_path, "2026-07-03")
        settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
            timeout_seconds=30,
        )
        run = stock_run_daily(settings, "2026-07-03")
        package_ref = run["data"]["analysis_package_ref"]
        first = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "same payload"},
        )
        replay = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "same payload"},
        )
        conflict = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "changed payload"},
        )

    assert first["ok"] is True
    assert replay["ok"] is True
    assert replay["data"]["analysis_result_id"] == first["data"]["analysis_result_id"]
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "result_ref_conflict"
    assert "changed payload" not in json.dumps(conflict)


def test_collector_stock_runtime_distribution_self_check_runs_full_fixture_loop() -> None:
    result = run_self_check(repo_root=ROOT)

    assert result["ok"] is True
    assert [step["name"] for step in result["steps"]] == [
        "validate",
        "health",
        "run",
        "read_package",
        "record_result",
        "digest",
        "feedback",
    ]
    assert result["analysis_package_ref"].startswith(f"file:{result['run_id']}/")
    assert result["digest_payload_ref"].startswith(f"file:{result['run_id']}/")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "/tmp/" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded
