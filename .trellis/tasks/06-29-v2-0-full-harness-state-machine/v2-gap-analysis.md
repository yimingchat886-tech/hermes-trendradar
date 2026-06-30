# V2 Gap Analysis

## Summary

V2.0 is directionally viable in this repo, but the execution plan needs to be grounded in the actual harness shape:

- There is no central Python `continue` or `finish-work` router yet.
- Archive is currently a built-in physical move with `completed` semantics.
- There are no tests.
- Oracle, RTM, and Ponytail are policy/spec surfaces only, not executable adapters.
- Codex prompt hooks cannot be treated as hard gates.

## Delivered In v2.0.0

- Created task `.trellis/tasks/06-29-v2-0-full-harness-state-machine`.
- Set the task to `meta.workflow_mode = "staged_overlay"`.
- Wrote `prd.md`.
- Wrote `harness-capability-report.md`.
- Wrote this gap analysis.
- Wrote `v2-1-0-follow-up-reference.md`.
- Did not modify core scripts, workflow routing, archive behavior, config, hooks, or tests.

## Conflicts And Downgrades

| Plan Assumption | Actual Repo State | Required Downgrade |
|---|---|---|
| Add `route_continue.py` / `route_finish.py` around existing route layer | Continue and finish-work are skill/workflow entry points, not central Python routers | Add explicit wrapper commands first; update skills/workflow after wrappers exist |
| State machine can gate archive directly | Built-in `task.py archive` writes `completed` and moves the directory immediately | Build `archive-child` as a separate command; do not alter built-in archive in first slices |
| Tests can be added to existing test framework | No test directory or config exists | Add minimal `tests/trellis/` with stdlib/pytest only |
| JSON schema can be enforced immediately | No schema tooling or dependency policy decision exists | v2.1.0 should use stdlib validation first; add schema tooling only if needed |
| Oracle adapter can become part of V2 early | Only Oracle policy/template exists | Keep Oracle executable adapter out of v2.1.0 |
| RTM auto-sync can be part of V2 early | No RTM docs or machine files exist | Keep RTM sync out of v2.1.0; first define state/evidence shape |
| Codex hook can enforce state gates | Hooks require user-level enablement and approval | Use CLI gates as source of truth; hooks may only remind |
| `harness_state_machine` can own current work | Mode does not exist yet | Use `staged_overlay` until the executable state engine exists |

## Unfinished Areas

### State Engine

Not done:

- `state_machine.py`
- `state_cli.py`
- transition table
- append-only `state-events.jsonl`
- invalid transition checks
- corrupted state detection

Recommended next: v2.1.0.

### Workflow Mode Routing

Not done:

- default/staged/state-machine mode router
- continue route gate
- finish-work route gate
- archive route gate

Recommended after state engine tests pass.

### Commit Gate

Not done:

- completion signal parser
- precommit gate
- postcommit state update
- diff-scope validation

Recommended after route wrappers exist.

### True Child Archive

Not done:

- archive-child
- archive manifest
- parent `child-evidence.jsonl`
- parent rollup command
- recovery/rollback path

Recommended after commit gate.

### Parent Closeout

Not done:

- parent closeout gate
- parent archive wrapper
- all-child archived check
- closeout report enforcement

Recommended after true child archive.

### Oracle / Ponytail / RTM

Not done:

- executable Oracle adapter
- Oracle findings schema/parser
- Ponytail gate script
- RTM JSON schema
- RTM sync/check scripts
- Done evidence gate

Recommended after the core lifecycle path works.

## Risk Notes

- Changing built-in `task.py archive` too early risks breaking default Trellis tasks.
- Adding a broad state model before tests risks creating another text-only workflow layer.
- Adding adapter scripts before core state events exist would create policy code with nowhere reliable to write results.
- Force-adding ignored `.trellis/tasks/` evidence must stay scoped to the current task only.

## Acceptance For Moving To v2.1.0

v2.1.0 can start when the user accepts this reality-check scope and agrees that the next slice is state-engine-only:

- No route wiring.
- No archive changes.
- No commit gate.
- No Oracle/RTM/Ponytail adapter.
- Tests first.
