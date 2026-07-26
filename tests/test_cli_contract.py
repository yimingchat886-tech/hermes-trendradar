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
from hermes_benchmark.state import begin_run, connect, finish_run, init_schema, record_error, record_transcript_state, upsert_content_ledger
from test_runtime_cdp import free_port, write_temp_profile

SAMPLE_PROFILE = ROOT / "profiles" / "examples" / "hermes.v1.4.douyin.sample.json"


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def test_json_envelopes_include_contract_metadata() -> None:
    code, stdout, stderr = run_cli("healthcheck", "--self-check", "--json")
    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["contract_version"] == "2.0"
    assert payload["ok"] is True
    assert payload["command"] == "healthcheck"
    assert payload["mode"] == "self-check"
    assert payload["exit_code"] == EXIT_OK
    assert payload["retryable"] is False
    assert payload["error"] is None

    code, stdout, stderr = run_cli("run-daily", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["contract_version"] == "2.0"
    assert payload["ok"] is False
    assert payload["command"] == "run-daily"
    assert payload["exit_code"] == EXIT_CONTRACT_MISMATCH
    assert payload["retryable"] is False
    assert payload["error"]["code"] == "contract_mismatch"


def test_mock_and_self_check_paths_must_be_explicit() -> None:
    code, stdout, stderr = run_cli("healthcheck", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    assert json.loads(stdout)["ok"] is False

    code, stdout, stderr = run_cli("run-daily", "--analysis-mode", "mock", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    assert json.loads(stdout)["ok"] is False

    code, stdout, stderr = run_cli("run-daily", "--analysis-mode", "mock", "--self-check", "--json")
    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "self-check"
    assert payload["data"]["run_started"] is False

    code, stdout, stderr = run_cli("apply-limited-live", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    assert json.loads(stdout)["ok"] is False


def test_validate_config_json_contract() -> None:
    code, stdout, stderr = run_cli("validate-config", "--profile", str(SAMPLE_PROFILE), "--json")
    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "profile"
    assert payload["exit_code"] == EXIT_OK
    assert payload["retryable"] is False
    assert payload["data"]["enabled_account_count"] == 10
    assert payload["data"]["required_enabled_accounts"] == 10
    assert payload["data"]["profile_hash"].startswith("sha256:")
    assert payload["data"]["errors"] == []
    assert payload["data"]["profile_ref"] == "redacted"


def test_sample_profile_healthcheck_does_not_fail_on_ref_shape() -> None:
    validate_code, _stdout, _stderr = run_cli("validate-config", "--profile", str(SAMPLE_PROFILE), "--json")
    code, stdout, stderr = run_cli("healthcheck", "--profile", str(SAMPLE_PROFILE), "--json")

    assert validate_code == EXIT_OK
    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "runtime"
    assert payload["data"]["schema_version"] == "1.4"
    assert payload["error"] is None


def test_profile_errors_keep_invoked_command_and_redact_temp_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing_profile = Path(tmp) / "missing-profile.json"
        code, stdout, stderr = run_cli("healthcheck", "--profile", str(missing_profile), "--json")

    assert code == EXIT_CONFIG_INVALID
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["command"] == "healthcheck"
    assert payload["retryable"] is False
    assert "profile file not found" in stdout
    assert str(missing_profile) not in stdout
    assert "/tmp/" not in stdout


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
        conn = connect(config.database_path)
        try:
            package_row = conn.execute("SELECT package_id FROM analysis_packages WHERE run_id = ?", (payload["data"]["run_id"],)).fetchone()
        finally:
            conn.close()

    assert code == EXIT_OK
    assert stderr == ""
    assert payload["ok"] is True
    assert payload["mode"] == "runtime"
    assert payload["data"]["analysis_mode"] == "hermes-handoff"
    assert ref == f"file:{payload['data']['run_id']}/artifacts/analysis_package.json"
    validate_handoff_package(package)
    assert payload["data"]["analysis_package_id"] == package["package_id"]
    assert package_row["package_id"] == package["package_id"]
    assert package["contents"][0]["account_display_name"] == "sample 001"
    assert package["contents"][0]["transcript_status"] == "success"


def test_record_analysis_result_persists_refs_without_mock_or_raw_text() -> None:
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
        assert code == EXIT_OK
        package_ref = json.loads(stdout)["data"]["analysis_package_ref"]
        package = json.loads((config.storage_dir / package_ref.removeprefix("file:")).read_text(encoding="utf-8"))
        raw_body = "raw analysis body must not be persisted"
        raw_body_hash = "sha256:0a7ac633c227c335d4b4d0da0487d336c72e403c5a7bfe91d7b447dd18296de2"
        result_ref = write_result_artifact(config, raw_body)
        result_path = Path(tmp) / "analysis-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.4",
                    "package_id": package["package_id"],
                    "run_id": package["run_id"],
                    "content_id": "content-1",
                    "transcript_artifact_ref": "file:transcripts/1.json",
                    "result_ref": result_ref,
                    "status": "succeeded",
                    "raw_body": raw_body,
                }
            ),
            encoding="utf-8",
        )

        sys.modules.pop("hermes_benchmark.decomposition", None)
        code, stdout, stderr = run_cli(
            "record-analysis-result",
            "--profile",
            str(profile_path),
            "--package",
            package_ref,
            "--result",
            str(result_path),
            "--json",
        )
        conn = connect(config.database_path)
        try:
            row = conn.execute("SELECT * FROM analysis_results").fetchone()
        finally:
            conn.close()

    assert code == EXIT_OK
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["command"] == "record-analysis-result"
    assert payload["data"]["package_id"] == package["package_id"]
    assert payload["data"]["content_id"] == "content-1"
    assert payload["data"]["result_ref"] == "file:analysis/results/content-1.json"
    assert payload["data"]["result_hash"] == raw_body_hash
    assert row["package_id"] == package["package_id"]
    assert row["result_ref"] == "file:analysis/results/content-1.json"
    assert raw_body not in stdout
    assert raw_body not in " ".join(str(row[key]) for key in row.keys())
    assert "hermes_benchmark.decomposition" not in sys.modules


def test_record_analysis_result_invalid_result_returns_exit_6() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        seed_handoff_state(profile_path, "2026-07-03")
        code, stdout, _stderr = run_cli(
            "run-daily",
            "--profile",
            str(profile_path),
            "--date",
            "2026-07-03",
            "--analysis-mode",
            "hermes-handoff",
            "--json",
        )
        assert code == EXIT_OK
        package_ref = json.loads(stdout)["data"]["analysis_package_ref"]
        result_path = Path(tmp) / "analysis-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.4",
                    "package_id": "package_other",
                    "run_id": "run-other",
                    "content_id": "content-1",
                    "transcript_artifact_ref": "file:transcripts/1.json",
                    "result_ref": "file:analysis/results/content-1.json",
                    "status": "succeeded",
                }
            ),
            encoding="utf-8",
        )

        code, stdout, stderr = run_cli(
            "record-analysis-result",
            "--profile",
            str(profile_path),
            "--package",
            package_ref,
            "--result",
            str(result_path),
            "--json",
        )

    assert code == cli_module.EXIT_ANALYSIS_RESULT_INVALID
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "analysis_result_invalid"
    assert payload["exit_code"] == 6


def test_record_analysis_result_conflict_returns_json_envelope_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        config = seed_handoff_state(profile_path, "2026-07-03")
        code, stdout, _stderr = run_cli(
            "run-daily",
            "--profile",
            str(profile_path),
            "--date",
            "2026-07-03",
            "--analysis-mode",
            "hermes-handoff",
            "--json",
        )
        assert code == EXIT_OK
        package_ref = json.loads(stdout)["data"]["analysis_package_ref"]
        package = json.loads((config.storage_dir / package_ref.removeprefix("file:")).read_text(encoding="utf-8"))
        first_ref = write_result_artifact(config, "first result", rel="analysis/results/content-1.json")
        second_ref = write_result_artifact(config, "changed result", rel="analysis/results/content-1-v2.json")
        first_result = Path(tmp) / "analysis-result-1.json"
        second_result = Path(tmp) / "analysis-result-2.json"
        base = {
            "schema_version": "1.4",
            "package_id": package["package_id"],
            "run_id": package["run_id"],
            "content_id": "content-1",
            "transcript_artifact_ref": "file:transcripts/1.json",
            "status": "succeeded",
        }
        first_result.write_text(json.dumps({**base, "result_ref": first_ref}), encoding="utf-8")
        second_result.write_text(json.dumps({**base, "result_ref": second_ref}), encoding="utf-8")

        first_code, _first_stdout, first_stderr = run_cli(
            "record-analysis-result",
            "--profile",
            str(profile_path),
            "--package",
            package_ref,
            "--result",
            str(first_result),
            "--json",
        )
        conflict_code, conflict_stdout, conflict_stderr = run_cli(
            "record-analysis-result",
            "--profile",
            str(profile_path),
            "--package",
            package_ref,
            "--result",
            str(second_result),
            "--json",
        )
        conn = connect(config.database_path)
        try:
            rows = conn.execute("SELECT * FROM analysis_results").fetchall()
        finally:
            conn.close()

    assert first_code == EXIT_OK
    assert first_stderr == ""
    assert conflict_code == cli_module.EXIT_ANALYSIS_RESULT_INVALID
    assert conflict_stderr == ""
    payload = json.loads(conflict_stdout)
    assert payload["ok"] is False
    assert payload["command"] == "record-analysis-result"
    assert payload["error"]["code"] == "analysis_result_invalid"
    assert payload["retryable"] is False
    assert len(rows) == 1
    assert rows[0]["result_ref"] == "file:analysis/results/content-1.json"


def test_build_internal_digest_writes_payload_without_table_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        config = seed_handoff_state(profile_path, "2026-07-03")
        code, stdout, _stderr = run_cli(
            "run-daily",
            "--profile",
            str(profile_path),
            "--date",
            "2026-07-03",
            "--analysis-mode",
            "hermes-handoff",
            "--json",
        )
        assert code == EXIT_OK
        package_ref = json.loads(stdout)["data"]["analysis_package_ref"]
        package = json.loads((config.storage_dir / package_ref.removeprefix("file:")).read_text(encoding="utf-8"))
        raw_body = "raw digest source must stay out"
        raw_body_hash = "sha256:6e8b1c64a8fc5295e30ff3ef59b07ca8f53758a0e08b884fe5f26de803f99db5"
        result_ref = write_result_artifact(config, raw_body)
        result_path = Path(tmp) / "analysis-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.4",
                    "package_id": package["package_id"],
                    "run_id": package["run_id"],
                    "content_id": "content-1",
                    "transcript_artifact_ref": "file:transcripts/1.json",
                    "result_ref": result_ref,
                    "status": "succeeded",
                    "raw_body": raw_body,
                }
            ),
            encoding="utf-8",
        )
        assert (
            run_cli(
                "record-analysis-result",
                "--profile",
                str(profile_path),
                "--package",
                package_ref,
                "--result",
                str(result_path),
                "--json",
            )[0]
            == EXIT_OK
        )
        conn = connect(config.database_path)
        try:
            record_error(conn, package["run_id"], "analysis", "content-1", "analysis_warning", "redacted warning", True)
            before_operations = conn.execute("SELECT COUNT(*) FROM feishu_operations").fetchone()[0]
        finally:
            conn.close()

        code, stdout, stderr = run_cli(
            "build-internal-digest",
            "--profile",
            str(profile_path),
            "--run-id",
            package["run_id"],
            "--json",
        )
        conn = connect(config.database_path)
        try:
            after_operations = conn.execute("SELECT COUNT(*) FROM feishu_operations").fetchone()[0]
        finally:
            conn.close()
        payload = json.loads(stdout)
        digest_ref = payload["data"]["digest_payload_ref"]
        digest_payload = json.loads((config.storage_dir / digest_ref.removeprefix("file:")).read_text(encoding="utf-8"))

    assert code == EXIT_OK
    assert stderr == ""
    assert payload["command"] == "build-internal-digest"
    assert payload["data"]["status"] == "degraded"
    assert payload["data"]["delivery"]["status"] == "blocked"
    assert payload["data"]["digest_payload_hash"].startswith("sha256:")
    assert digest_payload["items"][0]["trace"]["package_id"] == package["package_id"]
    assert digest_payload["items"][0]["trace"]["result_ref"] == "file:analysis/results/content-1.json"
    assert digest_payload["items"][0]["trace"]["result_hash"] == raw_body_hash
    assert digest_payload["degraded"][0]["error_code"] == "analysis_warning"
    assert before_operations == 0
    assert after_operations == 0
    assert raw_body not in stdout
    assert raw_body not in json.dumps(digest_payload)


def test_record_feedback_persists_idempotent_refs_without_promotion_or_writes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        profile_path = write_temp_profile(Path(tmp), free_port())
        config = seed_handoff_state(profile_path, "2026-07-03")
        code, stdout, _stderr = run_cli(
            "run-daily",
            "--profile",
            str(profile_path),
            "--date",
            "2026-07-03",
            "--analysis-mode",
            "hermes-handoff",
            "--json",
        )
        assert code == EXIT_OK
        package_ref = json.loads(stdout)["data"]["analysis_package_ref"]
        package = json.loads((config.storage_dir / package_ref.removeprefix("file:")).read_text(encoding="utf-8"))
        result_ref = write_result_artifact(config, "feedback result")
        result_path = Path(tmp) / "analysis-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.4",
                    "package_id": package["package_id"],
                    "run_id": package["run_id"],
                    "content_id": "content-1",
                    "transcript_artifact_ref": "file:transcripts/1.json",
                    "result_ref": result_ref,
                    "status": "succeeded",
                }
            ),
            encoding="utf-8",
        )
        code, stdout, _stderr = run_cli(
            "record-analysis-result",
            "--profile",
            str(profile_path),
            "--package",
            package_ref,
            "--result",
            str(result_path),
            "--json",
        )
        assert code == EXIT_OK
        analysis_result_id = json.loads(stdout)["data"]["analysis_result_id"]
        raw_message_text = "please adopt this because the exact message text is private"

        first_code, first_stdout, first_stderr = run_cli(
            "record-feedback",
            "--profile",
            str(profile_path),
            "--run-id",
            package["run_id"],
            "--content-id",
            "content-1",
            "--analysis-result-id",
            analysis_result_id,
            "--decision",
            "adopt",
            "--actor-ref",
            "feishu:user/redacted-1",
            "--source-message-ref",
            "feishu:message/msg-1",
            "--reason-code",
            "good_topic",
            "--json",
        )
        second_code, second_stdout, second_stderr = run_cli(
            "record-feedback",
            "--profile",
            str(profile_path),
            "--run-id",
            package["run_id"],
            "--content-id",
            "content-1",
            "--analysis-result-id",
            analysis_result_id,
            "--decision",
            "adopt",
            "--actor-ref",
            "feishu:user/redacted-1",
            "--source-message-ref",
            "feishu:message/msg-1",
            "--reason-code",
            "good_topic",
            "--json",
        )
        conflict_code, conflict_stdout, conflict_stderr = run_cli(
            "record-feedback",
            "--profile",
            str(profile_path),
            "--run-id",
            package["run_id"],
            "--content-id",
            "content-1",
            "--analysis-result-id",
            analysis_result_id,
            "--decision",
            "reject",
            "--actor-ref",
            "feishu:user/redacted-1",
            "--source-message-ref",
            "feishu:message/msg-1",
            "--reason-code",
            "good_topic",
            "--json",
        )
        invalid_code, invalid_stdout, invalid_stderr = run_cli(
            "record-feedback",
            "--profile",
            str(profile_path),
            "--run-id",
            package["run_id"],
            "--content-id",
            "content-other",
            "--analysis-result-id",
            analysis_result_id,
            "--decision",
            "adopt",
            "--actor-ref",
            "feishu:user/redacted-1",
            "--source-message-ref",
            "feishu:message/msg-2",
            "--json",
        )
        conn = connect(config.database_path)
        try:
            row = conn.execute("SELECT * FROM human_feedback").fetchone()
            feedback_count = conn.execute("SELECT COUNT(*) FROM human_feedback").fetchone()[0]
            operation_count = conn.execute("SELECT COUNT(*) FROM feishu_operations").fetchone()[0]
            audit_count = conn.execute("SELECT COUNT(*) FROM write_audit").fetchone()[0]
            column_names = [item["name"] for item in conn.execute("PRAGMA table_info(human_feedback)")]
        finally:
            conn.close()

    first_payload = json.loads(first_stdout)
    second_payload = json.loads(second_stdout)
    conflict_payload = json.loads(conflict_stdout)
    invalid_payload = json.loads(invalid_stdout)

    assert first_code == EXIT_OK
    assert first_stderr == ""
    assert second_code == EXIT_OK
    assert second_stderr == ""
    assert first_payload["command"] == "record-feedback"
    assert first_payload["data"]["feedback_id"] == second_payload["data"]["feedback_id"]
    assert first_payload["data"]["analysis_result_id"] == analysis_result_id
    assert first_payload["data"]["result_ref"] == "file:analysis/results/content-1.json"
    assert row["decision"] == "adopt"
    assert row["actor_ref"] == "feishu:user/redacted-1"
    assert row["source_message_ref"] == "feishu:message/msg-1"
    assert feedback_count == 1
    assert operation_count == 0
    assert audit_count == 0
    assert conflict_code == cli_module.EXIT_FEEDBACK_CONFLICT
    assert conflict_stderr == ""
    assert conflict_payload["error"]["code"] == "feedback_conflict"
    assert invalid_code == cli_module.EXIT_FEEDBACK_INVALID
    assert invalid_stderr == ""
    assert invalid_payload["error"]["code"] == "feedback_invalid"
    assert raw_message_text not in first_stdout
    assert "promotion" not in first_stdout
    forbidden_column_terms = ("promotion", "rag", "rule", "bitable", "operation")
    assert all(not any(term in name for term in forbidden_column_terms) for name in column_names)


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


def write_result_artifact(config, text: str, *, rel: str = "analysis/results/content-1.json") -> str:
    artifact_path = config.storage_dir / rel
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(text, encoding="utf-8")
    return f"file:{rel}"


if __name__ == "__main__":
    test_json_envelopes_include_contract_metadata()
    test_mock_and_self_check_paths_must_be_explicit()
    test_validate_config_json_contract()
    test_sample_profile_healthcheck_does_not_fail_on_ref_shape()
    test_profile_errors_keep_invoked_command_and_redact_temp_paths()
    test_validate_config_rejects_plaintext_sensitive_values()
    test_validate_config_scans_child_profiles_for_plaintext_sensitive_values()
    test_validate_config_rejects_enabled_non_douyin_accounts()
    test_profile_hash_is_deterministic()
    test_config_alias_matches_profile()
    test_healthcheck_with_profile_reports_runtime_contract_without_raw_endpoint()
    test_run_daily_hermes_handoff_writes_schema_valid_package_ref()
    test_record_analysis_result_persists_refs_without_mock_or_raw_text()
    test_record_analysis_result_invalid_result_returns_exit_6()
    test_record_analysis_result_conflict_returns_json_envelope_without_overwrite()
    test_build_internal_digest_writes_payload_without_table_write()
    test_record_feedback_persists_idempotent_refs_without_promotion_or_writes()
    test_run_daily_invalid_handoff_package_returns_exit_6()
    test_mediacrawler_page_timeout_is_not_unknown_error()
    test_invalid_args_json_contract()
