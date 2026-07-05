# Project Workflow Specs

## Scope

Project specs cover repository-level development workflow rules that are broader than one backend contract.

## Pre-Development Checklist

- Read `staged-delivery-overlay.md` before planning T3/T4 work, parent/child delivery, RTM-tracked work, or harness/tooling changes.
- Read `task-sizing.md` before deciding whether a request uses default Trellis or staged overlay.
- Read `pm-intake-protocol.md` before converting non-trivial user input into PRD requirements.
- Read `oracle-review-policy.md` before marking an Oracle review required or skipped.
- Read `ponytail-boundary.md` before adding dependencies, architecture, abstractions, or broad workflow surface.
- Read `claim-guard-ownership.md` before changing claim guard, task ownership, `task.py claim`, or `task.py release`.
- Read `cli-contracts.md` before changing `hermes-benchmark` CLI commands, JSON envelopes, or exit codes.
- Read `sqlite-state.md` before changing local SQLite schema, run state, dedup ledger, or write-audit persistence.
- Read `external-runtime-asr.md` before changing local ASR transcription runtime, provider names, model cache paths, GPU smoke commands, or transcript CLI arguments.
- Read `rtm-guidelines.md` before updating requirement traceability.
- Read `git-commit-push-policy.md` before reporting a staged task ready to commit or push.

## Quality Check

- Confirm ordinary low-risk work can still use default Trellis.
- Confirm staged overlay work records `task.json.meta.workflow_mode = "staged_overlay"`.
- Confirm staged child commit approval is paired with soft archive unless the user explicitly limits it.
- Confirm child soft archive does not call built-in `task.py archive`.
- Confirm staged parent acceptance commits parent evidence and runs built-in archive unless the user explicitly limits it.
- Confirm claim guard changes keep cross-owner matches blocked and explicit overrides audited.
- Confirm push is never implied by commit approval.
- Confirm CLI contract changes keep parseable JSON errors for `--json` invalid args.
- Confirm ASR runtime changes keep third-party source, venvs, model cache, temp media, and smoke outputs outside the main repo.
