# Hermes Stock Docs

This directory holds durable Hermes stock product and operator documents. Runtime
evidence, task execution state, and commit records stay in `.trellis/`.

## Current Product Surface

- [PRD master](PRD/PRD_MASTER.md) - long-term product boundary, roadmap, and
  release index.
- [v2.0 Extra release PRD](PRD/releases/PRD_v2.0_extra.md) - accepted Agent
  video downloader, orchestration, and seven-day retention contract.
- [v2.0 release PRD](PRD/releases/PRD_v2.0.md) - retained but paused Hermes
  analysis scope.
- [v2.1 trending-system PRD](PRD/trending-system.md) - draft module PRD for the
  Twitter-first hotspot slice.
- [v2.1 decision ledger](PRD/_ledger/trending-twitter-source.md) - Q/A record
  for the trending-system design.

## Runbooks

- [Agent media downloader](runbooks/agent-media-downloader.md) - request,
  completion envelope, manifest, backend, and cleanup contracts.
- [Hermes agent deployer handoff](runbooks/hermes-agent-deployer-handoff.md) -
  profile, transport, MCP, and SKILL.md contract for Hermes deployers.
- [External runtime smoke](runbooks/external-runtime-smoke.md) - local external
  runtime boundary and smoke evidence rules.

## Reports

- [Repo completion gap report](reports/repo-completion-gap-report.md) - current
  production-readiness gap report.

## Historical PRDs

- [v1.3 PRD](PRD/releases/archive/PRD_v1.3.md)
- [v1.4 split index](PRD/releases/archive/PRD_v1.4_split-index.md)
- [v1.4 Codex CLI implementation](PRD/releases/archive/PRD_v1.4_codex-cli-implementation.md)
- [v1.4 Hermes runtime and profiles](PRD/releases/archive/PRD_v1.4_hermes-runtime-and-profiles.md)

## Moved Out

Trellis workflow reports and operator guides now belong in the canonical
`/home/jym/workspace/trellis harness` repository. They are no longer maintained
as Hermes stock product docs.
