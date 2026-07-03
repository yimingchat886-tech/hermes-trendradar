# Oracle PLAN Review: Child 6a v1.4 Media Download Manifest

- Session: `child6a-media-download-plan-review-3`
- Result: APPROVE, with required execution constraints.
- Transcript: `/home/jym/.oracle/sessions/child6a-media-download-plan-review-3/artifacts/transcript.md`

## Required Execution Constraints

- Keep full `video_download_url` values only in external private manifests.
- Do not commit signed URLs, raw query strings, cookies, secrets, browser endpoints, raw headers, raw media, or verbose downloader logs.
- If a URL is expired, forbidden, unreachable, or non-media, record deterministic failure evidence; do not refresh, reacquire, scrape, rerun Child 4, or use cookies unless explicitly approved.
- Select deterministically by account quota, numeric `liked_count` descending, then stable tie-breaker.
- Download to a temporary file inside the external run root, verify regular readable file, size greater than zero, compute `sha256:...`, then atomically rename to the final path.
- Resolve paths and assert every media file is under `/home/jym/workspace/_external/hermes-stock-runs/` and outside the git repo; reject symlink escapes.
- If fewer than 99 readable media files are localized, produce a failure report but do not mark the manifest complete.

## Minimal Approved Shape

- One-shot script or thin helper only.
- No downloader framework, queue, daemon, worker pool, scheduler, retry service, or new dependency.
- Produce external `selection-manifest.private.jsonl` and `media-manifest.private.jsonl`.
- Commit only redacted `stage-report.md` evidence and task metadata.
