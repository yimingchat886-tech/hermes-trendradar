# Implementation Plan: v1.4 Transcription Threshold Smoke

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-055

## Scope

- Run only one approved 99-item transcription smoke path after child 5 and Child 6a are verified.
- Produce threshold evidence or a blocker report, not new core runner logic.
- Reuse child 5 `run_transcript_batch` and its caller-supplied video resolver.
- Do not add downloader, queue service, daemon, worker pool, scheduler, alternate transcription runner, or Feishu writes in this child.

## Expected Files

- `stage-report.md`
- Optional thin smoke harness if needed to load an approved manifest and call child 5 runner.
- Evidence references under external run root.
- `research/oracle-plan-review.md`

## Ponytail Pass

- Blocking findings: adding alternate runner logic here duplicates child 5.
- Blocking findings: adding downloader, queue service, daemon, worker pool, scheduler, or new dependency here expands child 6 beyond threshold validation.
- Advisory findings: a thin manifest-to-runner harness is acceptable only if manual invocation would be more error-prone.
- Decision: smoke/evidence only; if the Child 6a 99-media manifest is missing or invalid, write a blocker report instead of building acquisition infrastructure.

## Oracle

- Required: yes.
- Reason: expensive external runtime threshold and evidence interpretation.
- Output path: `research/oracle-plan-review.md`.
- Status: previous strict-100 PLAN was approved in `child6-transcript-threshold-plan-review-3`; revised 99-item PLAN plus Child 6a dependency requires re-review before execution.

## Selected Execution Inputs

- Input source: production Child 4 10-account Douyin output.
- Selection rule: for each enabled account, select up to the 10 videos with highest `liked_count` from MediaCrawler creator contents.
- Account shape: current user-approved 99 videos: 9 from `Ai小白Lab`, and 10 from each of the other 9 enabled accounts.
- Media source: Child 6a download manifest with concrete local media paths.
- Local profile: `profiles/local/hermes.v1.4.douyin.local.json`.
- Transcription profile: `profiles/local/transcription.v1.4.local.json`.
- Whisper runtime:
  - command: `/home/jym/workspace/_external/venvs/openai-whisper/bin/whisper`
  - model: `medium`
  - device: `cuda`
  - cache/model dir: `/home/jym/workspace/_external/model_cache/whisper`
  - timeout seconds: `1800`
- Current boundary: Child 6 consumes local media paths only. Downloading/localizing videos belongs to Child 6a.
- Current source preflight: recorded in `research/input-source-preflight.md`; user approved proceeding with the 99 eligible video rows currently available from Child 4 output.

## Steps

1. Confirm child 5 is closed for execution:
   - `soft_archive_completed == true` in child 5 `task.json`.
   - Child 5 implementation commit is recorded.
   - Child 5 stage report shows real Whisper smoke and cleanup evidence.
2. Confirm Oracle PLAN review approves this tightened plan before any real transcription run.
3. Select the approved input source:
   - production Child 4 MediaCrawler output;
   - 10 enabled accounts from `profiles/local/accounts.douyin.local.json`;
   - approved current 99-item selection by highest `liked_count`.
4. Preflight the selected manifest before calling child 5:
   - exactly 99 rows;
   - 99 unique content IDs;
   - approved account distribution: 9 from `Ai小白Lab`, 10 from each other enabled account;
   - no duplicate account/content pair;
   - 99 unique local media paths;
   - every media path is local-only, exists at preflight time, is a readable regular file, lives outside git-tracked project output, and requires no downloader/acquisition fallback.
5. Prepare local-only runtime inputs:
   - external run root under `/home/jym/workspace/_external/hermes-stock-runs/`;
   - fresh isolated SQLite DB under that run root, or an approved copied state DB with verification scoped to this Child 6 run id, selected 100 contents, model profile, and artifact namespace;
   - local profile/runtime refs with no committed secrets;
   - resolved Whisper command, model, device, timeout, and model cache path.
6. Run the current transcription smoke by passing the 99 selected contents and a manifest-backed `video_resolver` into child 5 `run_transcript_batch`.
7. Record the run summary and row-level evidence:
   - `candidate_count`, `queued`, `succeeded`, `failed`, `skipped`;
   - per-item content id, account id, queued flag, status, stable error code if failed;
   - transcript row count and error row count scoped to the selected run/content set;
   - artifact refs under the external run root, existing artifact paths, recorded hashes, and recomputed SHA-256 matches;
   - redacted command/log refs and temp media cleanup proof showing `exists_after == false` for every queued attempt.
8. Record blocker evidence instead of running or passing the threshold if any required gate is missing.

## Verification

- Command: exact command is produced after the Child 6a media manifest exists. It must invoke a thin harness or direct Python call that uses child 5 `run_transcript_batch`; it must not call a new runner implementation.
- Required command shape:
  - `PYTHONDONTWRITEBYTECODE=1 python3 <child6-harness-or-script> --profile <local-profile> --manifest <99-item-media-manifest> --db <external-run-db> --run-root <external-run-root> --timeout-seconds <n>`
- Pass criteria:
  - manifest preflight passes all uniqueness, locality, readability, account distribution, and outside-git checks
  - `candidate_count == 99`
  - `queued == 99`
  - `succeeded >= 80`
  - `failed <= 19`
  - `skipped == 0`
  - SQLite transcript rows scoped to this run/content/model/artifact namespace equal `succeeded + failed`
  - every successful item has an artifact ref under the external run root, an existing artifact file, and a recomputed SHA-256 matching the recorded `sha256:` hash
  - stage report evidence contains only refs/hashes/counts/redacted commands, not raw full transcript text
  - every queued temp media path has cleanup proof with `exists_after == false`
  - every failed queued item has exactly one failed transcript state and at least one deterministic error row with a stable error code, scoped to the selected content item/run evidence
- Blocker criteria:
  - fewer than 99 concrete local media paths;
  - duplicate content IDs, duplicate account/content pairs, or distribution other than the approved 9 + 9x10 shape;
  - any selected media path is non-local, missing, unreadable, not a regular file, inside git-tracked output, or needs acquisition fallback;
  - local Whisper command/model/device/cache is unavailable;
  - profile/runtime refs are unsafe or contain committed secrets;
  - external runtime or media acquisition constraints make the smoke unsafe;
  - Oracle PLAN review blocks execution.

## Rollback

- Remove only smoke helper/evidence references added by this child.

## Currently Missing Before Execution

- Child 6a media download manifest with 99 concrete local media paths.
- Exact 99-item manifest path with concrete local media paths, uniqueness preflight result, and account/content distribution evidence.
- Exact external run root for child 6.
- Exact SQLite DB source/path for the threshold run.
- Final user confirmation that the tightened PLAN may execute.

## Confirmation Gate

- [ ] User confirmed this PLAN.
- [ ] Oracle high-risk PLAN re-review completed for the 99-item revision.
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
