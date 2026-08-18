# Unified Downstream Sync

## Release Gate

Select latest qualified immutable release. Explicit current-source sync builds
and semantically qualifies its managed payload in the same source Sync TaskRun
before target writes. Failure leaves every target unchanged.

Release identity binds managed files/deletions, overlay, Trellis base,
capability range, logical check catalog, schema, ownership, and semantic suite.
Source commit, task/BOARD, host, path, stdout, and runtime doctor do not
invalidate unchanged payload.

## One Sync Intent

One named multi-target request creates one source TaskRun with one slot per
target. Targets receive no Task, PRD, Child, or TaskRun. Each slot owns at most
one branch, one worktree, and one closeout partition.

## Dirty Target

Capture HEAD, index, and dirt before planning. Unrelated staged/unstaged paths
are allowed and stay byte-identical. Managed or plan-dependent overlap, ref
change, conflict, or managed preimage drift blocks before write. Never stash,
reset, clean, or discard target changes.

Apply and verify only in the target worktree. Logical checks resolve from exact
target version/capabilities; unsupported inputs fail closed. Verified slots
install the exact release manifest and immutable base/overlay artifacts in the
target Git common directory, then write content-bound adoption and receipts.

## Partial And Closeout

One failure yields partial. Successful slots remain successful; retry selects
failed/stale slots only. Named-target request authorizes plan/apply/verify.
Target commit, local merge, and cleanup wait for source closeout. Push,
deployment, and activation remain separate.
