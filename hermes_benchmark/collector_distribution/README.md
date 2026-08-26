# Collector profile distribution

This directory is the versioned source-of-truth for the future blank Hermes `collector` profile. Case 4A prepared source templates; Case 4C adds the deterministic no-agent cron handoff runner. It still does not deploy anything to `~/.hermes/profiles`, does not create a live profile, does not activate cron, and does not run `run-daily` during deployment validation.

Included assets:

- `stock_runtime_plugin/` — Hermes plugin directory source for the profile-local `stock_runtime` toolset.
- `collector_config.template.yaml` — config fragment for the fixed runtime adapter and the real Hermes toolset keys (`toolsets`, `platform_toolsets`, and `agent.disabled_toolsets`).
- `scripts/hermes_benchmark_handoff.py` — deterministic no-agent cron runner source for a future collector profile script.
- `profile/config.template.yaml` — source template for a blank local collector profile.
- `profile/prefill/collector-prefill.json` — valid JSON prefill message array.
- `profile/env.guardrails.example` — blank/false environment guardrail overrides for local deployment.

Runtime contract:

1. The plugin manifest registers exactly seven tools: `stock_validate_config`, `stock_healthcheck`, `stock_run_daily`, `stock_read_analysis_package`, `stock_record_analysis_result`, `stock_build_internal_digest`, and `stock_record_feedback`.
2. The collector profile exposes only the `stock_runtime` toolset from `stock-runtime` through the Hermes-supported `toolsets` and `platform_toolsets.cli` keys; generic toolsets such as terminal, file, code_execution, web, browser, memory, session_search, and cronjob stay disabled through `agent.disabled_toolsets`.
3. All CLI adapter calls use fixed argv lists, fixed cwd `/home/jym/workspace/Hermes trendradar`, fixed profile ref `/home/jym/workspace/Hermes trendradar/profiles/local/hermes.v1.4.douyin.local.json`, no shell, a minimal subprocess env allowlist, timeout, stdout/stderr caps, JSON envelope validation, and allowlisted result fields.
4. Package/result/digest refs must be `file:` refs under the configured runtime storage root and scoped to the run id.
5. Feishu, Weixin, and API server surfaces are disabled in config and blanked/false in the env guardrail example. No real platform values or credentials belong in this distribution.

Deployment boundary:

- Case 4A only prepares committed source templates.
- Case 4C only commits the runner source. It does not create the live collector profile, does not create a live cron job, and does not run production `run-daily`.
- To deploy the runner in a later activation case, copy `scripts/hermes_benchmark_handoff.py` to `~/.hermes/profiles/collector/scripts/hermes_benchmark_handoff.py`, place the prefill JSON at live path `/home/jym/.hermes/profiles/collector/prefill/collector-prefill.json`, and configure a no-agent cron/script job at `0 6 * * *` with timezone `America/Denver`.
- The runner itself is not a scheduler: it computes `RUN_DATE` with `ZoneInfo("America/Denver")`, validates config, takes a non-blocking `run_date + profile_hash` lock, healthchecks, and only then calls `run-daily --analysis-mode hermes-handoff`.
- `--check-only` is the safe validation path: it runs only `validate-config` and `healthcheck`, emits a redacted receipt, and must not call `run-daily` or write business state.
- To deploy later, copy `profile/` into a new blank local `collector` profile and copy `profile/env.guardrails.example` to that profile's `.env` after reviewing every blank/false value.
- The live collector SOUL must be created only in Case 4B at `~/.hermes/profiles/collector/SOUL.md`; this repository intentionally has no SOUL template asset. Profile SOUL content belongs only in the live Hermes profile, not in repo source.
- Local scheduling is only planned here: 06:00 America/Denver, runner-authorized `fixed:300` backoff, and manifest-projected max attempts (the current v1.4 manifest is max2).
- The RAG adopted-content outbox is planned, not implemented. Do not write RAG, create the outbox, activate cron, or run collector production flow before a separate Case 4D.

Validation commands:

```bash
python3 -m pytest tests/test_stock_runtime.py tests/test_collector_profile_distribution.py tests/test_cron_handoff_runner.py -q
python3 -m pytest -q
python3 -m compileall hermes_benchmark/collector_distribution
uvx --from ruff==0.15.20 ruff check hermes_benchmark/collector_distribution/scripts/hermes_benchmark_handoff.py tests/test_cron_handoff_runner.py tests/test_collector_profile_distribution.py tests/test_stock_runtime.py
git diff --check
git check-ignore profiles/local/hermes.v1.4.douyin.local.json collector.local.yaml collector.production.yaml
python3 hermes_benchmark/collector_distribution/scripts/hermes_benchmark_handoff.py --check-only
```

The `git check-ignore` command must report all three probe paths. Do not inspect or commit anything under `profiles/local/` while validating this distribution.
