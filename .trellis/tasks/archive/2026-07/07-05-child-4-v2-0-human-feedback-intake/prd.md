# Child 4: Human Feedback Intake

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-050

## Goal

Record the smallest useful human feedback loop from internal-group adopt/reject decisions into SQLite.

## Requirements

- C4-REQ-001: Add a minimal adopt/reject feedback input contract.
- C4-REQ-002: Persist feedback in SQLite with run/content/result traceability.
- C4-REQ-003: Make repeated feedback writes idempotent or explicitly conflict-safe.
- C4-REQ-004: Keep rule/RAG promotion as a future manual/audited step.

## Out of Scope

- RAG/vector ingestion.
- Automatic rule enabling.
- Full moderation workflow.
- Bitable writes.

## Acceptance Criteria

- [ ] Adopt/reject feedback can be recorded locally.
- [ ] Feedback records include traceable source/result IDs.
- [ ] Duplicate feedback is safe and deterministic.
- [ ] No automatic rule/RAG promotion occurs.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-4-v2-0-human-feedback-intake`
- Focused SQLite feedback tests, then `git diff --check`

## In

- Minimal feedback intake and SQLite persistence.

## Out

- RAG, automatic learning promotion, moderation suite, and Bitable writes.
