# Implementation Plan: v1.4 Douyin Media Download Manifest

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-055

## Scope

- Build or run only the smallest path needed to turn the approved 99 Child 4 rows into local media files.
- Produce a manifest for Child 6; do not transcribe media here.
- Keep all media under `/home/jym/workspace/_external/hermes-stock-runs/`.

## Expected Files

- `stage-report.md`
- Optional thin downloader/manifest helper if manual download would be too error-prone.
- External media manifest under the run root.

## Ponytail Pass

- Blocking findings: a reusable crawler/downloader framework, queue, daemon, worker pool, or generic acquisition layer is out of scope.
- Advisory findings: prefer a direct one-shot stdlib/CLI download helper over new dependencies.
- Decision: localize the known 99 URLs and emit evidence only.

## Oracle

- Required: yes before real download execution.
- Reason: external media download, signed URL handling, local raw media retention, and evidence/privacy boundaries.
- Output path: `research/oracle-plan-review.md`.

## Selected Inputs

- Source root: `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-full-comments-20260702-local/raw`
- Selection rule: highest `liked_count` eligible videos per enabled account.
- Current approved count: 99 videos total.
- Distribution: 9 videos from `Ai小白Lab`; 10 videos from each other enabled account.

## Steps

1. Confirm Child 4 source files exist and match the 99-item preflight in Child 6.
2. Run Oracle PLAN review before downloading.
3. Build the selection manifest from Child 4 JSONL rows:
   - parse each account's `creator_contents_2026-07-02.jsonl`;
   - keep rows with `video_download_url`;
   - sort by numeric `liked_count` descending per account;
   - select the approved 9 + 9x10 distribution.
4. Download/localize each selected video to an external run root.
5. Verify each local media path:
   - local regular file;
   - readable;
   - outside git-tracked repo;
   - unique path;
   - size greater than 0;
   - `sha256:` hash recorded.
6. Produce a Child 6 media manifest with only refs, hashes, counts, and redacted source evidence.
7. Record deterministic error evidence if any selected item cannot be downloaded; do not pass a partial manifest as complete.

## Verification

- Required result:
  - selected content count: `99`;
  - downloaded local media count: `99`;
  - unique content IDs: `99`;
  - unique local media paths: `99`;
  - account distribution: approved 9 + 9x10 shape;
  - every item has file size > 0 and `sha256:` hash;
  - no committed raw media, cookies, secrets, or full signed URL dump.

## Rollback

- Remove only this task's external run root artifacts and task evidence references.
- Do not delete Child 4 raw output or unrelated external runs.

## Confirmation Gate

- [x] User confirmed this PLAN via "确认child task 6a".
- [x] Oracle high-risk PLAN review completed: `research/oracle-plan-review.md`.
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
