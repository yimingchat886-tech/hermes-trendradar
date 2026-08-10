#!/usr/bin/env python3
"""Agent-facing control plane for Unified Intent Loop v1."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from taskrun import (
    AuthorityError,
    append_binding,
    apply_cutover,
    build_release,
    cancel_task,
    claim_actions,
    close_finding,
    close_task,
    legacy_inventory,
    legacy_records,
    migration_plan,
    plan_task,
    qualify_release,
    rebuild_projections,
    record_attempt,
    record_check_result,
    record_review,
    run_task,
    sync_targets,
    task_status,
)


LEGACY_WRITERS = {
    "add-subtask",
    "archive",
    "archive-orphans",
    "archive-recover",
    "authorize-replacement",
    "claim",
    "complete-child",
    "create",
    "finish",
    "loop-v1",
    "reconcile-historical-replacement",
    "release",
    "remove-subtask",
    "set-base-branch",
    "set-branch",
    "set-scope",
    "soft-archive",
    "start",
}
PUBLIC_COMMANDS = ("plan", "run", "status", "resume", "close", "cancel")


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise AuthorityError("Current directory is not a Git repository")
    return Path(result.stdout.strip()).resolve()


def load_json(path: str) -> Any:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise AuthorityError(f"JSON input is unavailable: {path}")
    return json.loads(candidate.read_text(encoding="utf-8"))


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Intent Loop task control")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Create one stable Task from one intent")
    plan.add_argument("--title", required=True)
    plan.add_argument("--request")
    plan.add_argument("--prd")
    plan.add_argument("--task-id")
    plan.add_argument("--kind", choices=("code", "sync", "release"), default="code")
    plan.add_argument("--base", default="main")
    plan.add_argument("--branch")
    plan.add_argument("--worktree")
    plan.add_argument("--accepted-commit")

    run = commands.add_parser("run", help="Start or resume the stable run; Loop is default")
    run.add_argument("--task")
    run.add_argument("--title")
    run.add_argument("--request")
    run.add_argument("--prd")
    run.add_argument("--accepted-commit")
    run.add_argument("--kind", choices=("code", "sync", "release"), default="code")
    run.add_argument("--actions", help="JSON array of persistent actions")
    run.add_argument("--single", action="store_true")

    status = commands.add_parser("status", help="Read authoritative task state")
    status.add_argument("--task")
    status.add_argument("--json", action="store_true")

    resume = commands.add_parser("resume", help="Resume the same run as Loop")
    resume.add_argument("--task", required=True)

    close = commands.add_parser("close", help="Run authorized local closeout saga")
    close.add_argument("--task", required=True)
    close.add_argument("--authorization-ref", required=True)

    cancel = commands.add_parser("cancel", help="Cancel the original Task in place")
    cancel.add_argument("--task", required=True)
    cancel.add_argument("--authorization-ref", required=True)

    claim = commands.add_parser("action-claim", help=argparse.SUPPRESS)
    claim.add_argument("--task", required=True)
    claim.add_argument("--worker", required=True)

    result = commands.add_parser("action-result", help=argparse.SUPPRESS)
    result.add_argument("--task", required=True)
    result.add_argument("--action", required=True)
    result.add_argument("--result", required=True)
    result.add_argument("--passed", action="store_true")
    result.add_argument("--root-cause")
    result.add_argument("--candidate-digest")
    result.add_argument("--operation-id", required=True)

    check = commands.add_parser("check-record", help=argparse.SUPPRESS)
    check.add_argument("--task", required=True)
    check.add_argument("--action")
    check.add_argument("--result", required=True)

    review = commands.add_parser("review-record", help=argparse.SUPPRESS)
    review.add_argument("--task", required=True)
    review.add_argument("--candidate-digest", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--findings", required=True)
    review.add_argument("--semantic", action="store_true")
    review.add_argument("--operation-id", required=True)

    finding = commands.add_parser("finding-close", help=argparse.SUPPRESS)
    finding.add_argument("--finding", required=True)
    finding.add_argument("--targeted-check", action="append", required=True)
    finding.add_argument("--full-regression", required=True)
    finding.add_argument("--delta-path", action="append", default=[])

    commands.add_parser("projection-rebuild", help="Rebuild task and BOARD views")

    release_build = commands.add_parser("release-build", help=argparse.SUPPRESS)
    release_build.add_argument("--managed-path", action="append", required=True)
    release_build.add_argument("--delete", action="append", default=[])
    release_build.add_argument("--qualification", required=True)
    release_build.add_argument("--qualify", action="store_true")

    sync = commands.add_parser("sync", help="Plan/apply/verify named target slots")
    sync.add_argument("--task", required=True)
    sync.add_argument("--target", action="append", required=True)
    sync.add_argument("--current-source", action="store_true")

    dry = commands.add_parser("migrate-dry-run", help="Render deterministic cutover plan")
    dry.add_argument("--bootstrap-task", required=True)

    migrate = commands.add_parser("migrate-apply", help="Apply backed-up hard cutover")
    migrate.add_argument("--bootstrap-task", required=True)
    migrate.add_argument("--release-id", required=True)
    migrate.add_argument("--continue-legacy", action="append", default=[])

    commands.add_parser("legacy-status", help="Read sealed legacy inventory")
    commands.metavar = "{" + ",".join(PUBLIC_COMMANDS) + "}"
    # argparse parses hidden internal commands but otherwise lists them in root help.
    commands._choices_actions[:] = [
        action for action in commands._choices_actions if action.dest in PUBLIC_COMMANDS
    ]
    return parser


def execute(args: argparse.Namespace) -> object:
    root = repo_root()
    if args.command == "plan":
        return plan_task(
            root,
            title=args.title,
            request=args.request,
            prd_source=Path(args.prd) if args.prd else None,
            task_id=args.task_id,
            task_kind=args.kind,
            base_branch=args.base,
            task_branch=args.branch,
            worktree_path=Path(args.worktree) if args.worktree else None,
            accepted_commit=args.accepted_commit,
            isolate=True,
        )
    if args.command == "run":
        task_id = args.task
        if not task_id:
            if not args.title or not (args.request or args.prd):
                raise AuthorityError("run requires --task or --title plus --request/--prd")
            planned = plan_task(
                root,
                title=args.title,
                request=args.request,
                prd_source=Path(args.prd) if args.prd else None,
                task_kind=args.kind,
                accepted_commit=args.accepted_commit,
                isolate=True,
            )
            task_id = planned["task"]["task_id"]
        elif args.prd:
            if not args.accepted_commit:
                raise AuthorityError("Material PRD revision requires --accepted-commit")
            append_binding(
                root,
                task_id,
                prd_source=Path(args.prd),
                accepted_commit=args.accepted_commit,
                operation_id=f"binding:{task_id}:{args.accepted_commit}",
            )
        actions = load_json(args.actions) if args.actions else None
        return run_task(root, task_id, single=args.single, actions=actions)
    if args.command == "status":
        return task_status(root, args.task)
    if args.command == "resume":
        return run_task(root, args.task)
    if args.command == "close":
        return close_task(root, args.task, authorization_ref=args.authorization_ref)
    if args.command == "cancel":
        return cancel_task(root, args.task, authorization_ref=args.authorization_ref)
    if args.command == "action-claim":
        return {"actions": claim_actions(root, args.task, args.worker)}
    if args.command == "action-result":
        return record_attempt(
            root,
            args.task,
            args.action,
            passed=args.passed,
            root_cause_fingerprint=args.root_cause,
            candidate_digest=args.candidate_digest,
            result=load_json(args.result),
            operation_id=args.operation_id,
        )
    if args.command == "check-record":
        value = load_json(args.result)
        record_check_result(
            root,
            args.task,
            action_id=args.action,
            attempt_no=value["attempt_no"],
            check_id=value["check_id"],
            phase=value["phase"],
            operation_id=value["operation_id"],
        )
        return {"recorded": True}
    if args.command == "review-record":
        return record_review(
            root,
            args.task,
            candidate_digest=args.candidate_digest,
            reviewer_id=args.reviewer,
            findings=load_json(args.findings),
            semantic=args.semantic,
            operation_id=args.operation_id,
        )
    if args.command == "finding-close":
        close_finding(
            root,
            args.finding,
            targeted_checks=args.targeted_check,
            full_regression=args.full_regression,
            delta_paths=args.delta_path,
        )
        return {"closed": args.finding}
    if args.command == "projection-rebuild":
        return rebuild_projections(root)
    if args.command == "release-build":
        manifest = build_release(
            root,
            managed_paths=args.managed_path,
            intentional_deletions=args.delete,
            semantic_qualification=load_json(args.qualification),
        )
        if args.qualify:
            qualify_release(root, manifest)
        return manifest
    if args.command == "sync":
        return sync_targets(
            root,
            args.task,
            [Path(path) for path in args.target],
            current_source=args.current_source,
        )
    if args.command == "migrate-dry-run":
        return {
            "inventory": legacy_inventory(root, bootstrap_task_dir=args.bootstrap_task),
            "plan": migration_plan(root, bootstrap_task_dir=args.bootstrap_task),
        }
    if args.command == "migrate-apply":
        return apply_cutover(
            root,
            bootstrap_task_dir=args.bootstrap_task,
            first_release_id=args.release_id,
            continue_legacy=args.continue_legacy,
        )
    if args.command == "legacy-status":
        return {"records": legacy_records(root)}
    raise AuthorityError(f"Unknown command: {args.command}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in LEGACY_WRITERS:
        print(
            json.dumps(
                {
                    "code": "LEGACY_WRITE_DISABLED",
                    "cutover_report": ".trellis/migration/unified-intent-loop-v1/cutover-report.json",
                    "replacement": "task plan|run|status|resume|close|cancel",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        args = build_parser().parse_args()
        emit(execute(args))
        return 0
    except (AuthorityError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"code": "TASK_ERROR", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
