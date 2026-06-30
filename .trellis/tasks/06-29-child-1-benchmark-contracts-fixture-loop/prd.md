# Child PRD: Benchmark Contracts And Fixture Loop

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-001, P1-REQ-010, P1-REQ-020, P1-REQ-030

## Goal

Create the smallest local, no-credential loop that proves the benchmark tracking object model before any real MediaCrawler, Whisper, Hermes, or Feishu integration.

## Requirements

- Define contracts for benchmark-account tracking objects needed by parent 1.
- Provide fixture examples for account, content, transcript placeholder, source health, and card/topic output placeholders.
- Add one runnable check that validates fixture shape and trace IDs.
- Keep runtime choices minimal; do not add a framework unless the child PLAN proves it is necessary.

## Out of Scope

- Real platform crawling.
- Real Whisper execution.
- Real Hermes calls.
- Real Feishu writes.
- Hotspot/RSS/RAG vector storage.

## Acceptance Criteria

- [ ] Contracts cover the parent-required object fields.
- [ ] Fixtures run without credentials.
- [ ] Duplicate object IDs or missing trace fields fail validation.
- [ ] Later children can import the contracts without guessing field names.

## Risk Level

- T2: foundational contracts, but no external side effects.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
