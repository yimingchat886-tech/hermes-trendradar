# Implementation Plan: Feishu Table Sync Dry-Run

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-080, P1-REQ-100

## Scope

- Add Feishu field mapping for parent 1 tables.
- Implement dry-run operations.
- Validate responsibility and idempotency rules.

## Expected Files

- Feishu mapping/config files.
- Dry-run sync command or module.
- Dry-run fixtures/checks.

## Ponytail Pass

- Blocking findings: custom Feishu adapter would violate PRDv1.3.
- Advisory findings: dry-run first; live mode later.
- Decision: map and dry-run only.

## Oracle

- Required: decide before implementation.
- Reason: external write and permission boundary.

## Steps

1. Reuse child outputs.
2. Define table/field mapping.
3. Add dry-run operation builder.
4. Add checks for manual fields and idempotency keys.

## Verification

- Command: Feishu dry-run check plus `git diff --check`.
- Expected result: dry-run operations are deterministic and no manual fields are overwritten.

## Rollback

- Remove mapping, dry-run code, fixtures, and checks.

## Confirmation Gate

- [x] User confirmed this PLAN via "确认 child task 6，在新 branch 上执行"
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
