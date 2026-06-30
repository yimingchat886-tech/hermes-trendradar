# Oracle Review Policy

## Purpose

Oracle/GPT-5.5 Pro is an external review path for expensive or high-risk judgment. It is not run for every staged child task.

## Required Checkpoints

| Checkpoint | Required when | Purpose |
|---|---|---|
| PRD first complete draft | T3/T4 parent work | intent, conflict, missing requirement, unverifiable scope |
| High-risk trial PLAN | high-risk T2/T3/T4 child PLAN | architecture, data, security, migration, cross-module risk |
| Blocker | any unresolved blocker | avoid guessing through risky ambiguity |
| Parent closeout code/report review | T3/T4 parent closeout | final code, RTM, report, verification evidence |

## Usually Skipped

- ordinary child PLAN
- low-risk implementation
- normal stage-report with strong verification
- T0/T1 work

## Oracle Review Budget

Parent PRD first complete draft must create `oracle-review-budget.md` with:

```md
# Oracle Review Budget

| Checkpoint | Required | Trigger | Reason | Expected Cost | Decision |
|---|---:|---|---|---:|---|
| PRD first draft | yes | always | intent/risk review | high | run |
| SPEC | conditional | architecture/data/security risk | ... | medium | skip/run |
| child PLAN | conditional | high-risk only | ... | medium | skip/run |
| blocker | yes | when occurs | external challenge | high | run if occurs |
| stage-report | conditional | abnormal only | ... | medium | skip/run |
| closeout code review | yes | parent closeout | final evidence review | high | run |
```

## Unavailable Oracle

| Task | Behavior |
|---|---|
| T0/T1 | skip and report reason |
| ordinary T2 | local checklist fallback |
| high-risk T2 | block or ask user to allow downgrade |
| T3/T4 | block unless user explicitly allows downgrade |

## Manual Mode / Downgrade Checklist

When Oracle is unavailable or browser/API mode is too slow, record:

- Oracle mode attempted: API / browser / render-copy / manual / none
- Failure or skip reason:
- Task level: T0 / T1 / T2 / T3 / T4
- Downgrade allowed by policy: yes/no
- User approved downgrade: yes/no
- Local reviewer:
- Local checklist used:
- Remaining risk:

Manual mode is acceptable only when the policy row above allows downgrade, or the user explicitly accepts the downgrade for the current checkpoint.
