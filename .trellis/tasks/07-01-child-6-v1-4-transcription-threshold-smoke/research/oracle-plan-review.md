# Oracle PLAN Review: Child 6 v1.4 Transcription Threshold Smoke

## Scope

- Task: `.trellis/tasks/07-01-child-6-v1-4-transcription-threshold-smoke`
- Review type: high-risk child PLAN review
- Requirement: P14-REQ-055
- Reviewed plan files: `prd.md`, `implement.md`
- Reviewed implementation boundary: reuse child 5 `run_transcript_batch`; no downloader, daemon, queue service, worker pool, scheduler, alternate transcription runner, new dependency, or Feishu writes.

## Review 1

- Session: `child6-transcript-threshold-plan-review`
- Result: BLOCK
- Summary: PLAN needed stricter manifest uniqueness, concrete readable local media preflight, scoped SQLite verification, artifact hash recomputation/location checks, cleanup `exists_after == false`, and explicit failed-item evidence cardinality.

## Review 2

- Session: `child6-transcript-threshold-plan-review-2`
- Result: BLOCK
- Summary: PLAN still allowed a media-path uniqueness exception. Oracle required non-unique local media paths to produce blocker evidence, not pass evidence.

## Review 3

- Session: `child6-transcript-threshold-plan-review-3`
- Result: APPROVE
- Completed at: 2026-07-02T21:07:04-07:00
- Summary: Revised PLAN is sufficient to proceed to implementation/execution after execution-time inputs are supplied. Passing now requires exactly 100 rows, 100 unique content IDs, exactly 10 accounts x 10 distinct contents, no duplicate account/content pair, and 100 unique local media paths that are local, existing, readable, regular files outside git-tracked output with no acquisition fallback.

## Remaining Execution-Time Inputs

- Approved input source: child 4 production output or approved local fixture manifest.
- Exact manifest path and preflight result.
- Safe local profile/runtime refs with no committed secrets.
- External run root under `/home/jym/workspace/_external/hermes-stock-runs/`.
- Fresh isolated SQLite DB, or approved copied DB with verification scoped to the Child 6 run id, selected contents, model profile, and artifact namespace.
- Resolved Whisper command, model, device, cache path, and timeout.
- Exact execution command using child 5 runner through a thin harness or direct Python invocation.
- Final user confirmation before execution and `task.json.meta.staged_delivery.plan_confirmed = true`.

## Post-Review Plan Change

- On 2026-07-02, user approved proceeding with the 99 eligible Child 4 videos currently available and requested a separate download task.
- This materially changes the approved strict-100 PLAN. Child 6 now requires Oracle re-review before real 99-item transcription execution.
