# Task And Action Sizing

One independent user intent is one top-level Task. Do not use lifecycle tiers.

Use one Compact action for a local coherent touch surface. Use a persistent
action graph for dependencies, multiple modules, high-risk boundaries, release,
migration, or target slots. Actions may be research, implement, verify,
integration, recovery, release_qualify, or sync_target.

Compact may become Delegated in the same run after cross-module scope,
consecutive failures, or independent review. Delegation never creates a child
Task, successor, PRD, branch, worktree, or archive boundary. A genuinely
independent deliverable gets its own Task and a non-lifecycle relation.
