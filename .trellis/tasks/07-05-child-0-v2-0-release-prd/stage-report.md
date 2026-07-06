# Stage Report: Child 0: v2.0 release PRD and PRD map

## Acceptance

- [x] `docs/PRD/releases/PRD_v2.0.md` exists and links back to `PRD_MASTER.md`.
- [x] `PRD_MASTER.md` points v2.0 at `releases/PRD_v2.0.md`.
- [x] The release PRD explicitly keeps M3 Bitable work conditional.
- [x] No child execution state, stage report, commit hash, or verification evidence was copied into `docs/PRD/`.

## Verification

- `python3 ./.trellis/scripts/task.py validate 07-05-child-0-v2-0-release-prd` -> passed.
- `git diff --check` -> passed.
- `awk '/[ \t]$/ { printf "%s:%d: trailing whitespace\n", FILENAME, NR; bad=1 } END { exit bad }' docs/PRD/releases/PRD_v2.0.md` -> passed.
- `rg -n "\[releases/PRD_v2\.0\.md\]\(releases/PRD_v2\.0\.md\)|next_release: releases/PRD_v2\.0\.md" docs/PRD/PRD_MASTER.md` -> passed.
- `rg -n "parent: ../PRD_MASTER.md|M3 Bitable gate|不少于 2 周|P20-REQ-060" docs/PRD/releases/PRD_v2.0.md` -> passed.

## Ponytail Review

- Lean already. Ship. Docs-only diff; no dependency, abstraction, config layer, or code path added.

## User Completion Signal

- Raw signal: `提交git`
- Received at: 2026-07-06T05:43:16Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: no

## CI Run / Staging Verification

- CI run:
- Workflow head_sha:
- Expected merge SHA:
- Staging URL:
- Playwright smoke:
