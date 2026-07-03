# Oracle PRD/PLAN Review

## Session

- Slug: `hermes-v2-1-state-engine-plan-review`
- Session id: `hermes-v2-1-state-engine`
- Result: No-Go until PRD/PLAN blockers are fixed.

## Blockers

1. Two-file write order could advance `task.json` while missing the event log entry.
2. Re-initialization behavior was unspecified.
3. Blocked-state invalid transitions and `blocked_from_state` storage were under-specified.

## Accepted Fixes

- Specify guarded two-file writes and no-mutation tests for event-log write failure.
- Define first init as an event, same-kind re-init as no-op, and opposite-kind re-init as rejected.
- Store `blocked_from_state`; reject invalid blocker open/resolve cases.
- Add tests for status preservation and non-harness no-mutation behavior.

## Decision

Proceed only after these fixes are reflected in PRD, design, implementation plan, and tests.

## Follow-Up Review

- Slug: `hermes-v2-1-state-engine-plan-followup`
- Session id: `hermes-v2-1-state-engine-2`
- Result: Go.

Must-fix before merge:

1. Test rollback when `state-events.jsonl` replacement succeeds but `task.json` replacement fails.
2. Test non-harness no-mutation byte-for-byte with both absent and pre-existing `state-events.jsonl`.
