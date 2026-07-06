# Stabilize GitNexus Generated Guidance

## Goal

Stop routine GitNexus index refreshes from dirtying tracked guidance files (`AGENTS.md`, `CLAUDE.md`, and `.claude/skills/gitnexus/*`) with volatile index metadata.

## What I Already Know

- `gitnexus analyze` can update tracked guidance files while refreshing the local index.
- Current live registry state is newer than the committed guidance counts, so another full guidance refresh would create more churn.
- GitNexus supports `analyze --index-only`, which skips AGENTS, CLAUDE, and skill-file injection.
- This repo already has examples of using `node .gitnexus/run.cjs analyze --index-only --name "Hermes stock"` for index refresh.

## Requirements

- Prefer `node .gitnexus/run.cjs analyze --index-only --name hermes-trendradar` for routine index refreshes.
- Document the fallback for a missing `.gitnexus/run.cjs` as `npx gitnexus analyze --index-only --name hermes-trendradar`.
- Keep full guidance regeneration explicit and rare.
- Avoid committing volatile symbol/count churn as part of ordinary feature or docs work.

## Acceptance Criteria

- [x] Repo guidance tells agents to use index-only GitNexus refresh by default.
- [x] GitNexus skill docs no longer recommend plain `analyze` for routine stale-index recovery.
- [x] Guidance identifies plain/full `analyze` as an explicit metadata refresh, not the normal path.
- [x] `git diff --check` passes.

## Definition of Done

- Relevant guidance files are updated with the smallest durable wording change.
- No runtime source code, tests, dependencies, or generated index files are changed.
- Verification output is recorded in the final report.

## Out of Scope

- Editing GitNexus upstream behavior.
- Creating new wrappers, hooks, aliases, or config files unless simple guidance proves insufficient.
- Re-indexing the repo as part of this task.
- Committing or pushing changes.

## Technical Approach

Use the GitNexus CLI's built-in `--index-only` flag instead of adding a local wrapper. Update only the guidance surfaces that currently tell agents to run plain `analyze`.

## Decision (ADR-lite)

**Context**: Plain `gitnexus analyze` refreshes the index and may also inject AGENTS, CLAUDE, and skill guidance. Those files are tracked, so volatile stats become dirty diffs.

**Decision**: Make index-only refresh the repo default: `node .gitnexus/run.cjs analyze --index-only --name hermes-trendradar`.

**Consequences**: Routine graph refreshes stay local to `.gitnexus/` and the registry. Full guidance regeneration remains possible, but it is a deliberate tooling-doc update.
