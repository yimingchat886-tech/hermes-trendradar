# M2 task.py tiers and gates

## Goal

Implement the v2 local workflow foundation: tiered task templates, task metadata, validate gates, soft archive, and the first machine gates.

## REQ-ID

- WV2-M2-REQ-001: `task.py create` supports v2 `parent` / `child` / `light` tiers, writes `owner`, `touches`, `tier`, and `meta.workflow_mode = "harness_state_machine"`, and stops using the old staged 6-file template for new v2 cards.
- WV2-M2-REQ-002: `task.py validate`, `archive`, and `soft-archive` enforce G1 acceptance and parent/child evidence requirements before mutating task state.
- WV2-M2-REQ-003: G3 pre-commit scope gate and G5 claim guard are mechanized with repo-local scripts/hooks and audited escape paths.

## Verification Commands

- `python3 ./.trellis/scripts/task.py create "t" --slug tmp-parent --tier parent`
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/<tmp-parent>`
- `python3 ./.trellis/scripts/task.py soft-archive .trellis/tasks/<tmp-child> --commit deadbeef`
- `.trellis/scripts/install_hooks.sh`
- `git diff --check`

## In

- M2-0 through M2-6 from `docs/reports/workflow-v2-m2-m6-plan.md`.
- v2 templates under `.trellis/templates/v2/`.
- `task.py` create/validate/archive/soft-archive changes.
- Repo-local hooks and helper scripts for G1/G3/G5-WARN.

## Out

- M3 PR helper, G2, G4.
- M4 BOARD and SessionStart.
- M5 remote/CI/staging and M6 parallel-development BLOCK mode.
