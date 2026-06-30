# Implementation Plan: MediaCrawler Import And Dedup

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-030, P1-REQ-040, P1-REQ-050, P1-REQ-100

## Scope

- Add an import boundary for fixture data.
- Map fields to local contracts.
- Deduplicate exact URL and platform content ID.

## Expected Files

- Import adapter/module.
- Fixture input and expected output.
- Focused import/dedup check.

## Ponytail Pass

- Blocking findings: do not add crawler orchestration or credential handling in this child.
- Advisory findings: exact dedup first; title/semantic dedup later only if needed.
- Decision: fixture import only.

## Oracle

- Required: skipped by user-confirmed fixture-only scope.
- Reason: no real crawler execution, credentials, login state, or platform bypass behavior; local checks cover the adapter boundary.

## Steps

1. Inspect child 1 contracts and child 2 account registry.
2. Add MediaCrawler-style fixture input.
3. Implement mapping and exact dedup.
4. Add evidence-state and health-event outputs.

## Verification

- Command: import/dedup check plus `git diff --check`.
- Expected result: fixture duplicates collapse and missing metrics are marked.

## Rollback

- Remove import adapter, fixtures, and check.

## Confirmation Gate

- [x] User confirmed this PLAN via "已确认child task 3无误，执行child task 3"
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
