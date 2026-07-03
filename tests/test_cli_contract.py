from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.cli import EXIT_CONFIG_INVALID, EXIT_CONTRACT_MISMATCH, EXIT_OK, _classify_mediacrawler_failure, main
from test_runtime_cdp import free_port, write_temp_profile

SAMPLE_PROFILE = ROOT / "profiles" / "examples" / "hermes.v1.4.douyin.sample.json"


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def test_stub_json_contracts() -> None:
    commands = [
        ("healthcheck", "--json"),
        ("run-daily", "--json"),
        ("apply-limited-live", "--json"),
    ]
    for args in commands:
        code, stdout, stderr = run_cli(*args)
        assert code == EXIT_OK
        assert stderr == ""
        payload = json.loads(stdout)
        assert payload["ok"] is True
        assert payload["mode"] == "stub"
        assert payload["error"] is None


def test_validate_config_json_contract() -> None:
    code, stdout, stderr = run_cli("validate-config", "--profile", str(SAMPLE_PROFILE), "--json")
    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "profile"
    assert payload["data"]["enabled_account_count"] == 10
    assert payload["data"]["required_enabled_accounts"] == 10
    assert payload["data"]["profile_hash"].startswith("sha256:")
    assert payload["data"]["errors"] == []


def test_validate_config_rejects_plaintext_sensitive_values() -> None:
    profile = json.loads(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    profile["runtime_override"] = {"cdp_endpoint": "http://example.invalid/cdp"}
    with tempfile.TemporaryDirectory() as tmp:
        bad_profile = Path(tmp) / "bad-profile.json"
        bad_profile.write_text(json.dumps(profile), encoding="utf-8")
        code, stdout, stderr = run_cli("validate-config", "--profile", str(bad_profile), "--json")

    assert code == EXIT_CONFIG_INVALID
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "config_invalid"
    assert "http://example.invalid/cdp" not in stdout


def test_validate_config_scans_child_profiles_for_plaintext_sensitive_values() -> None:
    root_profile = json.loads(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    runtime_profile = json.loads((ROOT / "profiles" / "examples" / "runtime.v1.4.sample.json").read_text(encoding="utf-8"))
    runtime_profile["proxy_url"] = "http://example.invalid/proxy"
    with tempfile.TemporaryDirectory() as tmp:
        bad_runtime = Path(tmp) / "bad-runtime.json"
        bad_runtime.write_text(json.dumps(runtime_profile), encoding="utf-8")
        root_profile["runtime_profile_ref"] = f"file:{bad_runtime}"
        bad_profile = Path(tmp) / "bad-root.json"
        bad_profile.write_text(json.dumps(root_profile), encoding="utf-8")
        code, stdout, stderr = run_cli("validate-config", "--profile", str(bad_profile), "--json")

    assert code == EXIT_CONFIG_INVALID
    assert stderr == ""
    assert json.loads(stdout)["error"]["code"] == "config_invalid"
    assert "http://example.invalid/proxy" not in stdout


def test_validate_config_rejects_enabled_non_douyin_accounts() -> None:
    root_profile = json.loads(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    account_profile = json.loads((ROOT / "profiles" / "examples" / "accounts.douyin.sample.json").read_text(encoding="utf-8"))
    account_profile["accounts"][0]["platform"] = "xiaohongshu"
    with tempfile.TemporaryDirectory() as tmp:
        bad_accounts = Path(tmp) / "bad-accounts.json"
        bad_accounts.write_text(json.dumps(account_profile), encoding="utf-8")
        root_profile["account_profile_ref"] = f"file:{bad_accounts}"
        bad_profile = Path(tmp) / "bad-root.json"
        bad_profile.write_text(json.dumps(root_profile), encoding="utf-8")
        code, stdout, stderr = run_cli("validate-config", "--profile", str(bad_profile), "--json")

    assert code == EXIT_CONFIG_INVALID
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["error"]["code"] == "config_invalid"
    assert "must be douyin" in stdout


def test_profile_hash_is_deterministic() -> None:
    first_code, first_stdout, _ = run_cli("validate-config", "--profile", str(SAMPLE_PROFILE), "--json")
    second_code, second_stdout, _ = run_cli("validate-config", "--profile", str(SAMPLE_PROFILE), "--json")
    assert first_code == EXIT_OK
    assert second_code == EXIT_OK
    assert json.loads(first_stdout)["data"]["profile_hash"] == json.loads(second_stdout)["data"]["profile_hash"]


def test_config_alias_matches_profile() -> None:
    code, stdout, stderr = run_cli("validate-config", "--config", str(SAMPLE_PROFILE), "--json")
    assert code == EXIT_OK
    assert stderr == ""
    assert json.loads(stdout)["ok"] is True

    code, stdout, stderr = run_cli(
        "validate-config",
        "--profile",
        str(SAMPLE_PROFILE),
        "--config",
        "different.json",
        "--json",
    )
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    assert json.loads(stdout)["error"]["code"] == "contract_mismatch"


def test_healthcheck_with_profile_reports_runtime_contract_without_raw_endpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        code, stdout, stderr = run_cli("healthcheck", "--profile", str(profile_path), "--json")

    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "runtime"
    assert payload["data"]["schema_version"] == "1.4"
    assert "runtime_effective_status" in payload["data"]
    assert payload["data"]["checks"]["cdp"]["endpoint_ref"] == "redacted"
    assert "http://127.0.0.1" not in stdout


def test_mediacrawler_page_timeout_is_not_unknown_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        stderr_path = Path(tmp) / "stderr.log"
        stderr_path.write_text('Page.goto: Timeout 30000ms exceeded navigating to "https://www.douyin.com/"', encoding="utf-8")

        code, message = _classify_mediacrawler_failure({"stderr_path": str(stderr_path)})

    assert code == "DOUYIN_UI_CHANGED"
    assert not code.startswith("CDP_")
    assert code != "UNKNOWN_ERROR"
    assert "after CDP connection" in message


def test_invalid_args_json_contract() -> None:
    code, stdout, stderr = run_cli("missing-command", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "contract_mismatch"
    assert payload["exit_code"] == EXIT_CONTRACT_MISMATCH


if __name__ == "__main__":
    test_stub_json_contracts()
    test_validate_config_json_contract()
    test_validate_config_rejects_plaintext_sensitive_values()
    test_validate_config_scans_child_profiles_for_plaintext_sensitive_values()
    test_validate_config_rejects_enabled_non_douyin_accounts()
    test_profile_hash_is_deterministic()
    test_config_alias_matches_profile()
    test_healthcheck_with_profile_reports_runtime_contract_without_raw_endpoint()
    test_mediacrawler_page_timeout_is_not_unknown_error()
    test_invalid_args_json_contract()
