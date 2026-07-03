# Stage Report: Child 6a v1.4 Media Download Manifest

## Scope

- Child task: `07-02-child-6a-v1-4-media-download-manifest`
- Requirement IDs: `P14-REQ-055`

## Completed Work

- Added a one-shot stdlib-only media localization helper: `hermes_benchmark/media_manifest.py`.
- Added focused tests: `tests/test_media_manifest.py`.
- Ran Oracle PLAN review before real download execution.
- Selected exactly 99 eligible Child 4 rows from the approved full-comments raw root.
- Downloaded/localized exactly 99 local media files under the external run root.
- Wrote external private manifests:
  - `/home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local/selection-manifest.private.jsonl`
  - `/home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local/media-manifest.private.jsonl`
  - `/home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local/download-errors.redacted.jsonl`
  - `/home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local/summary.redacted.json`

## Output Summary

- Run root: `/home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local`
- Selected rows: `99`
- Localized media files: `99`
- Failed downloads: `0`
- Total media bytes: `5392549187`
- Approximate run root size: `5.1G`
- Unique content IDs: `99`
- Unique local media paths: `99`
- Temporary partial files remaining: `0`

| Account ID | Selected | Localized |
|---|---:|---:|
| `douyin_ai_xiaobai_lab` | 9 | 9 |
| `douyin_achuan_ai` | 10 | 10 |
| `douyin_zhuzi_tzfilm` | 10 | 10 |
| `douyin_dongdian_damoxing` | 10 | 10 |
| `douyin_xiaohui_boshi` | 10 | 10 |
| `douyin_mark_tech_workshop` | 10 | 10 |
| `douyin_josh_ai_notes` | 10 | 10 |
| `douyin_shanhai_youling_ai` | 10 | 10 |
| `douyin_kk_xuejie_codex` | 10 | 10 |
| `douyin_muzi_no_code` | 10 | 10 |

## Privacy / Evidence Boundary

- Raw media files remain outside the git repo.
- Full `video_download_url` values are stored only in external private manifests.
- Committed evidence contains paths, counts, hashes, and summaries only.
- No cookies, credentials, local browser endpoints, raw headers, raw downloader logs, raw media, or full signed URL dumps are committed.

## Unfinished Work

- Child 6 transcription smoke has not run in this task.
- No commit, push, built-in archive, or soft archive has been performed.

## Changed Files

- `hermes_benchmark/media_manifest.py`
- `tests/test_media_manifest.py`
- `.trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/task.json`
- `.trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/implement.md`
- `.trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/research/oracle-plan-review.md`
- `.trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/stage-report.md`

## Scope Compliance

- Missed work: none for Child 6a.
- Extra work: none; no transcription, Feishu writes, scheduler, queue, daemon, worker pool, generic acquisition framework, or Child 4 rerun.
- Deviations from PLAN: first download pass had 2 transient network timeouts; reran the same command without refreshing URLs or re-collecting data, reused 97 existing files, and completed 99/99.

## Ponytail Review

- Blocking findings: none.
- Advisory findings: helper stays one-shot and stdlib-only; no CLI contract change and no new dependency.
- Accepted cuts: no downloader framework, no retry service, no queue, no parallel worker pool, no source refresh.
- Rejected cuts and reason: did not remove path/hash/size/redaction checks because they are acceptance and privacy requirements.

## Oracle

- Required: yes.
- Result: pass with execution constraints.
- Output path: `research/oracle-plan-review.md`.
- Session: `child6a-media-download-plan-review-3`.

## Verification

| Command | Result | Evidence |
|---|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_media_manifest.py` | pass | focused helper tests |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m hermes_benchmark.media_manifest` | pass | module self-check |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_transcript_batch.py` | pass | downstream batch runner still imports/runs |
| `python3 -m compileall -q hermes_benchmark/media_manifest.py tests/test_media_manifest.py` | pass | syntax/import compile check |
| `python3 -m json.tool .trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/task.json` | pass | task metadata JSON valid |
| `python3 ./.trellis/scripts/task.py validate 07-02-child-6a-v1-4-media-download-manifest` | pass | Trellis context files valid |
| `python3 -m hermes_benchmark.media_manifest --source-root /home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-full-comments-20260702-local/raw --run-root /home/jym/workspace/_external/hermes-stock-runs/run-child6a-media-download-manifest-20260703-local --repo-root /home/jym/workspace/'Hermes stock' --timeout-seconds 180` | pass | `selected=99`, `downloaded=99`, `failed=0` |
| manifest verifier script | pass | `99` selected, `99` media, `0` errors, `5392549187` bytes, all hashes recomputed |
| whitespace checks for new source/test/task evidence | pass | no output |
| `npx gitnexus detect-changes --repo "Hermes stock" --scope unstaged` | pass | no mapped existing-symbol changes detected |

## Commit Plan

- Files:
  - `hermes_benchmark/media_manifest.py`
  - `tests/test_media_manifest.py`
  - `.trellis/tasks/07-02-child-6a-v1-4-media-download-manifest/{task.json,implement.md,research/oracle-plan-review.md,stage-report.md}`
- Message: `feat: add v1.4 media download manifest`
- Pushed: no

## User Completion Signal

- Raw signal: `提交git，归档，视频不要删除了`
- Received at: `2026-07-03T00:16:35-07:00`
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: keep external downloaded media files; do not delete videos
- Push allowed: no, unless explicitly requested

## Soft Archive Plan

- [x] Completion signal received
- [ ] Commit hash recorded
- [ ] `task.json.meta.staged_delivery.soft_archive_completed = true`
- [ ] Child directory kept in place
- [ ] Child is no longer the active implementation target

## Completion Signal

Waiting for user to say `任务完成`, `验证通过`, `可以提交`, or equivalent.
