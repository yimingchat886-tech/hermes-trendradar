# Stage Report: Child 4 v1.4 Douyin Collection Runner

## Scope

- Added a serial Douyin collection runner boundary.
- Used validated profile output as the only runtime account source.
- Used the confirmed 10-account set in fixture coverage:
  Ai小白Lab, 阿川同学, 柱子哥TzFilm, 懂点大模型, 晓辉博士, 马克的技术工作坊,
  Josh的AI笔记, 山海有灵AI, KK学姐, 木子不写代码.
- Normalized MediaCrawler-style rows through the existing importer and upserted
  accepted rows through the SQLite content ledger.
- Isolated account failures and wrote deterministic account/content error rows.
- Kept real MediaCrawler/Douyin execution gated.

## Oracle PLAN Gate

- Browser Oracle session: `child4-douyin-collection-plan-review-4`
- Result: implement boundary, fixture checks, ledger upsert, failure isolation,
  and redaction proof.
- Gate retained: no real external Douyin/MediaCrawler execution without
  validated local inputs and explicit approval.

## Implementation Summary

- Added `hermes_benchmark/collection_runner.py`.
- Added `tests/test_collection_runner.py`.
- No scheduler, queue, daemon, concurrency, Xiaohongshu path, transcription, or
  Feishu writes were added.
- No cookies, tokens, CDP endpoints, raw crawler outputs, or raw videos were
  added.

## Verification

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_collection_runner.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_cli_contract.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_state.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -s tests/test_collection_runner.py tests/test_state.py tests/test_cli_contract.py` | pass, 20 tests |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m hermes_benchmark.collection_runner` | pass |
| `python3 -m hermes_benchmark.cli validate-config --profile profiles/local/hermes.v1.4.douyin.local.json --json` | pass |
| `python3 ./.trellis/scripts/task.py validate 07-01-child-4-v1-4-douyin-collection-runner` | pass |
| redacted smoke manifest/log endpoint scan | pass, no raw CDP WebSocket URL, localhost endpoint, or debug port value |
| `git diff --check` | pass |
| `git diff --no-index --check /dev/null hermes_benchmark/collection_runner.py` | pass, no whitespace output |
| `git diff --no-index --check /dev/null tests/test_collection_runner.py` | pass, no whitespace output |
| `npx gitnexus detect-changes --repo "Hermes stock" --scope unstaged` | no mapped existing-symbol changes detected |

## Local Runtime Gate

- Added local ignored profile inputs under `profiles/local/`.
- Confirmed `.gitignore` ignores `profiles/**/*.local.*`.
- Local profile validation passed:
  `python3 -m hermes_benchmark.cli validate-config --profile profiles/local/hermes.v1.4.douyin.local.json --json`
- Profile hash:
  `sha256:2057cbb607379fba78f59e6c26ea078c41563167b32c45ec5e48150caa5507b6`
- External runtime preflight passed with repo root outside
  `/home/jym/workspace/_external`.
- CDP listener was bound to localhost during smoke and was closed after smoke.

## Real External Smoke

- Account: `木子不写代码` (`douyin_muzi_no_code`).
- Scope: one-account MediaCrawler creator smoke, no comments, no sub-comments,
  serial/low-concurrency command, no 10-account full run.
- MediaCrawler exit code: `0`.
- Raw output location:
  `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-smoke-20260702-local/raw`
- Redacted manifest:
  `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-smoke-20260702-local/child4-smoke-manifest.redacted.json`
- Normalization result: 36 JSONL rows loaded, 36 accepted content rows, 0
  source-health warnings.
- Ledger result: state run `run_f2f74d796eb2d7d9` succeeded; 36 ledger rows
  inserted, 0 conflicts, 0 errors.
- Redaction check: smoke manifest/logs contain no raw CDP WebSocket URL,
  localhost endpoint, or debug port value after post-run redaction.

## Full External Run With Comments

- User approval: `合理安排调度跑一次全量+评论`.
- Schedule used: 10 accounts serial, one MediaCrawler process per account,
  `max_concurrency_num=1`, first-level comments enabled, at most 10 comments per
  video, sub-comments disabled, 20-second gap between accounts.
- Run id: `run-child4-douyin-full-comments-20260702-local`.
- State run id: `run_be39f1deada62360`.
- State status: `partial_failed`.
- Runner result: `partial_success`.
- Redacted manifest:
  `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-full-comments-20260702-local/child4-full-comments-manifest.redacted.json`
- Raw output root:
  `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-full-comments-20260702-local/raw`

| Account | Terminal status | Content rows | Comment rows |
|---|---|---:|---:|
| Ai小白Lab | attempted_success | 9 | 62 |
| 阿川同学 | attempted_success | 16 | 156 |
| 柱子哥TzFilm | attempted_success | 70 | 660 |
| 懂点大模型 | attempted_success | 32 | 140 |
| 晓辉博士 | attempted_failed | 398 | 1800 |
| 马克的技术工作坊 | attempted_success | 32 | 301 |
| Josh的AI笔记 | attempted_success | 77 | 436 |
| 山海有灵AI | attempted_success | 50 | 304 |
| KK学姐 | attempted_success | 122 | 219 |
| 木子不写代码 | attempted_success | 36 | 343 |

- Summary after partial recovery: 842 content JSONL rows, 4421 comment JSONL
  rows, 842 normalized content rows written to the content ledger, 0 ledger
  conflicts.
- Account failure: `晓辉博士` exceeded the per-account 30-minute guard while
  collecting comments. Raw files were already written, so 398 content rows were
  recovered into the ledger after the process timeout. The account remains
  `attempted_failed` because comment collection did not finish cleanly.
- Error rows: 1 deterministic account error for the timeout failure.
- Redaction check: full-run manifest/logs contain no raw CDP WebSocket URL,
  localhost endpoint, or debug port value after post-run redaction.
- CDP listener was closed after the run.

## Ponytail Notes

- Used a serial loop and callback boundary instead of queue/concurrency/daemon.
- Reused existing profile validation, MediaCrawler row importer, redaction helper,
  and SQLite ledger/error helpers.
- No new dependencies.
- Ponytail review: Lean already. Ship.

## Not Run

- Retry or longer second pass for the timed-out `晓辉博士` comment collection.
- Sub-comment collection.
- Media download.
- Scheduler, transcription, Hermes analysis, or Feishu writes.

## Commit / Push

- Completion signal received: yes.
- User completion signal: `提交git`.
- Received at: `2026-07-02T17:49:15-07:00`.
- Commit allowed: yes.
- Explicit limits: commit only; no push and no soft archive.
- Soft archive completed: no.
- Pushed: no.
