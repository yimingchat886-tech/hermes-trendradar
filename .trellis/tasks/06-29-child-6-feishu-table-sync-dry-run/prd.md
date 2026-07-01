# Child PRD: Feishu Table Sync Dry-Run

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-080, P1-REQ-100

## Goal

Map parent 1 local objects to Feishu table operations and prove the sync plan with dry-run output before any live Feishu writes.

## Requirements

- Map tables 1, 2, 3, 4, 6, 7, and 9 for parent 1 outputs.
- Preserve field responsibility: `script_generated`, `hermes_managed`, `manual`.
- Prefer `lark-cli`; allow official API thin scripts only for gaps.
- Emit dry-run create/update operations with idempotency keys.
- Do not expose internal tables to external group members.

## Out of Scope

- Live Feishu writes without later confirmation.
- Custom Feishu/Bitable adapter.
- Complex external feedback entry.
- Table 5 hotspot radar and table 8 publishing review.

## Acceptance Criteria

- [ ] Dry-run output lists target table, operation, object ID, field mapping, and idempotency key.
- [ ] Manual fields are never overwritten by script/Hermes operations.
- [ ] Missing table IDs or credentials fail before live mode.
- [ ] Table 9 card output is decoupled from internal table fields.

## Risk Level

- T3: external API/write boundary.
- High-risk trial PLAN: yes.
- Oracle required: decide before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
