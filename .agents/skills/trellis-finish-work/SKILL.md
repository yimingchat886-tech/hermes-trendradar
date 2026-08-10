---
name: trellis-finish-work
description: "Verify one Unified Intent Loop candidate and, only after direct authority, run persisted local closeout."
---

# Finish Work

## Verify

1. Read exact task status and diff.
2. Run focused checks, then the full commands in .trellis/workflow.md.
3. Load trellis-check. The reviewer is read-only; Coordinator fixes findings.
4. Run Ponytail review and GitNexus detect_changes.
5. Confirm every blocking finding is fixed or explicitly waived and the
   authority reports VERIFIED.

## Report And Wait

Report actual changes, migration/release/sync facts, verification, advisories,
exact scoped commit plan, local merge/cleanup plan, and “Pushed: no”. Wait for
a current direct closeout phrase from protocol-phrases.md.

Do not commit, merge, archive, delete a worktree/branch, or push before that
signal.

## Authorized Closeout

For an unchanged candidate, run:

    python3 ./.trellis/scripts/task.py close       --task <task-id> --authorization-ref <current-direct-signal-ref>

The persisted saga reverifies, commits exact task paths, merges locally,
records completion, logically archives, and cleans task-owned resources.
cleanup_pending resumes in the same run. Push and all external effects remain
separate.
