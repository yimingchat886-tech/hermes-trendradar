# Parent v1.4: Benchmark Account Productionization

## Background

`docs/PRD/PRD_MASTER.md` now points at `docs/PRD/releases/PRD_v1.4_split_index.md`. v1.4 is split into Hermes Runtime/profile responsibilities and Codex CLI implementation responsibilities:

- `docs/PRD/releases/PRD_v1.4_split_index.md`
- `docs/PRD/releases/PRD_v1.4_Hermes_Runtime_and_Profiles.md`
- `docs/PRD/releases/PRD_v1.4_Codex_CLI_Implementation.md`

This parent turns the v1.4 split PRDs into staged Trellis child work for the productionized benchmark-account loop.

## PM Intake

- Original request: "创建parent task v1.4和相应的child task"
- Optimized requirement: Create a staged parent task for v1.4 benchmark-account productionization and decompose it into independently verifiable child tasks.
- Risk level: T3
- Staged overlay: yes, because the work crosses packaging, CLI contracts, profile/security boundaries, SQLite state, external runtimes, batch transcription, Hermes handoff, and Feishu limited-live writes.
- Oracle review budget: required before parent plan confirmation and before high-risk child PLANs.

## Locked User Decisions

- `PRD_v1.4_Hermes_Runtime_and_Profiles.md` is now present and is part of the source PRD set.
- v1.4 live write scope is table 4 and status-only boundaries, not table 6/7/9 production live writes.
- Account and runtime input starts from local configuration files that can be manually edited.
- Build the batch transcription runner first; run the 100 queued / 80 success threshold smoke only after the batch runner exists.

## Goals

- Provide a stable `hermes-benchmark` CLI surface for Hermes Runtime.
- Move production configuration into local profiles/env/secrets refs instead of hard-coded constants.
- Add durable local state for run locking, dedup, artifacts, operations, audits, and errors.
- Connect the existing external MediaCrawler/Whisper proof boundaries into production-oriented runners.
- Produce Hermes handoff packages rather than letting the CLI make LLM content decisions.
- Implement Feishu `limited-live` only for table 4/status allowlisted mutations with Hermes authorization.

## Non-Goals

- Do not build Hermes Scheduler inside this repo.
- Do not build a generic Feishu writer or write table 6/7/9 in v1.4 live mode.
- Do not implement hotspot/RSSHub/TrendRadar, formal RAG, publishing review, or autonomous topic generation.
- Do not commit cookies, tokens, CDP endpoints, proxy settings, raw videos, raw crawler outputs, model caches, or external source trees.
- Do not start with the 100/80 transcription threshold before a batch runner exists.

## Requirements

| ID | Requirement | Source | Acceptance |
|---|---|---|---|
| P14-REQ-001 | Treat the v1.4 split index and companion PRDs as the source for this parent. | PRD split index | Parent PRD links all three v1.4 docs and child tasks map to those sources. |
| P14-REQ-010 | Provide package metadata and a `hermes-benchmark` CLI with stable JSON output, exit codes, and error schema. | Codex PRD CX-FR-001, section 4/5 | `--version`, `validate-config`, `healthcheck`, and command skeletons return valid JSON contracts. |
| P14-REQ-020 | Support manually edited local profile/config files, schema validation, profile hash, and sensitive-value rejection. | Runtime PRD section 5; Codex PRD section 6 | `validate-config` accepts local v1.4 Douyin profiles and rejects plaintext secrets/endpoints. |
| P14-REQ-030 | Add SQLite/local state for run lock, content ledger, transcript state, handoff packages, operations, write-audit, and deterministic errors. | Codex PRD section 7 | Same date + profile rerun is safe, resumable, and idempotent. |
| P14-REQ-040 | Run Douyin collection for 10 enabled local-profile accounts through the external MediaCrawler boundary. | Codex PRD section 8 | Each account is processed or records a deterministic account-level failure. |
| P14-REQ-050 | Implement the Whisper batch runner and transcript artifact policy. | Codex PRD section 9 | Batch runner can queue/process content videos and write transcript artifacts/errors without retaining videos. |
| P14-REQ-055 | Validate the transcription threshold after the batch runner exists. | User clarification; Runtime PRD success metrics | A later smoke proves 100 queued / at least 80 success or records explicit blockers. |
| P14-REQ-060 | Generate deterministic mock output and Hermes handoff packages without real CLI-side LLM analysis. | Codex PRD section 10 | `run-daily --analysis-mode hermes-handoff` writes schema-valid package refs. |
| P14-REQ-070 | Implement Feishu `apply-limited-live` for table 4/status-only allowlisted mutations with Hermes authorization, read-before-write, and write-audit. | Runtime PRD non-goals; Codex PRD section 11 | Unauthorized or non-allowlisted operations fail closed; valid table 4/status operations create/update/no-op with audit. |
| P14-REQ-080 | Add focused tests, idempotency checks, security/redaction checks, and an end-to-end dry run. | Codex PRD sections 14/15/16 | Local verification covers CLI contracts, config, state, handoff, limited-live dry run, and redaction. |

## Child Task Plan

| Child | Scope | Requirement IDs | Oracle |
|---|---|---|---|
| Child 1: CLI contract skeleton | Packaging, entrypoint, JSON envelope, exit codes, command stubs. | P14-REQ-010, P14-REQ-080 | skip unless package tooling blocks |
| Child 2: profile and local config | Local YAML/profile loading, manual account config, schema/hash, secret detection. | P14-REQ-020, P14-REQ-040 | conditional for security/profile boundary |
| Child 3: SQLite run state and dedup ledger | DB schema, run lock, content ledger, operation/error/audit state. | P14-REQ-030 | run before implementation |
| Child 4: Douyin collection runner | 10-account external MediaCrawler runner, normalize, account failure isolation. | P14-REQ-040 | run before real external execution |
| Child 5: Whisper batch runner | Batch queue/runner, artifact refs, temp-video cleanup, deterministic errors. | P14-REQ-050 | run before implementation if runtime plan changes |
| Child 6: transcription threshold smoke | 100 queued / 80 success smoke after child 5. | P14-REQ-055 | run before real threshold execution |
| Child 7: Hermes handoff package | Mock mode and Hermes handoff package schema/output refs. | P14-REQ-060 | skip unless contract conflict appears |
| Child 8: Feishu limited-live table 4 | Authorization, allowlist, read-before-write, table 4/status-only write-audit. | P14-REQ-070 | run before implementation |
| Child 9: production hardening and E2E dry run | Contract/security/idempotency checks, runbook, gap closeout. | P14-REQ-080 | parent closeout review required |

## Acceptance Criteria

- [ ] Parent task and all child tasks exist under `.trellis/tasks/` and are linked.
- [ ] Parent has `child-task-index.md`, `conflict-review.md`, `oracle-review-budget.md`, `rtm-delta.md`, and `subphase-report.md`.
- [ ] Each child has `prd.md` and `implement.md`.
- [ ] Child ordering keeps batch runner before the 100/80 threshold smoke.
- [ ] v1.4 live scope is recorded as table 4/status only.
- [ ] Local config manual-input scope is recorded before implementation.
- [ ] No source code is changed by parent/child creation.

## Technical Approach

Use staged overlay. Parent owns scope and requirement traceability. Children own implementation slices. Ponytail constraint: each child must reuse existing proof modules where possible and add only the minimum production surface needed for its requirement.

## Decision (ADR-lite)

Context: v1.4 PRD is broader than one safe implementation pass.

Decision: Create one staged parent with nine child slices. Split the transcription work so the batch runner lands before the 100/80 threshold smoke. Keep Feishu live scope table 4/status-only.

Consequences: The first executable work can start with CLI/profile/state foundations, while high-risk external runtime and live-write work remains isolated behind later child PLAN reviews.

## Confirmation

- [ ] Parent PRD confirmed by user.
