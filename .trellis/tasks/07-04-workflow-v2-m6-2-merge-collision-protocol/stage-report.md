# Stage Report: M6-2 merge collision protocol

## Acceptance

- [x] WV2-M6-REQ-002: repo-local merge protocol and conflict checklist are implemented.
- [x] `conflict_checklist.py` lists git conflicted files, maps them to active child task cards by `touches`, prints owner/status/stage-report path, and exits nonzero when fewer than two cards match or a required stage report is missing/unreadable.
- [x] `merge_protocol.md` requires restoring both intents, preserving both first, no `git merge --abort` shortcut, no invented behavior, and parent governance for unavoidable trade-offs.
- [x] Focused tests cover child-card path matching, parent-card exclusion, single-card failure, and missing stage-report failure.
- [x] Code-spec contract added for the conflict checklist command.

## Verification

- PASS: `python3 ./.trellis/scripts/conflict_checklist.py --help`
- PASS: `python3 ./.trellis/scripts/conflict_checklist.py --repo .` -> `No conflicted files.`
- PASS: `env TMPDIR=/tmp python3 -m pytest tests/trellis -q` -> 25 passed.
- PASS: `uvx --from ruff==0.15.20 ruff check --select E9,F63,F7,F82 .trellis/scripts/conflict_checklist.py tests/trellis/test_conflict_checklist.py`
- PASS: `git diff --check`

## Changed Files

- `.trellis/scripts/conflict_checklist.py`
- `.trellis/scripts/merge_protocol.md`
- `tests/trellis/test_conflict_checklist.py`
- `.trellis/spec/project/merge-collision-protocol.md`
- `.trellis/spec/project/index.md`
- `.trellis/tasks/07-04-workflow-v2-m6-2-merge-collision-protocol/stage-report.md`
- `.trellis/tasks/07-04-workflow-v2-m6-parallel-development/governance.md`

## Conflict Exercise

- Real two-branch conflict/trade-off exercise remains a parent/main integration step.
- Reason: this worker was explicitly told not to commit, push, merge, archive, switch broader branch state, or touch the M6-3 task/worktree. Creating a real conflict safely requires the main integration session to merge the two child branches/worktrees.
- Substitute evidence in this child: focused tests simulate the checklist path-matching and missing-report failure paths without damaging repository state.

## CI Run / Staging Verification

- CI run:
- Workflow head_sha:
- Expected merge SHA:
- Staging URL:
- Playwright smoke:
