# Oracle Plan Review: Child 8 External Runtime Smoke

Oracle session: `child8-external-runtime-plan-review`

## Decision

Proceed with implementation after adding stricter preflight, cookie, import-proof, and cleanup constraints.

## Required Changes Captured

- Use local-only smoke account/video input; do not rely on committed placeholder registry for real platform targets.
- Prove importability by normalizing real MediaCrawler smoke output into child 3's accepted row shape and calling `import_mediacrawler_rows(rows, accounts=[smoke_account])`.
- Reject real execution when cookie files are tracked, unignored inside the repo, or unredacted in manifests/logs.
- Reject unsafe paths: external runtimes, venvs, model caches, run temp roots, and cleanup targets must not live in the main repo.
- Do not allow `shell=True`.
- Configure MediaCrawler output to a run-specific temp directory or record exact output files before cleanup.
- Copy user-provided media into run temp before passing it to cleanup-capable transcript code.
- Keep Whisper model cache outside the repo.
- Record Whisper result status as `done`, `fallback_done`, `blocked`, or `failed`.
- Keep real execution bounded to one account, one public video, one MediaCrawler run, one import proof, and one Whisper run or fallback/blocker.

## Evidence Required Before Reporting Done

- Fake/dry-run manifest with redacted command summary.
- Path preflight result.
- Cookie/input redaction proof.
- MediaCrawler import proof or explicit blocker.
- Whisper transcript, fallback, or explicit blocker.
- GPU detection and selected device.
- Retained transcript/log paths.
- Cleanup proof for run-specific temp artifacts.
- Repo contamination check proving no external source, model cache, cookies, or raw videos entered the repo.
