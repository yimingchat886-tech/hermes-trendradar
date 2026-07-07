# Hermes Skill Invocation Runbook

This runbook records the narrow Hermes handoff package contract used by
downstream projects. It is a documentation contract only; this repository does
not install Hermes, call Hermes live, or require an npm package.

## Command

Downstream projects that implement the Hermes handoff package should expose:

```bash
run-daily --analysis-mode hermes-handoff --json
```

## Success Contract

The JSON result must include:

- `analysis_package_ref`: reference to the generated handoff package.
- package metadata sufficient for the downstream validator to locate the
  generated artifact.

The reference should be treated as an artifact pointer, not as inline task
state. Trellis task state remains in `.trellis/tasks/**` and the workflow
source of truth remains `.trellis/workflow.md`.

## Invalid Package Contract

Invalid handoff packages are reported as:

- process exit code `6`;
- JSON error code `handoff_package_invalid`;
- a human-readable error message.

The validator should fail closed. Do not silently continue with a missing,
malformed, or stale handoff package.

## Context Files

- `AGENTS.md` is the portable AI context entry point.
- `HANDOFF.md` is the downstream handoff index.
- `.hermes.md` is optional and should exist only when a downstream target
  expects Hermes Agent-specific repo context.

Do not place secrets, credentials, cookies, or product runtime data in handoff
context files.
