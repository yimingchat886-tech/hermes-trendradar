# Child PRD: v1.4 CLI Contract Skeleton

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-010, P14-REQ-080

## Goal

Create the smallest installable `hermes-benchmark` CLI surface with stable JSON envelopes, command names, exit-code mapping, and error object shape.

## Requirements

- Add package metadata and `hermes-benchmark` entrypoint.
- Support `--version`.
- Add `validate-config`, `healthcheck`, `run-daily`, and `apply-limited-live` command stubs.
- Print JSON when `--json` is supplied.
- Centralize exit-code and error-envelope handling.
- Do not implement real profile parsing, SQLite, collection, transcription, Hermes handoff, or Feishu live writes in this child.

## Out of Scope

- Real config validation.
- Real daily orchestration.
- Live Feishu calls.
- Scheduler or MCP server.

## Acceptance Criteria

- [ ] `hermes-benchmark --version` returns a version.
- [ ] Each v1.4 command returns parseable JSON in stub mode.
- [ ] Invalid args produce the documented contract-mismatch/error envelope.
- [ ] Existing module self-checks still pass.

## Risk Level

- T2: packaging/CLI surface.
- High-risk trial PLAN: no.
- Oracle required: no unless package tooling forces a broader dependency choice.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
