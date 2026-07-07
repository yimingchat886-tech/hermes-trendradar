# Agents

Trellis agent files define specialized roles. For v3, first-class direct
sub-agent files are Codex and Claude Code only. Older adapter agent files are
legacy compatibility references; preserve them when encountered, but do not
describe them as supported v3 targets.

Common Trellis agents in a user project are:

- `trellis-research`
- `trellis-implement`
- `trellis-check`

## Agent Responsibilities

| Agent | Responsibility |
| --- | --- |
| `trellis-research` | Investigate the question and write findings into the current task's `research/`. |
| `trellis-implement` | Implement against `prd.md`, `info.md`, `implement.jsonl`, and related spec/research. |
| `trellis-check` | Review changes, fix discovered issues, and run necessary checks. |

Agent files should not become generic chat prompts. They should define input
sources, write boundaries, whether code may be changed, and how results are
reported.

## V3 Paths

| Platform | Agent path |
| --- | --- |
| Claude Code | `.claude/agents/trellis-*.md` |
| Codex | `.codex/agents/trellis-*.toml` |

## Legacy Adapter Paths

Older projects may still contain agent or prompt files under `.cursor/`,
`.opencode/`, `.kiro/`, `.gemini/`, `.qoder/`, `.codebuddy/`, `.factory/`,
`.pi/`, or `.github/`. Treat those as legacy migration evidence unless the
current task explicitly reopens support for that adapter.

## Two Context Loading Modes

### Hook Push

The platform hook injects task context before the agent starts. The agent file
itself can focus more on responsibilities and boundaries.

### Agent Pull

The agent file instructs the agent to read after startup:

- `python3 ./.trellis/scripts/task.py current --source`
- current task `prd.md`
- `info.md`
- `implement.jsonl` or `check.jsonl`
- spec/research files referenced by JSONL

This mode fits platforms whose hooks cannot reliably rewrite sub-agent
prompts. Codex sub-agent mode uses this pattern and still requires the main
session dispatch prompt to start with `Active task: <task path>`.

## Local Change Scenarios

| User need | Edit location |
| --- | --- |
| Implement agent must follow extra restrictions | The platform's `trellis-implement` agent file. |
| Check agent must run project-specific commands | `trellis-check` agent file, and `.trellis/spec/` if needed. |
| Research agent must output a fixed format | `trellis-research` agent file. |
| Agent cannot read task context | Agent prelude or platform hook. |
| Add a project-specific agent | Platform agent directory + related workflow/command/skill entry point. |

## Modification Principles

1. **Keep responsibilities single-purpose**. Do not mix research, implement, and check responsibilities into one agent.
2. **Specify the read order**. Agents must know to start from the active task and then find the PRD and JSONL.
3. **Specify write boundaries**. Research usually only writes `research/`; implement can write code; check can fix issues.
4. **Keep v3 platform semantics synchronized**. If the user configured both Claude Code and Codex, decide whether changes to one platform's agent also need to be applied to the other.

## Do Not Default To Editing Upstream Templates

Local AI should default to modifying platform agent files inside the user
project. Discuss upstream template source only when the user explicitly wants
to contribute the change back to Trellis.
