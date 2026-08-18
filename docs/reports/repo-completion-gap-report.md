# Repo Completion And Production Gap Report

Date: 2026-07-08

## Target Interpreted From Current v2.0 PRD

The current target is no longer "live Feishu writes first." v2.0 is a
message-first real-analysis loop:

```text
Hermes skill / cron
  -> hermes-benchmark CLI
  -> handoff package
  -> Hermes real analysis result
  -> internal digest payload + message
  -> human adopt/reject feedback
  -> SQLite audit record
```

Live Bitable writes are deferred to the v2.0 M3 gate: only start them after the
message flow runs for at least two weeks and the team confirms it needs
structured filtering or lookup.

## Current Bottom Line

The repo-side CLI bridge is now materially complete for M0/M1/M2 development,
but the full production loop still depends on Hermes-owned execution and real
runtime evidence.

Approximate completion against the current v2.0 target: **55-60%**.

What improved since the 2026-07-01 snapshot:

- `pyproject.toml` now exposes an installable `hermes-benchmark` console script.
- The CLI has stable command names, fail-closed v2 JSON envelopes, profile
  validation, retryability, and bounded exit codes.
- `run-daily --analysis-mode hermes-handoff` can emit a run-scoped handoff
  package ref and marks empty runs as no-op instead of fake analysis success.
- `record-analysis-result` can validate storage-scoped Hermes-owned result refs,
  hash referenced result artifacts, and persist immutable result refs.
- `build-internal-digest` can emit an internal digest payload ref without
  sending messages itself.
- `record-feedback` can persist idempotent adopt/reject refs in SQLite.

What remains outside the repo or incomplete:

- Hermes cron/skill still needs an end-to-end proof that it runs the CLI and
  sends an internal-group message.
- Real Hermes analysis is not performed by this repo; the repo only validates
  and records result refs.
- Real account source of truth and production multi-account collection remain
  unfinished.
- Live Feishu/Bitable writes remain conditional M3 work, not current default
  production scope.

## Capability Status

| Capability | Current state | Production risk |
|---|---|---|
| CLI invocation | Implemented console script and fail-closed JSON contract. | Low for repo-side M0; external Hermes invocation still unproven. |
| Profile/config safety | Local profile validation and redacted runtime status exist. | Medium; production profiles are local-only and must stay out of Git. |
| Handoff package | Implemented for Hermes-owned analysis. | Medium; needs real Hermes consumer evidence. |
| Analysis result intake | Implemented storage-scoped ref validation, artifact hashing, and immutable SQLite refs. | Medium; depends on real Hermes result shape staying within contract. |
| Internal digest payload | Implemented payload artifact refs. | Medium; Hermes still owns message delivery. |
| Human feedback | Implemented idempotent adopt/reject persistence. | Medium; needs live internal-group wiring. |
| Collection/transcription | Proof surfaces exist; production multi-account run remains incomplete. | High. |
| Live Feishu writes | Guarded/dry-run surface only; M3-gated. | High if prematurely enabled. |
| Scheduling/deployment | Repo intentionally does not own scheduler. | Medium; Hermes cron or systemd fallback must be proven. |

## Gap By Desired v2.0 Flow

| Desired flow step | Current state | Gap |
|---|---|---|
| Hermes invokes CLI | Repo-side command exists. | Prove Hermes skill/cron execution and message delivery. |
| CLI loads real account config | Placeholder and local profile paths exist. | Provide real local-only account source. |
| CLI collects new content | External runtime and import path exist. | Prove daily multi-account collection. |
| CLI writes handoff package | Implemented. | Run against real collected content. |
| Hermes analyzes package | Outside repo. | Produce one real result and feed it to `record-analysis-result`. |
| Repo builds digest payload | Implemented. | Hermes must send the digest message. |
| Humans adopt/reject | CLI persistence implemented. | Wire internal-group action refs into `record-feedback`. |
| Bitable write decision | Deferred. | Wait for M3 gate evidence; do not build by default. |

## Recommended Completion Plan

1. Prove M0 outside the repo: Hermes skill or cron runs `hermes-benchmark` and
   sends a redacted internal-group message.
2. Feed one real Hermes result through `record-analysis-result`.
3. Generate one digest payload through `build-internal-digest` and send it from
   Hermes.
4. Record one adopt/reject action through `record-feedback`.
5. Only after that, decide whether M3 Bitable writes are still worth building.

## Immediate Next Decision

Pick the M0 runner:

- Hermes cron, if it can execute the no-secret command and deliver redacted
  JSON reliably.
- `systemd --user` fallback, if Hermes cron cannot satisfy that gate.
