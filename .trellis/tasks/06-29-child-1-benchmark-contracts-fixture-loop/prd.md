# Child PRD: Benchmark Contracts And Fixture Loop

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-001, P1-REQ-010, P1-REQ-020, P1-REQ-030

## Goal

Create the smallest local, no-credential loop that proves the benchmark tracking object model before any real MediaCrawler, Whisper, Hermes, or Feishu integration.

## Requirements

- Define contracts for benchmark-account tracking objects needed by parent 1.
- Provide fixture examples for account, content, transcript placeholder, source health, and card/topic output placeholders.
- Add one runnable check that validates fixture shape and trace IDs.
- Keep runtime choices minimal; do not add a framework unless the child PLAN proves it is necessary.

## Out of Scope

- Real platform crawling.
- Real Whisper execution.
- Real Hermes calls.
- Real Feishu writes.
- Hotspot/RSS/RAG vector storage.

## Acceptance Criteria

- [ ] Contracts cover the parent-required object fields.
- [ ] Fixtures run without credentials.
- [ ] Duplicate object IDs or missing trace fields fail validation.
- [ ] Later children can import the contracts without guessing field names.

## Risk Level

- T2: foundational contracts, but no external side effects.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-1-benchmark-contracts-fixture-loop/implement.md
# Implementation Plan: Benchmark Contracts And Fixture Loop

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-001, P1-REQ-010, P1-REQ-020, P1-REQ-030

## Scope

- Choose the smallest runtime layout for product code.
- Add contract definitions and fixture validation.
- Add no-credential sample data.

## Expected Files

- Product contract files under the eventual app/package path.
- Fixture files under a test or sample data path.
- One minimal runnable check.

## Ponytail Pass

- Blocking findings: new framework or dependency would block until confirmed.
- Advisory findings: prefer standard library/native schema checks if enough.
- Decision: start fixture-first.

## Oracle

- Required: no.
- Reason: no external side effects.

## Steps

1. Inspect package/runtime options in the repo.
2. Define object contracts.
3. Add fixture data.
4. Add one focused validation check.

## Verification

- Command: child-specific fixture validation plus `git diff --check`.
- Expected result: fixtures validate and no whitespace errors.

## Rollback

- Remove the new contract and fixture files.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-2-benchmark-account-registry/prd.md
# Child PRD: Benchmark Account Registry

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-010, P1-REQ-030, P1-REQ-100

## Goal

Implement the manually maintained benchmark account registry for about 20 Douyin/Xiaohongshu accounts, with S/A/B/C level, enabled state, daily tracking, and health metadata.

## Requirements

- Support manually maintained account entries.
- Preserve platform, profile URL, level, enabled state, owner, daily tracking frequency, and notes.
- Expose account records to later import/digest children.
- Do not store credentials, cookies, or login state.

## Out of Scope

- Real crawler invocation.
- Automatic account discovery.
- Hermes auto-changing account level.
- Hotspot source registry.

## Acceptance Criteria

- [ ] Registry fixtures can represent the PRDv1.3 account fields.
- [ ] Disabled accounts are excluded from tracking plans.
- [ ] S/A/B/C level is preserved but not auto-mutated by Hermes.
- [ ] Output includes traceable account IDs.

## Risk Level

- T2: product configuration and data model.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-2-benchmark-account-registry/implement.md
# Implementation Plan: Benchmark Account Registry

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-010, P1-REQ-030, P1-REQ-100

## Scope

- Add account registry representation and daily tracking plan output.
- Validate fixture accounts.
- Keep secrets out.

## Expected Files

- Registry/config file or module.
- Fixture account list.
- Focused validation check.

## Ponytail Pass

- Blocking findings: a database migration is overkill unless child 1 proves persistent storage is needed now.
- Advisory findings: use a simple local config file first.
- Decision: manual registry first.

## Oracle

- Required: no.
- Reason: no credentials or external writes.

## Steps

1. Reuse child 1 contracts.
2. Add account registry fixture/config.
3. Add daily tracking plan output for enabled accounts.
4. Verify disabled and malformed accounts fail safely.

## Verification

- Command: registry validation check plus `git diff --check`.
- Expected result: valid fixtures pass; invalid registry examples fail in the check.

## Rollback

- Remove registry files and validation check.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-3-mediacrawler-import-dedup/prd.md
# Child PRD: MediaCrawler Import And Dedup

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-030, P1-REQ-040, P1-REQ-050, P1-REQ-100

## Goal

Import MediaCrawler-style benchmark content output into local `BenchmarkContent` records and deduplicate by URL and platform content ID first.

## Requirements

- Read fixture files shaped like MediaCrawler results.
- Map title, caption, platform, platform content ID, URL, published/crawled time, metrics, author/account, hashtags/topics, and comments summary when available.
- Mark evidence as insufficient when important metrics/comments are missing.
- Deduplicate exact URL and exact platform content ID.
- Keep MediaCrawler as an external boundary; do not copy its source or include login guidance.

## Out of Scope

- Running MediaCrawler.
- Handling platform login state.
- Semantic dedup.
- Cross-platform event clustering.

## Acceptance Criteria

- [ ] MediaCrawler-style fixture input maps to `BenchmarkContent`.
- [ ] Duplicate URL and duplicate platform ID collapse.
- [ ] Missing metrics produce evidence-insufficient state.
- [ ] Import errors are recorded as source health/ops events.

## Risk Level

- T3: adapter boundary and platform data handling.
- High-risk trial PLAN: yes.
- Oracle required: decide before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-3-mediacrawler-import-dedup/implement.md
# Implementation Plan: MediaCrawler Import And Dedup

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-030, P1-REQ-040, P1-REQ-050, P1-REQ-100

## Scope

- Add an import boundary for fixture data.
- Map fields to local contracts.
- Deduplicate exact URL and platform content ID.

## Expected Files

- Import adapter/module.
- Fixture input and expected output.
- Focused import/dedup check.

## Ponytail Pass

- Blocking findings: do not add crawler orchestration or credential handling in this child.
- Advisory findings: exact dedup first; title/semantic dedup later only if needed.
- Decision: fixture import only.

## Oracle

- Required: decide before implementation.
- Reason: platform-data boundary and open-source safety.

## Steps

1. Inspect child 1 contracts and child 2 account registry.
2. Add MediaCrawler-style fixture input.
3. Implement mapping and exact dedup.
4. Add evidence-state and health-event outputs.

## Verification

- Command: import/dedup check plus `git diff --check`.
- Expected result: fixture duplicates collapse and missing metrics are marked.

## Rollback

- Remove import adapter, fixtures, and check.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-4-whisper-transcript-pipeline/prd.md
# Child PRD: Local Whisper Transcript Pipeline

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-020, P1-REQ-060, P1-REQ-100

## Goal

Implement the local transcript contract around Whisper so benchmark video content can produce `Transcript` records while temporary video files are not retained.

## Requirements

- Represent transcript status: pending, done, failed, not_applicable.
- Store transcript text/segments paths or fixture equivalents.
- Record provider as `local_whisper`.
- Delete or avoid retaining temporary video inputs after successful transcript handling.
- Keep transcript output traceable to `BenchmarkContent`.

## Out of Scope

- Installing Whisper.
- GPU optimization.
- Long-term video storage.
- Full speech quality scoring beyond the PRD statuses.

## Acceptance Criteria

- [ ] Transcript fixture maps to `Transcript`.
- [ ] Failed transcript records preserve error state.
- [ ] Temporary video-retention behavior is covered by a check or dry-run proof.
- [ ] Output does not require real video files for basic validation.

## Risk Level

- T2: local file lifecycle and transcript contract.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-4-whisper-transcript-pipeline/implement.md
# Implementation Plan: Local Whisper Transcript Pipeline

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-020, P1-REQ-060, P1-REQ-100

## Scope

- Add transcript status and wrapper behavior.
- Keep real Whisper invocation optional or mockable.
- Prove temp video files are not retained.

## Expected Files

- Transcript wrapper/contract code.
- Transcript fixtures.
- File-lifecycle check.

## Ponytail Pass

- Blocking findings: do not add a queue or media pipeline unless needed.
- Advisory findings: use mock/stub transcript output for the first check.
- Decision: wrapper contract first.

## Oracle

- Required: no.
- Reason: bounded local behavior.

## Steps

1. Reuse child 1 contracts.
2. Add transcript fixture and wrapper boundary.
3. Add temp file cleanup behavior.
4. Verify status and traceability.

## Verification

- Command: transcript wrapper check plus `git diff --check`.
- Expected result: transcript status and temp cleanup pass.

## Rollback

- Remove transcript wrapper, fixtures, and check.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-5-hermes-benchmark-decomposition/prd.md
# Child PRD: Hermes Benchmark Decomposition Outputs

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-020, P1-REQ-070, P1-REQ-090, P1-REQ-100

## Goal

Define and validate the Hermes output boundary for benchmark content decomposition, cards, and topic-pool supplements without letting Hermes autonomously submit official topic titles.

## Requirements

- Accept benchmark content and transcript data.
- Produce summary, topic one-liner, content type, hook, title formula, structure, audience pain, reusable angle, non-reusable notes, evidence state, and sedimentation suggestion.
- Produce card fields for external display.
- Produce topic-pool supplement fields only after a manual topic title exists or as candidate-shaped suggestions.
- Keep output schema stable and mockable.

## Out of Scope

- Production LLM orchestration.
- Auto-enabling selection Skill.
- Official autonomous topic submission.
- Risk-review system.

## Acceptance Criteria

- [ ] Mock Hermes output validates against schema.
- [ ] Manual fields are not overwritten.
- [ ] Evidence-insufficient content remains marked.
- [ ] Card/topic outputs include source IDs and trace IDs.

## Risk Level

- T3: LLM boundary and product behavior.
- High-risk trial PLAN: yes.
- Oracle required: decide before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-5-hermes-benchmark-decomposition/implement.md
# Implementation Plan: Hermes Benchmark Decomposition Outputs

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-020, P1-REQ-070, P1-REQ-090, P1-REQ-100

## Scope

- Define Hermes decomposition output schema.
- Add mock output examples.
- Protect manual fields and evidence state.

## Expected Files

- Hermes output schema/module or prompt contract.
- Mock fixtures.
- Validation check.

## Ponytail Pass

- Blocking findings: do not build a general agent framework in this child.
- Advisory findings: keep the LLM boundary as input/output schema first.
- Decision: mockable schema first.

## Oracle

- Required: decide before implementation.
- Reason: LLM output can shape product behavior.

## Steps

1. Reuse child 1/3/4 outputs.
2. Define decomposition output shape.
3. Add card and topic supplement outputs.
4. Add validation and manual-field protection checks.

## Verification

- Command: decomposition schema check plus `git diff --check`.
- Expected result: valid mock output passes; manual overwrite attempts fail.

## Rollback

- Remove schema, fixtures, and checks.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-6-feishu-table-sync-dry-run/prd.md
# Child PRD: Feishu Table Sync Dry-Run

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-080, P1-REQ-100

## Goal

Map parent 1 local objects to Feishu table operations and prove the sync plan with dry-run output before any live Feishu writes.

## Requirements

- Map tables 1, 2, 3, 4, 6, 7, and 9 for parent 1 outputs.
- Preserve field responsibility: `script_generated`, `hermes_managed`, `manual`.
- Prefer `lark-cli`; allow official API thin scripts only for gaps.
- Emit dry-run create/update operations with idempotency keys.
- Do not expose internal tables to external group members.

## Out of Scope

- Live Feishu writes without later confirmation.
- Custom Feishu/Bitable adapter.
- Complex external feedback entry.
- Table 5 hotspot radar and table 8 publishing review.

## Acceptance Criteria

- [ ] Dry-run output lists target table, operation, object ID, field mapping, and idempotency key.
- [ ] Manual fields are never overwritten by script/Hermes operations.
- [ ] Missing table IDs or credentials fail before live mode.
- [ ] Table 9 card output is decoupled from internal table fields.

## Risk Level

- T3: external API/write boundary.
- High-risk trial PLAN: yes.
- Oracle required: decide before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-6-feishu-table-sync-dry-run/implement.md
# Implementation Plan: Feishu Table Sync Dry-Run

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-080, P1-REQ-100

## Scope

- Add Feishu field mapping for parent 1 tables.
- Implement dry-run operations.
- Validate responsibility and idempotency rules.

## Expected Files

- Feishu mapping/config files.
- Dry-run sync command or module.
- Dry-run fixtures/checks.

## Ponytail Pass

- Blocking findings: custom Feishu adapter would violate PRDv1.3.
- Advisory findings: dry-run first; live mode later.
- Decision: map and dry-run only.

## Oracle

- Required: decide before implementation.
- Reason: external write and permission boundary.

## Steps

1. Reuse child outputs.
2. Define table/field mapping.
3. Add dry-run operation builder.
4. Add checks for manual fields and idempotency keys.

## Verification

- Command: Feishu dry-run check plus `git diff --check`.
- Expected result: dry-run operations are deterministic and no manual fields are overwritten.

## Rollback

- Remove mapping, dry-run code, fixtures, and checks.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
*** Add File: .trellis/tasks/06-29-child-7-benchmark-daily-digest-ops-alerts/prd.md
# Child PRD: Benchmark Daily Digest And Ops Alerts

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-090, P1-REQ-100

## Goal

Generate the benchmark daily digest for trend-chief and ops alert objects for radar-ops from tracked content and health records.

## Requirements

- Summarize account updates, new content count, high-like/high-discussion/strong-hit candidates, and evidence-insufficient content.
- Produce ops alerts for crawler/import/transcript/Feishu failures and abnormal counts.
- Keep digest/alert outputs traceable to source/account/content/run IDs.
- v1 ops alerts notify exceptions only, not hotspot events.

## Out of Scope

- Feishu live notifications.
- Hotspot/rising trend alerts.
- Weekly strategy report.
- Topic adoption-rate analytics.

## Acceptance Criteria

- [ ] Digest fixture includes daily benchmark account update summary.
- [ ] Alert fixture includes failure reason and source/run IDs.
- [ ] High-performance thresholds match PRDv1.3 defaults.
- [ ] Hotspot notifications are absent from parent 1 output.

## Risk Level

- T2: downstream formatting and aggregation.
- High-risk trial PLAN: no.
- Oracle required: no.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
*** Add File: .trellis/tasks/06-29-child-7-benchmark-daily-digest-ops-alerts/implement.md
# Implementation Plan: Benchmark Daily Digest And Ops Alerts

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-090, P1-REQ-100

## Scope

- Add digest aggregation.
- Add ops alert output.
- Use PRDv1.3 high-performance thresholds.

## Expected Files

- Digest/alert formatter or module.
- Fixture inputs and expected outputs.
- Focused aggregation check.

## Ponytail Pass

- Blocking findings: do not add notification infrastructure in this child.
- Advisory findings: output objects first, delivery later.
- Decision: format objects only.

## Oracle

- Required: no.
- Reason: no external side effects.

## Steps

1. Reuse account/content/health outputs.
2. Add digest aggregate.
3. Add ops alert aggregate.
4. Verify thresholds and trace fields.

## Verification

- Command: digest/alert check plus `git diff --check`.
- Expected result: digest and alerts match fixture expectations.

## Rollback

- Remove digest/alert files and checks.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
