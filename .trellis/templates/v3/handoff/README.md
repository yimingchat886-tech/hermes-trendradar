# Trellis Harness

This repository uses the Trellis harness with a receipt-gated Loop v1 parent
runtime and an explicit current-Trellis compatibility selector.

## Start Here

- `AGENTS.md` is the portable AI project-context entry point. Do not edit inside
  its `TRELLIS:START` managed block.
- `.trellis/workflow.md` defines Trellis phases and routing.
- `.trellis/spec/project/loop-v1-qualification.md` defines qualification,
  activation, operation, and rollback gates.
- `.trellis/spec/project/loop-v1-overlay-manifest.json` defines exclusive path
  ownership.
- `HANDOFF.md` is the compatibility and downstream handoff index.

## Admission Boundary

Loop v1 is fail-closed. Enabling a selector is insufficient without a matching
hash-addressed receipt for the exact committed runtime and local environment.
Receipt drift blocks admission and pauses authorized parents before their next
write. Current-Trellis remains available explicitly; `loop_v4` is read-only
historical evidence.

No downstream overlay apply, pilot, remote, push, release, or deployment is
implied by this handoff template.
