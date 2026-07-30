# Trellis Harness Handoff

This is the Loop v1 compatibility and downstream handoff index. Local
qualification is repository- and environment-specific, not transferable proof.

## Source Of Truth

| Need | File |
|---|---|
| Portable AI context | `AGENTS.md` |
| Trellis phases and routing | `.trellis/workflow.md` |
| Loop v1 runtime contracts | `.trellis/spec/project/loop-v1-*.md` |
| Qualification and rollback | `.trellis/spec/project/loop-v1-qualification.md` |
| Overlay ownership | `.trellis/spec/project/loop-v1-overlay-manifest.json` |

## Activation Gate

Admission requires an enabled config, a matching hash-addressed receipt for the
exact committed runtime, and no local rollback marker. Receipt or environment
drift fails closed. Removing a rollback marker never resumes a paused parent.

## Compatibility

- `current_trellis` remains an explicit parent selector.
- `loop_v4` remains historical and cannot admit new work.
- Local correctness does not require a remote, CI, installed hooks, or network.
- Unknown or prohibited effects stop before execution.

## Downstream Order

Resolve official Trellis changes first, then validate the separately versioned
Loop overlay. Stop on unknown ownership, overlapping scopes, or fingerprint
drift. Preserve project data, generated runtime state, official metadata, and
the managed `TRELLIS:START` block.

This template does not authorize a downstream apply, pilot, push, release, or
deployment.
