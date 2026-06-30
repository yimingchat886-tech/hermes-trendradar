# Implementation Plan: Local Whisper Transcript Pipeline

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-020, P1-REQ-060, P1-REQ-100

## Scope

- Add transcript status and wrapper behavior.
- Keep real Whisper invocation optional or mockable.
- Prove temp video files are not retained.

## Expected Files

- Transcript wrapper/contract code.
- Transcript fixtures.
- File-lifecycle check.

## Ponytail Pass

- Blocking findings: do not add a queue or media pipeline unless needed.
- Advisory findings: use mock/stub transcript output for the first check.
- Decision: wrapper contract first.

## Oracle

- Required: no.
- Reason: bounded local behavior.

## Steps

1. Reuse child 1 contracts.
2. Add transcript fixture and wrapper boundary.
3. Add temp file cleanup behavior.
4. Verify status and traceability.

## Verification

- Command: transcript wrapper check plus `git diff --check`.
- Expected result: transcript status and temp cleanup pass.

## Rollback

- Remove transcript wrapper, fixtures, and check.

## Confirmation Gate

- [x] User confirmed this PLAN
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
