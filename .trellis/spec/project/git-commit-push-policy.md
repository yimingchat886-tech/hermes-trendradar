# Git Commit And Push Policy

## PLAN Confirmation

User approval of a PLAN approves its scope. For PRD-governed work,
implementation still requires the accepted binding and explicit start gates in
`prd-governance.md`. PLAN approval does not authorize:

- `git commit`
- `git push`
- built-in Trellis archive
- skipping verification
- moving to the next child task

## Completion Signal

For v3 parent/child tasks, report work, verification, missed/extra scope, commit plan, child completion plan, and `Pushed: no`, then wait for a completion signal.

Use `.trellis/spec/project/protocol-phrases.md` for the canonical completion,
commit, archive, limit, and push phrase table. Limit phrases override positive
phrases in the same user message.

For v3 child tasks, commit approval also allows child completion by default. A
commit-approval phrase means commit the approved child scope and complete
the child in the same close-out, unless the user explicitly excludes either
action.

Record the signal in the stage report:

```md
## User Completion Signal

- Raw signal:
- Received at:
- Allows commit: yes/no
- Allows child completion: yes for child unless explicitly limited
- Explicit limits:
- Push allowed: no, unless explicitly requested
```

## Commit

After completion signal, commit only approved current-task files. Exclude unrelated dirty files.

For v3 child tasks, record the commit hash and complete the child lifecycle
metadata immediately after the approved commit. Do not ask for a second archive
approval unless the user limited the original signal.

For v3 parent tasks, parent acceptance means commit approved parent evidence
and archive the parent task with the built-in Trellis archive flow.

## Force-Adding Task Evidence

Because `.trellis/tasks/` is ignored, v3 parent/child work may use `git add -f` only for the current task's evidence files:

```bash
git add -f .trellis/tasks/<current-task>/{prd.md,implement.md,implement.jsonl,check.jsonl,stage-report.md,state-events.jsonl,task.json}
```

Allowed:

- current task directory only
- PRD, implementation plan, JSONL context, stage/subphase reports, harness capability report, state events, and task metadata for the current task

Forbidden:

- `git add -f .trellis/tasks/`
- unrelated active or archived task directories
- `.trellis/.runtime/`
- `.trellis/workspace/`
- secrets, logs, caches, generated media, or production data

## Push

Commit approval never implies push. Push requires an explicit push-approval
phrase from `.trellis/spec/project/protocol-phrases.md`.
