# Child 2: Real Analysis Handoff Consumer

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-030

## Goal

Consume v1.4 handoff packages and persist real Hermes-owned analysis result references without pretending repo-side mock output is real analysis.

## Requirements

- C2-REQ-001: Read an existing handoff package as the input boundary.
- C2-REQ-002: Store Hermes-owned analysis result references with run/content/transcript/package traceability.
- C2-REQ-003: Keep deterministic mock decomposition available only for tests/fixtures.
- C2-REQ-004: Fail closed with stable errors when the handoff package or Hermes result is invalid.

## Out of Scope

- Prompt tuning beyond the minimal invocation/result contract.
- Digest message delivery.
- Human feedback intake.
- Bitable writes.

## Acceptance Criteria

- [ ] The real path bypasses `mock_hermes_output`.
- [ ] Stored result refs link back to package/content/transcript refs.
- [ ] Invalid input/result produces a deterministic error.
- [ ] Existing mock-based tests remain valid as fixture tests only.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-2-v2-0-real-analysis-handoff-consumer`
- Focused tests for handoff/result persistence, then `git diff --check`

## In

- Real analysis consumer contract and persistence refs.

## Out

- Digest sending, feedback intake, M3 Bitable, and v2.1 sources.
