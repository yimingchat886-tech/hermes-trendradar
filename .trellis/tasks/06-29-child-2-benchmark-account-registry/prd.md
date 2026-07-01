# Child PRD: Benchmark Account Registry

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-010, P1-REQ-030, P1-REQ-100

## Goal

Implement the manually maintained benchmark account registry for about 20 Douyin/Xiaohongshu accounts, with S/A/B/C level, enabled state, daily tracking, and health metadata.

## Requirements

- Support manually maintained account entries.
- Preserve platform, profile URL, level, enabled state, owner, daily tracking frequency, and notes.
- Expose account records to later import/digest children.
- Do not store credentials, cookies, or login state.

## Out of Scope

- Real crawler invocation.
- Automatic account discovery.
- Hermes auto-changing account level.
- Hotspot source registry.

## Acceptance Criteria

- [ ] Registry fixtures can represent the PRDv1.3 account fields.
- [ ] Disabled accounts are excluded from tracking plans.
- [ ] S/A/B/C level is preserved but not auto-mutated by Hermes.
- [ ] Output includes traceable account IDs.

## Risk Level

- T2: product configuration and data model.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
