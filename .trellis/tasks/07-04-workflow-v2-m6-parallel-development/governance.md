# Governance: Workflow v2 M6 Parallel Development

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| M6-1 claim-guard BLOCK | Flip claim guard to BLOCK; add `task.py claim` / `release`; audit `--override-claim` | M2 G5-WARN, M5 closeout, jym `开启并行` signal received | codex | TBD | completed | cfc5da9 |
| M6-2 merge collision protocol | Add merge protocol doc and conflict checklist script for card/owner-aware conflict handling | M6-1 ownership enforcement | codex | TBD | planned | TBD |
| M6-3 worktree parallel trial | Update operator guide worktree commands; run CC+Codex non-overlapping child trial | M6-1, M6-2 | codex + cc | cc/workflow-v2-m6-3-worktree-parallel-trial | partial | TBD |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| WV2-M6-REQ-001 | M6-1 claim-guard BLOCK | completed | 07-04-workflow-v2-m6-1-claim-guard-block/stage-report.md |
| WV2-M6-REQ-002 | M6-2 merge collision protocol | planned | TBD |
| WV2-M6-REQ-003 | M6-3 worktree parallel trial | partial | docs/runbooks/workflow-v2-operator-guide.md section 4; 07-04-workflow-v2-m6-3-worktree-parallel-trial/stage-report.md |
| WV2-M6-REQ-004 | Parent closeout | planned | v2 F1-F7 final coverage review in parent closeout |

## External Review

### PRD Review

Accepted for child planning. Parent creation used the accepted source plan; jym then gave the hard gate signal `开启并行`.

### Closeout Review

TBD

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
