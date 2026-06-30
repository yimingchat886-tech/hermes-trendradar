# v2.0 Full Harness State Machine Reality Check

## PM Intake

### Original Request

Execute v2.0.0 for the harness refactor, report unfinished parts, and organize them as v2.1.0 follow-up implementation reference.

### Real Goal

Use this new standalone repo as a safe Trellis harness testbed before changing core workflow behavior. The first slice should inspect real harness capability and produce implementation-ready references without prematurely wiring a state machine into `continue`, `finish-work`, or `archive`.

### Risk Level

T4: harness, state machine, archive/finish, and tool orchestration.

### Staged Overlay Needed

Yes. V2.0 state-machine mode does not exist yet, so this task uses `staged_overlay` as the governance container and records future `harness_state_machine` work as requirements.

## Goal

Produce a v2.0.0 Harness Reality Check for the copied Trellis harness in this repo, with a concrete capability report, gap analysis, and v2.1.0 landing reference.

## Requirements

- Create a Trellis task to hold the v2.0.0 evidence.
- Inspect the current harness in `.trellis/`, `.agents/`, `.codex/`, and project specs.
- Report current capability for `task.py`, workflow docs, config, archive behavior, finish-work behavior, hooks, parent/child helpers, tests, RTM, Oracle, and Ponytail.
- Identify conflicts between the V2.0 execution plan and the actual repo.
- List unfinished work and downgrade points.
- Produce a v2.1.0 implementation reference focused on the next smallest safe landing slice.
- Do not implement the state machine, route changes, archive changes, Oracle adapter, RTM sync, or commit gate in this task.

## Acceptance Criteria

- [x] Task exists under `.trellis/tasks/`.
- [x] `harness-capability-report.md` documents observed harness capabilities.
- [x] `v2-gap-analysis.md` documents conflicts, downgrade points, and unfinished areas.
- [x] `v2-1-0-follow-up-reference.md` defines the recommended next landing slice.
- [x] Core harness behavior is not modified.
- [x] Narrow verification commands are recorded.

## Definition of Done

- Reality-check artifacts are written in the task directory.
- No source code, scripts, config, archive behavior, or route behavior is changed.
- Verification confirms task context files still validate.
- Remaining work is explicit enough to start v2.1.0 without re-reading the whole v2.0 draft.

## Technical Approach

Read the external V2.0 execution plan from `/mnt/c/Users/Jym/Downloads/tele/v2_0_full_harness_state_machine_execution_plan.md`, inspect local Trellis harness files, run read-only CLI checks, then write task-local reports.

## Decision (ADR-lite)

Context: The V2.0 plan describes a full executable state machine, but this repo currently only has default Trellis plus a staged overlay spec.

Decision: Treat v2.0.0 as read/report-only harness capability discovery. Use `staged_overlay` metadata for this task. Defer executable state-machine code to v2.1.0.

Consequences: The default Trellis path remains untouched. V2.1.0 starts from real repo constraints instead of the aspirational plan.

## Out of Scope

- Creating `.trellis/scripts/state_machine.py` or `.trellis/scripts/state_cli.py`.
- Adding route, commit, finish-work, or archive gates.
- Adding Oracle, Ponytail, or RTM executable adapters.
- Creating child implementation tasks.
- Committing, pushing, archiving, or renaming existing repo files.

## Technical Notes

- Current repo mode: single-repo Trellis, no packages configured.
- Current task mode: `staged_overlay`, because `harness_state_machine` is future work.
- `.trellis/tasks/` is ignored by default, so later commits must force-add only current task evidence if the user asks to commit.
