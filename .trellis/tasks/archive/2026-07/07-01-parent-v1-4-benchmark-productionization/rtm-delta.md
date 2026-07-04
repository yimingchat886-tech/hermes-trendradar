# RTM Delta: v1.4 Benchmark Productionization

## Planned Requirements

| ID | Source | Child Task | Status | Notes |
|---|---|---|---|---|
| P14-REQ-001 | v1.4 split index | parent | completed | Source PRD set and parent/child decomposition captured. |
| P14-REQ-010 | Codex PRD CX-FR-001, sections 4/5 | child 1 | completed | CLI/package contract. |
| P14-REQ-020 | Runtime PRD section 5; Codex PRD section 6 | child 2 | completed | Local manual profiles and safe config validation. |
| P14-REQ-030 | Codex PRD section 7 | child 3 | completed | SQLite state and dedup ledger. |
| P14-REQ-040 | Codex PRD section 8 | child 4 | completed | Douyin collection runner. |
| P14-REQ-050 | Codex PRD section 9 | child 5 | completed | Whisper batch runner. |
| P14-REQ-055 | Runtime PRD metrics; user clarification | children 6a + 6 | completed | Completed for the approved 99-item current smoke; the original 100 queued wording is not claimed. |
| P14-REQ-060 | Codex PRD section 10 | child 7 | completed | Hermes handoff package. |
| P14-REQ-070 | Runtime PRD non-goals; Codex PRD section 11 | child 8 | deferred | Cancelled at v1.4 closeout. |
| P14-REQ-080 | Codex PRD sections 14/15/16 | child 9 | deferred | Cancelled at v1.4 closeout. |

## Completed Requirements

| ID | Evidence | Commit |
|---|---|---|
| P14-REQ-001 | Parent `prd.md` and `child-task-index.md` define the v1.4 source PRD set, child ordering, and scope guard. | closeout metadata only |
| P14-REQ-010 | `07-01-child-1-v1-4-cli-contract-skeleton/stage-report.md`: CLI entrypoint, JSON envelope, exit/error contract, install smoke, and contract checks passed. | `a515a9b` |
| P14-REQ-020 | `07-01-child-2-v1-4-profile-local-config/stage-report.md`: profile parser, validation, sensitive-value rejection, sample profiles, and focused checks passed. | `649d4de` |
| P14-REQ-030 | `07-01-child-3-v1-4-sqlite-dedup-state/stage-report.md`: SQLite schema/state helpers, idempotency helpers, and state tests passed. | `e860f3b` |
| P14-REQ-040 | `07-01-child-4-v1-4-douyin-collection-runner/stage-report.md`: serial 10-account MediaCrawler boundary, normalized ledger rows, deterministic account timeout error, and redaction checks recorded. | `04efdca32c120234a5cc65dedfa8a88007d40213` |
| P14-REQ-050 | `07-01-child-5-v1-4-whisper-batch-runner/stage-report.md`: serial Whisper batch runner, transcript artifact refs/hashes, SQLite rows, cleanup proof, and runner checks passed. | `490e6b42b3851462294b9959924ec51ca70541bf` |
| P14-REQ-055 | `07-02-child-6a-v1-4-media-download-manifest/stage-report.md` localized 99 media files; `07-01-child-6-v1-4-transcription-threshold-smoke/stage-report.md` verified `queued=99`, `succeeded=99`, `failed=0`, and cleanup/artifact checks. | `208016abf0e2ba9db98c36299118ace728a99360`, `2c842c1d232af66a038bd10b54299962712fbecb` |
| P14-REQ-060 | `07-01-child-7-v1-4-hermes-handoff-package/stage-report.md`: schema-valid handoff package refs, CLI wiring, invalid-package exit code, and focused checks passed. | `ec87f09d358de180d85092b0a6148704ea8d7196` |

## Partial Requirements

| ID | Reason | Next step |
|---|---|---|
| none | - | - |

## Deferred Requirements

| ID | Reason | Owner |
|---|---|---|
| P14-REQ-070 | child 8 cancelled at closeout. | Rebuild under workflow v2 if table 4/status limited-live is needed. |
| P14-REQ-080 | child 9 cancelled at closeout. | Rebuild under workflow v2 if production hardening/E2E closeout is needed. |

## Blocked Requirements

| ID | Blocker | Decision needed |
|---|---|---|
| none | - | - |

## RTM Files Updated

- Markdown: parent closeout delta updated in this file only.
- JSON: not updated; this triage work is constrained to `.trellis/tasks/`.
