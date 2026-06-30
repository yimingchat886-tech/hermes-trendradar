# v2.1.0 Follow-Up Reference

## Recommended Slice

Name: v2.1.0 State Engine MVP

Goal: Add the smallest executable V2 harness state engine that can be tested without changing default Trellis behavior.

## Product Requirement

As a Trellis harness maintainer, I need task-local state-machine events recorded in `task.json.meta.state_machine` and `state-events.jsonl`, so that later route/commit/archive gates can depend on durable state instead of prose instructions.

## Scope

Implement only:

- `.trellis/scripts/state_machine.py`
- `.trellis/scripts/state_cli.py`
- `tests/trellis/test_state_machine.py`

Use only the Python standard library plus existing system `pytest`.

## MVP Requirements

- Detect `task.json.meta.workflow_mode`.
- Treat missing `workflow_mode` as `default_trellis`.
- Only operate on tasks with `workflow_mode = "harness_state_machine"`.
- Store V2 state under `task.json.meta.state_machine`.
- Append every accepted event to `state-events.jsonl`.
- Reject invalid transitions with a non-zero exit and no partial write.
- Preserve unknown `task.json` fields.
- Keep `task.json.status` on the existing compatibility lifecycle.
- Provide a CLI:

```bash
python3 .trellis/scripts/state_cli.py init <task-dir> --kind parent|child
python3 .trellis/scripts/state_cli.py event <task-dir> <event> [--by user|agent|system] [--note "..."]
python3 .trellis/scripts/state_cli.py status <task-dir>
```

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

Start smaller than the V2.0 draft. Add intermediate review states only when a later router or gate needs them.

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
- [ ] Non-`harness_state_machine` tasks are ignored or rejected with a clear message.
- [ ] Existing `task.py create/start/list/archive` behavior is unchanged.
- [ ] Tests pass with:

```bash
TMPDIR=/tmp python3 -m pytest tests/trellis/test_state_machine.py
```

## Test Plan

Create temp task directories in pytest; do not mutate real `.trellis/tasks/` fixtures.

Tests:

- init parent state
- init child state
- valid child transition
- invalid child jump
- blocker open and resolve
- default_trellis task rejection
- append-only event log
- no partial write on invalid event

## Out Of Scope For v2.1.0

- `route_continue.py`
- `route_finish.py`
- `route_archive.py`
- `commit_gate.py`
- `archive_child.py`
- `archive_parent.py`
- `parent_rollup.py`
- Oracle adapter
- Ponytail gate
- RTM sync
- migration from `staged_overlay`
- hook enforcement

## Later Roadmap

| Version | Slice | Depends On |
|---|---|---|
| v2.2.0 | Workflow mode wrappers for continue/finish/archive | v2.1.0 state engine |
| v2.3.0 | Completion signal + commit gate | v2.2.0 wrappers |
| v2.4.0 | True child archive + parent evidence | v2.3.0 commit gate |
| v2.5.0 | Parent closeout + parent archive | v2.4.0 child archive |
| v2.6.0 | Oracle adapter | stable blocker state |
| v2.7.0 | Ponytail gate | stable blocker state |
| v2.8.0 | RTM sync + evidence validation | child/parent evidence files |
| v2.9.0 | Migration + integration tests | all core gates |

## Ponytail Constraint

Do not add new dependencies for v2.1.0. A table-driven Python module plus pytest checks is enough.

If JSON schema validation becomes necessary later, record why stdlib validation is insufficient before adding a package.
