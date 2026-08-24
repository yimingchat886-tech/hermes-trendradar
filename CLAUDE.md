# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hermes stock (Hermes Agent) is a content-topic tracking system for AI-sector benchmark accounts (Douyin/Xiaohongshu). Hermes is the **only** runtime LLM decision layer; this repo implements the deterministic tool layer: a Python CLI (`hermes-benchmark`) that collects benchmark content via MediaCrawler, transcribes video with local FunASR, tracks run state in SQLite, builds handoff packages for Hermes analysis, and plans Feishu (Lark) Bitable writes. The long-term product blueprint lives in `docs/PRD/PRD_MASTER.md` (Chinese); the current release spec is v1.4 under `docs/PRD/releases/`.

The package has **zero runtime dependencies** (stdlib only, Python ≥3.11). Keep it that way unless a task explicitly justifies a new dependency.

## Commands

```bash
# Run all tests (pure pytest, offline, no network needed)
python3 -m pytest tests -q

# Run a single test file / test
python3 -m pytest tests/test_state.py -q
python3 -m pytest tests/test_cli_contract.py -k healthcheck -q

# Run the CLI without installing
python3 -m hermes_benchmark.cli healthcheck --json
python3 -m hermes_benchmark.cli validate-config --profile profiles/local/hermes.v1.4.douyin.local.json --json

# Editable install (exposes the `hermes-benchmark` entry point)
pip install -e .
```

CLI subcommands: `validate-config`, `healthcheck`, `smoke-mediacrawler`, `run-daily` (`--analysis-mode mock|hermes-handoff`, `--feishu-mode dry-run|limited-live`), `apply-limited-live`. All accept `--profile` (alias `--config`) and `--json`.

## Development Workflow (Trellis)

This project is managed by Trellis — see `AGENTS.md` and `.trellis/workflow.md` (source of truth for phase order and task handling). Before non-trivial work read `.trellis/spec/guides/project-development.md`. Task PRDs/context live in `.trellis/tasks/`; stable rules go in `.trellis/spec/`. Default complexity constraint is "Ponytail full mode": prefer deletion, reuse, stdlib, and existing dependencies before adding code or abstractions.

## Architecture

Everything lives in the flat `hermes_benchmark/` package. The layering (data flows top to bottom):

1. **CLI contract** — `cli.py`. Every command returns a JSON envelope `{ok, command, mode, data, error}` with stable exit codes (`0` ok, `2` contract/config invalid, `3` runtime unavailable, `4` collection failed, `6` handoff package invalid, `9` run lock conflict). Argparse errors are converted to `CliContractError` so the envelope contract holds even for bad args. `tests/test_cli_contract.py` pins this contract.
2. **Profiles** — `profile.py`. v1.4 runtime profiles (see `profiles/examples/*.sample.json`) are a main profile referencing sub-profiles (accounts, analysis, transcription, runtime, feishu) via refs. Secret-looking values must use an allowed ref prefix (`env:`, `file:`, `secret:`, `vault:`, `redacted:`, `runtime:`) or be redacted — validation rejects inline credentials. `profile_hash` ties runs to the exact profile. Real profiles go in `profiles/local/` (gitignored: `*.local.*`, `*.production.*`); only `*.sample.json` is committed.
3. **State** — `state.py`. SQLite schema for runs (single active run lock via `begin_run`/`finish_run`), a content dedup ledger with immutable fields (upserts that mutate them are conflicts), transcript state, error records, and analysis package refs.
4. **Runtime boundary** — `runtime_cdp.py` (runner-owned Chrome CDP lifecycle: health preflight, port/profile `fcntl` lock, owner marker file) and `external_runtime.py` (subprocess adapter for MediaCrawler/FunASR living outside the repo at `/home/jym/workspace/_external`; wraps `run_process` with log capture and `redact_text` so ports, paths, and secrets never reach logs or reports).
5. **Collection → import** — `collection_runner.py` builds MediaCrawler creator commands per enabled Douyin account and loads its JSONL output; `mediacrawler_import.py` maps raw rows to `BenchmarkContent` and upserts into the ledger.
6. **Transcription** — `transcript_pipeline.py` (FunASR boundary, fixture-friendly) and `transcript_batch.py` (serial batch runner producing transcript artifacts).
7. **Analysis handoff** — `handoff.py` builds the versioned hermes-handoff package from state DB contents; `decomposition.py` defines the Hermes decomposition output contract (Hermes may not autonomously set manual-only fields like `official_topic_title`).
8. **Feishu output** — `feishu_dry_run.py` maps content to Bitable tables and plans operations. Every field has an owner: `script_generated`, `hermes_managed`, or `manual` — never write a field owned by another party. v1.4 limited-live only writes table 4 / status scope. `daily_digest.py` builds digests and ops alerts.

Cross-cutting: `contracts.py` holds the TypedDict data contracts + `validate_record` (the no-credential rules); `account_registry.py` is the manual benchmark account registry; `fixtures.py` provides the no-credential fixture loop used by tests.

## Invariants

- **No credentials anywhere**: no cookies, login state, tokens, or proxy configs in code, fixtures, profiles, docs, or reports. Redaction (`redact_text`, sensitive-key validation) is load-bearing — don't weaken it.
- Reports/envelopes reference artifacts by `artifact_ref` (relative to storage dir), never absolute paths or raw endpoints (`endpoint_ref: "redacted"`).
- Error codes are stable strings (e.g. `CDP_PORT_PROFILE_LOCK_CONFLICT`, `MAPPER_SCHEMA_ERROR`, `run_lock_conflict`) surfaced in envelopes and smoke reports; add new ones rather than repurposing existing ones.
- Operator runbooks for the real-runtime paths (smoke, deployment) are in `docs/runbooks/`.

<!-- gitnexus:start -->
# GitNexus

Repository id: `hermes-trendradar`. Keep the tracked tree stable: rebuild with `gitnexus analyze --index-only`; never commit `.gitnexus/`.

Use `gitnexus query` for unfamiliar flows, `context` for one symbol, `impact --direction upstream` before symbol edits, and `detect-changes --scope compare --base-ref <origin/HEAD>` before commit. Missing or stale index evidence is degraded, not proof of zero risk. Detailed workflows are under `.agents/skills/gitnexus/` and `.claude/skills/gitnexus/`.
<!-- gitnexus:end -->
