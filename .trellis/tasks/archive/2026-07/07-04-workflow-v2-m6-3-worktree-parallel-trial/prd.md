# M6-3 worktree parallel trial

## Goal

Update the operator worktree workflow and run one CC+Codex non-overlapping child parallel trial.

## REQ-ID

- WV2-M6-REQ-003: Operator worktree guidance includes the concrete M6 parallel command sequence, and one CC+Codex non-overlapping child trial proves isolated worktrees/branches, claim-guard protection, PR/acceptance/archive flow, and no cross-child pollution.

## Verification Commands

- `sed -n '72,120p' docs/runbooks/workflow-v2-operator-guide.md`
- `python3 ./.trellis/scripts/board.py --summary --max-lines 10`
- `git worktree list`
- `git status --short --branch`
- `git diff --check`

## In

- Update `docs/runbooks/workflow-v2-operator-guide.md` section 4 with the minimal worktree command sequence for M6 parallel work.
- Run one CC+Codex parallel trial using non-overlapping `touches`.
- Record the trial evidence and any claim-guard interception in `stage-report.md` / parent governance.

## Out

- Claim/release command implementation and merge conflict checklist implementation; those are M6-1 and M6-2.
- Long-lived worktree management beyond the single M6 trial.
