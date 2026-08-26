# Unified Intent Loop Runtime

## Authority

The sole lifecycle authority is <git-common-dir>/trellis/harness.sqlite3.
It uses SQLite WAL, foreign keys, synchronous=FULL, busy timeout,
BEGIN IMMEDIATE, mode 0600, atomic schema initialization, and hash-chained
idempotent events. Linked worktrees open the same database.

It stores tasks, bindings, relations, runs, actions, attempts, findings,
checks, reviews, target slots, closeout steps, events, projections, release
bindings, and compact legacy records.

## Task And Binding

A Task represents one intent and has no light/parent/child tier. Its directory
is stable for life; archive changes archive_state only. Independent work may
relate through depends_on, part_of, or related_to without lifecycle propagation.

A run ID stays stable through attempts, review, resume, closeout, and material
PRD revision. Accepted revisions append immutable binding generations to the
same run. Old generations remain evidence.

## Execution

Loop is default. single allows one attempt and may later resume as Loop.
Actions hold dependencies, REQ IDs, touches, logical checks, risk, and status.
Harness stores graph/results but never implements Codex spawn/wait scheduling.
Read actions may overlap; overlapping write actions serialize.

Attempt limits are soft 4 and hard 8. Two unchanged root-cause fingerprints
without new evidence enter human_blocked. Failures, findings, drift, archive
faults, and cleanup faults never create another Task, run, branch, or worktree.

## Checks And Review

Every attempt binds deterministic checks. Check Agent is read-only and runs only
for a complete deterministic candidate or high-risk boundary. Findings have
stable IDs and closure evidence. Only correctness, security, data loss,
accepted REQ, public compatibility, and invalid proof may block VERIFIED.

One candidate family gets at most two semantic model reviews. An accepted
binding generation starts a new family; evidence-only fixes inside that
generation stay in the same family and use checks plus delta closure instead
of another model review. A third review in one family is rejected and the run
becomes human_blocked.

## Projections

Task JSON, BOARD, pointer, archive view, release index, and status text are
rebuildable. Missing or stale projections never change or block authority.
SQLite terminal rows remain. Git tracks PRD, accepted binding when needed,
material decisions, run summary, and release/target/migration receipts.
Runtime SQLite and intermediate attempts are never committed.

## Closeout

One unchanged VERIFIED signal runs the persisted 13-step saga: freeze, drift
classify, reconcile, reverify, prepare artifacts, scoped commit, local merge,
completion, logical archive, pointer/runtime cleanup, worktree removal, merged
branch removal, and final projection. Steps have idempotency keys and evidence;
retry begins at the first incomplete step.

Non-overlapping base/projection/environment drift continues. Code conflicts,
managed overlap, accepted semantics, public API, or behavior changes pause.
An accepted binding revision may discard pre-effect closeout evidence only
before source scoped commit has a commit plan, staged change, commit, or later
effect. It then requires a new candidate verification and closeout signal.
Target partitions or any started Git effect reject the revision without
rewriting history.
After completion, cleanup failure retains completed with cleanup_pending.
Closeout never pushes, publishes, deploys, or activates.

## Cutover

Dry-run inventories exact legacy paths/digests without writes. Apply first
creates a byte-exact backup, initializes the shared DB, seals legacy records,
imports only bootstrap Task/binding/action graph, and writes report/marker.
It never imports legacy attempts, runtime, receipts, archive phase, or pointer.

After marker, partial rollback is refused. Legacy commands fail before mutation
with LEGACY_WRITE_DISABLED. Only compact read-only legacy inventory remains.
Terminal or archived authority wins over stale linked-worktree projections for
the same legacy task directory.
