# Legacy Staged Delivery Overlay

## Status

`staged_overlay` and `meta.staged_delivery` are legacy compatibility inputs.
Do not create new tasks, templates, docs, or hooks that write them.

New v3 parent/child work uses:

- `meta.workflow_mode = "harness_state_machine"`
- `meta.state_machine.schema_version = 2` in `task.json`
- `state-events.jsonl`
- parent and child evidence files such as `prd.md`, `implement.md`,
  `governance.md`, and `stage-report.md`

## Compatibility Rules

- Readers and validators may tolerate archived legacy tasks that already have
  `staged_overlay` or `meta.staged_delivery`.
- Do not auto-migrate active legacy tasks or downstream production tasks.
- Do not use `.trellis/templates/staged/**`; M1 retired those templates.
- Use `task.py complete-child <child> --commit <hash>` for v3 child closeout and
  `task.py archive <parent>` for parent closeout.
- Unmarked historical state machines remain lifecycle schema v1. The
  `soft-archive` compatibility alias preserves their `child_archived` state; on
  schema v2 it emits a warning and reaches `child_completed`.
- Qualification rejects the deprecated command from maintained workflows,
  scripts, templates, skills, and prompts; only compatibility implementation,
  compatibility tests, and historical evidence may retain it.
- Push still requires explicit user approval.

## Completion Signal

PLAN confirmation approves the planned scope. For PRD-governed work,
implementation begins only after the accepted binding and explicit start gates
in `prd-governance.md` pass. PLAN confirmation does not authorize commit, push,
built-in archive, child completion, or skipping verification.

After child work is verified and reported, stop and wait for a completion,
commit, or archive signal listed in
`.trellis/spec/project/protocol-phrases.md`. If the same message includes a
limit phrase, the limit wins.

For v3 child tasks, commit approval is also child-completion approval by default.
Do not wait for a second archive-specific approval unless the user explicitly
limits the signal.

## Child Completion

For lifecycle schema v2 child tasks, child completion means:

1. Commit only the approved child task files after completion or commit approval.
2. Record the commit hash in `stage-report.md`.
3. Run `task.py complete-child <child> --commit <hash>`.
4. Keep the child task directory in place so the parent can aggregate evidence.

RTM ownership used by child completion must be exact:

- A single-owner `Child` cell may use the exact task title or directory name.
- A shared `Child` cell lists every exact child task directory name joined by
  ` + `; aggregate labels such as `all children` are not ownership evidence.
- Shared rows append each child stage-report path once and remain nonterminal
  until every named Child Index row is `completed`, `done`, or `cancelled`.
  Existing `removed` and `deferred` dispositions are preserved.
- An unresolved, duplicated, or ambiguous owner fails before governance or
  child lifecycle state is written.

Do not call built-in `task.py archive` for child completion.

## Parent Acceptance And Archive

For v3 parent tasks, parent acceptance means the full parent scope is ready to
close after its required child evidence has been aggregated.

When the user accepts the parent task with a completion, commit, or archive
phrase from `.trellis/spec/project/protocol-phrases.md`, do both unless the
user explicitly limits the signal:

1. Commit only approved parent evidence and task metadata.
2. Archive the parent task with the built-in Trellis archive flow.

Parent archive is not child completion. Push still requires explicit user
approval.

Parent archive preserves both relationship endpoints and physically archives
the complete terminal descendant tree in children-first order, with the parent
last and every member in one month. A linked child cannot be hard-archived
directly. Schema v2 `child_completed`, v1 `child_archived`, and structured
`child_cancelled` are accepted terminal evidence. An archived terminal parent
may uniquely own one historical terminal child whose `parent` field is null;
task bytes are never rewritten. Ambiguous ownership, duplicate locations,
cycles, missing tasks, cross-month members, and nonterminal descendants fail
before mutation.

Run `task.py archive-orphans --check` before an explicit downstream orphan
sweep. It reports eligible families, exact moves, and blockers without
creating a journal, moving files, refreshing BOARD, or staging Git.
`task.py archive-orphans` reuses the same validated plan and archive
transaction. Harness installation preserves `.trellis/tasks` and never
auto-migrates historical task records.

The move, generator-only BOARD refresh, exact Git staging, and optional local
commit use one archive journal with task-tree, BOARD, HEAD, and index preimages.
Failure rolls back; incomplete rollback becomes `recovery_required`, blocks new
archives, and must use `task.py archive-recover <transaction-id>`. Standalone
light-task archive behavior is unchanged.
