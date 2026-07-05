#!/usr/bin/env python3
"""Verify the latest GitHub Actions workflow run head SHA."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify latest workflow run head SHA.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--workflow", required=True, help="workflow file name or id")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--require-success", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    return parser.parse_args()


def gh_json(path: str, allow_missing: bool) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", path],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if allow_missing and "HTTP 404" in result.stderr:
            return {"workflow_runs": [], "missing_reason": "workflow_not_found"}
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "gh api failed")
    return json.loads(result.stdout)


def main() -> int:
    args = parse_args()
    branch = urllib.parse.quote(args.branch, safe="")
    workflow = urllib.parse.quote(args.workflow, safe="")
    data = gh_json(
        f"/repos/{args.repo}/actions/workflows/{workflow}/runs?branch={branch}&per_page=1",
        args.allow_missing,
    )
    runs = data.get("workflow_runs") or []
    if not runs:
        payload = {
            "ok": bool(args.allow_missing),
            "repo": args.repo,
            "workflow": args.workflow,
            "branch": args.branch,
            "run_found": False,
            "missing_reason": data.get("missing_reason", "no_runs"),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if args.allow_missing else 1

    run = runs[0]
    head_sha = run.get("head_sha")
    conclusion = run.get("conclusion")
    ok = True
    if args.expected_sha:
        ok = ok and head_sha == args.expected_sha
    if args.require_success:
        ok = ok and conclusion == "success"

    payload = {
        "ok": ok,
        "repo": args.repo,
        "workflow": args.workflow,
        "branch": args.branch,
        "run_found": True,
        "html_url": run.get("html_url"),
        "status": run.get("status"),
        "conclusion": conclusion,
        "head_sha": head_sha,
        "expected_sha": args.expected_sha,
        "matches_expected": None if not args.expected_sha else head_sha == args.expected_sha,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
