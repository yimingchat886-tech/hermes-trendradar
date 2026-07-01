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
