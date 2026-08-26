<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

# Project Development Standard

- Before non-trivial development, process, dependency, verification, or spec work, read `.trellis/spec/guides/project-development.md`.
- Use Ponytail full mode by default: prefer deletion, reuse, standard library, platform-native behavior, and existing dependencies before adding code or abstractions.
- Do not weaken validation, security, accessibility, or data-loss protections to reduce code.
- This is an independent project; do not assume another project's backend, media, scheduler, or product contracts apply here unless a future local spec says so.

<!-- gitnexus:start -->
# GitNexus

Repository id: `hermes-trendradar`. Keep the tracked tree stable: rebuild with `gitnexus analyze --index-only`; never commit `.gitnexus/`.

Use `gitnexus query` for unfamiliar flows, `context` for one symbol, `impact --direction upstream` before symbol edits, and `detect-changes --scope compare --base-ref <origin/HEAD>` before commit. Missing or stale index evidence is degraded, not proof of zero risk. Detailed workflows are under `.agents/skills/gitnexus/` and `.claude/skills/gitnexus/`.
<!-- gitnexus:end -->
