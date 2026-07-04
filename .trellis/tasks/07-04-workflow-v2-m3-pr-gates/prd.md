# M3 PR helper and impact push gates

## Goal

Mechanize local PR readiness and the impact/push gates after M2 establishes task metadata and hook installation.

## REQ-ID

- WV2-M3-REQ-001: `trellis_pr.sh` runs the local PR seven-point check, G2 blocks unreviewed source edits, and G4 pre-push/debugging guardrails are installed.

## Verification Commands

- `.trellis/scripts/trellis_pr.sh .trellis/tasks/<real-child>`
- `.trellis/scripts/install_hooks.sh`
- `git diff --check`

## In

- M3-1 through M3-3 from `docs/reports/workflow-v2-m2-m6-plan.md`.
- `trellis_pr.sh`, G2 impact gate, G4 pre-push hook, and debugging skill update.

## Out

- M2 template/task.py foundation.
- M4 BOARD and SessionStart.
- M5 real GitHub PR/CI/staging.
