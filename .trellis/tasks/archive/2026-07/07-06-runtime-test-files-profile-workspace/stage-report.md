# Stage Report: Codify runtime test files in Hermes profile workspace

## Acceptance

- [x] Added the runtime-test file boundary to `.trellis/spec/project/external-runtime-asr.md`.
- [x] Linked the boundary from `.trellis/spec/project/index.md` so future runtime/profile/smoke tasks read it.
- [x] Kept the earlier `sync-trellis-harness` task active but paused in notes; it was not archived.

## Verification

- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-06-runtime-test-files-profile-workspace` passed.
- `git diff --check` passed.
- No runtime test artifacts, raw media, caches, local DBs, or profile workspace files were added.
- Ponytail review: Lean already. Ship.

## User Completion Signal

- Raw signal: 提交git，归档
- Received at: 2026-07-07T21:30:28-07:00
- Allows commit: yes
- Allows archive: yes
- Explicit limits: none
- Push allowed: no

## Protocol Gates

Use `.trellis/spec/project/protocol-phrases.md` for completion, commit,
archive, limit, and push wording. Do not copy the phrase table here.
