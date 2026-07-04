# Child PRD: v1.4 Douyin Media Download Manifest

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-055

## Goal

Localize the user-approved 99 Douyin videos from Child 4 production MediaCrawler output into concrete local media files, then produce a manifest that Child 6 can consume for transcription. This task exists because Child 6 must not contain downloader or acquisition logic.

## Requirements

- Use Child 4 full-comments MediaCrawler output as the only source.
- Select the approved 99 eligible videos:
  - 9 videos from `Ai小白Lab`;
  - 10 videos from each of the other 9 enabled accounts;
  - within each account, highest `liked_count` first.
- Download/localize each selected `video_download_url` to an external run root under `/home/jym/workspace/_external/hermes-stock-runs/`.
- Produce a media manifest with content id, account id, source URL, liked count, local media path, media size, and media hash.
- Keep raw media, signed URLs, cookies, secrets, and raw downloader logs out of git.
- Preserve redacted evidence paths and counts in `stage-report.md`.

## Out of Scope

- Transcription.
- Whisper runtime changes.
- Feishu writes.
- Scheduler, queue service, daemon, worker pool, or generic media acquisition framework.
- Re-running Child 4 collection unless explicitly requested.
- Expanding the current smoke beyond the user-approved 99 videos.

## Acceptance Criteria

- [ ] Selection manifest has exactly 99 unique content IDs with the approved 9 + 9x10 account distribution.
- [ ] Every selected item has a source `video_download_url`.
- [ ] Download manifest has exactly 99 unique readable local regular files outside the git-tracked repo.
- [ ] Every local media file has a byte size greater than 0 and a `sha256:` hash.
- [ ] Failed downloads, if any, have deterministic error evidence and do not silently reduce the manifest count.
- [ ] Evidence is redacted: no cookies, raw credentials, local browser endpoint, or full signed URL dump is committed.
- [ ] Output manifest path is recorded for Child 6.

## Risk Level

- T3: external media download and raw local media handling.
- High-risk trial PLAN: yes.
- Oracle required: yes before real download execution.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.

## Notes

- This task is a prerequisite for Child 6's current 99-item transcription smoke.
