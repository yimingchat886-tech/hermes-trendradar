# Stage Report: M6-1 claim-guard BLOCK

## Acceptance

- [x] `claim_guard.py` is BLOCK mode for cross-owner active-card `touches` matches.
- [x] `task.py claim <dir> --owner <owner>` updates owner and records a state-events audit row.
- [x] `task.py release <dir>` releases ownership to `jym` by default and records a state-events audit row.
- [x] Explicit override path is auditable: hook override reason and `task.py claim --override-claim --reason` both write `override_claim` events.
- [x] Hidden dot-directory paths such as `.claude/hooks/claim_guard.py` match `touches` globs correctly.

## Verification

- PASS: `env TMPDIR=/tmp python3 -m pytest tests/trellis -q` -> 23 passed.
- PASS: `uvx --from ruff==0.15.20 ruff check --select E9,F63,F7,F82 .claude/hooks/claim_guard.py .trellis/scripts/task.py .trellis/scripts/common/task_store.py tests/trellis/test_task_v2.py tests/trellis/test_m3_workflow.py tests/trellis/test_m4_board.py`.
- PASS: `printf '{"tool_input":{"file_path":".claude/hooks/claim_guard.py"}}' | TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py; echo exit:$?` -> exit 2, BLOCK.
- PASS: `TRELLIS_OVERRIDE_CLAIM_REASON='M6-1 smoke override after timezone fix'` with the same hook payload -> exit 0 and appended `override_claim` audit event.
- PASS: `python3 ./.trellis/scripts/task.py claim .trellis/tasks/07-04-workflow-v2-m6-1-claim-guard-block --owner codex --override-claim --reason "M6-1 smoke claim"` -> appended `override_claim` audit event.
- PASS: `python3 ./.trellis/scripts/task.py claim --help && python3 ./.trellis/scripts/task.py release --help`.
- PASS: `git diff --check -- .claude/hooks/claim_guard.py .trellis/scripts/task.py .trellis/scripts/common/task_store.py tests/trellis/test_task_v2.py tests/trellis/test_m3_workflow.py tests/trellis/test_m4_board.py BOARD.md`.
- PASS: `python3 -m compileall -q .claude/hooks/claim_guard.py .trellis/scripts/task.py .trellis/scripts/common/task_store.py`.

## Impact / Scope

- GitNexus index was refreshed with `node .gitnexus/run.cjs analyze --index-only --name "Hermes stock"`.
- `gitnexus impact` could not resolve hidden-directory symbols in `.trellis/` / `.claude/`; impact risk is treated as unknown/medium and covered with focused Trellis tests plus real hook smoke.
- `gitnexus detect-changes --scope unstaged --repo "Hermes stock"` reported low risk before M6 edits, covering only pre-existing dirty docs/BOARD files.
- Ponytail review: removed speculative string scanning from the override parser; no remaining dependency, abstraction, or new layer.

## Changed Files

- `.claude/hooks/claim_guard.py`
- `.trellis/scripts/task.py`
- `.trellis/scripts/common/task_store.py`
- `.trellis/spec/project/claim-guard-ownership.md`
- `.trellis/spec/project/index.md`
- `tests/trellis/test_m3_workflow.py`
- `tests/trellis/test_m4_board.py`
- `tests/trellis/test_task_v2.py`
- `BOARD.md`

## Commit / Push

- User completion signal: `验收，提交git`.
- Allows commit: yes.
- Allows soft archive: yes.
- Commit: pending.
- Push: no.

## CI Run / Staging Verification

- CI run:
- Workflow head_sha:
- Expected merge SHA:
- Staging URL:
- Playwright smoke:
