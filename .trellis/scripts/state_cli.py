#!/usr/bin/env python3
"""CLI wrapper for the Trellis v2 state engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from state_machine import StateMachineError, apply_event, init_task, status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trellis v2 state engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize a harness state machine")
    init.add_argument("task_dir")
    init.add_argument("--kind", choices=["parent", "child"], required=True)
    init.add_argument("--by", choices=["user", "agent", "system"], default="agent")
    init.add_argument("--note", default="")

    event = subparsers.add_parser("event", help="apply one state-machine event")
    event.add_argument("task_dir")
    event.add_argument("event")
    event.add_argument("--by", choices=["user", "agent", "system"], default="agent")
    event.add_argument("--note", default="")

    show = subparsers.add_parser("status", help="show current state-machine status")
    show.add_argument("task_dir")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task_dir = Path(args.task_dir)

    try:
        if args.command == "init":
            result = init_task(task_dir, args.kind, by=args.by, note=args.note)
        elif args.command == "event":
            result = apply_event(task_dir, args.event, by=args.by, note=args.note)
        else:
            result = status(task_dir)
    except StateMachineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
