# RTM Delta

## Source

- Repo PRD: `docs/PRD/PRDv1.3.md`
- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`

## Requirement Trace

| PRDv1.3 Area | Parent Requirement | Child |
|---|---|---|
| 5.3 MediaCrawler link into benchmark tracking | P1-REQ-040, P1-REQ-045 | child 8, child 3 |
| 6.1 v1 must do account library, daily tracking, structured capture | P1-REQ-010, P1-REQ-030, P1-REQ-045 | child 1, child 2, child 8, child 3 |
| 7.2 account scale and S/A/B/C levels | P1-REQ-010 | child 2 |
| 7.3 account crawl fields | P1-REQ-020, P1-REQ-040, P1-REQ-045 | child 1, child 8, child 3 |
| 7.4 decomposition output | P1-REQ-070 | child 5 |
| 9.4 video file handling | P1-REQ-060 | child 4 |
| 10.1 dedup | P1-REQ-050 | child 3 |
| 11.x core data models | P1-REQ-020 | child 1 |
| 13.2 trend-chief digest | P1-REQ-090 | child 7 |
| 13.3 radar-ops exceptions | P1-REQ-090 | child 7 |
| 13.4 competitor-analyst | P1-REQ-040, P1-REQ-060, P1-REQ-070 | child 3, child 4, child 5 |
| 14.x Feishu tables | P1-REQ-080 | child 6 |
| 17.1 observability | P1-REQ-100 | all children |
| 17.2 traceability | P1-REQ-100 | all children |
| Parent final acceptance with real collection | P1-REQ-110 | parent-level evidence after children |

## Deferred

- Hotspot information system: v2.
- RSSHub / TrendRadar: v2.
- Formal RAG vector database: later.
- Publishing review and performance learning loop: later.
- Complete risk-review system: later.
- Real MediaCrawler call in child 8: deferred to parent final acceptance with user-provided accounts.
