"""Read-only local CLI for pinned upstream-release candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .core import UpstreamReleaseError, plan_candidate, verify_candidate


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UpstreamReleaseError(message)


def _plan_file(path: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpstreamReleaseError("plan file must contain one local JSON object") from exc
    if not isinstance(value, dict):
        raise UpstreamReleaseError("plan file must contain one local JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("command", choices=("plan", "verify"))
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--official-root", required=True)
    parser.add_argument("--manifest-path")
    parser.add_argument("--scratch-root")
    parser.add_argument("--candidate-output")
    parser.add_argument("--candidate-root")
    parser.add_argument("--plan-file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "plan":
            if not args.scratch_root or args.candidate_root or args.plan_file:
                raise UpstreamReleaseError("plan requires scratch root only")
            result = plan_candidate(
                args.source_root,
                args.official_root,
                args.scratch_root,
                candidate_output=args.candidate_output,
                manifest_path=args.manifest_path,
            )
        else:
            if (
                not args.candidate_root
                or not args.plan_file
                or args.scratch_root
                or args.candidate_output
            ):
                raise UpstreamReleaseError("verify requires candidate root and plan file only")
            result = verify_candidate(
                args.source_root,
                args.official_root,
                args.candidate_root,
                _plan_file(args.plan_file),
                manifest_path=args.manifest_path,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except UpstreamReleaseError as exc:
        print(json.dumps({"error": str(exc), "status": "rejected"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
