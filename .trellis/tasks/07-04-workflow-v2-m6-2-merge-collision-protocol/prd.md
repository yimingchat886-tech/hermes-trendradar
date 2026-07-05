# M6-2 merge collision protocol

## Goal

Mechanize the parallel-branch merge collision protocol and conflict checklist.

## REQ-ID

- WV2-M6-REQ-002: Parallel-branch conflict handling is mechanized with a repo-local protocol and checklist that identifies relevant task cards/owners, requires reading both stage reports, preserves both intents first, and records unavoidable trade-offs in parent governance.

## Verification Commands

- `python3 ./.trellis/scripts/conflict_checklist.py --help`
- `python3 ./.trellis/scripts/conflict_checklist.py --repo .`
- `python3 -m pytest tests/trellis -q`
- `git diff --check`

## In

- `.trellis/scripts/merge_protocol.md`.
- A small conflict checklist script that reports conflicted files, matching active cards, owners, and required evidence files.
- A simulated two-branch conflict exercise with the trade-off recorded in parent governance when both sides cannot be kept.

## Out

- Claim-guard BLOCK implementation; that is M6-1.
- Real product feature merges or unrelated conflict-resolution policy.
