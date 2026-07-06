# brainstorm: trellis workflow migration skill

## Goal

Create a repo-local skill that helps migrate Trellis workflow changes safely, without mixing unrelated Trellis lifecycle, task state, or project runtime changes.

## What will change?

Add the global Codex skill `/mnt/c/Users/Jym/.codex/skills/trellis-workflow-migration/`, a guidance-only skill for migrating the current Hermes stock AI workflow skeleton into new projects.

## Why now?

New projects should be able to adopt the latest proven Trellis/Codex workflow from the current Hermes stock branch without copying historical tasks, runtime state, or Hermes-specific project details.

## How will it be verified?

Validate the skill structure, check whitespace on changed files, validate the Trellis task metadata, and review the diff for unnecessary complexity.

## What I Already Know

- User asked to create a `.trellis` workflow migration skill and clarify boundaries before implementation.
- This repository already has repo-local Trellis skills under `.agents/skills/`.
- Workflow semantics live primarily in `.trellis/workflow.md`; stable reusable rules live under `.trellis/spec/`.
- Current workflow guidance requires task planning before implementation and context curation before activation.
- Skill creation should stay concise: required `SKILL.md`, optional `agents/openai.yaml`, and only necessary bundled references/scripts.

## Assumptions To Validate

- The skill should live in `/mnt/c/Users/Jym/.codex/skills/`, because it must be available to new Codex threads across projects.
- The skill should guide Codex-thread migration work in conversation, not perform broad automatic rewrites or include an automation script.

## Open Questions

- None.

## Requirements (Evolving)

- Ask for boundary clarification before creating the skill body.
- Create only a migration guidance skill.
- The migration itself is performed by Codex threads through user conversation.
- Keep the implementation minimal and global so new Codex threads can use it outside this repository.
- Do not add automatic migration scripts in the first version.
- Cover the complete AI workflow package for bootstrapping new projects:
  `.trellis/`, `.agents/skills/`, and `.codex/` workflow hooks/config/agents.
- The purpose is to help new projects migrate an existing proven workflow instead of rebuilding the workflow from scratch.
- Once migration starts, default to replacing target Trellis/Codex workflow files with the latest approved workflow package.
- The user is responsible for deciding before migration that the target project should adopt this workflow; the skill should not add another conflict-confirmation loop by default.
- The authoritative source for "latest workflow" is the current branch of the current Hermes stock checkout.
- Migrate only the workflow skeleton, not historical or runtime state.
- Include workflow skeleton files such as `.trellis/workflow.md`, `.trellis/config.yaml`, `.trellis/.gitignore`, `.trellis/scripts/`, `.trellis/templates/`, `.trellis/spec/`, `.agents/skills/`, and `.codex/`.
- Exclude stateful project history such as `.trellis/tasks/`, `.trellis/workspace/`, `.trellis/.runtime/`, active task pointers, archived tasks, session journals, and generated run state.
- After migration, Codex must replace source-project names, local paths, and Hermes stock-specific conventions with the target project's name, paths, and local rules.
- The skill should guide Codex to search for likely source markers such as `Hermes stock`, `hermes`, `/home/jym/workspace/Hermes stock`, and other copied local paths before finishing.
- Migration verification must prove both:
  - Trellis context starts in the target project with `python3 ./.trellis/scripts/get_context.py`, `--mode phase`, and `--mode packages`.
  - Basic task lifecycle smoke works in the target project, using a temporary smoke task and cleaning its state afterward.

## Acceptance Criteria

- [x] Global skill location and trigger scope are explicit.
- [x] Skill makes clear that migration starts after the user has chosen to adopt the workflow.
- [x] Skill says which Trellis/Codex workflow skeleton files to migrate.
- [x] Skill defines what is out of scope for migration work.
- [x] Skill requires Trellis context startup verification in the target project.
- [x] Skill requires a temporary task lifecycle smoke in the target project and cleanup afterward.

## Definition of Done

- Global skill file(s) are created or updated in the agreed location.
- The repo diff contains only task-related Trellis records.
- Relevant validation runs, or skipped checks are recorded with reasons.

## Out of Scope (Tentative)

- Runtime source changes.
- Dependency changes.
- Automatic migration of every repo on the machine.
- Automatic file rewrite scripts for migration.
- Narrow `.trellis/`-only migration.
- Preserving older target workflow files by default once migration has begun.
- Migrating Hermes stock task history, archived tasks, runtime session pointers, or developer journals into new projects.
- Leaving Hermes stock project names, local paths, or project-specific business/runtime conventions in the target workflow.
- Commit, push, or archive unless separately requested.

## Technical Notes

- Loaded `.agents/skills/trellis-start/SKILL.md`.
- Loaded `.agents/skills/trellis-brainstorm/SKILL.md`.
- Loaded system `skill-creator` guidance.
- Loaded `.trellis/spec/guides/project-development.md`.
- Inspected `.trellis/spec/project/index.md`, `.agents/skills/`, `.trellis/templates/`, and `.trellis/workflow.md`.
- Loaded `.agents/skills/trellis-meta/SKILL.md`.
- Loaded Trellis meta references for local architecture, local customization, skill changes, and Codex platform paths.
- Implemented `/mnt/c/Users/Jym/.codex/skills/trellis-workflow-migration/SKILL.md` as a single guidance-only global skill.
- Generated `/mnt/c/Users/Jym/.codex/skills/trellis-workflow-migration/agents/openai.yaml`.
