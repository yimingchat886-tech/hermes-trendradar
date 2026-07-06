# Child 0: v2.0 Release PRD And PRD Map

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-000

## Goal

Create the v2.0 release PRD and update `PRD_MASTER.md` so implementation children have a release-level source of truth before code begins.

## Requirements

- C0-REQ-001: Create `docs/PRD/releases/PRD_v2.0.md` for the real analysis loop.
- C0-REQ-002: Update `docs/PRD/PRD_MASTER.md` v2.0 index row to point at the new release PRD.
- C0-REQ-003: Keep release PRD content at release level: M0-M3 goals, non-goals, gates, PRD Map, and parent/child mapping only.
- C0-REQ-004: Do not copy child execution status, stage reports, or implementation evidence into `docs/PRD/`.

## Out of Scope

- Runtime code changes.
- Creating or editing child implementation code.
- v2.1 hotspot/Twitter design.
- Live Feishu/Bitable implementation.

## Acceptance Criteria

- [ ] `docs/PRD/releases/PRD_v2.0.md` exists and links back to `PRD_MASTER.md`.
- [ ] `PRD_MASTER.md` points v2.0 at `releases/PRD_v2.0.md`.
- [ ] The release PRD explicitly keeps M3 Bitable work conditional.
- [ ] No child execution state is copied into `docs/PRD/`.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-0-v2-0-release-prd`
- `git diff --check`

## In

- Release PRD draft and master index update.

## Out

- Source code, tests, profiles, runtime config, and child implementation.
