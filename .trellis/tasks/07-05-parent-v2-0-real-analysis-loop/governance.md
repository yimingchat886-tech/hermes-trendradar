# Governance: Parent v2.0: real analysis loop

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| Child 0: v2.0 release PRD and PRD map | `docs/PRD/releases/PRD_v2.0.md` + master index update | Parent confirmation | TBD | TBD | planned | TBD |
| Child 1: Hermes skill invocation gate | no-secret skill/config template + invocation proof | Child 0 | TBD | TBD | planned | TBD |
| Child 2: real analysis handoff consumer | Hermes-owned result refs from handoff package | Child 1 | TBD | TBD | planned | TBD |
| Child 3: internal digest message path | internal-group digest payload/send evidence | Child 2 | TBD | TBD | planned | TBD |
| Child 4: human feedback intake | adopt/reject feedback persisted to SQLite | Child 3 | TBD | TBD | planned | TBD |
| Child 5: M3 write-table gate | decision/evidence only unless M3 conditions are met | Child 4 + 2-week message flow | TBD | TBD | gated | TBD |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| P20-REQ-000 | Child 0 | planned | `prd.md` requires release PRD before code children. |
| P20-REQ-010 | Child 1 | planned | `prd.md` keeps Hermes skill path and no-MCP/no-dependency constraint. |
| P20-REQ-020 | Child 1 | planned | M0 gate requires one invocation and message/fallback evidence. |
| P20-REQ-030 | Child 2 | planned | M1 gate requires real Hermes result refs, not mock decomposition. |
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

Parent setup only created Trellis planning artifacts. It did not modify runtime code, docs source PRDs, profiles, tests, or configs.

Scoped out for now:

- v2.1 hotspot/Twitter work;
- MCP server;
- live Bitable writes;
- RAG/vector storage;
- scheduler framework before the one-shot Hermes invocation works.
