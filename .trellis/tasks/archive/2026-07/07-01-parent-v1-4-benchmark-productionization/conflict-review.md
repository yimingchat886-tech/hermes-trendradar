# Conflict Review: v1.4 Benchmark Productionization

## Compared Sources

- User request: create parent task v1.4 and child tasks.
- Current PRDs: `docs/PRD/releases/PRD_v1.4_split_index.md`, `PRD_v1.4_Hermes_Runtime_and_Profiles.md`, `PRD_v1.4_Codex_CLI_Implementation.md`.
- Current repo behavior: proof modules under `hermes_benchmark/`; no packaged CLI, SQLite, production runner, live Feishu writer, or real profile source.
- Prior gap report: `docs/reports/repo-completion-gap-report.md`.

## Conflicts

| Conflict | Sources | Impact | Recommendation | Decision |
|---|---|---|---|---|
| Missing Hermes runtime/profile PRD | Feasibility review vs current docs | Previously blocked profile/auth interpretation. | Include runtime/profile PRD in parent source set. | resolved |
| Feishu live scope could imply table 6/7/9 writes | Codex PRD vs current dry-run table mapping | Generic live writes would overbuild and violate current user decision. | v1.4 live only writes table 4/status boundaries. | resolved |
| Account source could imply Feishu table 3 read | v1.4 future path vs current user decision | Feishu-read account source is not needed for v1.4. | Use manually edited local profile/config files. | resolved |
| Transcription threshold could be attempted before runner exists | Runtime metrics vs current implementation | Threshold smoke would be noisy and premature. | Create batch runner child first, threshold child second. | resolved |

## Missing Requirements

- No global RTM file is updated yet; this parent owns `rtm-delta.md` until implementation evidence exists.
- No Oracle review has been run for this parent draft yet; budget records it as required before confirmation.

## Ambiguities

- Exact production profile file locations and whether examples are committed should be finalized in child 2.
- Exact Feishu table 4/status field allowlist should be finalized in child 8 from the profile PRD and actual Base mapping.

## User Requirement Challenge

| Requirement | Risk | Proposed rewrite | Needs confirmation |
|---|---|---|---|
| Create all child tasks from v1.4 | Too broad if interpreted as implementation now. | Create planning artifacts only; each child implementation still needs PLAN confirmation. | no |

## Decision Log

- Use staged overlay because this is T3 parent/child productionization work.
- Keep child count to nine; avoid splitting every test type into a separate task.
- Keep batch runner and threshold smoke separate because the user explicitly approved that order.
