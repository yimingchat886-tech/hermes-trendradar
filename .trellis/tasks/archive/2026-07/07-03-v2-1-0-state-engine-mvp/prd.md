# v2.1.0 State Engine MVP

## PM Intake

### Original Request

执行 v2.1。

### Real Goal

Land the smallest executable Trellis harness state engine from the v2.1.0 follow-up reference, without touching current active tasks or changing default Trellis behavior.

### Risk Level

T4: harness state machine and tooling behavior.

### Staged Overlay Needed

Yes. This is harness/tooling work, but this task itself stays on `staged_overlay`; the new engine only operates on test tasks explicitly marked `workflow_mode = "harness_state_machine"`.

### Oracle Review Budget Needed

Yes for the first complete PRD/PLAN checkpoint because this is T4 harness work.

## Goal

Add a tested, opt-in state engine for future Trellis harness v2 work.

The engine records state under `task.json.meta.state_machine` and appends accepted transitions to `state-events.jsonl`. It must not modify `task.py`, current active tasks, archive behavior, hooks, or existing default/staged flows.

## Requirements

- Add `.trellis/scripts/state_machine.py`.
- Add `.trellis/scripts/state_cli.py`.
- Add `tests/trellis/test_state_machine.py`.
- Use only the Python standard library plus existing `pytest`.
- Treat missing `meta.workflow_mode` as `default_trellis`.
- Only initialize or transition tasks whose `meta.workflow_mode` is `harness_state_machine`.
- Store state under `task.json.meta.state_machine`.
- Append exactly one JSON line to `state-events.jsonl` for each accepted event.
- Reject invalid transitions with a non-zero exit and no partial write.
- Reject event-log write failures with no `task.json` mutation.
- Treat first-time `init` as an accepted event that writes one JSONL line.
- Treat same-kind `init` as idempotent with no write.
- Reject opposite-kind `init` with no write.
- Store blocker return state explicitly as `blocked_from_state`.
- Reject `blocker_resolved` when not blocked or when `blocked_from_state` is missing/corrupt.
- Reject `blocker_opened` while already blocked or archived.
- Preserve unknown `task.json` fields.
- Keep `task.json.status` on the existing compatibility lifecycle.
- Provide CLI commands:
  - `python3 .trellis/scripts/state_cli.py init <task-dir> --kind parent|child`
  - `python3 .trellis/scripts/state_cli.py event <task-dir> <event> [--by user|agent|system] [--note "..."]`
  - `python3 .trellis/scripts/state_cli.py status <task-dir>`

## Minimal State Set

Parent:

- `parent_prd_draft`
- `parent_waiting_completion_signal`
- `parent_commit_ready`
- `parent_archive_ready`
- `parent_archived`
- `parent_blocked`

Child:

- `child_plan_draft`
- `child_waiting_completion_signal`
- `child_commit_ready`
- `child_archive_ready`
- `child_archived`
- `child_blocked`

## Minimal Event Set

- `prd_drafted`
- `parent_completion_signal_received`
- `parent_commit_created`
- `parent_archive_completed`
- `plan_drafted`
- `completion_signal_received`
- `commit_created`
- `child_archive_completed`
- `blocker_opened`
- `blocker_resolved`

## Acceptance Criteria

- [ ] `state_cli.py init` creates `meta.state_machine` for parent and child tasks.
- [ ] Valid events update `current_state`, `previous_state`, `last_event`, and `updated_at`.
- [ ] Valid events append exactly one JSON line to `state-events.jsonl`.
- [ ] Invalid events fail without changing `task.json` or `state-events.jsonl`.
- [ ] Simulated event-log write failure leaves `task.json` and `state-events.jsonl` unchanged.
- [ ] Simulated `task.json` replace failure after log replace restores the previous `state-events.jsonl`.
- [ ] Same-kind re-init is a no-op; opposite-kind re-init is rejected with no write.
- [ ] Blocker edge cases are covered: resolve while not blocked, open while blocked, open from archived, and missing/corrupt `blocked_from_state`.
- [ ] `task.json.status` remains unchanged across init, valid events, invalid events, and blocker events.
- [ ] Non-harness `init` and `event` attempts leave task files byte-for-byte unchanged with both absent and pre-existing `state-events.jsonl`.
- [ ] Non-`harness_state_machine` tasks are rejected with a clear message.
- [ ] Existing `task.py create/start/list/archive` behavior is unchanged.
- [ ] Tests pass with `TMPDIR=/tmp python3 -m pytest tests/trellis/test_state_machine.py`.
- [ ] `git diff --check` passes for the scoped diff.

## Definition of Done

- Required Trellis/spec context is loaded.
- Oracle PRD/PLAN review is run or a documented downgrade is recorded.
- Focused tests pass.
- No new dependency is added.
- No existing active task is initialized or transitioned by the new CLI.
- Stage report records verification and remaining risk.

## Technical Approach

Use a small table-driven Python module with guarded two-file write semantics for `task.json` and `state-events.jsonl`. Keep the CLI as a thin argparse wrapper over the module.

## Decision (ADR-lite)

Context: v2.0.0 proved the repo can be a harness testbed, but changing `task.py`, archive, continue, finish-work, hooks, Oracle, Ponytail, or RTM all at once would risk current task flow.

Decision: v2.1.0 implements only the opt-in state engine and CLI, with tests on temporary task directories.

Consequences: Existing Trellis behavior stays unchanged. Later v2.2+ wrapper work can consume this engine after it is proven.

## Out of Scope

- `task.py` changes.
- `route_continue.py`, `route_finish.py`, or `route_archive.py`.
- Commit gate.
- Built-in archive behavior changes.
- `archive_child.py`, `archive_parent.py`, or `parent_rollup.py`.
- Oracle/Ponytail/RTM executable adapters.
- Migration from `staged_overlay`.
- Hook enforcement.

## Technical Notes

- Source reference: `.local-state.git` snapshot of `06-29-v2-0-full-harness-state-machine/v2-1-0-follow-up-reference.md`.
- Current active repo tasks use `staged_overlay`; no active task uses `harness_state_machine`.
- Oracle PRD/PLAN review initially returned No-Go until the write-failure, re-init, and blocker semantics were specified; this PRD now locks those semantics before implementation.
- Oracle follow-up returned Go, with must-fix tests for log rollback after `task.json` replace failure and byte-for-byte non-harness no-mutation coverage.
