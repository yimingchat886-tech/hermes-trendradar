# Child PRD: Local Whisper Transcript Pipeline

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-020, P1-REQ-060, P1-REQ-100

## Goal

Implement the local transcript contract around Whisper so benchmark video content can produce `Transcript` records while temporary video files are not retained.

## Requirements

- Represent transcript status: pending, done, failed, not_applicable.
- Store transcript text/segments paths or fixture equivalents.
- Record provider as `local_whisper`.
- Delete or avoid retaining temporary video inputs after successful transcript handling.
- Keep transcript output traceable to `BenchmarkContent`.

## Out of Scope

- Installing Whisper.
- GPU optimization.
- Long-term video storage.
- Full speech quality scoring beyond the PRD statuses.

## Acceptance Criteria

- [ ] Transcript fixture maps to `Transcript`.
- [ ] Failed transcript records preserve error state.
- [ ] Temporary video-retention behavior is covered by a check or dry-run proof.
- [ ] Output does not require real video files for basic validation.

## Risk Level

- T2: local file lifecycle and transcript contract.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
