# Platform File Map

This page lists Trellis file locations in a user project by platform. For v3,
first-class support is limited to Codex and Claude Code. Older adapter paths
are legacy compatibility references only; do not present them as supported v3
targets.

## V3 Matrix

| Platform | CLI flag | Main directory | Skill directory | Agent directory | Hooks/settings |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `--claude` | `.claude/` | `.claude/skills/` | `.claude/agents/` | `.claude/hooks/` + `.claude/settings.json` |
| Codex | `--codex` | `.codex/` | `.agents/skills/` | `.codex/agents/` | `.codex/hooks/` + `.codex/hooks.json` |

## Legacy Adapter Paths

These paths may exist in older projects or migration fixtures. Treat them as
read-compatibility evidence only unless a task explicitly reopens legacy
adapter support:

| Legacy flag | Main directory | Skill directory | Agent/prompt directory | Hook/config path |
| --- | --- | --- | --- | --- |
| `--cursor` | `.cursor/` | `.cursor/skills/` | `.cursor/agents/` | `.cursor/hooks.json` |
| `--opencode` | `.opencode/` | `.opencode/skills/` | `.opencode/agents/` | `.opencode/plugins/` |
| `--kilo` | `.kilocode/` | `.kilocode/skills/` | Usually none | `.kilocode/workflows/` |
| `--kiro` | `.kiro/` | `.kiro/skills/` | `.kiro/agents/` | `.kiro/hooks/` |
| `--gemini` | `.gemini/` | `.agents/skills/` | `.gemini/agents/` | `.gemini/settings.json` |
| `--antigravity` | `.agent/` | `.agent/skills/` | Usually none | `.agent/workflows/` |
| `--windsurf` | `.windsurf/` | `.windsurf/skills/` | Usually none | `.windsurf/workflows/` |
| `--qoder` | `.qoder/` | `.qoder/skills/` | `.qoder/agents/` | `.qoder/settings.json` |
| `--codebuddy` | `.codebuddy/` | `.codebuddy/skills/` | `.codebuddy/agents/` | `.codebuddy/settings.json` |
| `--copilot` | `.github/` | `.github/skills/` | `.github/agents/` | `.github/copilot/hooks/` |
| `--droid` | `.factory/` | `.factory/skills/` | `.factory/droids/` | `.factory/settings.json` |
| `--pi` | `.pi/` | `.pi/skills/` | `.pi/agents/` | `.pi/extensions/trellis/` |

## Capability Groups

### V3 Sub-Agent Support

These platforms have first-class `trellis-research`, `trellis-implement`, and
`trellis-check` surfaces:

- Claude Code
- Codex

When changing implementation/check/research behavior, start from those files
and keep `.trellis/workflow.md` synchronized.

### Legacy Main-Session Workflows

Some older adapters relied on workflow or skill files instead of Trellis
sub-agent files. Leave those references alone unless the task explicitly
targets legacy migration.

### Shared `.agents/skills/`

Codex writes the shared `.agents/skills/` layer. Treat that layer as Codex's v3
skill root unless a separate compatibility task proves another adapter should
consume it.

## Decision Rules When Modifying Platform Files

1. User specified Codex or Claude Code: modify only that platform directory unless shared workflow/spec files must also change.
2. User says "all v3 platforms should do this": synchronize Codex and Claude Code only.
3. User only says "my AI": inspect the configuration directories that actually exist in the project and infer the current first-class platform.
4. User wants project rules: prefer `.trellis/spec/` or a project-local skill.
5. User wants Trellis behavior: edit `.trellis/workflow.md` plus the relevant Codex/Claude hook, agent, skill, or command file.

## When Paths Differ

Project-local files are authoritative. If a table disagrees with actual
settings/config, follow the local files and report the mismatch. Do not delete
custom or legacy files just because they are not part of the v3 support matrix.
