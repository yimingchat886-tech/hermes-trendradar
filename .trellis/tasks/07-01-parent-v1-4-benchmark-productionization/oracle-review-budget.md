# Oracle Review Budget

| Checkpoint | Required | Trigger | Reason | Expected Cost | Decision |
|---|---:|---|---|---:|---|
| PRD first draft | yes | parent PRD ready for confirmation | v1.4 crosses CLI, config, persistence, external runtimes, and live writes | high | run before parent confirmation |
| SPEC | conditional | if child changes repo-wide workflow, schema, security, or deployment rules | avoid freezing unstable contracts into `.trellis/spec/` | medium | decide per child |
| child PLAN | conditional | high-risk child only | SQLite state, real collection, media download, threshold smoke, and live Feishu writes need external challenge | medium | run for children 3, 4, 6a, 6, 8 by default |
| blocker | yes | unresolved blocker | avoid guessing through production/security ambiguity | high | run if occurs |
| stage-report | conditional | abnormal verification or broad diff | catch missed regressions before soft archive | medium | decide per child |
| closeout code review | yes | parent closeout | final evidence/RTM review | high | run |

## Oracle Availability

- Tool path: Browser Oracle / project Oracle bridge if available in session.
- Model: external Oracle reviewer.
- Available: not checked in this planning-only creation turn.
- If unavailable, downgrade approved by user: no.
