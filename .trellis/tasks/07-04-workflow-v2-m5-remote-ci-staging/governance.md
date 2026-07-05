# Governance: Workflow v2 M5 Remote CI Staging

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| M5-1 remote and branch protection | Configure `origin`, push `main`, and protect `main` after remote decisions | branch protection enabled after repo became public | codex | codex/workflow-v2-m4-board | ready_for_acceptance | 2552bba |
| M5-2 CI | Add lean GitHub Actions lint/typecheck/test workflow and required checks | M5-1 | codex | TBD | planned | TBD |
| M5-3 staging deploy and G6 | Add local-Docker staging deployment path gated on green CI | M5-1, M5-2 | codex | TBD | planned | TBD |
| M5-4 acceptance automation | Add Playwright smoke checks and CI/staging evidence reporting | M5-2, M5-3 | codex | TBD | planned | TBD |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| WV2-M5-REQ-001 | M5-1 remote and branch protection | ready_for_acceptance | 07-04-workflow-v2-m5-1-remote-branch-protection/stage-report.md |
| WV2-M5-REQ-002 | M5-2 CI | planned | TBD |
| WV2-M5-REQ-003 | M5-3 staging deploy and G6 | planned | TBD |
| WV2-M5-REQ-004 | M5-4 acceptance automation | planned | TBD |

## External Review

### PRD Review

Pending. The source PLAN explicitly blocks M5 implementation until jym decides the GitHub repository, visibility, and staging host. This parent only records the execution boundary and child candidates.

### Closeout Review

TBD

## Boundary Pass

1. `main` remains the base branch for this parent.
2. M5 is independent from the completed M2-M4 parent; M6 remains blocked until this parent and M2-M4 are closed out.
3. No remote, push, GitHub Actions, branch protection, secrets, or staging deploy changes are authorized by parent creation alone.
4. Child tasks must be created only after the M5 front-door decisions are explicit.
5. Each child needs at least one reproducible verification command and one negative-path check where a gate is expected to block.
6. Push remains separate from local commit unless jym explicitly approves it.
