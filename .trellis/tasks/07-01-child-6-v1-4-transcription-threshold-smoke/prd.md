# Child PRD: v1.4 Transcription Threshold Smoke

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-055

## Goal

After the child 5 batch runner exists and is verified, run the user-approved current transcription smoke with the 99 eligible videos currently available from Child 4 production MediaCrawler output. At least 80 must produce successful transcript artifacts, and every failure must have deterministic evidence. This 99-item smoke is accepted for the current trial run; it must not be reported as satisfying the original 100-queued P14-REQ-055 production threshold.

## Requirements

- Depend on child 5 batch runner and Child 6a media download manifest.
- Use local profile/runtime inputs, not committed secrets.
- Use exactly one approved input source: Child 4 production MediaCrawler output, selecting the highest-`liked_count` eligible videos per account.
- Use the current user-approved 99-item shape: 9 eligible videos from `Ai小白Lab`, and 10 eligible videos from each of the other 9 enabled accounts.
- Depend on Child 6a media download manifest for concrete local media paths.
- Preflight the approved input source before calling the runner: 99 unique content IDs, no duplicate account/content pair, and 99 unique readable local regular media files outside git-tracked project output.
- Reuse the child 5 batch runner and its caller-supplied video resolver. Do not add downloader, queue service, daemon, worker pool, scheduler, or alternate transcription runner logic in this child.
- Record candidate/queued/success/failure/skipped summary, deterministic error rows, transcript rows, artifact refs, artifact hashes, redacted command evidence, and temp media cleanup proof.
- Preserve evidence under an external run root and keep secrets, cookies, raw videos, and raw full transcripts out of git and Feishu-bound output.

## Out of Scope

- Building the batch runner.
- Feishu writes.
- Scheduler.

## Acceptance Criteria

- [ ] The smoke is not attempted before child 5 is verified, Child 6a produces a valid 99-item local media manifest, and Oracle PLAN re-review approves this revision.
- [ ] Input source is Child 4 production MediaCrawler output with `candidate_count == 99` and the approved 9 + 9x10 account distribution.
- [ ] Input preflight proves 99 unique content IDs and 99 unique readable local media paths from Child 6a, with no acquisition fallback inside Child 6.
- [ ] Passing current-smoke evidence has `queued == 99`, `succeeded >= 80`, `failed <= 19`, and `skipped == 0`.
- [ ] SQLite verification is scoped to the Child 6 run id, selected 100 contents, model profile, and artifact namespace, or uses a fresh isolated DB.
- [ ] Every successful item has a transcript artifact ref under the external run root, an existing artifact file, and a recomputed `sha256:` hash matching the recorded hash.
- [ ] Stage report evidence contains refs/hashes only, not raw full transcript text.
- [ ] Every queued attempt has temp media cleanup evidence with `exists_after == false`.
- [ ] Every failed queued item has exactly one failed transcript state and at least one deterministic error row with a stable error code, scoped to that content/run.
- [ ] If the threshold cannot run, blocker evidence explains which gate failed and does not mark the threshold passed.

## Risk Level

- T3: external runtime scale smoke.
- High-risk trial PLAN: yes.
- Oracle required: yes before real 99-item transcription execution.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
