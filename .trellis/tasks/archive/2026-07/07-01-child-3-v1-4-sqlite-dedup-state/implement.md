# Implementation Plan: v1.4 SQLite Run State And Dedup Ledger

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-030

## Scope

- Add SQLite schema and tiny state helpers.
- Keep migrations simple and local.

## Expected Files

- `hermes_benchmark/state.py`
- Focused state/dedup tests or self-checks.

## Ponytail Pass

- Blocking findings: ORM or migration framework is overkill for this child.
- Advisory findings: use stdlib `sqlite3` and explicit schema creation.
- Decision: stdlib SQLite.

## Oracle

- Required: yes.
- Reason: persistence and idempotency bugs are production-risky.
- Output path: `research/oracle-plan-review.md`.

## Steps

1. Define schema creation.
2. Add run lock helpers.
3. Add content ledger upsert/no-op/conflict behavior.
4. Add errors/write-audit placeholder tables.
5. Add focused rerun/dedup checks.

## Verification

- Command: focused state self-check/test.
- Command: `python3 -m compileall -q hermes_benchmark`
- Command: `git diff --check`

## Rollback

- Remove state module and tests.

## Confirmation Gate

- [x] User confirmed this PLAN.
- [x] Oracle high-risk PLAN review completed.
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
