# Implementation Plan: Benchmark Account Registry

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-010, P1-REQ-030, P1-REQ-100

## Scope

- Add account registry representation and daily tracking plan output.
- Validate fixture accounts.
- Keep secrets out.

## Expected Files

- Registry/config file or module.
- Fixture account list.
- Focused validation check.

## Ponytail Pass

- Blocking findings: a database migration is overkill unless child 1 proves persistent storage is needed now.
- Advisory findings: use a simple local config file first.
- Decision: manual registry first.

## Oracle

- Required: no.
- Reason: no credentials or external writes.

## Steps

1. Reuse child 1 contracts.
2. Add account registry fixture/config.
3. Add daily tracking plan output for enabled accounts.
4. Verify disabled and malformed accounts fail safely.

## Verification

- Command: registry validation check plus `git diff --check`.
- Expected result: valid fixtures pass; invalid registry examples fail in the check.

## Rollback

- Remove registry files and validation check.

## Confirmation Gate

- [x] User confirmed this PLAN via "执行 Task 2"
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
