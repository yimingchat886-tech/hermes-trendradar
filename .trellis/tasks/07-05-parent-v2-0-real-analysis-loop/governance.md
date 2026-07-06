# Governance: Parent v2.0: real analysis loop

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| 07-05-child-0-v2-0-release-prd | `docs/PRD/releases/PRD_v2.0.md` + master index update | Parent confirmation | codex | current | planning | TBD |
| 07-05-child-1-v2-0-hermes-skill-invocation | no-secret skill/config template + invocation proof | Child 0 | codex | current | completed | 840a068 |
| 07-05-child-2-v2-0-real-analysis-handoff-consumer | Hermes-owned result refs from handoff package | Child 1 | codex | current | completed | 6b1b67d |
| 07-05-child-3-v2-0-internal-digest-message | internal-group digest payload/send evidence | Child 2 | codex | current | planning | TBD |
| 07-05-child-4-v2-0-human-feedback-intake | adopt/reject feedback persisted to SQLite | Child 3 | codex | current | planning | TBD |
| 07-05-child-5-v2-0-m3-write-table-gate | decision/evidence only unless M3 conditions are met | Child 4 + 2-week message flow | codex | current | gated | TBD |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| P20-REQ-000 | Child 0 | planned | `prd.md` requires release PRD before code children. |
| P20-REQ-010 | Child 1: Hermes skill invocation gate | completed | 07-05-child-1-v2-0-hermes-skill-invocation/stage-report.md |
| P20-REQ-020 | Child 1: Hermes skill invocation gate | completed | 07-05-child-1-v2-0-hermes-skill-invocation/stage-report.md |
| P20-REQ-030 | Child 2: real analysis handoff consumer | completed | 07-05-child-2-v2-0-real-analysis-handoff-consumer/stage-report.md |
| P20-REQ-040 | Child 3 | planned | Daily digest message path must include trace IDs. |
| P20-REQ-050 | Child 4 | planned | Feedback intake must be idempotent and auditable in SQLite. |
| P20-REQ-060 | Child 5 | gated | M3 requires 2-week message-flow evidence plus explicit decisions. |

## External Review

### PRD Review

Required before confirming the parent. Reviewer must challenge:

- whether `PRD_v2.0.md` repeats only release-level scope and not child execution state;
- whether M0 truly proves Hermes can call the repo and send a message;
- whether M1 avoids repo-side fake LLM analysis;
- whether M3 remains conditional and does not smuggle in live Bitable writes.

### Closeout Review

TBD

## Boundary Pass

Parent setup and child creation only created Trellis planning artifacts. It did not modify runtime code, docs source PRDs, profiles, tests, or configs.

Scoped out for now:

- v2.1 hotspot/Twitter work;
- MCP server;
- live Bitable writes;
- RAG/vector storage;
- scheduler framework before the one-shot Hermes invocation works.
