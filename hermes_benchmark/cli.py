"""CLI contract skeleton for Hermes benchmark productionization."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .collection_runner import (
    content_to_ledger_item,
    enabled_douyin_accounts,
    load_mediacrawler_jsonl,
    mediacrawler_creator_command,
)
from .external_runtime import redact_text, run_process
from .handoff import (
    HandoffPackageError,
    account_display_names,
    build_handoff_package,
    contents_from_state,
    write_handoff_package,
)
from .mediacrawler_import import import_mediacrawler_rows
from .profile import ProfileError, load_profile, validate_profile
from .runtime_cdp import (
    ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT,
    RuntimeCdpError,
    artifact_ref,
    build_runtime_health,
    ensure_runner_cdp,
    resolve_runtime_config,
)
from .state import begin_run, connect, finish_run, init_schema, record_analysis_package_ref, record_error, upsert_content_ledger

VERSION = "0.1.0"
EXIT_OK = 0
EXIT_CONTRACT_MISMATCH = 2
EXIT_CONFIG_INVALID = 2
EXIT_RUNTIME_UNAVAILABLE = 3
EXIT_COLLECTION_FAILED = 4
EXIT_HANDOFF_PACKAGE_INVALID = 6
EXIT_RUN_LOCK_CONFLICT = 9
ERROR_CONTRACT_MISMATCH = "contract_mismatch"
ERROR_CONFIG_INVALID = "config_invalid"
ERROR_HANDOFF_PACKAGE_INVALID = "handoff_package_invalid"


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliContractError(message)


class CliContractError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    try:
        args = parser.parse_args(args_list)
        payload = args.handler(args)
    except CliContractError as exc:
        payload = error_envelope(str(exc))
        if wants_json(args_list):
            print_json(payload)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_MISMATCH
    except ProfileError as exc:
        payload = config_error_envelope(exc)
        if wants_json(args_list):
            print_json(payload)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_INVALID

    exit_code = int(payload.get("exit_code", EXIT_OK))
    if getattr(args, "json", False):
        print_json(payload)
    elif payload.get("ok") is False:
        print(f"error: {payload.get('error', {}).get('message', 'command failed')}", file=sys.stderr)
    else:
        print(f"{payload['command']}: {payload['mode']} ok")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(prog="hermes-benchmark")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--json", action="store_true", help="Print a JSON envelope.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    add_command(subparsers, "validate-config", validate_config, profile=True)
    add_command(subparsers, "healthcheck", healthcheck, profile=True)
    add_command(subparsers, "smoke-mediacrawler", smoke_mediacrawler, profile=True)
    add_command(subparsers, "run-daily", run_daily, profile=True, daily=True)
    add_command(subparsers, "apply-limited-live", apply_limited_live, profile=True)
    return parser


def add_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    handler: Callable[[argparse.Namespace], dict[str, Any]],
    *,
    profile: bool = False,
    daily: bool = False,
) -> None:
    command = subparsers.add_parser(name)
    if profile:
        command.add_argument("--profile", default="", help="Path to a local runtime profile.")
        command.add_argument("--config", default="", help="Compatibility alias for --profile.")
    if daily:
        command.add_argument("--date", default="", help="Run date supplied by Hermes runtime.")
        command.add_argument("--analysis-mode", default="mock", choices=("mock", "hermes-handoff"))
        command.add_argument("--feishu-mode", default="dry-run", choices=("dry-run", "limited-live"))
    command.add_argument("--json", action="store_true", help="Print a JSON envelope.")
    command.set_defaults(handler=handler)


def validate_config(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args, required=True)
    profile = load_profile(profile_path)
    summary = validate_profile(profile)
    summary["profile"] = profile_path
    summary["validation"] = "ok"
    return success_envelope("validate-config", summary, mode="profile")


def healthcheck(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args)
    if profile_path:
        profile = load_profile(profile_path)
        data = build_runtime_health(profile)
        data["version"] = VERSION
        return success_envelope("healthcheck", data, mode="runtime")
    return success_envelope(
        "healthcheck",
        {
            "version": VERSION,
            "checks": [{"name": "cli_contract", "status": "ok"}],
        },
    )


def smoke_mediacrawler(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args, required=True)
    profile = load_profile(profile_path)
    config = resolve_runtime_config(profile)
    run_id = f"run-mediacrawler-smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_root = config.storage_dir / run_id
    report_dir = run_root / "reports"
    raw_dir = run_root / "raw"
    log_dir = run_root / "logs"
    report_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    lock = None
    try:
        cdp_result = ensure_runner_cdp(config)
        lock = cdp_result["lock"]
        account = enabled_douyin_accounts(profile)[0]
        command = mediacrawler_creator_command(
            account,
            python_executable=str(config.mediacrawler_python),
            output_dir=raw_dir,
            max_notes=1,
            comments=False,
            headless=False,
        )
        process = run_process(
            "mediacrawler-smoke",
            command,
            mode="real",
            cwd=config.mediacrawler_root,
            log_dir=log_dir,
            sensitive_values=[str(config.cdp_port), str(config.chrome_user_data_dir), str(config.mediacrawler_root)],
            timeout_seconds=600,
        )
        if process.get("exit_code") not in (0, None):
            code, message = _classify_mediacrawler_failure(process)
            return _smoke_failure(config, run_id, report_dir, code, message, cdp_result)

        rows = load_mediacrawler_jsonl(raw_dir, account)
        report = _map_smoke_rows(config, profile, run_id, rows, account, cdp_result)
        _write_smoke_reports(report, report_dir)
        if report["result"] == "failed":
            code = report["errors"][0]["code"] if report["errors"] else "UNKNOWN_ERROR"
            return command_error_envelope("smoke-mediacrawler", "runtime", code, "MediaCrawler smoke failed", EXIT_COLLECTION_FAILED, data=report)
        return success_envelope("smoke-mediacrawler", report, mode="runtime")
    except RuntimeCdpError as exc:
        exit_code = EXIT_RUN_LOCK_CONFLICT if exc.code == ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT else EXIT_RUNTIME_UNAVAILABLE
        report = _smoke_report(config, run_id, "failed", exc.code, exc.summary)
        _add_report_refs(config, run_id, report)
        _write_smoke_reports(report, report_dir)
        return command_error_envelope("smoke-mediacrawler", "runtime", exc.code, exc.summary, exit_code, data=report)
    except subprocess.TimeoutExpired:
        report = _smoke_report(config, run_id, "failed", "ACCOUNT_GUARD_TIMEOUT", "MediaCrawler smoke exceeded the process guard")
        _add_report_refs(config, run_id, report)
        _write_smoke_reports(report, report_dir)
        return command_error_envelope("smoke-mediacrawler", "runtime", "ACCOUNT_GUARD_TIMEOUT", "MediaCrawler smoke exceeded the process guard", EXIT_COLLECTION_FAILED, data=report)
    except Exception as exc:
        report = _smoke_report(config, run_id, "failed", "UNKNOWN_ERROR", str(exc))
        _add_report_refs(config, run_id, report)
        _write_smoke_reports(report, report_dir)
        return command_error_envelope("smoke-mediacrawler", "runtime", "UNKNOWN_ERROR", "MediaCrawler smoke failed", EXIT_COLLECTION_FAILED, data=report)
    finally:
        if lock is not None:
            lock.release()


def run_daily(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args)
    if args.analysis_mode == "hermes-handoff":
        return _run_daily_handoff(args, profile_path)
    return success_envelope(
        "run-daily",
        {
            "profile": profile_path,
            "date": args.date or "",
            "analysis_mode": args.analysis_mode,
            "feishu_mode": args.feishu_mode,
            "run_started": False,
            "reason": "stub_only",
        },
    )


def _run_daily_handoff(args: argparse.Namespace, profile_path: str) -> dict[str, Any]:
    if not profile_path:
        raise CliContractError("--profile is required for hermes-handoff")
    profile = load_profile(profile_path)
    config = resolve_runtime_config(profile)
    run_date = args.date or date.today().isoformat()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.database_path)
    run_id = ""
    run_status = ""
    try:
        init_schema(conn)
        run = begin_run(conn, run_date, config.profile_hash, profile_id=config.profile_id)
        run_id = run["run_id"]
        run_status = run["status"]
        if run_status == "locked":
            return command_error_envelope(
                "run-daily",
                "runtime",
                "run_lock_conflict",
                "run lock is already held",
                EXIT_RUN_LOCK_CONFLICT,
                data={"schema_version": "1.4", "run_id": run_id, "analysis_mode": args.analysis_mode, "errors": ["run_lock_conflict"]},
            )
        contents = contents_from_state(conn, account_display_names(profile))
        package = build_handoff_package(run_id, config.profile_hash, contents)
        package_ref, package_hash = write_handoff_package(config.storage_dir, run_id, package)
        package_id = record_analysis_package_ref(
            conn,
            run_id,
            "hermes-handoff",
            "ready",
            package_ref,
            artifact_hash=package_hash,
            content_count=len(package["contents"]),
        )
        if run_status != "noop":
            finish_run(conn, run_id, "succeeded")
        return success_envelope(
            "run-daily",
            {
                "schema_version": "1.4",
                "run_id": run_id,
                "date": run_date,
                "profile_id": config.profile_id,
                "profile_hash": config.profile_hash,
                "status": "succeeded",
                "analysis_mode": "hermes-handoff",
                "feishu_mode": args.feishu_mode,
                "account_summary": {"configured": 10, "enabled": 10, "processed": 0, "succeeded": 0, "partial_failed": 0, "failed": 0},
                "content_summary": {"new_content_count": len(package["contents"]), "dedup_noop_count": 0, "dedup_conflict_count": 0},
                "transcript_summary": _handoff_transcript_summary(package["contents"]),
                "analysis_package_id": package_id,
                "analysis_package_ref": package_ref,
                "errors": [],
                "artifact_refs": [package_ref],
            },
            mode="runtime",
        )
    except HandoffPackageError as exc:
        if run_id:
            record_error(conn, run_id, "handoff", run_id, ERROR_HANDOFF_PACKAGE_INVALID, str(exc), True)
            if run_status not in ("", "noop", "locked"):
                finish_run(conn, run_id, "failed")
        return command_error_envelope(
            "run-daily",
            "runtime",
            ERROR_HANDOFF_PACKAGE_INVALID,
            str(exc),
            EXIT_HANDOFF_PACKAGE_INVALID,
            data={"schema_version": "1.4", "run_id": run_id, "analysis_mode": args.analysis_mode, "errors": [str(exc)]},
        )
    finally:
        conn.close()


def apply_limited_live(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args)
    return success_envelope(
        "apply-limited-live",
        {
            "profile": profile_path,
            "live_write_attempted": False,
            "allowed_tables": ["table_4"],
            "status_scope": "status_only",
            "reason": "stub_only",
        },
    )


def profile_arg(args: argparse.Namespace, *, required: bool = False) -> str:
    profile = getattr(args, "profile", "") or ""
    config = getattr(args, "config", "") or ""
    if profile and config and profile != config:
        raise CliContractError("--profile and --config must reference the same path when both are supplied")
    selected = profile or config
    if required and not selected:
        raise CliContractError("--profile is required")
    return selected


def success_envelope(command: str, data: dict[str, Any], *, mode: str = "stub") -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "mode": mode,
        "data": data,
        "error": None,
    }


def error_envelope(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "command": None,
        "mode": "contract",
        "data": None,
        "error": {
            "code": ERROR_CONTRACT_MISMATCH,
            "message": message,
        },
        "exit_code": EXIT_CONTRACT_MISMATCH,
    }


def config_error_envelope(error: ProfileError) -> dict[str, Any]:
    return {
        "ok": False,
        "command": "validate-config",
        "mode": "profile",
        "data": {
            "ok": False,
            "errors": error.errors,
        },
        "error": {
            "code": ERROR_CONFIG_INVALID,
            "message": "profile/config validation failed",
            "errors": error.errors,
        },
        "exit_code": EXIT_CONFIG_INVALID,
    }


def command_error_envelope(
    command: str,
    mode: str,
    code: str,
    message: str,
    exit_code: int,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "mode": mode,
        "data": data,
        "error": {
            "code": code,
            "message": message,
        },
        "exit_code": exit_code,
    }


def _handoff_transcript_summary(contents: list[dict[str, Any]]) -> dict[str, Any]:
    queued = len(contents)
    succeeded = sum(1 for item in contents if item["transcript_status"] == "success")
    failed = sum(1 for item in contents if item["transcript_status"] not in ("", "missing", "success"))
    return {"queued": queued, "succeeded": succeeded, "failed": failed, "success_threshold_met": queued == 0 or succeeded >= min(queued, 80)}


def wants_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _map_smoke_rows(
    config: Any,
    profile: Any,
    run_id: str,
    rows: list[dict[str, Any]],
    account: dict[str, Any],
    cdp_result: dict[str, Any],
) -> dict[str, Any]:
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.database_path)
    try:
        init_schema(conn)
        run = begin_run(conn, date.today().isoformat(), profile_hash=config.profile_hash, profile_id=profile.root["profile_id"])
        imported = import_mediacrawler_rows(rows, [account])
        mapper_errors = len(imported["source_health"])
        inserted = 0
        noop = 0
        for content in imported["contents"]:
            result = upsert_content_ledger(conn, run["run_id"], content_to_ledger_item(content))
            if result["status"] == "inserted":
                inserted += 1
            elif result["status"] == "noop":
                noop += 1
        if mapper_errors:
            record_error(conn, run["run_id"], "content", account["id"], "MAPPER_SCHEMA_ERROR", "MediaCrawler smoke mapping failed", True)
        if mapper_errors:
            status = "failed"
        elif not rows or inserted == 0:
            status = "success_noop"
        else:
            status = "passed"
        finish_run(conn, run["run_id"], "succeeded" if status in {"passed", "success_noop"} else "failed")
    except sqlite3.Error as exc:
        return _smoke_report(config, run_id, "failed", "DB_WRITE_FAILED", str(exc))
    finally:
        conn.close()

    report = _smoke_report(
        config,
        run_id,
        status,
        "NO_NEW_CONTENT" if status == "success_noop" else ("MAPPER_SCHEMA_ERROR" if mapper_errors else None),
        "MediaCrawler smoke mapping failed" if mapper_errors else "",
    )
    report["cdp"] = {
        "initial_status": "launch_required" if cdp_result["initial"].error_code else "passed",
        "final_preflight_status": "passed",
        "error_code": None,
        "endpoint_ref": "redacted",
    }
    report["accounts"] = {
        "total": 1,
        "succeeded": 0 if status == "failed" else 1,
        "failed": 1 if status == "failed" else 0,
        "no_new_content": 1 if status == "success_noop" else 0,
    }
    report["contents"] = {"seen": len(rows), "imported": inserted, "dedup_noop": noop}
    report["mapper"] = {"success_rate": 0.0 if mapper_errors else 1.0, "error_code": "MAPPER_SCHEMA_ERROR" if mapper_errors else None}
    _add_report_refs(config, run_id, report)
    return report


def _smoke_failure(config: Any, run_id: str, report_dir: Path, code: str, message: str, cdp_result: dict[str, Any]) -> dict[str, Any]:
    report = _smoke_report(config, run_id, "failed", code, message)
    report["cdp"] = {
        "initial_status": "launch_required" if cdp_result["initial"].error_code else "passed",
        "final_preflight_status": "passed",
        "error_code": None,
        "endpoint_ref": "redacted",
    }
    _add_report_refs(config, run_id, report)
    _write_smoke_reports(report, report_dir)
    return command_error_envelope("smoke-mediacrawler", "runtime", code, message, EXIT_COLLECTION_FAILED, data=report)


def _smoke_report(config: Any, run_id: str, result: str, error_code: str | None, summary: str) -> dict[str, Any]:
    errors = []
    if error_code and error_code != "NO_NEW_CONTENT":
        errors.append({"code": error_code, "summary": redact_text(summary, [str(config.cdp_port), str(config.chrome_user_data_dir), str(config.mediacrawler_root)])})
    return {
        "schema_version": "1.4",
        "command": "smoke-mediacrawler",
        "scenario": "mediacrawler_smoke",
        "run_id": run_id,
        "result": result,
        "cdp": {"initial_status": "failed", "final_preflight_status": "failed", "error_code": error_code, "endpoint_ref": "redacted"},
        "accounts": {"total": 1, "succeeded": 0, "failed": 1 if result == "failed" else 0, "no_new_content": 1 if error_code == "NO_NEW_CONTENT" else 0},
        "contents": {"seen": 0, "imported": 0, "dedup_noop": 0},
        "mapper": {"success_rate": 0.0, "error_code": None},
        "errors": errors,
        "artifact_refs": [],
    }


def _write_smoke_reports(report: dict[str, Any], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "smoke.json"
    md_path = report_dir / "smoke.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_smoke_markdown(report), encoding="utf-8")


def _add_report_refs(config: Any, run_id: str, report: dict[str, Any]) -> None:
    report["artifact_refs"] = [
        artifact_ref(config.storage_dir, config.storage_dir / run_id / "reports" / "smoke.json"),
        artifact_ref(config.storage_dir, config.storage_dir / run_id / "reports" / "smoke.md"),
    ]


def _classify_mediacrawler_failure(process: dict[str, Any]) -> tuple[str, str]:
    text = ""
    stderr_path = process.get("stderr_path")
    if isinstance(stderr_path, str):
        try:
            text = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    lowered = text.lower()
    if "login" in lowered or "qrcode" in lowered or "登录" in text or "扫码" in text:
        return "LOGIN_UI_TIMEOUT", "MediaCrawler reached the login flow but did not complete it"
    if "page.goto" in lowered and "timeout" in lowered:
        return "DOUYIN_UI_CHANGED", "Douyin page navigation timed out after CDP connection"
    if "timeout" in lowered:
        return "ACCOUNT_GUARD_TIMEOUT", "MediaCrawler account smoke timed out"
    return "UNKNOWN_ERROR", "MediaCrawler smoke process failed"


def _smoke_markdown(report: dict[str, Any]) -> str:
    error_lines = "\n".join(f"- {item['code']}: {item['summary']}" for item in report["errors"]) or "- none"
    return "\n".join(
        [
            "# MediaCrawler Smoke Report",
            "",
            f"- Run: `{report['run_id']}`",
            f"- Result: `{report['result']}`",
            f"- Final CDP: `{report['cdp']['final_preflight_status']}`",
            f"- Account succeeded: `{report['accounts']['succeeded']}`",
            f"- Contents imported: `{report['contents']['imported']}`",
            "",
            "## Errors",
            "",
            error_lines,
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
