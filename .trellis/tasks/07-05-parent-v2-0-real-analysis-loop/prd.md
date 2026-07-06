# Parent v2.0: Real Analysis Loop

## Source PRD

- Master: `docs/PRD/PRD_MASTER.md`
- Last closed release: `docs/PRD/releases/PRD_v1.4_split_index.md`
- v1.4 closeout evidence: `.trellis/tasks/archive/2026-07/07-01-parent-v1-4-benchmark-productionization/subphase-report.md`
- Missing source that this parent must create before implementation: `docs/PRD/releases/PRD_v2.0.md`

## PM Intake

- Original request: "根据已设置流程，以审查者身份挑战该这个PRD-master，后再设置parent task"
- Real goal: turn `PRD_MASTER.md` into the next executable parent task only after challenging its hidden assumptions and protecting the v2.0 boundary.
- Optimized requirement: create a v2.0 parent task that first drafts the missing release PRD and then delivers the smallest real analysis loop: Hermes invokes the repo, consumes a handoff package, produces an internal digest message, records minimal human feedback, and only then evaluates conditional Bitable writes.
- Risk level: T3
- Staged/harness flow: yes. This is parent/child work across PRD, Hermes integration, CLI contracts, SQLite state, message delivery, feedback, and optional Feishu writes.
- Oracle review budget: required before parent PRD confirmation, and before any child that changes Hermes invocation, feedback persistence, or live Feishu writes.

## Reviewer Challenge Against PRD_MASTER

Conclusion: `PRD_MASTER.md` is good enough to seed v2.0, but not good enough to implement directly. The parent must lock the missing release PRD and keep M0-M3 as gates, not one blended "production" task.

| Challenge | Evidence | Parent Rule |
|---|---|---|
| v2.0 is still only a roadmap row, not a release PRD. | `PRD_MASTER.md` says `next_release: v2.0（PRD 待创建）` and the v2.0 index row is `待创建`. | Child 0 must create `docs/PRD/releases/PRD_v2.0.md` before code children. |
| The value loop's riskiest gap is not crawling; it is "handoff has no consumer". | Master states real Hermes analysis never ran and handoff package has no consumer. v1.4 closeout completed handoff but cancelled live writes/hardening. | M0/M1 must prove Hermes invocation and real analysis before feedback or write-table work. |
| `run-daily` still has stub paths. | `hermes_benchmark/cli.py` returns `reason: stub_only` for default `run-daily`; only `--analysis-mode hermes-handoff` creates a package. | Acceptance cannot claim full daily automation until collection/transcript/handoff invocation is wired through the chosen skill path. |
| Feishu writes are explicitly conditional. | Master sets M3 after 2 weeks of message flow and open decisions for auth trust root and table 2/3. | No Bitable child starts until message flow evidence and auth/table decisions are recorded. |
| v2.1 depends on v2.0's message path. | `trending-system.md` says the Twitter digest reuses v2.0 M1's message channel. | Do not start hotspot source work in this parent. |

## Goals

- Create and index the v2.0 release PRD before implementation.
- Prove the smallest Hermes-to-repo invocation path with no new dependency and no MCP server.
- Convert a v1.4 handoff package into real Hermes analysis output owned by hermes-agent, not by repo-side mock logic.
- Send one internal-group digest through the message-first path.
- Record minimal human feedback in SQLite as the seed for future rules/RAG.
- Gate any Bitable work behind the two-week message-flow condition and explicit authorization/table decisions.

## Non-Goals

- Do not build v2.1 hotspot/Twitter/RSSHub/TrendRadar work.
- Do not build MCP server unless the Hermes skill path is proven impossible.
- Do not auto-publish, auto-edit videos, or create RAG/vector storage.
- Do not store credentials, cookies, login state, raw videos, or raw crawler outputs in this repo.
- Do not implement live Bitable writes before M3 conditions are met.

## Requirements

| ID | Requirement | Source | Acceptance |
|---|---|---|---|
| P20-REQ-000 | Draft `docs/PRD/releases/PRD_v2.0.md` and update the master index in the same change. | PRD_MASTER lines 188 and 193-194 | Release PRD exists, is linked from master, and repeats no child execution state. |
| P20-REQ-010 | Define the Hermes skill invocation contract for `hermes-benchmark` without adding MCP or repo runtime dependencies. | PRD_MASTER §5.3 and §9 | A no-secret skill/config template can call `healthcheck` and one handoff run from the repo. |
| P20-REQ-020 | M0 proves one scheduled-or-manual Hermes invocation can call the CLI and send a Feishu internal-group message; if Hermes cron fails, record systemd fallback. | PRD_MASTER v2.0 M0 | Evidence shows one CLI invocation result and one internal message delivery or explicit fallback decision. |
| P20-REQ-030 | M1 consumes a handoff package and stores real Hermes analysis output with evidence refs. | PRD_MASTER §5.1 and v2.0 M1 | Mock decomposition is bypassed for the real path; output records source content refs, transcript refs, and Hermes result refs. |
| P20-REQ-040 | M1 sends the daily digest to the internal group using the message-first path. | PRD_MASTER §4.1 and §6 | Digest message includes analyzed items, errors/degraded status, and trace IDs back to run/content/package refs. |
| P20-REQ-050 | M2 records minimal human feedback from internal group into SQLite. | PRD_MASTER v2.0 M2 | Adopt/reject feedback writes are idempotent, auditable, and do not mutate manual-only fields implicitly. |
| P20-REQ-060 | M3 is only a gate, not default implementation. | PRD_MASTER §8 and v2.0 M3 | Parent evidence includes 2-week message-flow result and explicit decisions before any Bitable child exists. |

## Child Task Plan

| Child | Scope | Requirement IDs | Oracle |
|---|---|---|---|
| Child 0: v2.0 release PRD and PRD map | Write `PRD_v2.0.md`, update master index, preserve PRD/task boundary. | P20-REQ-000 | run |
| Child 1: Hermes skill invocation gate | No-secret skill/config template plus one `healthcheck`/handoff invocation proof. | P20-REQ-010, P20-REQ-020 | run |
| Child 2: real analysis handoff consumer | Consume handoff package and persist Hermes-owned result refs. | P20-REQ-030 | run |
| Child 3: internal digest message path | Build/send digest payload through Hermes/Feishu message channel with trace evidence. | P20-REQ-040 | conditional |
| Child 4: human feedback intake | Minimal adopt/reject CLI/state path into SQLite. | P20-REQ-050 | run |
| Child 5: M3 write-table gate | Decide whether Bitable starts; create a new child only if M3 conditions are met. | P20-REQ-060 | run if conditions met |

## Acceptance Criteria

- [ ] Parent challenge is recorded before implementation begins.
- [ ] `docs/PRD/releases/PRD_v2.0.md` exists and `PRD_MASTER.md` points to it.
- [ ] Child tasks are created only after the parent PRD is confirmed.
- [ ] M0 evidence exists before M1 begins.
- [ ] M3 Bitable work is not started without 2-week message-flow evidence and explicit authorization/table decisions.
- [ ] No source code is changed by this parent setup pass.

## Technical Approach

Ponytail path: keep the repo as the deterministic tool layer, use the existing `hermes-benchmark` console script, and make Hermes skill/config the integration surface. Add no MCP server, scheduler framework, or write-table adapter until a gate proves the previous rung is insufficient.

## Confirmation

- [x] Parent PRD confirmed by user on 2026-07-05.
