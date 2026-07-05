# Governance: Workflow v2 M6 Parallel Development

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| M6-1 claim-guard BLOCK | Flip claim guard to BLOCK; add `task.py claim` / `release`; audit `--override-claim` | M2 G5-WARN, M5 closeout, jym `开启并行` signal received | codex | TBD | completed | cfc5da9 |
| M6-2 merge collision protocol | Add merge protocol doc and conflict checklist script for card/owner-aware conflict handling | M6-1 ownership enforcement | codex | codex/workflow-v2-m6-2-merge-collision-protocol | completed | 643526a |
| M6-3 worktree parallel trial | Update operator guide worktree commands; run CC+Codex non-overlapping child trial | M6-1, M6-2 | codex + cc | cc/workflow-v2-m6-3-worktree-parallel-trial | completed | adbf0a7 |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| WV2-M6-REQ-001 | M6-1 claim-guard BLOCK | completed | 07-04-workflow-v2-m6-1-claim-guard-block/stage-report.md |
| WV2-M6-REQ-002 | M6-2 merge collision protocol | completed | 07-04-workflow-v2-m6-2-merge-collision-protocol/stage-report.md |
| WV2-M6-REQ-003 | M6-3 worktree parallel trial | completed | 07-04-workflow-v2-m6-3-worktree-parallel-trial/stage-report.md |
| WV2-M6-REQ-004 | Parent closeout | completed | `governance.md` Closeout Review / F1-F7 Coverage |

## External Review

### PRD Review

Accepted for child planning. Parent creation used the accepted source plan; jym then gave the hard gate signal `开启并行`.

### Closeout Review

Completed locally before parent archive. Evidence reviewed:

- M6-1 `stage-report.md`: claim guard BLOCK, claim/release, override audit.
- M6-2 `stage-report.md`: merge protocol, conflict checklist, real conflict exercise.
- M6-3 `stage-report.md`: worktree runbook, CC+Codex isolated trial, negative claim-guard smoke.
- Parent integration commits: `643526a`, `adbf0a7`, `8398979`, `f8eba19`.

### F1-F7 Coverage

| Original problem | Covered by |
|---|---|
| F1 completion/soft-archive state drift | `task.py soft-archive` writes child state and parent governance; M6-1/M6-2/M6-3 are `completed` / `child_archived`. |
| F2 impact and PR gate drift | M3/M5 gates remain upstream dependencies; M6 changes passed staged `detect-changes` with low risk before commit. |
| F3 parent/child pointer and task-card drift | M6 parent children are all completed; archive gate validated child status before parent archive. |
| F4 remote/CI/staging separation | M5 parent is closed; M6 did not bundle push/merge/archive semantics, and push stayed explicit. |
| F5 cross-owner edit safety | M6-1 claim guard is BLOCK; M6-3 negative smoke proved cc cannot edit codex-owned M6-2 path. |
| F6 board/waiting-state drift | `BOARD.md` refreshed after child soft archive; active board now shows M6 parent `[3/3 done]` with no waiting cards. |
| F7 parallel collision handling | M6-2 checklist identified both owners/cards/reports during real conflict; M6-3 worktree trial proved isolated branches/worktrees. |

## Boundary Pass

1. M6 scope is limited to parallel-development enablement; M2-M4 and M5 remain closed parent scopes.
2. The `开启并行` signal authorizes M6 child planning and parent activation; it does not authorize commits, archive, push, or merge.
3. Child tasks must declare owner and touches before implementation, then verify claim-guard negative paths.
4. `--override-claim` and conflict trade-offs must leave auditable evidence in task metadata, state events, or governance.
5. Merge conflict protocol must read both child stage reports before resolving a conflict.
6. Worktree trial must use separate worktrees/branches and prove no cross-child pollution before parent closeout.
7. Final closeout must re-check F1-F7 coverage from `docs/reports/workflow-v2-m2-m6-plan.md`.

Ponytail note: only the three source-plan child cards are created. No extra orchestration layer or speculative child is added.

## Parallel Trial Notes

- M6-3 evidence captured from `/home/jym/workspace/Hermes-stock-m6-3` on branch `cc/workflow-v2-m6-3-worktree-parallel-trial`; sibling M6-2 worktree observed at `/home/jym/workspace/Hermes-stock-m6-2` on `codex/workflow-v2-m6-2-merge-collision-protocol`.
- Negative claim-guard smoke from cc against codex-owned `.trellis/scripts/conflict_checklist.py` exited 2, as expected.
- Final PR / acceptance / archive no-cross-pollution evidence stays pending until M6-2 finishes.
- Parent integration merged M6-2 then M6-3; conflict checklist passed before resolution and no cross-child pollution was found outside declared task/runbook/protocol surfaces.

### Merge trade-off: BOARD.md

- Conflicted children: M6-2 merge collision protocol; M6-3 worktree parallel trial.
- Stage reports read: `07-04-workflow-v2-m6-2-merge-collision-protocol/stage-report.md`; `07-04-workflow-v2-m6-3-worktree-parallel-trial/stage-report.md`.
- Preserved: both child task statuses, owners, and evidence links in governance; generated board is refreshed from merged task metadata.
- Dropped or deferred: branch-local `BOARD.md` ordering/timestamp snapshots from each child branch.
- Reason: `BOARD.md` is generated state; preserving either branch snapshot would lose the other child state.
- Follow-up: rerun `python3 ./.trellis/scripts/board.py --summary --max-lines 10` after merge resolution and before soft archive.
