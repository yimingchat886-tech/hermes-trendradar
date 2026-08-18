"""No-secret collector stock_runtime distribution self-check."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

from .profile import load_profile
from .runtime_cdp import resolve_runtime_config
from .state import (
    begin_run,
    connect,
    finish_run,
    init_schema,
    record_transcript_state,
    upsert_content_ledger,
)
from .stock_runtime import (
    StockRuntimeError,
    StockRuntimeSettings,
    assert_tool_result_is_safe,
    stock_build_internal_digest,
    stock_healthcheck,
    stock_read_analysis_package,
    stock_record_analysis_result,
    stock_record_feedback,
    stock_run_daily,
    stock_validate_config,
)


def run_self_check(repo_root: Path | None = None) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="stock-runtime-self-check-"))
    repo = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    profile_path = _write_fixture_profile(root)
    config = resolve_runtime_config(load_profile(profile_path))
    _seed_handoff_state(profile_path, "2026-07-03")
    settings = StockRuntimeSettings(
        executable=(sys.executable, "-m", "hermes_benchmark.cli"),
        cwd=repo,
        profile_ref=profile_path,
        storage_root=config.storage_dir,
        timeout_seconds=30,
    )

    steps: list[dict[str, Any]] = []
    validate = _record_step(steps, "validate", stock_validate_config(settings))
    health = _record_step(steps, "health", stock_healthcheck(settings))
    run = _record_step(steps, "run", stock_run_daily(settings, "2026-07-03"))
    package_ref = run["data"]["analysis_package_ref"]
    package = _record_step(steps, "read_package", stock_read_analysis_package(settings, package_ref))
    content_id = package["data"]["contents"][0]["content_id"]
    result = _record_step(
        steps,
        "record_result",
        stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id=content_id,
            status="succeeded",
            analysis={"summary": "Fixture analysis only", "recommendation": "adopt", "evidence_refs": ["fixture:content-1"]},
        ),
    )
    digest = _record_step(steps, "digest", stock_build_internal_digest(settings, run["data"]["run_id"]))
    feedback = _record_step(
        steps,
        "feedback",
        stock_record_feedback(
            settings,
            run_id=run["data"]["run_id"],
            content_id=content_id,
            analysis_result_id=result["data"]["analysis_result_id"],
            decision="adopt",
            actor_ref="feishu:user/fixture",
            source_message_ref="feishu:message/fixture",
            reason_code="fixture_ok",
        ),
    )

    return {
        "ok": validate["ok"] and health["ok"] and run["ok"] and package["ok"] and result["ok"] and digest["ok"] and feedback["ok"],
        "self_check": "collector_stock_runtime",
        "steps": steps,
        "run_id": run["data"]["run_id"],
        "analysis_package_ref": package_ref,
        "analysis_result_id": result["data"]["analysis_result_id"],
        "digest_payload_ref": digest["data"]["digest_payload_ref"],
        "feedback_id": feedback["data"]["feedback_id"],
        "excluded_live_actions": ["no_feishu", "no_live_profile", "no_network_collection", "no_bitable", "no_wiki"],
    }


def _record_step(steps: list[dict[str, Any]], name: str, result: dict[str, Any]) -> dict[str, Any]:
    assert_tool_result_is_safe(result)
    steps.append({"name": name, "ok": bool(result.get("ok")), "error_code": (result.get("error") or {}).get("code", "")})
    if not result.get("ok"):
        raise StockRuntimeError(str((result.get("error") or {}).get("code") or "self_check_failed"), str((result.get("error") or {}).get("message") or "self-check failed"))
    return result


def _write_fixture_profile(root: Path) -> Path:
    external = root / "_external"
    storage = external / "hermes-stock-runs"
    chrome_profile = external / "MediaCrawler" / "browser_data" / "cdp_dy_user_data_dir"
    mediacrawler_python = external / "venvs" / "mediacrawler" / "bin" / "python"
    chrome_profile.mkdir(parents=True)
    storage.mkdir(parents=True)
    mediacrawler_python.parent.mkdir(parents=True)
    mediacrawler_python.write_text("#!/bin/sh\n", encoding="utf-8")
    (external / "MediaCrawler").mkdir(exist_ok=True)

    accounts = {
        "schema_version": "1.4",
        "profile_id": "accounts-fixture",
        "profile_type": "account_profile",
        "platform": "douyin",
        "required_enabled_accounts": 10,
        "accounts": [
            {
                "account_id": f"douyin_sample_{index:03d}",
                "platform": "douyin",
                "display_name": f"sample {index:03d}",
                "enabled": True,
            }
            for index in range(1, 11)
        ],
    }
    analysis = {"schema_version": "1.4", "profile_id": "analysis-fixture", "profile_type": "analysis_profile"}
    transcription = {"schema_version": "1.4", "profile_id": "transcription-fixture", "profile_type": "transcription_profile"}
    runtime = {
        "schema_version": "1.4",
        "profile_id": "runtime-fixture",
        "profile_type": "runtime_profile",
        "storage_ref": f"file:{storage}",
        "database_ref": f"file:{storage / 'state' / 'fixture.sqlite'}",
        "cdp_endpoint_ref": f"runtime:127.0.0.1:{_free_port()}",
        "proxy_url_ref": "env:HERMES_PROXY_URL",
        "cookie_ref": "env:HERMES_DOUYIN_COOKIE_REF",
        "login_state_ref": f"file:{chrome_profile}",
    }
    field_mapping = {"schema_version": "1.4", "profile_id": "feishu-field-map-fixture", "profile_type": "feishu_field_mapping_profile"}
    feishu = {
        "schema_version": "1.4",
        "profile_id": "feishu-fixture",
        "profile_type": "feishu_limited_live_profile",
        "field_mapping_ref": "file:field-mapping.json",
        "base_config_ref": "env:HERMES_FEISHU_BASE_CONFIG",
        "authorization_profile_ref": "env:HERMES_V14_AUTHORIZATION_PROFILE",
        "allowed_tables": ["table_4"],
        "allowed_mutations": ["status_update"],
    }
    root_profile = {
        "schema_version": "1.4",
        "profile_id": "collector-stock-runtime-fixture",
        "profile_type": "hermes_runtime",
        "profile_mode": "sample",
        "platform_scope": {"production_platforms": ["douyin"], "reject_non_production_platforms": True},
        "account_profile_ref": "file:accounts.json",
        "analysis_profile_ref": "file:analysis.json",
        "transcription_profile_ref": "file:transcription.json",
        "runtime_profile_ref": "file:runtime.json",
        "feishu_profile_ref": "file:feishu.json",
        "schedule": {"enabled": False, "cadence": "manual", "timezone": "Asia/Shanghai", "trigger_time_ref": "env:COLLECTOR_DISABLED"},
        "retry": {"max_attempts": 1, "retryable_exit_codes": [3, 4, 6, 9], "non_retryable_exit_codes": [2]},
    }
    for name, payload in {
        "accounts.json": accounts,
        "analysis.json": analysis,
        "transcription.json": transcription,
        "runtime.json": runtime,
        "field-mapping.json": field_mapping,
        "feishu.json": feishu,
        "hermes.json": root_profile,
    }.items():
        (root / name).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return root / "hermes.json"


def _seed_handoff_state(profile_path: Path, run_date: str) -> None:
    config = resolve_runtime_config(load_profile(profile_path))
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
        record_transcript_state(
            conn,
            content["content_id"],
            "funasr:test",
            "done",
            artifact_ref=f"file:transcripts/{run['run_id']}/1.json",
        )
        finish_run(conn, run["run_id"], "succeeded")
    finally:
        conn.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hermes_benchmark.stock_runtime_self_check")
    parser.add_argument("--json", action="store_true", help="Print a JSON self-check result.")
    args = parser.parse_args(argv)
    try:
        result = run_self_check()
    except StockRuntimeError as exc:
        result = {"ok": False, "self_check": "collector_stock_runtime", "error": {"code": exc.code, "message": str(exc)}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print("collector stock_runtime self-check ok" if result.get("ok") else "collector stock_runtime self-check failed")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
