# M4 BOARD and session start

## Goal

Expose active workflow state through a generated BOARD and shared Claude/Codex SessionStart summaries.

## REQ-ID

- WV2-M4-REQ-001: `BOARD.md` generation and shared SessionStart summaries list active cards, waiting-for-acceptance cards, recent archives, stale-task nudges, and current owner identity.

## Verification Commands

- `python3 ./.trellis/scripts/board.py`
- `python3 ./.trellis/scripts/board.py --summary --max-lines 10`
- `git diff --check`

## In

- M4-1 and M4-2 from `docs/reports/workflow-v2-m2-m6-plan.md`.
- BOARD generator, shared summary entrypoint, Claude/Codex SessionStart hooks, and task.py regeneration calls after create/archive/soft-archive.

## Out

- M2 task.py gate foundation.
- M3 PR helper and push/impact gates.
- M5 remote/CI/staging and M6 parallel-development enablement.
