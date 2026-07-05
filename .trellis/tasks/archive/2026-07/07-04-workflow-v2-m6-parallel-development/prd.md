# Parent: Workflow v2 M6 Parallel Development

## PM Intake

- Original request: create the M6 parent task from `docs/reports/workflow-v2-m2-m6-plan.md`.
- Real goal: open the planning container for Workflow v2 parallel development without starting child implementation yet.
- Risk level: T3, because M6 changes concurrent ownership, merge conflict handling, and worktree workflow.
- Staged overlay needed: yes, via v2 `harness_state_machine` parent/child cards.
- User confirmation point: satisfied by jym saying `开启并行`.

## Gate Signal

- Raw signal: `开启并行`
- Meaning: M6 child planning may be created and the parent may enter execution.
- Limits: this does not authorize commit, archive, push, or skipping per-child verification.

## Source PRD

- `docs/reports/workflow-v2-m2-m6-plan.md`
- `docs/runbooks/workflow-v2-operator-guide.md`

## Stage Scope

- In: M6 only: G5 claim-guard BLOCK mode, `task.py claim` / `release`, audited `--override-claim`, merge collision protocol, conflict checklist, worktree parallel runbook updates, and one CC+Codex parallel trial.
- Out: M2-M4 mechanization, M5 remote/CI/staging, unrelated product feature work, and any child implementation before jym explicitly opens parallel development.

## Stage Constraints

- M6 starts after the M2-M4 parent and M5 parent are closed out, and after jym explicitly says `开启并行`; all three are satisfied for child planning.
- Claim ownership changes must be auditable through task metadata and state events.
- Conflict resolution must preserve both sides' intent first; unresolved trade-offs are recorded in parent governance instead of silently choosing a side.
- Worktree parallelism must not mix unrelated dirty files, private runtime state, secrets, or `.trellis/.runtime` markers into commits.
- Child tasks need at least one reproducible verification command and one negative-path check for each BLOCK gate.
