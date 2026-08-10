# Git Commit And Push Policy

PRD acceptance authorizes only its scoped PRD commit. Implementation stops at
VERIFIED until a direct closeout signal.

For an unchanged candidate, report exact staged-set plan, local merge/cleanup,
verification, and “Pushed: no”. Closeout stages only task-owned paths. Exclude
unrelated dirt, runtime SQLite, attempts, BOARD, task JSON, secrets, logs, and
other tasks.

“只提交” limits authority to local work commit. It does not merge, archive,
clean a worktree, or push. Push always requires a separate current-message
phrase. Never use git add -A for closeout and never auto-stash, reset, clean,
force-push, or delete an unmerged branch.
