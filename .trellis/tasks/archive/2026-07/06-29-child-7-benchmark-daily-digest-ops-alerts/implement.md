# Implementation Plan: Benchmark Daily Digest And Ops Alerts

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-090, P1-REQ-100

## Scope

- Add digest aggregation.
- Add ops alert output.
- Use PRDv1.3 high-performance thresholds.

## Expected Files

- Digest/alert formatter or module.
- Fixture inputs and expected outputs.
- Focused aggregation check.

## Ponytail Pass

- Blocking findings: do not add notification infrastructure in this child.
- Advisory findings: output objects first, delivery later.
- Decision: format objects only.

## Oracle

- Required: no.
- Reason: no external side effects.

## Steps

1. Reuse account/content/health outputs.
2. Add digest aggregate.
3. Add ops alert aggregate.
4. Verify thresholds and trace fields.

## Verification

- Command: digest/alert check plus `git diff --check`.
- Expected result: digest and alerts match fixture expectations.

## Rollback

- Remove digest/alert files and checks.

## Confirmation Gate

- [x] User confirmed this PLAN
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
