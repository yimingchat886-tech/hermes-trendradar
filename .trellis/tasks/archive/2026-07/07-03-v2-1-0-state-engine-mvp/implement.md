# v2.1.0 Implementation Plan

## Scope

Implement the v2.1 state engine only:

- `.trellis/scripts/state_machine.py`
- `.trellis/scripts/state_cli.py`
- `tests/trellis/test_state_machine.py`

## Steps

1. Add table-driven state transitions and guarded task/log persistence.
2. Add the CLI wrapper.
3. Add temp-directory tests for init, re-init, valid events, invalid events, event-log write failure, task-write rollback, blocker edge cases, append-only logging, status preservation, and mode rejection.
4. Run focused tests and `git diff --check`.
5. Record verification in `stage-report.md`.

## Non-Goals

No `task.py`, archive, continue, finish-work, commit gate, hook, Oracle adapter, Ponytail adapter, RTM, or migration work.
