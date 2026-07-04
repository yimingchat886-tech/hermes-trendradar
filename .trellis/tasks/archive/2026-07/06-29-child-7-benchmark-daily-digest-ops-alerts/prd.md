# Child PRD: Benchmark Daily Digest And Ops Alerts

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-090, P1-REQ-100

## Goal

Generate the benchmark daily digest for trend-chief and ops alert objects for radar-ops from tracked content and health records.

## Requirements

- Summarize account updates, new content count, high-like/high-discussion/strong-hit candidates, and evidence-insufficient content.
- Produce ops alerts for crawler/import/transcript/Feishu failures and abnormal counts.
- Keep digest/alert outputs traceable to source/account/content/run IDs.
- v1 ops alerts notify exceptions only, not hotspot events.

## Out of Scope

- Feishu live notifications.
- Hotspot/rising trend alerts.
- Weekly strategy report.
- Topic adoption-rate analytics.

## Acceptance Criteria

- [x] Digest fixture includes daily benchmark account update summary.
- [x] Alert fixture includes failure reason and source/run IDs.
- [x] High-performance thresholds match PRDv1.3 defaults.
- [x] Hotspot notifications are absent from parent 1 output.

## Risk Level

- T2: downstream formatting and aggregation.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
