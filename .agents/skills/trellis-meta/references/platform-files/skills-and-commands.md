# Skills, Commands, Prompts, And Workflows

Skills and commands are textual entry points for user interaction with Trellis.
For v3, first-class platform entry points are Codex and Claude Code only.
Legacy adapter paths may still exist in older projects, but they are
read-compatibility references rather than v3 support targets.

## Conceptual Differences

| Type | Trigger mode | Best for |
| --- | --- | --- |
| skill | AI auto-match or explicit user mention | Long-term capabilities, workflow rules, modification guides. |
| command | Explicit user invocation | Clear operation entry points such as continue and finish-work. |
| prompt | Explicit user invocation or platform selection | Similar to command, but in a platform prompt format. |
| workflow | Explicit user selection or platform auto-match | Legacy main-session guidance when no sub-agent/hook exists. |

Trellis workflow skills usually share one semantic set: brainstorm,
before-dev, check, update-spec, break-loop. Multi-file built-in skills such as
`trellis-meta` use layered references.

## V3 Paths

| Platform | Common entries |
| --- | --- |
| Claude Code | `.claude/skills/`, `.claude/commands/` |
| Codex | `.agents/skills/`, `.codex/skills/` |

## Legacy Adapter Paths

If a project still contains older adapter directories such as `.cursor/`,
`.opencode/`, `.gemini/`, `.qoder/`, `.codebuddy/`, `.github/`, `.factory/`,
or `.pi/`, treat them as legacy compatibility files. Inspect them when
migrating or preserving existing behavior, but do not claim v3 support from
their presence.

## Skill Structure

A common skill is a directory:

```text
trellis-meta/
├── SKILL.md
└── references/
```

`SKILL.md` should tell the AI:

- When to use this skill.
- Which reference to read first for the current task.
- What not to do.

References hold longer explanations so the entry file does not contain
everything.

## Command/Prompt/Workflow Structure

Commands, prompts, and workflows are usually single files. Their content should
include:

- When to use it.
- Which `.trellis/` files to read.
- Which scripts to run.
- How to report after completion.

They should not store task state; task state belongs in `.trellis/tasks/` and
`.trellis/.runtime/`.

## Local Change Scenarios

| User need | Edit location |
| --- | --- |
| Change AI auto-trigger rules | The corresponding skill's frontmatter description. |
| Change user command behavior | The corresponding command/prompt/workflow file. |
| Add a project-local skill | Platform skill directory, or shared `.agents/skills/`. |
| Let Codex and Claude Code share one capability | Write equivalent skills in both v3 roots, or use `.agents/skills/` when Codex is the only consumer. |
| Change finish/continue entry points | Platform commands/prompts/workflows. |

## Modification Principles

1. **Keep entry files short; references carry long content**. This matters especially for multi-file skills like `trellis-meta`.
2. **Make trigger descriptions specific**. A description that is too broad can mis-trigger; one that is too narrow may not trigger.
3. **Keep Codex and Claude Code semantics consistent**. File formats can differ, but behavior descriptions should match.
4. **Put project-specific capabilities in local skills**. Do not put team-private flows into public `trellis-meta`.

If the user only wants local AI to know one more project rule, usually create a
project-local skill or update `.trellis/spec/` instead of changing a Trellis
built-in workflow skill.
