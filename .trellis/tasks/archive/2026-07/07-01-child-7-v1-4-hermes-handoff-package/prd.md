# Child PRD: v1.4 Hermes Handoff Package

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-060

## Goal

Generate schema-valid Hermes handoff packages and deterministic mock outputs without running real LLM analysis inside the CLI.

## Requirements

- Support `--analysis-mode mock` deterministic outputs for tests.
- Support `--analysis-mode hermes-handoff` package generation.
- Include content, account, source URL, publish/collect timestamps, transcript status/artifact refs, and dedup key.
- Persist package refs through local state/artifact policy.
- Validate package schema before success output.

## Out of Scope

- Real Hermes LLM call.
- Prompt/Skill versioning.
- Feishu live writes.

## Acceptance Criteria

- [ ] Handoff package validates against the v1.4 schema with `schema_version`, `package_id`, `run_id`, `profile_hash`, `mode`, and `contents[]`.
- [ ] Each package content item includes `content_id`, `platform`, `account_id`, `account_display_name`, `source_url`, `title_or_caption_raw`, `publish_at`, `collected_at`, `transcript_status`, `transcript_artifact_ref`, and `dedup_key`.
- [ ] Mock mode remains deterministic and does not create a Hermes handoff package.
- [ ] `run-daily --analysis-mode hermes-handoff --json` returns `analysis_mode = hermes-handoff`, writes `analysis_package_ref`, and the referenced file passes package schema validation.
- [ ] Invalid handoff package returns exit code `6` with a JSON error code for package validation failure.

## Risk Level

- T2: contract and artifact generation.
- High-risk trial PLAN: no.
- Oracle required: no unless package schema conflicts with runtime PRD.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
