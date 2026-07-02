"""CLI contract skeleton for Hermes benchmark productionization."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Sequence

from .profile import ProfileError, load_profile, validate_profile

VERSION = "0.1.0"
EXIT_OK = 0
EXIT_CONTRACT_MISMATCH = 2
EXIT_CONFIG_INVALID = 2
ERROR_CONTRACT_MISMATCH = "contract_mismatch"
ERROR_CONFIG_INVALID = "config_invalid"


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

    if getattr(args, "json", False):
        print_json(payload)
    else:
        print(f"{payload['command']}: {payload['mode']} ok")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(prog="hermes-benchmark")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--json", action="store_true", help="Print a JSON envelope.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    add_command(subparsers, "validate-config", validate_config, profile=True)
    add_command(subparsers, "healthcheck", healthcheck)
    add_command(subparsers, "run-daily", run_daily, profile=True)
    add_command(subparsers, "apply-limited-live", apply_limited_live, profile=True)
    return parser


def add_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    handler: Callable[[argparse.Namespace], dict[str, Any]],
    *,
    profile: bool = False,
) -> None:
    command = subparsers.add_parser(name)
    if profile:
        command.add_argument("--profile", default="", help="Path to a local runtime profile.")
        command.add_argument("--config", default="", help="Compatibility alias for --profile.")
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
    return success_envelope(
        "healthcheck",
        {
            "version": VERSION,
            "checks": [{"name": "cli_contract", "status": "ok"}],
        },
    )


def run_daily(args: argparse.Namespace) -> dict[str, Any]:
    profile_path = profile_arg(args)
    return success_envelope(
        "run-daily",
        {
            "profile": profile_path,
            "run_started": False,
            "reason": "stub_only",
        },
    )


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


def wants_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
