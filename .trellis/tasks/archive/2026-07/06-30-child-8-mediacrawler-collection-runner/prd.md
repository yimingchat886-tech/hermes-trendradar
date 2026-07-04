# Child PRD: External Runtimes Adapter and Local Deployment Smoke Test

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-045, P1-REQ-060, P1-REQ-065, P1-REQ-100, P1-REQ-110

## Goal

Create the final external-runtime child for benchmark account tracking. MediaCrawler and openai-whisper are installed and debugged locally as external capabilities under `/home/jym/workspace/_external` with independent virtual environments, while the main repo keeps only adapters, contracts, manifests, logs, transcripts, runbooks, and checks.

## User Decision

- Merge the prior child 8 MediaCrawler runner scope and child 9 Whisper runtime scope into this final child.
- Use CLI/process adapters now.
- Reserve queue, concurrency, and daemon/service mode for future development.
- Install external projects under `/home/jym/workspace/_external` with independent virtual environments.
- The user will provide temporary cookies for testing; they do not enter the repo.
- Real smoke target account/video are local-only inputs and are not taken from the committed placeholder registry.
- Use `openai-whisper`.
- GPU scope is detection, device selection, and CPU fallback only.
- Final smoke standard: 1 account, 1 public video, MediaCrawler raw artifact importable, and Whisper succeeds on a video/audio sample or records explicit fallback.
- Retain transcripts, logs, external runtime installs, venvs, and model caches after smoke so other projects can reuse them; delete only run-specific raw videos, temporary cookies, temporary raw collection artifacts, and similar test-only inputs.

## Requirements

- Read enabled benchmark accounts from the local registry/tracking plan.
- Keep MediaCrawler and openai-whisper outside the main repo.
- Call both external runtimes through CLI/process adapters.
- Document or verify `/home/jym/workspace/_external` layout and independent venv usage.
- Support dry-run/fake-command mode before real external execution.
- Use temporary user-provided cookies only from local ignored files or environment variables.
- Reject real execution when cookie values or cookie file paths are tracked, unignored inside the repo, or not redacted in manifests/logs.
- Reject real execution when external runtime paths, venvs, model caches, run temp roots, or cleanup targets are inside unsafe locations.
- Run one real local MediaCrawler smoke test against 1 user-provided account and 1 public video.
- Prove the MediaCrawler raw artifact is importable by normalizing it into child 3's accepted row shape and calling `import_mediacrawler_rows(rows, accounts=[smoke_account])` before cleanup.
- Run openai-whisper against one video/audio sample or record explicit CPU/GPU fallback/blocker.
- Copy user-provided audio/video samples into the run temp directory before passing them to cleanup-capable transcript code.
- Detect GPU availability, choose device when available, and fall back to CPU when needed.
- Write run manifests and logs with `run_id`, account/source IDs, platform, command summary, redacted sensitive inputs, mode, timestamps, exit code, retained transcript/log paths, and cleanup status.
- Retain transcripts and logs after verification.
- Retain external runtime installs, venvs, and model caches after verification for reuse by other projects.
- Delete raw videos, temporary cookies, temporary raw collection artifacts, and other run-specific test artifacts after verification.

## Out of Scope

- Vendoring or copying MediaCrawler source into the main repo.
- Vendoring or copying Whisper model caches into the main repo; reusable caches may stay outside the repo.
- Committing cookies, login state, proxy settings, or bypass guidance.
- Implementing queue, concurrency, daemon, worker, or service mode now.
- Long-term video storage.
- Feishu, Hermes decomposition, digest, or alert delivery.
- Replacing child 3 import/dedup logic or child 4 transcript contract logic.

## Acceptance Criteria

- [x] External runtime layout uses `/home/jym/workspace/_external` and independent venvs.
- [x] Main repo contains adapters/runbooks/checks only, not MediaCrawler source, Whisper source, model caches, cookies, or raw videos.
- [x] Dry-run/fake-command mode still works before real external execution.
- [ ] Real MediaCrawler smoke test uses 1 user-provided account and 1 public video.
- [ ] MediaCrawler raw artifact is normalized and importable by `import_mediacrawler_rows(rows, accounts=[smoke_account])` before cleanup.
- [ ] openai-whisper records `done`, `fallback_done`, `blocked`, or `failed`; only `done` / `fallback_done` satisfy the success path.
- [x] GPU detection, selected device, and CPU fallback state are recorded.
- [ ] Repo/task evidence is limited to transcripts and logs.
- [ ] External runtime installs, venvs, and model caches remain available outside the repo for reuse.
- [ ] Raw videos, temporary cookies, temporary raw collection artifacts, and other run-specific test artifacts are deleted after verification.

## Risk Level

- T3: external tool install/debug, temporary cookies, real platform collection, and local GPU/runtime behavior.
- High-risk trial PLAN: yes.
- Oracle required: completed before implementation; real external execution still needs user-provided local inputs.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
