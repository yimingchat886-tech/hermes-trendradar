# Implementation Plan: v1.4 Whisper Batch Runner

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-050

## Scope

- Add a serial batch runner around the existing transcript boundary.
- Resolve transcription/runtime profile inputs for the real local Whisper command/model/device path.
- Persist transcript artifact refs, hashes, statuses, and deterministic errors through existing SQLite helpers.
- Keep raw full transcript text out of summaries and Feishu-bound output.
- Keep threshold validation for child 6.

## Runtime Config Contract

- Source: `LoadedProfile.profiles_by_ref[root["transcription_profile_ref"]]` and `LoadedProfile.profiles_by_ref[root["runtime_profile_ref"]]`.
- Required transcription fields for real completion evidence:
  - `provider = "local-whisper"`
  - `command_ref` or `command`
  - `model_ref` or `model`
  - `device_ref` or `device`
- Runtime artifact source:
  - `runtime_profile.storage_ref` supplies artifact root.
  - `runtime_profile.database_ref` supplies SQLite path when not using a provided connection.
- Refs are resolved only for this local process:
  - `env:NAME` reads `os.environ["NAME"]`.
  - `file:path` resolves relative to repo root / profile path context where existing profile loading already points.
  - raw command/model/device literals are accepted only for non-sensitive local runtime settings.
- Missing or empty required fields fail closed with a stable runtime config error; they are valid blocker evidence, not completion evidence.

## Expected Files

- `hermes_benchmark/transcript_batch.py`
- `tests/test_transcript_batch.py`

## Ponytail Pass

- Blocking findings: worker pool/concurrency is not needed for first batch runner.
- Advisory findings: downloader/daemon/queue service is not needed for this slice; use a temporary video resolver boundary.
- Decision: serial batch runner with real command smoke evidence.

## Oracle

- Required: yes.
- Reason: this PLAN validates real local Whisper command/model/device behavior and artifact cleanup.
- Current Oracle result: blocked until this PLAN defines runtime config source, artifact/hash/state contract, deterministic error taxonomy, and queue/counting semantics.

## Queue And Counting Semantics

- Candidate content rows come from the child 4 content ledger or focused fixture rows.
- `queued` means a content item has a concrete temporary video path from the caller-supplied resolver.
- `failed` means either:
  - a candidate item failed before queueing because no concrete temporary video path was available, and persisted `transcription_video_missing`; or
  - a queued item failed validation, Whisper execution, or artifact persistence, and persisted the matching stable error code plus transcript failure state.
- `skipped` is reserved only for intentional future pre-filter exclusions that are not attempted and are not transcript failures; child 5 does not need a skipped success path.
- `succeeded` means local Whisper was invoked and a transcript artifact ref/hash was persisted.
- Summary counts must match SQLite transcript rows and deterministic error rows for the same run.
- Missing or unusable video input is counted as `failed`, not `skipped`, to satisfy the PRD failure-record requirement.
- `failed` count includes item-level transcript failure rows for both pre-queue video failures and queued-attempt failures.
- `queued` count includes only items with a concrete temporary video path.
- Therefore `failed` may be greater than the number of queued failures when candidates fail before queueing.

## Artifact / State Contract

- On success, write one transcript artifact JSON under the configured artifact root, compute `sha256:<hex>`, and persist:
  - `record_transcript_state(content_id, model_profile, "done", artifact_ref=..., artifact_hash=...)`
- On failure, persist:
  - `record_error(run_id, "transcript", content_id, error_code, redacted_summary, retryable, redacted_details_ref=...)`
  - `record_transcript_state(content_id, model_profile, "failed", error_code=...)`
- Summary may include `content_id`, status, `artifact_ref`, `artifact_hash`, and `error_code`.
- Summary must not include raw full transcript text.
- No `done` transcript state may be written without a persisted artifact ref and artifact hash.
- If artifact writing, hashing, or state persistence fails, delete the partial artifact when possible; otherwise record the partial path only in redacted error details, never as a done artifact.

## Deterministic Error Taxonomy

- `transcription_config_missing`: required command/model/device/storage profile input is missing.
- `transcription_video_missing`: resolver returned no concrete temp video path.
- `transcription_video_unreadable`: temp video path does not exist or is not readable.
- `transcription_command_failed`: configured local Whisper command exits non-zero or raises.
- `transcription_artifact_failed`: artifact write/hash/persist step fails.

| Error Code | Count | Whisper Attempted | SQLite Transcript State | Error Row | Child Done? |
|---|---|---:|---|---|---|
| `transcription_config_missing` | run-level blocker; per-item rows optional but not required | no | no `done`; if per-item rows are written they must be `failed` with this code | required run/blocker error | no |
| `transcription_video_missing` | `failed` | no | `failed`, `error_code=transcription_video_missing` | required item error | yes, if real command/model/device evidence also exists for at least one attempted item |
| `transcription_video_unreadable` | `failed` | no | `failed`, `error_code=transcription_video_unreadable` | required item error | yes, if real command/model/device evidence also exists for at least one attempted item |
| `transcription_command_failed` | `failed` | yes | `failed`, `error_code=transcription_command_failed` | required item error | yes, if invocation proves resolved command/model/device was attempted |
| `transcription_artifact_failed` | `failed` | yes | `failed`, `error_code=transcription_artifact_failed` | required item error | no, unless another item produced persisted artifact ref/hash success evidence |

## Run-Level Blocker Rule

- If command/model/device/storage cannot be resolved before processing, write deterministic blocker evidence and do not mark child 5 done.
- In that blocker case, do not fabricate per-item success or queue counts. Candidate counts may be reported separately from queued/succeeded/failed.
- If per-item rows are written during a blocker path, they must be `failed` with `transcription_config_missing` and must match the summary.

## Steps

1. Confirm child 4 is soft-archived and use its content ledger as the upstream boundary.
2. Read queued content from state/fixture and require a concrete temporary video path per queued item.
3. Resolve transcription/runtime profile inputs for command/model/device; fail closed if required fields are missing.
4. Invoke the existing temporary-video transcript wrapper per item.
5. Persist artifact refs, artifact hashes, transcript status, and stable error codes.
6. Return a queued/succeeded/failed/skipped summary that matches persisted transcript state.
7. Verify temp cleanup on success and failure paths.

## Verification

- Command: `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_transcript_batch.py`
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 -m hermes_benchmark.transcript_batch`
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_state.py`
- Command: `python3 -m compileall -q hermes_benchmark`
- Command: `git diff --check`
- Command: `npx gitnexus detect-changes --repo "Hermes stock" --scope unstaged`
- Real-plan evidence: stage report must include resolved Whisper command/model/device, exact attempted `content_id`, exact invocation argv with only sensitive values redacted, exit status or deterministic exception/error code, resulting artifact ref/hash or deterministic blocker, temp video path before attempt, and proof the temp video path no longer exists after cleanup.
- State evidence: stage report must include SQLite transcript-state rows and deterministic-error rows proving summary counts match persisted state.
- Stubbed tests may verify control flow only. They are not completion evidence.

## Rollback

- Remove batch module and tests.

## Confirmation Gate

- [ ] User confirmed this PLAN.
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
