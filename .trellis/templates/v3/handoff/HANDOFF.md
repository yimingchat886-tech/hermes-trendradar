# Trellis Harness Handoff

This file is the downstream handoff index for the v3 Trellis harness. It tells
humans and agents where to look, what may be copied, and which checks prove the
handoff docs stayed inside their boundary.

## Source Of Truth

| Need | File |
|---|---|
| Portable AI project context | `AGENTS.md` |
| Trellis workflow phases and routing | `.trellis/workflow.md` |
| Stable project rules | `.trellis/spec/` |
| v3 task templates | `.trellis/templates/v3/` |
| Hermes handoff command contract | `docs/runbooks/hermes-skill-invocation.md` |

Do not copy workflow-state blocks, routing tables, or phase text into generated
handoff docs. Link to `.trellis/workflow.md` instead.

## Downstream Apply Order

1. Run `trellis update --dry-run` in the downstream repo.
2. Resolve official Trellis update decisions first.
3. Run the future mother-repo overlay dry run.
4. Apply only manifest-listed files whose expected hashes match.

The overlay must not write into `.trellis/tasks/**`, `.trellis/workspace/**`,
`.trellis/.runtime/**`, `.trellis/.template-hashes.json`, or the
`TRELLIS:START` managed block in `AGENTS.md`.

## Handoff Files

Required:

- `README.md`
- `HANDOFF.md`
- `docs/runbooks/hermes-skill-invocation.md`

Conditional:

- `.hermes.md`, only when the downstream target expects Hermes Agent to read
  repo-local context directly.

## Hermes Package Contract

The production handoff contract is file-based. A downstream Hermes-capable
runtime may expose:

```bash
run-daily --analysis-mode hermes-handoff --json
```

Successful output must include `analysis_package_ref`. Invalid package input is
reported as `handoff_package_invalid`, with the production contract using exit
code `6`.

This harness does not invoke Hermes live. It documents the contract so a
downstream project can wire its own skill, CLI, or validation path.

## Verification

Use the M11 task verification commands before accepting a handoff update:

```bash
test -f README.md
test -f HANDOFF.md
test -f docs/runbooks/hermes-skill-invocation.md
rg -n "analysis_package_ref|handoff_package_invalid|run-daily --analysis-mode hermes-handoff --json" HANDOFF.md docs/runbooks/hermes-skill-invocation.md
git diff --check
```
