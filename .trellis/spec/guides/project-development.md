# Project Development Standard

## Scope

Use this guide for development work in this repository when Codex App or Codex CLI is the main execution surface, Trellis manages task/spec context, and Ponytail constrains implementation complexity.

This guide complements `.trellis/workflow.md`. If the workflow and this guide differ, `.trellis/workflow.md` is the source of truth for phase order, active-task handling, and Codex dispatch mode.

## Responsibilities

### Codex

Codex is the executor:

- Read project instructions, Trellis workflow, relevant specs, and task files before changing files.
- Modify code, docs, tests, and configuration in WSL/Linux.
- Run the smallest relevant verification commands.
- Report changed files, verification evidence, and remaining risk.
- Use Ponytail review when a change adds complexity, abstractions, dependencies, or new workflow surface.

Codex must not:

- Treat Windows native shells as the default development environment.
- Bypass Trellis for implementation, build, refactor, or non-trivial docs/spec work unless the user explicitly uses a workflow skip phrase.
- Add dependencies without recording why existing platform, standard library, or installed dependencies are insufficient.
- Claim success without verification output or an explicit skipped-check reason.

### Trellis

Trellis is the repo state layer:

- `.trellis/workflow.md` defines phase order and skill/agent routing.
- `.trellis/spec/` stores stable project rules.
- `.trellis/tasks/` stores task PRDs, context JSONL, research, and implementation notes.
- `.trellis/workspace/` stores developer session records and lessons.

Put stable, reusable rules in `.trellis/spec/`. Put current-task facts in `.trellis/tasks/<task>/`. Do not leave important engineering decisions only in chat.

### Ponytail

Ponytail is the default complexity constraint. Use `full` mode unless the user or task explicitly opts into another mode.

Before adding code, ask:

1. Can this be avoided?
2. Can existing code be deleted or reused instead?
3. Does the standard library or platform already solve it?
4. Does an installed dependency already solve it?
5. Is a direct function or direct call enough?
6. What is the smallest test or check that covers the risk?

Ponytail does not justify weakening correctness, validation, security, accessibility, or data-loss protections.

## Context Loading Order

For development tasks, load context in this order:

1. `AGENTS.md`
2. `.trellis/workflow.md`
3. `.trellis/spec/guides/index.md`
4. This guide, when the task touches process, scope, dependencies, verification, or complexity
5. Package/layer specs listed by `python3 ./.trellis/scripts/get_context.py --mode packages`
6. Active task metadata and PRD under `.trellis/tasks/<active-task>/`
7. Task research or technical design files, when present
8. Target source, tests, fixtures, scripts, and configs

For source symbol edits, the GitNexus rules in `AGENTS.md` still apply before editing.

## Task Sizing

Follow `.trellis/workflow.md` for the authoritative task gate. In this project, implementation, build, refactor, and non-trivial docs/spec work should create or continue a Trellis task unless the current user message explicitly opts out using one of the workflow skip phrases.

### Direct Answer

Allowed for pure Q&A, explanation, lookup, or very small repo reads with no file writes.

### Inline Change Escape Hatch

Allowed only when the current user message explicitly asks to skip Trellis or directly make a small change. Keep the diff minimal and still run relevant verification when files change.

### Trellis Task

Required for:

- Runtime code changes.
- Multi-file documentation or spec changes.
- Build, test, type, package, or configuration changes.
- Behavioral changes.
- Dependency changes.
- Work that needs explicit acceptance criteria.

### Complex Task

Add a technical design artifact, such as `info.md`, `design.md`, or task research, when the work affects:

- API contracts or cross-layer data flow.
- Prisma schema, migrations, persistence, or generated artifacts.
- Scheduler, runner, generated artifacts, or cross-layer runtime contracts.
- Security, privacy, permissions, data deletion, deployment, rollback, or performance.
- Multiple implementation phases.

## Implementation Rules

- Locate the smallest change surface before editing.
- Prefer deleting stale complexity before adding new code.
- Do one task at a time.
- Do not mix unrelated refactors, dependency upgrades, or formatting churn into task diffs.
- Do not add interfaces, factories, adapters, registries, service layers, or configuration systems for only one real use case.
- Do not create a new utility file for logic that is clearer at the call site.
- Use `ponytail:` comments only to document a deliberate, bounded simplification and its upgrade path.

Examples:

```ts
// ponytail: linear scan is acceptable for project-local fixture lists; index only if this becomes hot.
```

```ts
// ponytail: native Node test runner keeps this check dependency-free.
```

## Dependency Policy

Adding a dependency requires all of the following:

- Standard library support is insufficient.
- Platform-native support is insufficient.
- Existing dependencies are insufficient.
- A minimal local implementation would increase correctness, security, or maintenance risk.
- License, maintenance, bundle/runtime impact, and deployment impact are acceptable.
- The reason is recorded in the task PRD, design artifact, or final report.

Do not add dependencies for small formatting, validation, conversion, or one-off UI behavior.

## Verification

Run the narrowest relevant command set after changes. Current project commands include:

```bash
npm run typecheck
npm test
npm run test:v5
npm run test:v6
npm run test:backend
npm run e2e:v6
git diff --check
```

Choose focused tests for the changed contract. Use `npm run typecheck` for TypeScript changes. Use `git diff --check` before commit or final handoff.

If a check cannot run, record:

- Command.
- Result as skipped or failed.
- Concrete reason.
- Manual follow-up or narrower substitute.

## Ponytail Review Gate

`trellis-check` must run Ponytail review after correctness checks when a diff
adds code, dependencies, abstractions, workflow surface, or broad docs/config.
Use the installed Codex Ponytail plugin's `ponytail-review` skill or command
when it is loaded. If the current session predates the plugin install, apply the
same review manually and report that fallback.

Accepted Ponytail findings should remove only unnecessary complexity:

- `delete`: dead code, speculative behavior, unused flexibility.
- `stdlib`: custom code replaced by the standard library.
- `native`: dependency or code replaced by platform-native behavior.
- `yagni`: abstraction, config, registry, or layer with one real use case.
- `shrink`: same behavior in fewer lines.

Do not accept Ponytail cuts that weaken validation, security, accessibility,
data-loss protections, correctness checks, or explicitly requested behavior.

## Finish Rules

Before reporting a task complete:

- Confirm the diff only contains task-related changes.
- Run relevant verification or state why it was skipped.
- Use Ponytail review for added complexity, new dependencies, new abstractions, or broad diffs.
- Update `.trellis/spec/` for reusable lessons or repeated rules.
- State remaining risk clearly.

## Definition of Done

A development task is done only when:

- Relevant context was loaded.
- Required Trellis task artifacts exist.
- Implementation follows Ponytail's smallest-working-change rule.
- No unjustified dependency, abstraction, config layer, or broad refactor was added.
- Relevant checks ran or skipped checks have exact reasons.
- Reusable rules were promoted to `.trellis/spec/`.
- The final diff is reviewable and scoped.
