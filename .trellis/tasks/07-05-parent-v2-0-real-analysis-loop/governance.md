# Governance: Parent v2.0: real analysis loop

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| 07-05-child-0-v2-0-release-prd | `docs/PRD/releases/PRD_v2.0.md` + master index update | Parent confirmation | codex | current | completed | 8fcdce0 |
| 07-05-child-1-v2-0-hermes-skill-invocation | no-secret skill/config template + invocation proof | Child 0 | codex | current | completed | 840a068 |
| 07-05-child-2-v2-0-real-analysis-handoff-consumer | Hermes-owned result refs from handoff package | Child 1 | codex | current | completed | 6b1b67d |
| 07-05-child-3-v2-0-internal-digest-message | internal-group digest payload/send evidence | Child 2 | codex | current | completed | 3209700 |
| 07-05-child-4-v2-0-human-feedback-intake | adopt/reject feedback persisted to SQLite | Child 3 | codex | current | completed | 5fdab42 |
| 07-05-child-5-v2-0-m3-write-table-gate | decision/evidence only unless M3 conditions are met | Child 4 + 2-week message flow | codex | current | completed | 900f807 |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| P20-REQ-000 | Child 0: v2.0 release PRD and PRD map | completed | 07-05-child-0-v2-0-release-prd/stage-report.md |
| P20-REQ-010 | Child 1: Hermes skill invocation gate | completed | 07-05-child-1-v2-0-hermes-skill-invocation/stage-report.md |
| P20-REQ-020 | Child 1: Hermes skill invocation gate | partial | 07-05-child-1-v2-0-hermes-skill-invocation/stage-report.md (CLI proof done; live message/fallback remains external blocker) |
| P20-REQ-030 | Child 2: real analysis handoff consumer | completed | 07-05-child-2-v2-0-real-analysis-handoff-consumer/stage-report.md |
| P20-REQ-040 | Child 3: internal digest message path | partial | 07-05-child-3-v2-0-internal-digest-message/stage-report.md (`message_channel_not_configured`) |
| P20-REQ-050 | Child 4: human feedback intake | completed | 07-05-child-4-v2-0-human-feedback-intake/stage-report.md |
| P20-REQ-060 | Child 5: M3 write-table gate | completed | 07-05-child-5-v2-0-m3-write-table-gate/stage-report.md |

## External Review

### PRD Review

Required before confirming the parent. Reviewer must challenge:

- whether `PRD_v2.0.md` repeats only release-level scope and not child execution state;
- whether M0 truly proves Hermes can call the repo and send a message;
- whether M1 avoids repo-side fake LLM analysis;
- whether M3 remains conditional and does not smuggle in live Bitable writes.

### Closeout Review

Completed locally on 2026-07-06 after reviewing parent PRD, child `task.json`
state, and all six child `stage-report.md` files.

Result: partial pass for repo-owned v2.0 scope. All six child tasks are
completed and soft-archived. Two RTM rows remain partial because live
Hermes/Feishu message delivery is not configured in this repo, and M3 correctly
closed as no-go instead of starting live Bitable writes.

| Requirement | Status | Missing / blocked | Extra scope | Evidence |
|---|---|---|---|---|
| P20-REQ-000 | completed | none | none | Child 0 created `docs/PRD/releases/PRD_v2.0.md` and linked `PRD_MASTER.md`; commit `8fcdce0`. |
| P20-REQ-010 | completed | none | none | Child 1 recorded the no-secret Hermes skill invocation contract and repo CLI proof; commit `840a068`. |
| P20-REQ-020 | partial | Live Hermes cron/manual message delivery or a concrete systemd fallback run is not proven here. | none | Child 1 proves repo-side CLI invocation only; child 3 later records delivery blocked. |
| P20-REQ-030 | completed | none | none | Child 2 persists Hermes-owned analysis refs and rejects invalid result refs; commit `6b1b67d`. |
| P20-REQ-040 | partial | Internal-group delivery is blocked by `message_channel_not_configured`; this repo only emits the traceable digest payload. | none | Child 3 digest payload and blocker evidence; commit `3209700`. |
| P20-REQ-050 | completed | none | none | Child 4 adds immutable adopt/reject feedback audit rows; commit `5fdab42`. |
| P20-REQ-060 | completed | none | none | Child 5 records M3 no-go: no 2-week message flow, no auth trust-root decision, and no table 2/3 decision; commit `900f807`. |

Conclusion: parent can be closed only as a narrowed repo-owned closeout. Do not
claim live internal message delivery, two-week message-flow evidence, or Bitable
write readiness from this parent.

## Boundary Pass

Final boundary after child rollup:

1. v2.0 release PRD and repo-side CLI contracts are in scope and completed.
2. Repo-owned real-analysis refs, digest payload, and feedback audit paths are in scope and completed.
3. Live Hermes cron, live Feishu internal-message delivery, and systemd fallback operation are not proven in this repo.
4. M3 Bitable writes remain blocked until there is at least two weeks of message-flow evidence plus explicit auth/table decisions.
5. No v2.1 hotspot/Twitter work, MCP server, RAG/vector storage, scheduler framework, or live Bitable write child was added.

Pushed: no.
