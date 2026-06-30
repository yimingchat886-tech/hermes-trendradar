# Child Task Index

Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`

| Order | Child Task | Purpose | Depends On | Output |
|---|---|---|---|---|
| 1 | `06-29-child-1-benchmark-contracts-fixture-loop` | Contract-first local loop for benchmark tracking. | none | Schema/contracts, fixtures, local demo expectation. |
| 2 | `06-29-child-2-benchmark-account-registry` | Maintain about 20 benchmark accounts and daily tracking state. | child 1 | Account config/registry and health status. |
| 8 | `06-30-child-8-mediacrawler-collection-runner` | Build the external MediaCrawler execution boundary with dry-run and fake-command/fixture output. | child 1, child 2 | Collection plan, raw artifacts, run manifest, health output. |
| 3 | `06-29-child-3-mediacrawler-import-dedup` | Read MediaCrawler-style result data or child8 raw artifacts and dedupe benchmark content. | child 1, child 2, child 8 for full parent chain | Mapped `BenchmarkContent` records. |
| 4 | `06-29-child-4-whisper-transcript-pipeline` | Convert video inputs into `Transcript` records and clean temp files. | child 1, child 3 | Transcript records and status. |
| 5 | `06-29-child-5-hermes-benchmark-decomposition` | Produce Hermes decomposition fields, card fields, and topic-pool supplements. | child 1, child 3, child 4 | Structured analysis output. |
| 6 | `06-29-child-6-feishu-table-sync-dry-run` | Map local objects to Feishu table operations without live writes first. | child 1, child 2, child 5 | Dry-run sync plan and field mapping. |
| 7 | `06-29-child-7-benchmark-daily-digest-ops-alerts` | Generate daily benchmark summary and ops alerts. | child 2, child 3, child 5, child 6 | Digest and alert objects. |

## Execution Rule

Start with child 1. Child 8 stays dry-run/fake-command only. Do not use real platform access until parent final acceptance, when the user provides 1-2 test accounts.

## Scope Guard

RSSHub, TrendRadar, hotspot radar, formal RAG vector storage, release review, autonomous topic-title generation, credential storage, cookie handling, proxy setup, and platform bypass guidance are not part of this child set.
