# Subphase Report: v1.4 Benchmark Productionization

## Status

Closeout completed for the approved v1.4 cleanup decision on 2026-07-04.

## Created Scope

- Parent task: `07-01-parent-v1-4-benchmark-productionization`
- Original child count: 9
- Completed implementation slices: 8 (`child 1` through `child 7`, plus added `child 6a`)
- Cancelled slices: 2 (`child 8`, `child 9`)
- Source PRDs:
  - `docs/PRD/releases/PRD_v1.4_split_index.md`
  - `docs/PRD/releases/PRD_v1.4_Hermes_Runtime_and_Profiles.md`
  - `docs/PRD/releases/PRD_v1.4_Codex_CLI_Implementation.md`

## Key Decisions Captured

- v1.4 live writes are table 4/status-only.
- Local profiles/config files are manually editable inputs for v1.4.
- Batch transcription runner precedes the threshold smoke.
- Child 6a was added midstream to localize and retain the approved 99 Douyin media files before the child 6 transcription smoke.
- Child 8/9 were cancelled at closeout; their requirements remain in the PRD set for future workflow v2 rebuild.

## Verification

Closeout evidence is recorded in `rtm-delta.md` and `child-task-index.md`.

## Closeout Summary

- Completed: CLI contract skeleton, profile/local config, SQLite state, Douyin collection runner, Whisper batch runner, 99-item media localization, 99-item transcription smoke, and Hermes handoff package.
- Cancelled: Feishu limited-live table 4 and production hardening/E2E dry run.
- Not claimed: scheduler, table 6/7/9 live writes, generic Feishu writer, raw transcript/video writes to Feishu, or the original 100-queued wording beyond the approved 99-item current smoke.

## Closeout Review (Codex)

Role: rescue review against `rtm-delta.md` and child `stage-report.md` evidence.

| Requirement | 漏做哪条 | 超做哪条 | Conclusion |
|---|---|---|---|
| P14-REQ-001 | none | none | Parent PRD, child index, and scope guard exist. |
| P14-REQ-010 | none | none | Child 1 evidence covers CLI/package contract. |
| P14-REQ-020 | none | none | Child 2 evidence covers local profile validation and sensitive-value rejection. |
| P14-REQ-030 | none | none | Child 3 evidence covers SQLite run/dedup/artifact/error state. |
| P14-REQ-040 | none | none | Child 4 evidence covers the 10-account collection boundary; the timed-out account has deterministic error evidence. |
| P14-REQ-050 | none | none | Child 5 evidence covers the serial Whisper batch runner and transcript artifact policy. |
| P14-REQ-055 | none for the approved 99-item current smoke | Child 6a is an added support slice, not out-of-scope feature work. | Completed under the accepted 99-item closeout scope; original 100-queued wording is not claimed. |
| P14-REQ-060 | none | none | Child 7 evidence covers schema-valid Hermes handoff package refs. |
| P14-REQ-070 | intentionally not implemented | none | Deferred because child 8 was cancelled by the 2026-07-04 closeout decision. |
| P14-REQ-080 | intentionally not implemented | none | Deferred because child 9 was cancelled by the 2026-07-04 closeout decision. |

Conclusion: no real closeout gap blocks archiving the v1.4 parent. The only unfinished requirements are explicitly cancelled/deferred by the closeout decision.
