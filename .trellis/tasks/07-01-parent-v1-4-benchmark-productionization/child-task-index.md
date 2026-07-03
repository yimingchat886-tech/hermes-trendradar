# Child Task Index

Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`

| Order | Child Task | Purpose | Depends On | Output |
|---|---|---|---|---|
| 1 | `07-01-child-1-v1-4-cli-contract-skeleton` | Establish installable CLI and stable JSON contracts. | none | Package metadata, entrypoint, command skeleton, exit/error schema. |
| 2 | `07-01-child-2-v1-4-profile-local-config` | Load manually edited local profiles and reject unsafe config. | child 1 | Profile parser, validator, hash, local config examples. |
| 3 | `07-01-child-3-v1-4-sqlite-dedup-state` | Add durable local run/dedup/operation/error state. | child 1, child 2 | SQLite schema and idempotent state helpers. |
| 4 | `07-01-child-4-v1-4-douyin-collection-runner` | Run 10-account Douyin collection through external MediaCrawler boundary. | child 2, child 3 | Collection runner, normalized rows, account summaries. |
| 5 | `07-01-child-5-v1-4-whisper-batch-runner` | Build the batch transcription runner before threshold testing. | child 3, child 4 | Queue processing, transcript artifacts, temp cleanup. |
| 6a | `07-02-child-6a-v1-4-media-download-manifest` | Download/localize the approved 99 Child 4 Douyin videos into local media files. | child 4 | 99-item local media manifest with paths, sizes, hashes, and redacted evidence. |
| 6 | `07-01-child-6-v1-4-transcription-threshold-smoke` | Validate the user-approved 99-item transcription smoke with at least 80 successes after the batch runner and media manifest exist. | child 5, child 6a | Current-smoke evidence or explicit blocker report; does not claim full 100-queued production threshold. |
| 7 | `07-01-child-7-v1-4-hermes-handoff-package` | Emit mock and Hermes handoff packages. | child 3, child 5 | Schema-valid `analysis_package_ref` artifacts. |
| 8 | `07-01-child-8-v1-4-feishu-limited-live-table4` | Apply table 4/status-only live operations under Hermes authorization. | child 2, child 3, child 7 | Read-before-write, fail-closed writes, write-audit. |
| 9 | `07-01-child-9-v1-4-production-hardening` | Run focused contract/security/idempotency/E2E dry-run checks. | children 1-8 | Verification report, runbook updates, closeout evidence. |

## Execution Rule

Start with child 1. Do not run child 6 before child 5 is implemented and verified and child 6a has produced a valid local media manifest. Do not implement live table 6/7/9 writes in this parent.

## Scope Guard

Hotspot/RSSHub/TrendRadar, formal RAG, scheduler implementation, generic Feishu writes, table 6/7/9 live writes, raw transcript/full video Feishu writes, and credential storage are outside this child set.
