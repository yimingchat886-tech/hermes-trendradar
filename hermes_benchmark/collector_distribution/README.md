# Collector profile distribution

This directory is the versioned source-of-truth for the future blank Hermes `collector` profile. Case 4A is source-only: it does not deploy anything to `~/.hermes/profiles`, does not create a live profile, and does not run `run-daily`.

Included assets:

- `stock_runtime_plugin/` — Hermes plugin directory source for the profile-local `stock_runtime` toolset.
- `collector_config.template.yaml` — config fragment for the fixed runtime adapter and the toolset allowlist.
- `profile/config.template.yaml` — source template for a blank local collector profile.
- `profile/prefill/collector-prefill.json` — valid JSON prefill message array.
- `profile/env.guardrails.example` — blank/false environment guardrail overrides for local deployment.

Runtime contract:

1. The plugin manifest registers exactly seven tools: `stock_validate_config`, `stock_healthcheck`, `stock_run_daily`, `stock_read_analysis_package`, `stock_record_analysis_result`, `stock_build_internal_digest`, and `stock_record_feedback`.
2. The collector profile enables only the `stock_runtime` toolset from `stock-runtime`; generic toolsets such as terminal, file, code_execution, web, browser, memory, session_search, and cronjob stay disabled.
3. All CLI adapter calls use fixed argv lists, fixed cwd `/home/jym/workspace/Hermes trendradar`, fixed profile ref `/home/jym/workspace/Hermes trendradar/profiles/local/hermes.v1.4.douyin.local.json`, no shell, a minimal subprocess env allowlist, timeout, stdout/stderr caps, JSON envelope validation, and allowlisted result fields.
4. Package/result/digest refs must be `file:` refs under the configured runtime storage root and scoped to the run id.
5. Feishu, Weixin, and API server surfaces are disabled in config and blanked/false in the env guardrail example. No real platform values or credentials belong in this distribution.

Deployment boundary:

- Case 4A only prepares committed source templates.
- To deploy later, copy `profile/` into a new blank local `collector` profile and copy `profile/env.guardrails.example` to that profile's `.env` after reviewing every blank/false value.
- The live collector SOUL must be created only in Case 4B at `~/.hermes/profiles/collector/SOUL.md`; this repository intentionally has no SOUL template asset.
- Local scheduling is only planned here: 06:00 America/Denver, `fixed:300/max2`.
- The RAG adopted-content outbox is planned, not implemented. Do not write RAG, create the outbox, activate cron, or run collector production flow before a separate Case 4D.

Validation commands:

```bash
python3 -m pytest tests/test_stock_runtime.py tests/test_collector_profile_distribution.py -q
python3 -m pytest -q
python3 -m compileall hermes_benchmark/collector_distribution
ruff check
git diff --check
git check-ignore profiles/local/hermes.v1.4.douyin.local.json collector.local.yaml collector.production.yaml
```

The `git check-ignore` command must report all three probe paths. Do not inspect or commit anything under `profiles/local/` while validating this distribution.
