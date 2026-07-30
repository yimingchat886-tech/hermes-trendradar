# sync trellis harness

## PM Intake

### Original Request

同步trellis harness

### Real Goal

Refresh this repository's local Trellis harness files from the canonical harness repo at `/home/jym/workspace/trellis harness` without copying runtime state or deleting Hermes-specific project guidance.

## What will change?

Refresh the reusable Trellis harness files, local Trellis skills, Codex hooks, tracked Claude hooks/settings, templates, and harness tests from the canonical source.

## Why now?

The canonical harness has newer v3 workflow, done-gate, hook, template, and skill updates that this downstream repo should absorb.

## How will it be verified?

Run Trellis script compilation, Trellis/harness pytest coverage, context/current-task smoke checks, updater syntax validation, GitNexus change detection, and `git diff --check`.

### Risk Level

T4: harness/tooling behavior.

### Optimized Requirement

Use the existing updater script's downstream sync boundary for this repo only: sync reusable Trellis workflow/scripts/templates/spec guide updates, repo-local Trellis skills, and Codex agents/hooks from the canonical harness while preserving active tasks, workspace state, developer state, project-specific specs, and updater registration.

## Requirements

- Source harness is `/home/jym/workspace/trellis harness`.
- Target repo is `/home/jym/workspace/Hermes stock`.
- Preserve `.trellis/tasks/`, `.trellis/workspace/`, `.trellis/.runtime/`, and `.trellis/.developer`.
- Preserve project-specific spec files by copying `.trellis/spec/guides/` and `.trellis/spec/project/` without `--delete`.
- Sync tracked `.claude/settings.json` and `.claude/hooks/` files, while preserving ignored local Claude settings.
- Do not copy Python cache artifacts.
- Do not add a duplicate `~/.local/bin/update-ai-tooling` registration because the target is already listed.
- Update `~/.local/bin/update-ai-tooling` so future downstream syncs include tracked `.claude` hooks/settings.

## Acceptance Criteria

- [x] Harness sync completes from the canonical source into this repo.
- [x] `python3 ./.trellis/scripts/get_context.py --mode packages` still reports single-repo project context.
- [x] `python3 ./.trellis/scripts/task.py current --source` still identifies this active task.
- [x] `bash -n ~/.local/bin/update-ai-tooling` passes.
- [x] `git diff --check` passes.

## Definition of Done

- Changed files are limited to the harness refresh and this task's Trellis bookkeeping.
- Runtime/task/workspace state from the source harness is not copied.
- No dependency is added.

## Out of Scope

- Running the full `~/.local/bin/update-ai-tooling` script and upgrading global tools.
- Committing, pushing, or archiving unless explicitly requested after review.
- Deleting Hermes-specific project specs.

## Technical Notes

- Existing updater contains most of the safe downstream sync function; this task applies that boundary manually because the active Trellis task would cause the updater to skip this repo.
- The source harness also has tracked `.claude` hooks/settings, so this task synced those tracked files directly, preserved `.claude/settings.local.json`, and updated the updater to mirror those tracked files in future runs.
- Source and target git worktrees were clean before task creation; current dirtiness is the new Trellis task and generated board entry.
