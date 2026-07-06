# Child 5: M3 Write-Table Gate

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-060

## Goal

Record the M3 Bitable decision gate. This child does not implement writes unless message-flow evidence and explicit decisions already exist.

## Requirements

- C5-REQ-001: Check whether the message-first flow has run for at least 2 weeks.
- C5-REQ-002: Record the authorization trust-root decision before any live write child exists.
- C5-REQ-003: Record table 2/table 3 keep/drop decision before any live write child exists.
- C5-REQ-004: If conditions are not met, close with a no-implementation decision and leave Bitable work uncreated.
- C5-REQ-005: If conditions are met, create a separate child for the narrow write implementation instead of expanding this gate child.

## Out of Scope

- Implementing Bitable writes by default.
- Generic Feishu adapter.
- Table 6/7/9 writes.
- Weakening allowlist or fail-closed behavior.

## Acceptance Criteria

- [ ] 2-week message-flow evidence is present or explicitly missing.
- [ ] Authorization trust root is decided or recorded as blocking.
- [ ] Table 2/3 decision is decided or recorded as blocking.
- [ ] No live write implementation is added inside this gate child.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-5-v2-0-m3-write-table-gate`
- `git diff --check`

## In

- M3 decision evidence and next-child decision.

## Out

- Live Bitable implementation unless a separate child is created after the gate passes.
