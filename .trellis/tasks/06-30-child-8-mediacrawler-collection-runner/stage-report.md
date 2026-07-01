# Stage Report: External Runtimes Adapter And Local Deployment Smoke Test

## Task

- Child task: `06-30-child-8-mediacrawler-collection-runner`
- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: `P1-REQ-045`, `P1-REQ-060`, `P1-REQ-065`, `P1-REQ-100`, `P1-REQ-110`
- Status: adapter/dry-run slice implemented; real external smoke pending local inputs

## Implemented

- Added `hermes_benchmark/external_runtime.py`.
- Added `docs/runbooks/external-runtime-smoke.md`.
- Recorded Oracle high-risk PLAN review in `research/oracle-plan-review.md`.
- Added `/home/jym/workspace/_external` runtime layout planning.
- Added path preflight:
  - rejects external runtime paths inside the main repo
  - rejects run temp roots inside the repo
  - rejects cleanup outside the run temp root
  - rejects `shell=True`
  - rejects tracked or unignored repo cookie files
- Added redacted CLI/process dry-run boundary.
- Added local-only smoke account helper.
- Added MediaCrawler row normalization/import proof through child 3's `import_mediacrawler_rows`.
- Added Whisper status semantics: `done`, `fallback_done`, `blocked`, `failed`.
- Added media-copy helper so cleanup-capable code does not delete the user's original sample.
- Added cleanup confinement for run temp artifacts.
- Added redacted manifest writing for real/dry-run evidence.

## Oracle Review Result

- Oracle session: `child8-external-runtime-plan-review`
- Decision: proceed after adding strict preflight, cookie, import-proof, fallback, and cleanup constraints.
- Status: required constraints are represented in code, runbook, and PRD/implementation plan.

## Verification

- `python3 -m hermes_benchmark.external_runtime` — pass
- `python3 -m hermes_benchmark.mediacrawler_import` — pass
- `python3 -m hermes_benchmark.transcript_pipeline` — pass
- `python3 -m compileall -q hermes_benchmark` — pass
- `git diff --check` — pass
- Default layout preflight — pass
- GPU detection — available, selected device `cuda`, GPU: NVIDIA GeForce RTX 4070 SUPER
- `npx gitnexus impact --repo "Hermes stock" validate_preflight` — LOW risk
- `npx gitnexus impact --repo "Hermes stock" write_manifest` — LOW risk
- `npx gitnexus detect-changes --repo "Hermes stock"` — low risk
- `npx gitnexus detect-changes --scope staged --repo "Hermes stock"` — HIGH risk from new-module breadth.
  Targeted impact checks above are LOW; affected flows are new external-runtime and import-proof self-check paths.
- `npx gitnexus status` — up to date at current commit

## Pending Real Smoke Inputs

Real external execution was not run because these local-only inputs are still missing:

- temporary cookie source as ignored local file or environment variable;
- one verified account/handle to use as the smoke target;
- one public video/audio sample or URL;
- confirmed local MediaCrawler checkout/venv command path if already installed.

## Not Done Yet

- Real MediaCrawler smoke against one user-provided account and one public video.
- Real MediaCrawler artifact import proof from actual platform output.
- Real openai-whisper transcript or explicit runtime blocker/fallback.
- Cleanup proof from a real run temp directory.
- Retained real logs/transcripts.

## Kept Out Of Scope

- No queue, concurrency, daemon, scheduler, or service mode.
- No vendored MediaCrawler source, Whisper source, model cache, cookies, login state, proxy config, or raw videos.
- No long-term video storage.

## Commit And Archive State

- Completion signal received: yes
- Commit allowed: yes
- Soft archive completed: no
- Pushed: no
- Unrelated existing dirty files from Trellis update are excluded from this child implementation.

## User Completion Signal

- Raw signal: 提交git
- Received at: 2026-07-01T02:58:42-07:00
- Allows commit: yes
- Allows soft archive: no
- Explicit limits: push not requested; archive not requested
- Push allowed: no

## Spec Update Judgment

- `.trellis/spec/` update needed: no
- Reason: this slice adds child-specific external-runtime smoke contracts, not a reusable repo-wide rule.
- Contract location: `hermes_benchmark/external_runtime.py`, runbook, and this stage report.
