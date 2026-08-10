# Task Cancellation

Cancellation is terminal on the original Task. It requires direct user
authorization and never creates a cancellation or cleanup Task.

Uncommitted task-owned changes may be discarded only when explicitly cancelled.
Existing commits and unknown/user changes remain. Cleanup reuses the same saga
for pointer, worktree, and a safely disposable local branch. Failures enter
cleanup_pending and resume in place. Cancellation never pushes or rewrites
history.

Sealed legacy records cannot be cancelled or repaired through active writers.
