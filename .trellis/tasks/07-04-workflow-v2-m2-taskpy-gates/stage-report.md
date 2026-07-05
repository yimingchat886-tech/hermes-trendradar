# Stage Report: M2 task.py tiers and gates

## Acceptance

- [x] WV2-M2-REQ-001: v2 tiered create/templates and metadata work.
- [x] WV2-M2-REQ-002: validate, archive, and soft-archive gates block bad state before mutation.
- [x] WV2-M2-REQ-003: G3 and G5-WARN are mechanized and verified.

## Verification

- `python3 -m py_compile .trellis/scripts/task.py .trellis/scripts/common/task_store.py .trellis/scripts/common/task_context.py .claude/hooks/claim_guard.py .claude/hooks/scope_gate.py tests/trellis/test_task_v2.py` — pass.
- `TMPDIR=/tmp python3 -m pytest tests/trellis/test_task_v2.py tests/trellis/test_state_machine.py` — pass, 13 tests.
- `python3 ./.trellis/scripts/task.py create --help` — pass; shows `--tier`, `--owner`, `--touches`.
- `python3 ./.trellis/scripts/task.py soft-archive --help` — pass.
- `.trellis/scripts/install_hooks.sh` — pass; installed `.git/hooks/pre-commit`.
- G3 pre-commit temp-index smoke — pass: missing marker blocked with exit 2, matching marker allowed, changed staged fingerprint blocked with exit 2.
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-04-workflow-v2-mechanization` — pass.
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-04-workflow-v2-m2-taskpy-gates` — pass.
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-04-workflow-v2-m3-pr-gates` — pass.
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-04-workflow-v2-m4-board` — pass.
- `git diff --check` — pass.
- `git diff --no-index --check /dev/null <new-file>` over all untracked files — pass.
- `npx gitnexus detect-changes --repo "Hermes stock"` — ran; GitNexus reported low risk but only mapped tracked `CLAUDE.md` symbols, so direct tests/diff checks are the source of truth for new files.
- Ponytail review — pass after deleting the unused legacy PRD skeleton and test-only fallback template code.

## Scope Notes

- M3, M4, M5, and M6 remain out of scope.
- No push or archive has been performed; user requested local git commit only.

## User Completion Signal

- Raw signal: 把 M2 用 c34665a 补 soft-archive
- Received at: 2026-07-04T23:44:01Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: no
