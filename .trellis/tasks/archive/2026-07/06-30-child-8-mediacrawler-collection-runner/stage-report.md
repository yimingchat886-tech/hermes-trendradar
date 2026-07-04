# Stage Report: External Runtimes Adapter And Local Deployment Smoke Test

## Task

- Child task: `06-30-child-8-mediacrawler-collection-runner`
- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: `P1-REQ-045`, `P1-REQ-060`, `P1-REQ-065`, `P1-REQ-100`, `P1-REQ-110`
- Status: real MediaCrawler and openai-whisper smoke completed; external evidence retained

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
- Real MediaCrawler smoke — pass:
  - platform: Douyin
  - target video: `7648838418205641994`
  - run root: `/home/jym/workspace/_external/hermes-stock-runs/run-child8-douyin-mark-codex-login-20260701T063157`
  - retained log: `logs/mediacrawler-douyin-detail.log`
  - import proof: `logs/import-proof.json`
  - result: 1 content row, 114 comment rows, 20 first-level comments, 94 second-level comments, `status=importable`
- Real openai-whisper smoke — pass:
  - runtime: `/home/jym/workspace/_external/venvs/openai-whisper`
  - model cache: `/home/jym/workspace/_external/model-cache/openai-whisper`
  - sample: first 60 seconds of the Douyin target video
  - model/device: `tiny` / `cpu`
  - retained proof: `logs/whisper-proof.json`
  - retained transcript: `transcripts/douyin-7648838418205641994-60s.txt`
  - result: `status=done`

## Pending Real Smoke Inputs

Completed in local external workspace. Temporary login state, raw JSONL,
screenshots, downloaded video, and wav sample were kept outside the repo and
cleaned after proof generation.

## Not Done Yet

- No remaining child-8 acceptance blocker for parent task 1 closeout.

## Kept Out Of Scope

- No queue, concurrency, daemon, scheduler, or service mode.
- No vendored MediaCrawler source, Whisper source, model cache, cookies, login state, proxy config, or raw videos.
- No long-term video storage.

## Commit And Archive State

- Completion signal received: yes
- Commit allowed: yes
- Soft archive completed: yes
- Pushed: no
- Unrelated existing dirty files from Trellis update are excluded from this child implementation.

## User Completion Signal

- Raw signal: 提交git，归档parent task 1
- Received at: 2026-07-01T07:55:00-07:00
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: push not requested; parent archive requested
- Push allowed: no

## Spec Update Judgment

- `.trellis/spec/` update needed: no
- Reason: this slice adds child-specific external-runtime smoke contracts, not a reusable repo-wide rule.
- Contract location: `hermes_benchmark/external_runtime.py`, runbook, and this stage report.

## Parent Closeout Soft Archive

- User signal: `提交git，归档parent task 1`
- Received at: 2026-07-01T07:55:00-07:00
- Soft archive completed: yes
- Work commit: `106ecc5`
- Built-in child archive: not used; staged overlay keeps child evidence directories in place.
