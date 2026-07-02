# Hermes Production Deployment Handoff

Date: 2026-07-01
Scope: current repo handoff boundary, not a full production deployment claim.

## Executive Summary

This repo has completed the v1 benchmark-account tracking proof path for Hermes stock. It is ready to hand off as a small, inspectable Python tool layer with fixture checks, dry-run Feishu mapping, and one local external-runtime smoke proof.

It is not yet a production service. There is no scheduler, daemon, queue, live Feishu writer, MCP server, packaged CLI, RAG/vector store, or hotspot/RSS/TrendRadar runtime in this repo today.

## Current MCP And CLI State

There is currently no repo-owned MCP server for Hermes to call.

There is currently no packaged production CLI command for Hermes to call. The repo has importable Python modules and module self-checks such as:

```bash
python3 -m hermes_benchmark.account_registry
python3 -m hermes_benchmark.mediacrawler_import
python3 -m hermes_benchmark.transcript_pipeline
python3 -m hermes_benchmark.decomposition
python3 -m hermes_benchmark.feishu_dry_run
python3 -m hermes_benchmark.daily_digest
python3 -m hermes_benchmark.external_runtime
```

The PRD mentions `trend-cli`, `trend-mcp`, and `lark-cli`, but in the current repo:

- `trend-cli` and `trend-mcp` are target architecture, not implemented artifacts.
- `lark-cli` is the accepted Feishu transport direction and appears in the dry-run plan, but live Feishu calls are not implemented.
- `hermes_benchmark.external_runtime` wraps local process boundaries for smoke evidence; it is not a stable Hermes-facing CLI.

## Implemented Repo Surface

| Area | File | Current state |
|---|---|---|
| Shared contracts | `hermes_benchmark/contracts.py` | TypedDict contracts and validation for sources, accounts, contents, transcripts, topic candidates, RAG documents, and source health. |
| Fixture loop | `hermes_benchmark/fixtures.py` | No-credential fixture loop and self-check. |
| Account registry | `hermes_benchmark/account_registry.py` | 20 placeholder Douyin/Xiaohongshu benchmark accounts and daily tracking plan. |
| MediaCrawler import | `hermes_benchmark/mediacrawler_import.py` | Fixture-style import and exact dedup by normalized URL plus platform content ID. |
| Whisper transcript boundary | `hermes_benchmark/transcript_pipeline.py` | Local transcript record wrapper, fixture transcript, status handling, and temp video cleanup wrapper. |
| Hermes decomposition | `hermes_benchmark/decomposition.py` | Deterministic mock Hermes output contract, card export, topic-pool supplement boundary, and manual-field protections. |
| Feishu dry-run | `hermes_benchmark/feishu_dry_run.py` | Table mapping and create/update/no-op dry-run operations for parent-1 tables. No live write. |
| Digest and alerts | `hermes_benchmark/daily_digest.py` | Daily digest object and ops exception alerts assembled from child outputs. No notification sender. |
| External runtime smoke | `hermes_benchmark/external_runtime.py` | External layout, preflight guards, process redaction, import proof, Whisper status, cleanup, and manifest writing. |

## What Is Production-Ready Today

- Contract and fixture validation can be run locally.
- The v1 data shape for benchmark-account tracking is explicit and importable.
- MediaCrawler-style rows can be normalized into local `BenchmarkContent` records.
- Transcript status records can be created and failed safely.
- Hermes decomposition output shape is bounded and rejects autonomous writes to manual-only fields.
- Feishu table writes can be planned in dry-run mode with deterministic idempotency keys.
- Daily digest and ops alert payloads can be generated locally from the fixture pipeline.
- External runtime smoke has strict path, cookie, command, redaction, cleanup, and evidence-retention guards.

## What Is Not Production-Ready Yet

- No MCP server exposes these modules to Hermes.
- No stable production CLI wraps these modules with arguments, config, exit codes, and JSON output.
- No live Feishu read/write path exists; only dry-run operation planning exists.
- No scheduler, daemon, queue, retry worker, or daily automation exists.
- No persistent DB/object storage implementation exists in this repo.
- No real account registry source-of-truth is committed; accounts are placeholder fixtures.
- No production MediaCrawler orchestration is committed; external MediaCrawler stays outside this repo.
- No production Whisper model/runtime management is committed; external venv/cache stays outside this repo.
- No hotspot/RSS/TrendRadar path is implemented in v1.
- No RAG ingestion/export pipeline is implemented.
- No long-term raw video storage is implemented, by design.

## External Runtime Boundary

Use `/home/jym/workspace/_external` for external tools and run artifacts:

```text
/home/jym/workspace/_external/
  MediaCrawler/
  venvs/mediacrawler/
  venvs/openai-whisper/
  model-cache/openai-whisper/
  hermes-stock-runs/<run_id>/
```

Do not commit:

- cookies, tokens, login state, proxy settings, or credentials;
- raw videos, downloaded media, raw crawler outputs, or temporary screenshots;
- external source trees, virtualenvs, model caches, or run temp files.

Retain only redacted manifests, logs, transcripts, and import proofs needed for handoff evidence.

## Known Smoke Evidence

Child 8 completed one local external-runtime smoke outside the repo:

- MediaCrawler Douyin detail run produced one importable content row and comments.
- openai-whisper produced a transcript proof from a 60-second local sample.
- Evidence was retained under `/home/jym/workspace/_external/hermes-stock-runs/...`.
- Temporary login state, raw JSONL, screenshots, downloaded video, and wav sample were kept outside the repo and cleaned after proof generation.

Treat this as deployment evidence, not a reusable production runner.

## Minimum Verification Commands

Run from repo root:

```bash
python3 -m hermes_benchmark.fixtures
python3 -m hermes_benchmark.account_registry
python3 -m hermes_benchmark.mediacrawler_import
python3 -m hermes_benchmark.transcript_pipeline
python3 -m hermes_benchmark.decomposition
python3 -m hermes_benchmark.feishu_dry_run
python3 -m hermes_benchmark.daily_digest
python3 -m hermes_benchmark.external_runtime
python3 -m compileall -q hermes_benchmark
git diff --check
```

These commands verify local contracts and dry-run boundaries only. They do not prove live Feishu writes, scheduled execution, real account crawling, or Hermes tool invocation.

## Deployment Blockers Before Formal Production

1. Decide Hermes invocation surface: MCP server, packaged CLI, or direct Python module calls.
2. Add one stable invocation wrapper with JSON input/output and clear exit codes.
3. Replace placeholder account rows with a local-only source of truth for real accounts.
4. Wire live Feishu writes through official `lark-cli` or thin official API scripts.
5. Add production secrets handling outside the repo.
6. Add scheduling and retry only after the one-shot invocation works.
7. Add persistent local DB/object storage if daily history must survive beyond run artifacts.
8. Define evidence retention and cleanup policy for production runs.

## Recommended Next Step

Do not build MCP, scheduler, and live Feishu writes at once. The smallest production bridge is one stable CLI wrapper around the current Python modules:

```text
Hermes -> one CLI command -> JSON payload -> local module -> JSON result/logs
```

Add MCP only after the CLI contract is stable and Hermes needs interactive tool discovery or multi-tool orchestration.
