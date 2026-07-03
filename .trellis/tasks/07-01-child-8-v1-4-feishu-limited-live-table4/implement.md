# Implementation Plan: v1.4 Feishu Limited-Live Table 4

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-070

## Scope

- Build the restricted apply backend.
- Keep live scope table 4/status-only.

## Expected Files

- `hermes_benchmark/feishu_apply.py`
- Operation/auth/audit fixtures.
- Focused fail-closed tests.

## Ponytail Pass

- Blocking findings: generic Feishu adapter would violate v1.4 scope.
- Advisory findings: reuse dry-run mapping where safe, but do not broaden live writes.
- Decision: table 4/status-only apply path.

## Oracle

- Required: yes.
- Reason: external write and authorization boundary.
- Output path: `research/oracle-plan-review.md`.

## Steps

1. Define operation/auth schema checks.
2. Verify operation hash and allowlist.
3. Add read-before-write abstraction.
4. Add create/update/no-op/fail-closed decision.
5. Persist write-audit.
6. Add fail-closed checks.

## Verification

- Command: unauthorized fixture fails with exit 10.
- Command: non-allowlisted fixture fails closed.
- Command: dry-run/no-op fixture writes audit.
- Command: `git diff --check`

## Rollback

- Remove apply module and fixtures.

## Confirmation Gate

- [ ] User confirmed this PLAN.
- [ ] Oracle high-risk PLAN review completed.
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
