# Child PRD: v1.4 SQLite Run State And Dedup Ledger

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-030

## Goal

Add durable local SQLite state for safe daily reruns: run lock, content ledger, transcript state, handoff package refs, operations, write-audit, and deterministic errors.

## Requirements

- Use SQLite from the standard library.
- Lock run scope by date + profile hash.
- Implement three-level dedup keys from the v1.4 PRD.
- Record dedup conflicts as errors; do not silently overwrite.
- Store artifact refs rather than raw videos or raw full transcript text.
- Support no-op/resume behavior for same date + profile.

## Out of Scope

- Collection runner.
- Transcription runner.
- Feishu live writes.

## Acceptance Criteria

- [ ] Same date + profile cannot create overlapping active runs.
- [ ] Re-ingesting existing content is a no-op.
- [ ] Dedup conflict writes a deterministic error record.
- [ ] SQLite schema is initialized without external dependencies.

## Risk Level

- T2 high-risk: persistence/idempotency.
- High-risk trial PLAN: yes.
- Oracle required: yes before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
