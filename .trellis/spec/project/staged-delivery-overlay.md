# Staged Delivery Overlay

## Purpose

Use staged delivery overlay for complex work that needs parent/child governance, explicit PLAN confirmation, completion signals, RTM evidence, or external review.

This is an opt-in overlay. It does not replace the default Trellis lifecycle or create new workflow-state statuses.

## Modes

| Mode | Use for | Behavior |
|---|---|---|
| `default_trellis` | T0/T1 and ordinary low-risk T2 work | Existing Plan / Execute / Finish flow. |
| `staged_overlay` | T3/T4 and high-risk T2 work | Parent/child artifacts, completion signal before commit, soft archive for child tasks. |
| `harness_state_machine` | Future v2.0 work | Not used in v1.4.1. |

## Required Metadata

Staged overlay tasks record:

```json
{
  "meta": {
    "workflow_mode": "staged_overlay",
    "staged_delivery": {
      "phase": "planning",
      "plan_confirmed": false,
      "implementation_task_submitted": false,
      "completion_signal_received": false,
      "commit_allowed": false,
      "soft_archive_completed": false,
      "trellis_archive_completed": false
    }
  }
}
```

Use these fields as evidence, not as a new state machine. The authoritative built-in status remains `planning`, `in_progress`, or `completed`.

## Parent Responsibilities

Parent tasks represent a larger subphase and own:

- `prd.md`
- `conflict-review.md`
- `spec.md`
- `child-task-index.md`
- `oracle-review-budget.md`
- `rtm-delta.md`
- `subphase-report.md`

Parent tasks should not mix large implementation work directly into the parent. Use child tasks for code changes.

## Child Responsibilities

Child tasks represent one small, independently verifiable slice and own:

- `prd.md`
- `implement.md`
- `implement.jsonl`
- `check.jsonl`
- `stage-report.md`

Child tasks must not implement future child scope or unrelated cleanup.

## Completion Signal

PLAN confirmation means implementation may begin. It does not authorize commit, push, built-in archive, or skipping verification.

After child work is verified and reported, stop and wait for a completion signal such as:

- `任务完成`
- `验证通过`
- `通过`
- `可以提交`
- `可以提交并归档`
- `这个任务 OK`

If the same user message includes a limit such as `先别提交`, `不要归档`, or `还要改`, the limit wins.

Record the signal before committing:

```md
## User Completion Signal

- Raw signal:
- Received at:
- Allows commit: yes/no
- Allows soft archive: yes/no
- Explicit limits:
- Push allowed: no, unless the same message explicitly says push
```

## Soft Archive

For staged child tasks, soft archive means:

1. Commit only the approved child task files after completion signal.
2. Record commit hash in `stage-report.md`.
3. Record commit hash and `soft_archive_completed = true` in `task.json.meta.staged_delivery`.
4. Keep the child task directory in place so the parent can aggregate evidence.

Do not call built-in `task.py archive` for a child soft archive.

After soft archive, the child task remains evidence only. It is no longer the active implementation target, even if the session's current task still points at it. Any further implementation needs a new child task or an explicit user decision to reopen the soft-archived child.
