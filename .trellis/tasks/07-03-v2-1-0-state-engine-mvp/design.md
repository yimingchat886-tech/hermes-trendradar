# v2.1.0 State Engine Design

## Shape

- `state_machine.py` owns validation, transitions, persistence, and event-log writes.
- `state_cli.py` only parses CLI arguments and prints human-readable or JSON output.
- Tests create temporary task directories and never mutate real `.trellis/tasks/`.

## State Storage

`task.json.meta.state_machine`:

```json
{
  "kind": "child",
  "current_state": "child_plan_draft",
  "previous_state": null,
  "last_event": "init",
  "blocked_from_state": null,
  "updated_at": "2026-07-03T00:00:00Z"
}
```

`state-events.jsonl` stores one JSON object per accepted event. First-time `init` is an accepted event and writes one line. Same-kind re-init is a no-op with no write. Opposite-kind re-init is rejected with no write.

## Transition Rules

Parent:

- init -> `parent_prd_draft`
- `prd_drafted` -> `parent_waiting_completion_signal`
- `parent_completion_signal_received` -> `parent_commit_ready`
- `parent_commit_created` -> `parent_archive_ready`
- `parent_archive_completed` -> `parent_archived`

Child:

- init -> `child_plan_draft`
- `plan_drafted` -> `child_waiting_completion_signal`
- `completion_signal_received` -> `child_commit_ready`
- `commit_created` -> `child_archive_ready`
- `child_archive_completed` -> `child_archived`

Blockers:

- `blocker_opened` moves any non-archived state to the matching blocked state and records the blocked-from state.
- `blocker_resolved` returns to the blocked-from state.
- `blocker_opened` is invalid while already blocked or archived.
- `blocker_resolved` is invalid when not blocked or when `blocked_from_state` is missing/corrupt.

## Write Safety

- Validate mode and transition before writing.
- Build the new `task.json` and full new `state-events.jsonl` bytes before touching disk.
- Write both temp files first.
- Replace `state-events.jsonl`, then replace `task.json`.
- If `state-events.jsonl` replacement fails, `task.json` is still unchanged.
- If `task.json` replacement fails after the log replacement, restore the previous log bytes before returning failure.
- Invalid events and simulated event-log write failures must leave both files unchanged.
- MVP assumes single-writer CLI execution; concurrent writers are out of scope until v2.2+ wrappers define locking needs.

## Ponytail Notes

No dependency, schema framework, router layer, plugin hook, or archive wrapper in v2.1.0.
