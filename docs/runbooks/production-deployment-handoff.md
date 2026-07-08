# Hermes Production Deployment Handoff

Date: 2026-07-08
Scope: current v2.0 handoff boundary, not a full production deployment claim.

## Executive Summary

This repo now has a small installable `hermes-benchmark` CLI with JSON
envelopes, local SQLite state, profile validation, external-runtime guards,
handoff package generation, analysis-result ingestion, internal digest payload
generation, and human feedback recording.

It is still not a standalone production service. Hermes owns scheduling, message
delivery, secret resolution, and real analysis execution. This repo owns the
deterministic CLI boundary and local state transitions.

## Current MCP And CLI State

There is no repo-owned MCP server. That is intentional for v2.0 unless the
Hermes skill route fails.

The repo exposes an installable console script through `pyproject.toml`:

```bash
hermes-benchmark --version
hermes-benchmark validate-config --profile <profile> --json
hermes-benchmark healthcheck --profile <profile> --json
hermes-benchmark smoke-mediacrawler --profile <profile> --json
hermes-benchmark run-daily --profile <profile> --date YYYY-MM-DD --analysis-mode hermes-handoff --json
hermes-benchmark record-analysis-result --profile <profile> --package <package-ref> --result <result-json> --json
hermes-benchmark build-internal-digest --profile <profile> --run-id <run-id> --json
hermes-benchmark record-feedback --profile <profile> --run-id <run-id> --content-id <content-id> --analysis-result-id <id> --decision adopt --actor-ref <ref> --source-message-ref <ref> --json
```

## Implemented Repo Surface

| Area | Current state |
|---|---|
| Packaging | `pyproject.toml` declares the zero-dependency `hermes-benchmark` console script. |
| CLI contract | `hermes_benchmark/cli.py` provides JSON envelopes, command names, bounded exit codes, and profile aliases. |
| Profiles | `hermes_benchmark/profile.py` validates local runtime profile shape without logging secret values. |
| Runtime health | `healthcheck` resolves CDP/runtime state and reports redacted status. |
| Collection bridge | MediaCrawler output can be normalized into local ledger state; external runtime stays outside the repo. |
| Handoff package | `run-daily --analysis-mode hermes-handoff` emits an analysis package ref for Hermes. |
| Analysis result intake | `record-analysis-result` validates Hermes-owned result JSON and records traceable refs. |
| Internal digest payload | `build-internal-digest` writes a digest payload ref; Hermes still sends the message. |
| Feedback loop | `record-feedback` stores idempotent adopt/reject refs in SQLite without promotion or Feishu writes. |
| Feishu | Dry-run and limited-live guard surfaces exist, but v2.0 keeps live Bitable write behind the M3 gate. |

## What Is Production-Ready Today

- The repo-side CLI invocation contract is stable enough for Hermes skill or
  `systemd --user` wrapper testing.
- The CLI can produce redacted JSON outputs suitable for logging by Hermes.
- Local state can record runs, content ledger rows, analysis package refs,
  analysis result refs, digest payload refs, and feedback refs.
- The external runtime boundary keeps cookies, login state, raw media, model
  caches, and run artifacts outside this source repo.

## What Is Not Production-Ready Yet

- No repo-owned scheduler, daemon, queue, retry worker, or message sender.
- No proof in this repo that Hermes cron has executed the command and delivered
  an internal-group message end to end.
- No repo-owned real Hermes analysis call; Hermes creates the result and the
  repo validates/records it.
- No committed real account source of truth.
- No production multi-account MediaCrawler runbook result for the current v2.0
  loop.
- No live Feishu/Bitable write path accepted for default v2.0; that remains a
  conditional M3 decision after message-first usage proves the need.

## External Runtime Boundary

Use `/home/jym/workspace/_external` for external tools and run artifacts:

```text
/home/jym/workspace/_external/
  MediaCrawler/
  venvs/mediacrawler/
  FunASR/
  venvs/funasr/
  model-cache/funasr/
  hermes-stock-runs/<run_id>/
```

Do not commit credentials, tokens, cookies, login state, proxy settings, CDP
endpoints, raw videos, crawler dumps, external source trees, virtualenvs, model
caches, temp DBs, or unredacted smoke output.

## Minimum Verification Commands

Run from repo root:

```bash
python3 -m compileall -q hermes_benchmark
python3 -m pytest tests -q
git diff --check
```

For a local M0 smoke, use the command set in
[`hermes-skill-invocation.md`](hermes-skill-invocation.md).

## Recommended Next Step

Prove the Hermes-owned side of M0/M1: run the installed `hermes-benchmark`
command from the Hermes skill or cron path, deliver the redacted JSON summary to
the internal group, then feed one real Hermes analysis result back through
`record-analysis-result` and `build-internal-digest`.
