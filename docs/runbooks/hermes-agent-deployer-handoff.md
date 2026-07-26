# Hermes Agent Deployer Handoff

Date: 2026-07-08
Scope: current v2.0 deployer-facing contract for a NousResearch
`hermes-agent` operator. This is not a full production deployment claim.

## Executive Summary

This repo exposes an installable `hermes-benchmark` CLI with JSON envelopes,
local SQLite state, profile validation, external-runtime guards, handoff package
generation, analysis-result ingestion, internal digest payload generation, and
human feedback recording.

It is still not a standalone production service. Hermes owns scheduling, message
delivery, secret resolution, and real analysis execution. This repo owns the
deterministic CLI boundary and local state transitions.

## Four-Piece Design Contract

| Piece | Contract |
|---|---|
| Profile | Hermes passes a repo-local profile ref to `hermes-benchmark --profile`. The profile may reference external runtime, storage, account, analysis, transcription, and Feishu refs, but the deployer must not log resolved secret values or raw profile contents. |
| Transport | The repo-side transport is CLI plus parseable JSON stdout. Hermes owns cron or scheduler execution, internal-group message delivery, and secret lookup. `systemd --user` is the fallback if Hermes cron cannot run the same command safely. |
| MCP | There is no repo-owned MCP server in v2.0. That is intentional unless the Hermes skill route fails. |
| SKILL.md | The deployer-facing `SKILL.md` should call the existing console script, log only allowed redacted fields, and treat all secrets, raw media, crawler dumps, profile contents, CDP endpoints, and local temp paths as forbidden. |

## Profile Contract

Use the committed example profiles as shape references, then point production
refs at external files, env vars, or secret managers outside this repo.

Useful examples:

- `profiles/examples/hermes.v1.4.douyin.sample.json`
- `profiles/examples/runtime.v1.4.sample.json`
- `profiles/examples/analysis.v1.4.sample.json`
- `profiles/examples/feishu.v1.4.sample.json`
- `profiles/examples/account-source.v1.4.sample.json`

Allowed skill inputs:

- repo root ref, console script ref, profile ref, and run date;
- environment variable names such as `HERMES_PROXY_URL`;
- JSON fields from CLI output: `contract_version`, `ok`, `command`, `mode`,
  `exit_code`, `retryable`, `run_id`, `analysis_package_ref`,
  `digest_payload_ref`, `delivery.status`, `runtime_effective_status`,
  `run_eligible`, `result_ref`, `result_hash`, and `error.code`.

Forbidden skill inputs or logs:

- credentials, tokens, cookies, proxy values, CDP endpoints, login-state
  contents, raw profile contents, raw crawler dumps, and raw videos;
- `.env` contents or resolved secret values;
- local absolute temp paths unless they are already redacted by the CLI.

## Transport Contract

Hermes calls this repo through the existing `hermes-benchmark` console script.
The repo returns bounded exit codes and JSON envelopes. Hermes decides when to
run the command and where to send the redacted summary.

The repo exposes this command surface:

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

Every JSON response uses the v2 envelope: `contract_version`, `command`, `ok`,
`exit_code`, `retryable`, `data`, and `error`. Missing production arguments must
fail closed with `contract_mismatch`; local mock/stub behavior is only valid when
explicitly requested via `--self-check`.

Explicit local contract checks:

```bash
hermes-benchmark healthcheck --self-check --json
hermes-benchmark run-daily --analysis-mode mock --self-check --json
hermes-benchmark apply-limited-live --self-check --json
```

Hermes cron is usable for M0 only if it can run the same no-secret command,
capture the JSON result, and send an internal-group message with allowed fields.
If Hermes cron cannot satisfy that gate, use a `systemd --user` timer to call
the same console script and hand the redacted JSON summary to the Hermes message
path.

Feishu dry-run and limited-live guard surfaces exist, but v2.0 keeps live
Bitable writes behind the M3 gate.

## MCP Contract

There is no repo-owned MCP server. That is intentional for v2.0 unless the
Hermes skill route fails.

Reopen the MCP decision only if the console-script skill route cannot provide
safe scheduling, parseable JSON capture, and internal-group message delivery
without leaking secrets.

## SKILL.md Artifact Contract

The deployer-facing `SKILL.md` should be a thin wrapper around the existing
console script. It should not introduce a scheduler framework, MCP server, real
analysis implementation, or live Bitable write path.

Minimal invocation template:

```json
{
  "schema_version": "2.0-m0",
  "name": "hermes-benchmark-invocation",
  "repo_root_ref": "env:HERMES_BENCHMARK_REPO_ROOT",
  "console_script_ref": "env:HERMES_BENCHMARK_CONSOLE_SCRIPT",
  "profile_ref": "env:HERMES_BENCHMARK_PROFILE",
  "commands": {
    "healthcheck": [
      "${HERMES_BENCHMARK_CONSOLE_SCRIPT}",
      "healthcheck",
      "--profile",
      "${HERMES_BENCHMARK_PROFILE}",
      "--json"
    ],
    "handoff": [
      "${HERMES_BENCHMARK_CONSOLE_SCRIPT}",
      "run-daily",
      "--profile",
      "${HERMES_BENCHMARK_PROFILE}",
      "--date",
      "${RUN_DATE}",
      "--analysis-mode",
      "hermes-handoff",
      "--json"
    ],
    "digest": [
      "${HERMES_BENCHMARK_CONSOLE_SCRIPT}",
      "build-internal-digest",
      "--profile",
      "${HERMES_BENCHMARK_PROFILE}",
      "--run-id",
      "${RUN_ID}",
      "--json"
    ]
  }
}
```

Setup uses an external venv or an already installed console script. Do not
install runtime dependencies for this M0 gate; `pyproject.toml` currently
declares none.

```bash
python3 -m venv "$HERMES_BENCHMARK_VENV"
"$HERMES_BENCHMARK_VENV/bin/pip" install "$HERMES_BENCHMARK_REPO_ROOT" --no-deps
export HERMES_BENCHMARK_CONSOLE_SCRIPT="$HERMES_BENCHMARK_VENV/bin/hermes-benchmark"
```

M0 commands:

```bash
"$HERMES_BENCHMARK_CONSOLE_SCRIPT" healthcheck \
  --profile "$HERMES_BENCHMARK_PROFILE" \
  --json
```

```bash
"$HERMES_BENCHMARK_CONSOLE_SCRIPT" run-daily \
  --profile "$HERMES_BENCHMARK_PROFILE" \
  --date "$RUN_DATE" \
  --analysis-mode hermes-handoff \
  --json
```

Success criteria:

- both commands exit `0`;
- both outputs are parseable JSON;
- handoff output contains `analysis_package_ref`;
- skill logs only allowed JSON fields.

After Hermes records real analysis refs, build the internal digest payload:

```bash
"$HERMES_BENCHMARK_CONSOLE_SCRIPT" build-internal-digest \
  --profile "$HERMES_BENCHMARK_PROFILE" \
  --run-id "$RUN_ID" \
  --json
```

Current repo-side contract emits `digest_payload_ref` and a delivery blocker
such as `message_channel_not_configured`. Hermes owns the actual internal-group
message send and secret resolution.

## Implemented Repo Surface

| Area | Current state |
|---|---|
| Packaging | `pyproject.toml` declares the zero-dependency `hermes-benchmark` console script. |
| CLI contract | `hermes_benchmark/cli.py` provides fail-closed v2 JSON envelopes, command names, bounded exit codes, retryability, and profile aliases. |
| Profiles | `hermes_benchmark/profile.py` validates local runtime profile shape without logging secret values. |
| Runtime health | `healthcheck` resolves CDP/runtime state and reports redacted status. |
| Collection bridge | MediaCrawler output can be normalized into local ledger state; external runtime stays outside the repo. |
| Handoff package | `run-daily --analysis-mode hermes-handoff` emits a run-scoped analysis package ref for Hermes; empty runs are explicit no-op packages. |
| Analysis result intake | `record-analysis-result` validates Hermes-owned result JSON, checks storage-scoped `result_ref` targets, hashes referenced result artifacts, and records immutable trace refs. |
| Internal digest payload | `build-internal-digest` writes a digest payload ref; Hermes still sends the message. |
| Feedback loop | `record-feedback` stores idempotent adopt/reject refs in SQLite without promotion or Feishu writes. |
| Feishu | Dry-run and limited-live guard surfaces exist, but v2.0 keeps live Bitable write behind the M3 gate. |

## What Is Production-Ready Today

- The repo-side CLI invocation contract is stable enough for Hermes skill or
  `systemd --user` wrapper testing.
- The CLI can produce redacted JSON outputs suitable for logging by Hermes.
- Local state can record runs, content ledger rows, analysis package refs,
  analysis result refs, digest payload refs, and feedback refs.
- Analysis results are idempotent for exact repeats and conflict on changed
  package/content result refs instead of silently overwriting.
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

## Local Evidence

2026-07-05 repo-side proof used a temporary external venv and a temporary
no-secret profile/state outside the repo.

| Command | Exit | Result |
|---|---:|---|
| `hermes-benchmark healthcheck --profile <temp-profile> --json` | 0 | `mode=runtime`, `runtime_effective_status=launch_required`, `run_eligible=true`, no raw endpoint in stdout |
| `hermes-benchmark run-daily --profile <temp-profile> --date 2026-07-05 --analysis-mode hermes-handoff --json` | 0 | `mode=runtime`, `analysis_mode=hermes-handoff`, `new_content_count=1`, `transcripts_succeeded=1`, `analysis_package_ref=file:run_be176b239a66e564/artifacts/analysis_package.json` |

## Minimum Verification Commands

Run from repo root:

```bash
python3 -m compileall -q hermes_benchmark
python3 -m pytest tests -q
git diff --check
```

## Recommended Next Step

Prove the Hermes-owned side of M0/M1: run the installed `hermes-benchmark`
command from the Hermes skill or cron path, deliver the redacted JSON summary to
the internal group, then feed one real Hermes analysis result back through
`record-analysis-result` and `build-internal-digest`.
