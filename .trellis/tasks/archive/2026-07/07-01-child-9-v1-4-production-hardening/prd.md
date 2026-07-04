# Child PRD: v1.4 Production Hardening And E2E Dry Run

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-080

## Goal

Close v1.4 with focused contract/security/idempotency checks, an end-to-end dry run, runbook updates, and parent closeout evidence.

## Requirements

- Run CLI contract checks.
- Run config security/redaction checks.
- Run same date + profile idempotency checks.
- Run `validate-config -> healthcheck -> run-daily mock/hermes-handoff -> apply-limited-live dry-run` flow.
- Update handoff/runbook docs if behavior changed.
- Produce closeout report without claiming scheduler or full production service exists.

## Out of Scope

- New feature work beyond gaps found by verification.
- Scheduler/MCP server.
- Table 6/7/9 live writes.

## Acceptance Criteria

- [ ] Focused verification commands pass or have explicit blockers.
- [ ] End-to-end dry run produces parseable JSON summaries and artifact refs.
- [ ] Security check confirms no committed secrets/raw media.
- [ ] Parent `rtm-delta.md` and `subphase-report.md` are updated with evidence.

## Risk Level

- T3 closeout.
- High-risk trial PLAN: conditional.
- Oracle required: parent closeout review yes.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
