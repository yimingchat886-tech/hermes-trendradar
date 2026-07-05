# M6-1 claim-guard BLOCK

## Goal

Upgrade G5 claim guard from WARN to BLOCK and add audited task.py claim/release ownership commands.

## REQ-ID

- WV2-M6-REQ-001: G5 claim guard runs in BLOCK mode for cross-owner active-card `touches` matches, while `task.py claim` / `release` can transfer ownership with auditable task/event evidence and an explicit override path leaves a trace.

## Verification Commands

- `python3 -m pytest tests/trellis -q`
- `printf '{"tool_input":{"file_path":"src/foo.py"}}' | TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py`
- `python3 ./.trellis/scripts/task.py claim .trellis/tasks/<tmp-child> --owner codex`
- `python3 ./.trellis/scripts/task.py release .trellis/tasks/<tmp-child>`
- `git diff --check`

## In

- `claim_guard.py` changes from `MODE = "WARN"` to `MODE = "BLOCK"`.
- `task.py claim <dir>` and `task.py release <dir>` update `owner` safely and record an audit event.
- `--override-claim` is explicit and auditable; silent cross-owner edits remain blocked.
- Focused tests or smoke checks cover block, claim, release, and override evidence.

## Out

- Merge conflict protocol and worktree trial; those are M6-2 and M6-3.
- New production dependencies or broad task-state rewrites.
