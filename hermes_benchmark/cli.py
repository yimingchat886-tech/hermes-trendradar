"""CLI contract skeleton for Hermes benchmark productionization."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Sequence

VERSION = "0.1.0"
EXIT_OK = 0
EXIT_CONTRACT_MISMATCH = 2
ERROR_CONTRACT_MISMATCH = "contract_mismatch"


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
    command.add_argument("--json", action="store_true", help="Print a JSON envelope.")
    command.set_defaults(handler=handler)


def validate_config(args: argparse.Namespace) -> dict[str, Any]:
    return success_envelope(
        "validate-config",
        {
            "profile": args.profile,
            "profile_read": False,
            "validation": "stub",
        },
    )


def healthcheck(args: argparse.Namespace) -> dict[str, Any]:
    return success_envelope(
        "healthcheck",
        {
            "version": VERSION,
            "checks": [{"name": "cli_contract", "status": "ok"}],
        },
    )


def run_daily(args: argparse.Namespace) -> dict[str, Any]:
    return success_envelope(
        "run-daily",
        {
            "profile": args.profile,
            "run_started": False,
            "reason": "stub_only",
        },
    )


def apply_limited_live(args: argparse.Namespace) -> dict[str, Any]:
    return success_envelope(
        "apply-limited-live",
        {
            "profile": args.profile,
            "live_write_attempted": False,
            "allowed_tables": ["table_4"],
            "status_scope": "status_only",
            "reason": "stub_only",
        },
    )


def success_envelope(command: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "mode": "stub",
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


def wants_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
