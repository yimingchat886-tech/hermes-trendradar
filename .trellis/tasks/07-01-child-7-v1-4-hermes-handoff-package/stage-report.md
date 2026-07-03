# Stage Report: Child 7 v1.4 Hermes Handoff Package

## Scope

- Tightened child acceptance criteria around the v1.4 handoff package schema,
  required content item fields, `analysis_package_ref`, and exit code `6`.
- Added `hermes_benchmark/handoff.py`.
- Wired `run-daily --analysis-mode hermes-handoff --json` to build, validate,
  write, and persist an analysis package ref.
- Added focused package and CLI contract checks.

## Implemented

- Handoff package schema validates:
  - `schema_version`
  - `package_id`
  - `run_id`
  - `profile_hash`
  - `mode`
  - `contents[]`
- Each content item includes:
  - `content_id`
  - `platform`
  - `account_id`
  - `account_display_name`
  - `source_url`
  - `title_or_caption_raw`
  - `publish_at`
  - `collected_at`
  - `transcript_status`
  - `transcript_artifact_ref`
  - `dedup_key`
- `run-daily` in `hermes-handoff` mode:
  - requires `--profile`
  - reads existing SQLite content/transcript refs
  - writes `analysis_package.json` under the runtime artifact root
  - records the package ref through `record_analysis_package_ref`
  - returns `analysis_package_ref` in the JSON envelope
- Invalid package validation returns exit code `6` with
  `error.code = handoff_package_invalid`.

## Kept Out Of Scope

- No real Hermes LLM call.
- No prompt or skill versioning.
- No Feishu live writes.
- No scheduler, downloader, queue, daemon, or worker pool.
- No new dependency.

## Acceptance Evidence

- Handoff package validates against the v1.4 schema: pass.
- Content item includes all tightened fields: pass.
- Mock mode remains deterministic and does not create a package: preserved;
  the existing no-profile `run-daily --json` stub contract still passes.
- `run-daily --analysis-mode hermes-handoff --json` returns
  `analysis_mode = hermes-handoff`, writes `analysis_package_ref`, and the
  referenced file validates: pass.
- Invalid package returns exit code `6` and JSON error code
  `handoff_package_invalid`: pass.

## Verification

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_handoff.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_cli_contract.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_state.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_transcript_batch.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -s -q tests/test_handoff.py tests/test_cli_contract.py tests/test_state.py tests/test_transcript_batch.py` | pass, 26 tests |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m hermes_benchmark.handoff` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q hermes_benchmark` | pass |
| `python3 ./.trellis/scripts/task.py validate 07-01-child-7-v1-4-hermes-handoff-package` | pass, 5 implement entries and 6 check entries |
| `git diff --check` | pass |

## GitNexus

- `npx gitnexus impact -r "Hermes stock" -f hermes_benchmark/cli.py run_daily`
  before edit: LOW risk, 0 impacted symbols/processes.
- `npx gitnexus detect-changes -r "Hermes stock" --scope unstaged`:
  medium risk, 7 files, 13 symbols, 1 affected flow.
- The detected flow points at existing `cli.py` smoke helper symbols after line
  movement; focused CLI and smoke-adjacent contract tests pass.

## Ponytail Notes

- Reused existing profile loading, runtime artifact ref, SQLite package ref,
  and transcript state helpers.
- Used plain dict schema validation instead of adding a schema dependency.
- Kept package generation separate from Hermes analysis and Feishu writes.

## Commit / Push

- Completion signal received: no.
- Commit allowed: no.
- Soft archive completed: no.
- Pushed: no.
