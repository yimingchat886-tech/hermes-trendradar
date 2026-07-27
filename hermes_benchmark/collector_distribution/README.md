# Collector profile distribution

This directory is the versioned source-of-truth for the future blank Hermes `collector` profile. Case 3 does not deploy it to `~/.hermes/profiles` and does not create a live profile.

Included assets:

- `stock_runtime_plugin/` — Hermes plugin directory source for the profile-local `stock_runtime` toolset.
- `collector_config.template.yaml` — config fragment showing the intended collector tool allowlist and the fixed Hermes stock runtime adapter settings.

Runtime contract:

1. The plugin registers exactly seven tools: `stock_validate_config`, `stock_healthcheck`, `stock_run_daily`, `stock_read_analysis_package`, `stock_record_analysis_result`, `stock_build_internal_digest`, and `stock_record_feedback`.
2. All Hermes stock CLI calls use fixed argv lists, fixed cwd, fixed profile ref, no shell, a minimal subprocess env allowlist, timeout, stdout/stderr caps, JSON envelope validation, and allowlisted result fields.
3. Package/result/digest refs must be `file:` refs under the configured Hermes stock storage root and scoped to the run id.
4. Case 3 self-check is local-only and fixture-only. It does not start Feishu, does not send messages, does not collect live accounts, and does not modify any live Hermes profile.
