# Trellis Guides

## Project Development Standard

- Read `project-development.md` before non-trivial development, process, dependency, verification, or spec work.
- It defines the Codex + Trellis + Ponytail operating standard for this repository.
- It also defines the Ponytail review gate used by the Trellis check phase.
- Use it alongside `.trellis/workflow.md`; the workflow remains authoritative for phase order and active-task handling.

## Parent/Child Harness Work

- Read `../project/index.md` before T3/T4 work, parent/child delivery, RTM-tracked work, Oracle review budgeting, soft archive, or harness/tooling changes.
- New v3 parent/child work uses `task.json.meta.workflow_mode = "harness_state_machine"` plus evidence files.
- `staged_overlay` and `meta.staged_delivery` are legacy read-compatibility inputs only.
