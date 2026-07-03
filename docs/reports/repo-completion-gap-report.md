# Repo Completion And Production Gap Report

Date: 2026-07-01

## Target Interpreted From User Goal

The desired production state is:

```text
Hermes can call this repo as an independently deployed tool.
The tool runs on a schedule.
It tracks benchmark accounts, collects new content, transcribes it, lets Hermes decompose it,
generates cards and topic-pool supplements, then updates Feishu benchmark/account tracking tables.
```

This report uses that target, not the narrower child-task proof scope, as the completion bar.

## Current Bottom Line

The repo has completed a useful v1 proof layer, but it is not yet independently deployable.

Current state:

- Local contracts and fixture checks: mostly complete.
- External runtime smoke boundary: partially complete.
- End-to-end production runner: not complete.
- Hermes invocation surface: not complete.
- Live Feishu scheduled update: not complete.

Overall completion against the target is roughly **40%**.

The missing 60% is mostly integration and production operation work: stable CLI/MCP entrypoint, real account config, real collection runner, real Hermes call, live Feishu write, persistence, scheduler, deployment packaging, and operational recovery.

## What Is Completed

| Capability | Current implementation | Completion for proof | Completion for production |
|---|---|---:|---:|
| Shared data contracts | `hermes_benchmark/contracts.py` defines source/account/content/transcript/topic/RAG/health contracts and validation. | High | Medium |
| Fixture loop | `hermes_benchmark/fixtures.py` proves no-credential object model. | High | Low |
| Account registry | `hermes_benchmark/account_registry.py` defines 20 placeholder Douyin/Xiaohongshu accounts and daily plan. | Medium | Low |
| MediaCrawler import | `hermes_benchmark/mediacrawler_import.py` imports MediaCrawler-style fixture rows and dedups exact duplicates. | High | Medium |
| Transcript wrapper | `hermes_benchmark/transcript_pipeline.py` creates transcript records and handles failure/temp cleanup boundary. | Medium | Low-Medium |
| Hermes decomposition shape | `hermes_benchmark/decomposition.py` validates decomposition, card, and topic supplement output shape. | High | Low-Medium |
| Feishu mapping | `hermes_benchmark/feishu_dry_run.py` maps parent-1 tables and emits deterministic dry-run operations. | High | Low |
| Daily digest/alerts | `hermes_benchmark/daily_digest.py` builds local digest and ops alert objects from child outputs. | Medium | Low-Medium |
| External runtime smoke | `hermes_benchmark/external_runtime.py` adds path/cookie/process/redaction/cleanup guards and smoke proof helpers. | Medium-High | Medium |
| Handoff docs | `docs/runbooks/external-runtime-smoke.md` and `docs/runbooks/production-deployment-handoff.md`. | Medium | Medium |

## What Is Not Completed

### 1. Hermes-callable tool surface

Missing:

- No MCP server.
- No packaged production CLI.
- No JSON input/output command contract.
- No stable command names, arguments, exit codes, or error schema.

Impact:

- Hermes cannot reliably call this repo as a tool today.
- `python3 -m hermes_benchmark.*` entries are self-checks, not production commands.

### 2. Independent deployment

Missing:

- No `pyproject.toml`, requirements file, lock file, package metadata, install command, Dockerfile, systemd unit, or deployment script.
- No environment variable contract.
- No deployment health check command.

Impact:

- The repo can be run from source by a developer, but cannot yet be handed to Hermes as an installable/deployable runtime.

### 3. Scheduled production run

Missing:

- No cron/systemd timer/scheduler.
- No single `run daily` orchestration function.
- No retry, no backoff, no resume state.
- No run lock to prevent overlapping scheduled runs.

Impact:

- It cannot yet "定时在飞书更新追踪博主表".

### 4. Real benchmark account source

Missing:

- Current account rows are placeholder fixtures.
- No local-only production account config.
- No Feishu-read account source.
- No account verification/update workflow.

Impact:

- The system does not yet know which real bloggers/accounts to track in production.

### 5. Real collection pipeline

Partially present:

- One external MediaCrawler smoke proved a target can produce importable output.

Missing:

- No multi-account MediaCrawler runner.
- No daily incremental collection.
- No per-platform credential/cookie loading contract.
- No production artifact normalization from real MediaCrawler output beyond proof.

Impact:

- Real collection is proven once, but not automated for all benchmark accounts.

### 6. Real transcription pipeline

Partially present:

- Transcript wrapper and one historical Whisper smoke proof exist; current production tests use FunASR.

Missing:

- No batch transcription runner.
- No media download/copy orchestration tied to collected content.
- No model/device selection policy for production.
- No persisted transcript artifacts indexed by content ID.

Impact:

- The repo can represent transcript output, but does not yet produce transcripts for daily collected content.

### 7. Real Hermes decomposition

Partially present:

- Output schema and validation exist.

Missing:

- Current decomposition uses deterministic mock output.
- No Hermes LLM/Agent call.
- No prompt/skill versioning.
- No cost/rate/error handling.
- No result persistence.

Impact:

- The repo cannot yet perform actual Hermes analysis. It only validates the shape of expected analysis.

### 8. Cards and topic-pool supplement

Partially present:

- Card and topic-pool supplement fields exist in decomposition output.
- Feishu dry-run maps table 7 and table 9 rows.

Missing:

- No real topic-pool state read from Feishu.
- No "待 Hermes 补充" selection and version check loop.
- No live card write to Feishu.
- No external card publish state transition.

Impact:

- The data shape exists, but the actual Feishu card/topic-pool update loop does not.

### 9. Live Feishu update

Partially present:

- Table mapping and dry-run operations exist.
- `validate_live_readiness` checks table IDs and credentials presence.

Missing:

- No `lark-cli` invocation.
- No official API fallback script.
- No field type reconciliation against real Feishu Base.
- No pagination/read-before-write.
- No live create/update/no-op execution.
- No idempotent write log.

Impact:

- Feishu remains dry-run only. This is the largest blocker for the user's final visible outcome.

### 10. Persistence and idempotency

Missing:

- No SQLite/local DB.
- No durable run table.
- No processed content ledger.
- No last-success cursor.
- No write-audit table.

Impact:

- A scheduled production run cannot safely know what is new, what already succeeded, or what needs retry.

## Gap By Desired End-To-End Flow

| Desired flow step | Current state | Gap |
|---|---|---|
| Hermes invokes tool | Not available | Add CLI first, MCP later if needed. |
| Tool loads real benchmark accounts | Placeholder registry only | Add local account config or Feishu read path. |
| Tool collects new blogger content | One smoke proof only | Add daily multi-account MediaCrawler runner. |
| Tool dedups/imports content | Fixture import exists | Connect real output to import and persist dedup state. |
| Tool transcribes videos | Wrapper/smoke exists | Add production batch FunASR runner. |
| Hermes decomposes content | Mock output exists | Add real Hermes call and prompt/version contract. |
| Tool creates cards | Dry-run field mapping exists | Add live Feishu card write. |
| Tool supplements topic pool | Dry-run field mapping exists | Add state-machine read/write and version guards. |
| Tool updates Feishu tracking tables | Dry-run only | Add `lark-cli` or official API live sync. |
| Tool runs on schedule | Not available | Add cron/systemd timer after one-shot CLI is stable. |

## Recommended Completion Plan

### Milestone 1: One-shot production CLI

Goal:

```bash
hermes-benchmark run-daily --date YYYY-MM-DD --json
```

Deliver:

- package/install metadata;
- one CLI entrypoint;
- JSON result output;
- clear non-zero exit codes;
- config/env validation;
- no scheduler yet.

Why first:

- Hermes needs a stable call surface before MCP or automation matters.

### Milestone 2: Real account and collection runner

Deliver:

- local-only real account config or Feishu account-table read;
- multi-account MediaCrawler command generation;
- real output normalization;
- persisted imported content IDs.

### Milestone 3: Transcription runner

Deliver:

- batch FunASR invocation;
- transcript artifact path policy;
- failed/blocked status handling;
- no raw video retention.

### Milestone 4: Real Hermes decomposition

Deliver:

- Hermes call boundary;
- prompt/skill version;
- response validation with current schema;
- retry/failure records.

### Milestone 5: Live Feishu sync

Deliver:

- official `lark-cli` live write path or thin official API script;
- read-before-write;
- idempotency log;
- table 3/4/6/7/9 live update;
- table field/type reconciliation.

### Milestone 6: Schedule and deployment

Deliver:

- cron or systemd timer;
- run lock;
- log and artifact directory;
- health check;
- deployment runbook.

## Completion Estimate

Using the user's production goal as 100%:

| Category | Weight | Current |
|---|---:|---:|
| Data contracts and local proof | 20% | 18% |
| External runtime collection/transcription | 20% | 8% |
| Hermes real analysis | 15% | 4% |
| Feishu live sync | 20% | 5% |
| Invocation/deployment/scheduling | 20% | 3% |
| Ops, persistence, recovery | 5% | 1% |
| **Total** | **100%** | **39%** |

This is intentionally approximate. The main point: the repo has a solid proof skeleton, but production depends on live integration work that was deliberately kept out of earlier child tasks.

## Immediate Next Decision

The next task should not be "build everything". It should be:

```text
Create the smallest stable production CLI that Hermes can call for one daily run.
```

That CLI can initially run dry-run Feishu mode, but it must establish the durable input/output contract. Once that contract is stable, live Feishu sync and scheduler can attach without redesigning the whole repo.
