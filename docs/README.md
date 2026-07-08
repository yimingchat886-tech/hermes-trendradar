# Hermes Stock Docs

This directory holds durable Hermes stock product and operator documents. Runtime
evidence, task execution state, and commit records stay in `.trellis/`.

## Current Product Surface

- [PRD master](PRD/PRD_MASTER.md) - long-term product boundary, roadmap, and
  release index.
- [v2.0 release PRD](PRD/releases/PRD_v2.0.md) - current release scope for the
  real-analysis loop.
- [v2.1 trending-system PRD](PRD/trending-system.md) - draft module PRD for the
  Twitter-first hotspot slice.
- [v2.1 decision ledger](PRD/_ledger/trending-twitter-source.md) - Q/A record
  for the trending-system design.

## Runbooks

- [Hermes skill invocation](runbooks/hermes-skill-invocation.md) - no-secret
  CLI invocation contract for Hermes.
- [External runtime smoke](runbooks/external-runtime-smoke.md) - local external
  runtime boundary and smoke evidence rules.
- [Production deployment handoff](runbooks/production-deployment-handoff.md) -
  current handoff state and blockers.

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
