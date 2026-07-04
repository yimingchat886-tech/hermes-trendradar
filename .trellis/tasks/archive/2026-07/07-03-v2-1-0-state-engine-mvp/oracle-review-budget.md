# Oracle Review Budget

| Checkpoint | Required | Trigger | Reason | Expected Cost | Decision |
|---|---:|---|---|---:|---|
| PRD first draft | yes | T4 harness state machine | catch workflow/active-task risks before code | medium | run |
| SPEC | conditional | reusable workflow rule learned | avoid freezing premature conventions | medium | decide after implementation |
| child PLAN | no | no child decomposition in v2.1 | slice is already minimal | none | skip |
| blocker | yes | only if blocked | avoid guessing through risky ambiguity | high | run if occurs |
| stage-report | conditional | abnormal verification or broad diff | catch missed regressions | medium | decide after verification |
| closeout code review | no | no parent closeout in this task | single v2.1 slice | none | skip |
