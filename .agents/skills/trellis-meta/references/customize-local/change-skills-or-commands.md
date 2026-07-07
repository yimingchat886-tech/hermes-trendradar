# Change Local Skills, Commands, Prompts, And Workflows

When the user wants to change AI entry points, auto-trigger rules, or explicit command behavior, edit skills, commands, prompts, or workflows in local platform directories.

## Read These Files First

1. `.trellis/workflow.md`
2. Target platform skill/command/prompt/workflow directory
3. Related agent or hook files
4. Whether project rules already exist in `.trellis/spec/`

## Which Entry Type To Choose

| Goal | Recommendation |
| --- | --- |
| AI should automatically know a capability | Add or modify a skill. |
| User wants to trigger manually with a command | Add or modify a command/prompt/workflow. |
| Team project conventions | Prefer `.trellis/spec/` or a project-local skill. |
| Change Trellis flow semantics | Synchronize `.trellis/workflow.md`. |

## Modify A Skill

A skill is usually:

```text
<skill-name>/
├── SKILL.md
└── references/
```

`SKILL.md` should be short and responsible for triggering/routing. Put long content in `references/` so AI can read it on demand.

The frontmatter description should specify when to use the skill. Example:

```yaml
description: "Use when customizing this project's deployment workflow and release checklist."
```

Do not write vague descriptions such as "helpful project skill"; they can trigger incorrectly.

## Modify A Command/Prompt/Workflow

Explicit entry points should state:

- How the user triggers it.
- Which `.trellis/` files to read.
- Which scripts to run.
- How to report after completion.

If a command only repeats workflow rules, prefer making it reference/read `.trellis/workflow.md` instead of maintaining a second copy of the flow.

## Common Paths

| Platform | Entry directories |
| --- | --- |
| Claude Code | `.claude/skills/`, `.claude/commands/` |
| Codex | `.agents/skills/`, `.codex/skills/` |

Legacy adapter directories such as `.cursor/`, `.opencode/`, `.github/`,
`.kilocode/`, `.agent/`, and `.windsurf/` may exist in older projects; treat
them as migration evidence, not v3 support targets.

## Add A Project-Local Skill

If the user wants to document team-private customizations, create a project-local skill, for example:

```text
.claude/skills/project-trellis-local/
└── SKILL.md
```

For v3 multi-platform projects, add equivalent versions in the Codex and
Claude Code skill roots, or use `.agents/skills/` when Codex is the intended
consumer.

## Notes

- Do not mix every platform's syntax into one file.
- Do not change only one platform entry point while claiming all platforms are supported.
- Do not hide long-term engineering conventions inside a command; write them to `.trellis/spec/`.
