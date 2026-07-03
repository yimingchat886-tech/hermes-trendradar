# Git Commit And Push Policy

## PLAN Confirmation

User approval of a PLAN means the agent may implement the current task. It does not authorize:

- `git commit`
- `git push`
- built-in Trellis archive
- skipping verification
- moving to the next child task

## Completion Signal

For staged overlay tasks, report work, verification, missed/extra scope, commit plan, soft archive plan, and `Pushed: no`, then wait for a completion signal.

Completion signal examples:

- `任务完成`
- `验证通过`
- `通过`
- `可以提交`
- `可以归档`
- `可以提交并归档`
- `这个任务 OK`

If the user says `先别提交`, `不要归档`, `等等`, `还要改`, or `先不要动`, do not commit or archive.

For staged child tasks, commit approval also allows soft archive by default. A
signal such as `可以提交`, `提交git`, `验收`, or `验证通过` means commit the
approved child scope and soft archive the child in the same close-out, unless
the user explicitly excludes either action.

Record the signal in the stage report:

```md
## User Completion Signal

- Raw signal:
- Received at:
- Allows commit: yes/no
- Allows soft archive: yes for child unless explicitly limited
- Explicit limits:
- Push allowed: no, unless explicitly requested
```

## Commit

After completion signal, commit only approved current-task files. Exclude unrelated dirty files.

For staged child tasks, record the commit hash and complete the soft archive
metadata immediately after the approved commit. Do not ask for a second archive
approval unless the user limited the original signal.

For staged parent tasks, parent acceptance means commit approved parent evidence
and archive the parent task with the built-in Trellis archive flow.

## Force-Adding Task Evidence

Because `.trellis/tasks/` is ignored, staged overlay may use `git add -f` only for the current task's evidence files:

```bash
git add -f .trellis/tasks/<current-task>/{prd.md,implement.jsonl,check.jsonl,stage-report.md,task.json}
```

Allowed:

- current staged task directory only
- PRD, JSONL context, stage/subphase reports, harness capability report, and task metadata for the current task

Forbidden:

- `git add -f .trellis/tasks/`
- unrelated active or archived task directories
- `.trellis/.runtime/`
- `.trellis/workspace/`
- secrets, logs, caches, generated media, or production data

## Push

Commit approval never implies push. Push requires an explicit command such as:

- `push`
- `推送`
- `git push`
- `可以推到远端`
