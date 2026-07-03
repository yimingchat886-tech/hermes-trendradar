# Child PRD: v1.4 Whisper Batch Runner

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-050

## Goal

Build the real-plan batch transcription runner and artifact policy before attempting the 100 queued / 80 success threshold smoke.

## Requirements

- Queue content videos from collection results through an explicit temporary video path/resolver boundary.
- Invoke local Whisper via configured `command`/`model`/`device` profile fields; stub-only success is not completion evidence.
- Store transcript artifact refs, artifact hashes, transcript state, and deterministic error records.
- Delete temporary video files after every attempted transcription, including failure paths.
- Do not write raw full transcripts to Feishu.
- Report queued/succeeded/failed/skipped summary that matches persisted state; missing/unusable video input counts as failed.

## Out of Scope

- The 100/80 threshold acceptance smoke; that is child 6.
- Hermes analysis.
- Feishu writes.

## Acceptance Criteria

- [ ] Batch runner queues fixture content only when a concrete temporary video path is available.
- [ ] Missing/unusable video input records deterministic transcript failure instead of fake success.
- [ ] Real verification resolves and invokes the configured local Whisper command/model/device path.
- [ ] Successful transcripts persist artifact refs and artifact hashes, not raw full transcript text in Feishu-bound output.
- [ ] Failed transcripts persist stable error code/error record and transcript state.
- [ ] Temp video cleanup is verified for both success and failure paths.
- [ ] Summary distinguishes queued, succeeded, failed, skipped, and matches SQLite transcript/error state.
- [ ] If command/model/device cannot be resolved or invoked, the task records a deterministic blocker and is not marked done.

## Risk Level

- T2 high-risk because this child validates real local Whisper command/model/device behavior and temp-video deletion.
- High-risk trial PLAN: required.
- Oracle required: yes, before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
