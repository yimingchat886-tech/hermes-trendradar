# Hermes Skill Invocation Runbook

## Scope

Hermes calls this repo through the existing `hermes-benchmark` console script.
This runbook defines the no-secret M0 invocation contract only; it does not add
an MCP server, scheduler framework, real analysis, or live Bitable writes.

## Contract

The Hermes skill owns scheduling, message delivery, and secret resolution. This
repo owns deterministic CLI execution and JSON results.

Template:

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
    ]
  }
}
```

Allowed skill inputs:

- repo root ref, console script ref, profile ref, run date;
- environment variable names such as `HERMES_PROXY_URL`;
- JSON fields from CLI output: `ok`, `mode`, `run_id`, `analysis_package_ref`,
  `runtime_effective_status`, `run_eligible`, `error.code`, and `exit_code`.

Forbidden skill inputs or logs:

- credentials, tokens, cookies, proxy values, CDP endpoints, login-state
  contents, raw profile contents, raw crawler dumps, and raw videos;
- `.env` contents or resolved secret values;
- local absolute temp paths unless they are already redacted by the CLI.

## Setup

Use an external venv or an already installed console script. Do not install
runtime dependencies for this M0 gate; `pyproject.toml` currently declares none.

```bash
python3 -m venv "$HERMES_BENCHMARK_VENV"
"$HERMES_BENCHMARK_VENV/bin/pip" install "$HERMES_BENCHMARK_REPO_ROOT" --no-deps
export HERMES_BENCHMARK_CONSOLE_SCRIPT="$HERMES_BENCHMARK_VENV/bin/hermes-benchmark"
```

## M0 Commands

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
- skill logs only the allowed JSON fields above.

## Cron Gate

Hermes cron is usable for M0 only if it can run the same no-secret command,
capture the JSON result, and send an internal-group message with the allowed
fields. This child proves the repo-side invocation path, not the Hermes cron
runtime itself.

Fallback decision: if Hermes cron cannot satisfy that M0 gate, use a
`systemd --user` timer to call the same console script and hand the redacted JSON
summary to the Hermes message path.

## Local Evidence

2026-07-05 repo-side proof used a temporary external venv and a temporary
no-secret profile/state outside the repo.

| Command | Exit | Result |
|---|---:|---|
| `hermes-benchmark healthcheck --profile <temp-profile> --json` | 0 | `mode=runtime`, `runtime_effective_status=launch_required`, `run_eligible=true`, no raw endpoint in stdout |
| `hermes-benchmark run-daily --profile <temp-profile> --date 2026-07-05 --analysis-mode hermes-handoff --json` | 0 | `mode=runtime`, `analysis_mode=hermes-handoff`, `new_content_count=1`, `transcripts_succeeded=1`, `analysis_package_ref=file:run_be176b239a66e564/artifacts/analysis_package.json` |
