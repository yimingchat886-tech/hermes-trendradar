# Project Specs

## Pre-Development Checklist

- taskrun-runtime.md: Task, shared SQLite authority, action graph, review,
  projections, closeout, migration, and legacy fail-closed rules.
- prd-governance.md: draft, acceptance, immutable binding generations, and
  requirement ownership.
- downstream-deployer.md: immutable release, target slots, dirty overlap,
  exact registry binding, GitNexus foundation, apply/verify, partial retry, and
  receipts.
- upstream-release.md: AVAILABLE, ADOPTED, and INSTALLED state boundaries for
  immutable npm base plus local overlay.
- protocol-phrases.md: start, closeout, limits, and external-effect authority.
- git-commit-push-policy.md: scoped local commits and separate push gate.
- task-cancel-lifecycle.md: in-place cancellation and cleanup.
- task-sizing.md: compact action versus persistent action graph.
- ponytail-boundary.md and oracle-review-policy.md.

## Quality Check

- One intent produced one Task and one stable run.
- New tasks have no lifecycle tier and default to Loop.
- Git-common-dir SQLite is authoritative; projections never gate it.
- Check Agent is read-only and reviews complete deterministic candidates only.
- Logical checks resolve from an exact version/capability catalog.
- Closeout is persisted, replayable, logical-archive only, and does not push.
- Sync uses one source run with target slots and preserves unrelated dirt.
- Legacy writer commands return LEGACY_WRITE_DISABLED with zero writes.
