from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hermes_benchmark.cli as cli_module
from hermes_benchmark.cli import EXIT_CONFIG_INVALID, EXIT_CONTRACT_MISMATCH, EXIT_OK, _classify_mediacrawler_failure, main
from hermes_benchmark.handoff import validate_handoff_package
from hermes_benchmark.profile import load_profile
from hermes_benchmark.runtime_cdp import resolve_runtime_config
from hermes_benchmark.state import begin_run, connect, finish_run, init_schema, record_transcript_state, upsert_content_ledger
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


def test_run_daily_hermes_handoff_writes_schema_valid_package_ref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        config = seed_handoff_state(profile_path, "2026-07-03")
        code, stdout, stderr = run_cli(
            "run-daily",
            "--profile",
            str(profile_path),
            "--date",
            "2026-07-03",
            "--analysis-mode",
            "hermes-handoff",
            "--json",
        )

        payload = json.loads(stdout)
        ref = payload["data"]["analysis_package_ref"]
        package = json.loads((config.storage_dir / ref.removeprefix("file:")).read_text(encoding="utf-8"))

    assert code == EXIT_OK
    assert stderr == ""
    assert payload["ok"] is True
    assert payload["mode"] == "runtime"
    assert payload["data"]["analysis_mode"] == "hermes-handoff"
    assert ref == f"file:{payload['data']['run_id']}/artifacts/analysis_package.json"
    validate_handoff_package(package)
    assert package["contents"][0]["account_display_name"] == "sample 001"
    assert package["contents"][0]["transcript_status"] == "success"


def test_run_daily_invalid_handoff_package_returns_exit_6() -> None:
    old_builder = cli_module.build_handoff_package
    cli_module.build_handoff_package = lambda *_args, **_kwargs: {"bad": True}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = write_temp_profile(Path(tmp), free_port())
            code, stdout, stderr = run_cli(
                "run-daily",
                "--profile",
                str(profile_path),
                "--date",
                "2026-07-03",
                "--analysis-mode",
                "hermes-handoff",
                "--json",
            )
    finally:
        cli_module.build_handoff_package = old_builder

    assert code == cli_module.EXIT_HANDOFF_PACKAGE_INVALID
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "handoff_package_invalid"
    assert payload["exit_code"] == 6


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


def seed_handoff_state(profile_path: Path, run_date: str):
    profile = load_profile(profile_path)
    config = resolve_runtime_config(profile)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.database_path)
    try:
        init_schema(conn)
        run = begin_run(conn, run_date, config.profile_hash, profile_id=config.profile_id)
        content = upsert_content_ledger(
            conn,
            run["run_id"],
            {
                "content_id": "content-1",
                "platform": "douyin",
                "platform_content_id": "aweme-1",
                "normalized_source_url": "https://www.douyin.com/video/1",
                "account_id": "douyin_sample_001",
                "publish_at": "2026-07-03T00:00:00Z",
                "normalized_title_or_caption_hash": "sha256:title",
                "source_url": "https://www.douyin.com/video/1",
                "status": "seen",
            },
        )
        record_transcript_state(conn, content["content_id"], "funasr:test", "done", artifact_ref="file:transcripts/1.json")
        finish_run(conn, run["run_id"], "succeeded")
    finally:
        conn.close()
    return config


if __name__ == "__main__":
    test_stub_json_contracts()
    test_validate_config_json_contract()
    test_validate_config_rejects_plaintext_sensitive_values()
    test_validate_config_scans_child_profiles_for_plaintext_sensitive_values()
    test_validate_config_rejects_enabled_non_douyin_accounts()
    test_profile_hash_is_deterministic()
    test_config_alias_matches_profile()
    test_healthcheck_with_profile_reports_runtime_contract_without_raw_endpoint()
    test_run_daily_hermes_handoff_writes_schema_valid_package_ref()
    test_run_daily_invalid_handoff_package_returns_exit_6()
    test_mediacrawler_page_timeout_is_not_unknown_error()
    test_invalid_args_json_contract()
