# Implementation Plan: v1.4 Production Hardening And E2E Dry Run

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-080

## Scope

- Verify the assembled v1.4 CLI path.
- Update docs and parent evidence.

## Expected Files

- Verification report or `stage-report.md`.
- Runbook updates if needed.
- Parent `rtm-delta.md` / `subphase-report.md` updates.

## Ponytail Pass

- Blocking findings: adding new features during hardening hides scope creep.
- Advisory findings: fix only blockers found by targeted checks.
- Decision: verify and report.

## Oracle

- Required: yes at parent closeout.
- Reason: final evidence review.
- Output path: parent closeout review note.

## Steps

1. Run focused command contract checks.
2. Run profile/security checks.
3. Run idempotency checks.
4. Run E2E dry-run flow.
5. Update evidence docs.

## Verification

- Command: v1.4 focused check sequence.
- Command: `python3 -m compileall -q hermes_benchmark`
- Command: `git diff --check`

## Rollback

- Revert hardening-only docs/check changes.

## Confirmation Gate

- [ ] User confirmed this PLAN.
- [ ] Parent closeout review completed.
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
