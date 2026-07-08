# Stage Report: Review docs naming archive and update status

## Acceptance

- [x] `docs/README.md` created as the Hermes docs index.
- [x] Current PRD surface retained: `PRD_MASTER.md`, `PRD_v2.0.md`,
      `trending-system.md`, and `_ledger/trending-twitter-source.md`.
- [x] Historical v1.3/v1.4 PRDs physically archived and links updated.
- [x] Before/after rename table captured and implemented.
- [x] `docs/PRD/` casing unchanged.
- [x] Trellis workflow docs transferred to `/home/jym/workspace/trellis harness`
      before removal from Hermes stock.
- [x] `production-deployment-handoff.md` and `repo-completion-gap-report.md`
      updated.
- [x] Heading noise fixed in workflow/triage docs.

## Verification

- `git diff --check` from `/home/jym/workspace/Hermes stock`: passed.
- `git diff --check` from `/home/jym/workspace/trellis harness`: passed.
- New-file whitespace checks with `git diff --no-index --check`: passed for
  new Hermes files and transferred Trellis harness docs.
- Hermes docs Markdown relative-link check: passed.
- Trellis harness docs Markdown relative-link check: passed.
- `python3 -m compileall -q hermes_benchmark && python3 -m hermes_benchmark.cli --version`: passed, printed `hermes-benchmark 0.1.0`.
- `TMPDIR=/tmp python3 -m pytest -s tests -q`: passed, 75 tests.
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-08-docs-naming-archive-update-review`: passed.

Note: plain `python3 -m pytest tests -q` hit the known WSL pytest capture temp
file issue (`FileNotFoundError` in pytest capture). Re-running with
`TMPDIR=/tmp` and `-s` passed.

## Worktree Notes

- Hermes stock has only current task changes plus ignored cache directories from
  verification.
- `trellis harness` had pre-existing unrelated dirty state before this task:
  `BOARD.md` and `.trellis/tasks/07-07-add-familios-to-trellis-updater/`.
  This task only changed/added `docs/` files there.

## User Completion Signal

- Raw signal: `提交git，并归档`
- Received at: 2026-07-08
- Allows commit: yes
- Allows archive: yes
- Explicit limits: none
- Push allowed: no

## Protocol Gates

Use `.trellis/spec/project/protocol-phrases.md` for completion, commit,
archive, limit, and push wording. Do not copy the phrase table here.
