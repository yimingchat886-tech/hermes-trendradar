# Parent 1: Benchmark Account Tracking

## Background

`docs/PRD/PRDv1.3.md` is the repository-level product PRD. This parent task is the first implementation subphase derived from it: ship the v1 benchmark account tracking line before hotspot tracking, full RAG, publishing review, or autonomous topic generation.

The PRD narrows v1 to "benchmark account tracking + decomposition + cards + topic-pool supplements". Ponytail interpretation: prove that chain with fixtures and stable contracts first, then connect real tools one slice at a time.

## PM Intake

- Original request: "PRDv1.3作为本仓库总PRD，从中构建父任务1：先做对标账号跟踪。再拆解child work"
- Optimized requirement: Store PRDv1.3 as the repo source PRD, create staged parent task 1 for benchmark account tracking, and decompose it into independently verifiable child tasks.
- Risk level: T3
- Staged overlay: yes, because the work crosses product PRD, data contracts, external tools, Feishu, and multiple implementation children.
- Oracle review budget: skip for planning artifacts; decide per child before implementation.

## Goals

- Establish PRDv1.3 as the repo source of product truth.
- Define the smallest v1 parent scope: benchmark account tracking only.
- Decompose the parent into child tasks that can be implemented and verified independently.
- Keep hotspot/RSSHub/TrendRadar/formal RAG/publish-review work out of parent 1.

## Non-Goals

- Do not implement runtime code in this parent planning task.
- Do not connect real platform accounts, cookies, login state, proxy configuration, or scraping bypass guidance.
- Do not implement RSSHub, TrendRadar, hotspot radar, cross-source event clustering, formal RAG vector storage, publishing review, or risk-review systems.
- Do not let Hermes automatically create official topic titles before the selection Skill strategy exists.
- Do not design a custom Feishu/Bitable adapter; use `lark-cli` first and official API thin scripts only where required.

## Requirements

| ID | Requirement | Source | Acceptance |
|---|---|---|---|
| P1-REQ-001 | Keep PRDv1.3 in repo as the product source PRD. | PRDv1.3 section 0 | `docs/PRD/PRDv1.3.md` exists and is referenced by task docs. |
| P1-REQ-010 | Model benchmark account registry for about 20 Douyin/Xiaohongshu accounts with S/A/B/C level, enabled state, daily tracking, owner, and notes. | 6.1, 7.2, 11.3, 14.6 | Registry supports fixture data and manual account maintenance. |
| P1-REQ-020 | Define local contracts for `Source`, `BenchmarkAccount`, `BenchmarkContent`, `Transcript`, `TopicCandidate`, `RAGDocument` placeholder, and `SourceHealth`. | 11.x | Contracts have fixture validation and trace IDs. |
| P1-REQ-030 | Provide a fixture-first local loop for benchmark tracking before real platform access. | 5.5, 17.4 | A local demo can run without credentials and produce benchmark content outputs. |
| P1-REQ-040 | Import MediaCrawler-style benchmark content results through an adapter boundary without embedding MediaCrawler source or credentials. | 5.3, 5.4, 5.5, 7.3 | Import maps fields, records errors, and preserves raw source links. |
| P1-REQ-045 | Execute benchmark collection through an external MediaCrawler command boundary with dry-run and local fake-command output in child scope. | 5.3, 5.4, 5.5, 6.1 | Child 8 emits a collection plan, raw artifact path, run manifest, and health output without real platform access. |
| P1-REQ-050 | Deduplicate benchmark content by URL and platform content ID first; title similarity/semantic dedup stay optional later. | 10.1 | Duplicate fixture rows collapse to one canonical content object. |
| P1-REQ-060 | Add local Whisper transcript pipeline contract with transcript status and temporary video cleanup. | 6.1, 9.4, 11.5 | Transcript fixtures produce `Transcript`; video files are not retained. |
| P1-REQ-070 | Define Hermes benchmark decomposition outputs for summary, hook, title formula, structure, pain, reusable angle, evidence state, card fields, and topic-pool supplements. | 7.4, 13.4, 14.7, 15.2 | Hermes output is schema-validated and mockable in tests. |
| P1-REQ-080 | Map parent 1 outputs to Feishu tables 1, 2, 3, 4, 6, 7, and 9 with dry-run writes first. | 14.x | Dry-run emits planned table operations and does not require live credentials. |
| P1-REQ-090 | Produce daily benchmark digest and ops alert objects from tracked content and health records. | 13.2, 13.3, 17.1 | Digest and alert fixtures include traceable source/content/task IDs. |
| P1-REQ-100 | Preserve observability and traceability across every child output. | 17.1, 17.2 | Outputs include source link, local object ID, task/run ID, and timestamps. |
| P1-REQ-110 | Parent final acceptance must run one real external MediaCrawler collection chain with 1-2 user-provided accounts. | user decision 2026-06-30 | Real-call evidence is recorded at parent level only; no credentials, cookies, or account secrets are committed. |

## Child Task Plan

| Child | Scope | Requirement IDs | Oracle |
|---|---|---|---|
| Child 1: benchmark contracts and fixture loop | Define contracts, fixtures, and a no-credential local path. | P1-REQ-001, P1-REQ-010, P1-REQ-020, P1-REQ-030 | skip |
| Child 2: benchmark account registry | Implement account registry, manual config, daily tracking plan, and account health fields. | P1-REQ-010, P1-REQ-030, P1-REQ-100 | skip |
| Child 8: MediaCrawler collection runner dry-run | Build collection execution boundary with dry-run plus fake-command/fixture output only. | P1-REQ-045, P1-REQ-100 | skip |
| Child 3: MediaCrawler import and dedup | Import MediaCrawler-style output fixtures or child8 raw artifacts, map fields, dedupe, and log failures. | P1-REQ-030, P1-REQ-040, P1-REQ-050, P1-REQ-100 | skipped by fixture-only scope |
| Child 4: local Whisper transcript pipeline | Add transcript wrapper/contract, transcript status, and temp-video cleanup behavior. | P1-REQ-020, P1-REQ-060, P1-REQ-100 | skip |
| Child 5: Hermes benchmark decomposition outputs | Add mockable Hermes decomposition schema and card/topic-pool output contracts. | P1-REQ-020, P1-REQ-070, P1-REQ-090, P1-REQ-100 | decide before implementation |
| Child 6: Feishu table sync dry-run | Map contracts to Feishu tables and implement dry-run operations around `lark-cli`/official API boundaries. | P1-REQ-080, P1-REQ-100 | decide before implementation |
| Child 7: benchmark daily digest and ops alerts | Build digest and `radar-ops` alert output from child outputs. | P1-REQ-090, P1-REQ-100 | skip |

## Acceptance Criteria

- [ ] PRDv1.3 is available in `docs/PRD/PRDv1.3.md`.
- [ ] Parent PRD narrows v1 to benchmark account tracking only.
- [ ] Child tasks exist and are linked to the parent.
- [ ] Each child has a PRD and an implementation plan.
- [ ] Child task index maps child scopes to parent requirements.
- [ ] RTM delta maps PRDv1.3 requirements to parent and child work.
- [ ] Child 8 is dry-run/fake-command only.
- [ ] Parent final acceptance records a real MediaCrawler call using 1-2 user-provided accounts.
- [ ] No source code is written by parent planning.

## Technical Approach

Use staged overlay. Parent owns scope and traceability. Children own implementation. The first runnable child should be a contract-and-fixture loop, because it avoids account credentials, platform volatility, and Feishu write-risk while proving the object model.

Implementation should follow the first rung that works:

1. Reuse repo Trellis workflow and specs.
2. Use local fixtures and standard library validation where possible.
3. Add dependencies only after a child proves stdlib/existing tooling is insufficient.
4. Keep third-party tools behind adapter boundaries.

## Decision (ADR-lite)

Context: PRDv1.3 describes a broad v1/v2 product system. The repo is a fresh standalone Trellis project with no product runtime yet.

Decision: Treat PRDv1.3 as the repo source PRD, create parent 1 for benchmark account tracking, and decompose implementation into small child tasks. Do not start with real scraping, Feishu writes, RAG, or hotspot features. Add child 8 as the collection execution boundary, but keep child 8 dry-run/fake-command only; parent final acceptance owns the later real MediaCrawler call with 1-2 user-provided accounts.

Consequences: The first implementation can be verified locally without credentials. The real external call is delayed until the parent can test the complete chain and the user supplies test accounts.

## Confirmation

- [ ] Parent PRD confirmed by user.
