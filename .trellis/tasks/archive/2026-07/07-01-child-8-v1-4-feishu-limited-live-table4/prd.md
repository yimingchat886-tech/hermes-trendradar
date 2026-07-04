# Child PRD: v1.4 Feishu Limited-Live Table 4

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-070

## Goal

Implement `apply-limited-live` for Hermes-authorized table 4/status-only mutations with fail-closed validation, read-before-write, and write-audit.

## Requirements

- Validate Hermes-issued authorization and expiry.
- Validate operations hash.
- Enforce operation/table/field allowlist from local profile.
- Restrict v1.4 live writes to table 4/status boundary.
- Execute read-before-write and decide create/update/no-op/fail-closed.
- Use `lark-cli` first, official API thin script only if needed.
- Write deterministic audit for all outcomes.

## Out of Scope

- Table 6/7/9 live writes.
- Generic arbitrary Feishu writer.
- Schema update/delete/bulk unvalidated update.
- Writing raw full transcript to Feishu.

## Acceptance Criteria

- [ ] Unauthorized operation exits with authorization error and writes nothing.
- [ ] Non-allowlisted table/field fails closed.
- [ ] Existing identical row is no-op.
- [ ] Valid table 4/status operation writes audit.

## Risk Level

- T3: live external write boundary.
- High-risk trial PLAN: yes.
- Oracle required: yes before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
