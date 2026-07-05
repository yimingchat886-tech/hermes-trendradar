# Parent: Workflow v2 Mechanization

## Source PRD

- docs/reports/workflow-v2-m2-m6-plan.md

## Stage Scope

- In: M2 task.py tiers/templates/validate/G1/G3/G5-WARN; M3 PR helper/G2/G4; M4 BOARD and Claude/Codex SessionStart.
- Out: M5 remote/CI/staging parent; M6 parallel development enablement; production feature work outside workflow tooling.

## Stage Constraints

- Use v2 new-flow contracts over legacy staged overlay where they conflict.
- New tier cards write `meta.workflow_mode = "harness_state_machine"`.
- G3 must be real git pre-commit with staged diff/tree fingerprint matching, not mtime.
- PLAN confirmation allows implementation only; commit, push, archive, and M6 still need explicit gates.
