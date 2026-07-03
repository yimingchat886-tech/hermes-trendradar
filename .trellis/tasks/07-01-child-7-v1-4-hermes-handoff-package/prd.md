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

- [ ] Handoff package is schema-valid.
- [ ] Mock mode remains deterministic.
- [ ] `run-daily` summary includes `analysis_package_ref`.
- [ ] Invalid package returns documented error/exit code.

## Risk Level

- T2: contract and artifact generation.
- High-risk trial PLAN: no.
- Oracle required: no unless package schema conflicts with runtime PRD.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
