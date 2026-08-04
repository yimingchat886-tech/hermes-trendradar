# Loop v1 Runtime Authority

## Scope

This contract governs the bootstrap SQLite authority in
`.trellis/scripts/loop_v1/ledger.py`, `context.py`, `scheduler.py`,
`worker_commit.py`, and `integration.py`. It implements
`LV1-LED-001` through `LV1-LED-006`, `LV1-AUTH-003` through `LV1-AUTH-007`,
`LV1-CTX-001` through `LV1-CTX-005`, and `LV1-SCH-001` through `LV1-SCH-006`
plus `LV1-INT-001` through `LV1-INT-009`, `LV1-REC-001` through `LV1-REC-007`,
and `LV1-REV-001` through `LV1-REV-005` plus `LV1-EVI-001` through
`LV1-EVI-006`. When `taskrun_v1.new_code_tasks: true`, these APIs remain the
authority for existing Loop-owned runs only; new loop execution uses TaskRun
and never initializes this ledger.
The directly invoked bootstrap APIs may create isolated worktrees, validate a
returned candidate, create its reviewed local child commit, integrate that
commit through an isolated no-ff candidate, and reconcile local crash windows.
They do not admit a Loop parent, dispatch a model worker, push, or replace
current-Trellis lifecycle authority. The final-gate API may merge the exact
approved integration HEAD into local `main` and archive only the Loop parent
ledger after exact retained-start or strict direct approval and post-merge
verification.

## Location And Identity

```text
.trellis/.runtime/loop-v1/parents/<parent-run-id>/ledger.sqlite3
```

- `parent-run-id` is a stable 1-128 character ASCII identifier using letters,
  digits, `.`, `_`, or `-`.
- The path is deterministic, unique per parent run, and covered by the existing
  `.trellis/.gitignore` `.runtime/` rule.
- All children of that run receive the same `ParentLedger.reference()` value.
  They do not create databases or receive a writer lease.
- SQLite uses WAL, foreign keys, `synchronous=FULL`, a busy timeout, and
  `BEGIN IMMEDIATE` writer transactions.

## Public API

```python
ledger, lease = ParentLedger.initialize(
    repo_root,
    parent_run_id,
    selector="loop_v1",
    writer_id="parent-process-id",
    start_gate_ref="approved-request-id",
)

ledger.prepare_operation(
    lease,
    operation_id="stable-id",
    kind="local-effect",
    input_fingerprint="sha256:...",
    intent={"exact": "recovery input"},
)

ledger.advance_operation(
    lease,
    operation_id="stable-id",
    expected_phase="prepared",
    phase="effect_observed",
    output_fingerprint="sha256:...",
    outcome={"result": "observed"},
)

request = create_start_request(
    ledger,
    lease,
    request_id="stable-request-id",
    envelope=complete_envelope,
)
approve_start_request(
    ledger,
    lease,
    request_id=request["request_id"],
    request_digest=request["request_digest"],
    response_identity="direct-user-response-id",
    response_at="2026-07-13T18:00:00Z",
    direct_user_action=True,
)

record_context_revision(
    ledger,
    lease,
    request_id=request["request_id"],
    revision_id="context-1",
    reason="initial approved context",
    context=canonical_context,
)

decision = schedule_ready(
    ledger,
    lease,
    decision_id="stable-schedule-id",
    live_capacity=3,
)

release_child_resources(
    ledger,
    lease,
    operation_id="stable-release-id",
    child_id="scheduled-child-id",
    reason="child work reached a release boundary",
)
```

- Reopening an existing database through `initialize` requires the exact
  active `existing_lease`; matching a writer name is insufficient.
- `rotate_writer` requires the current lease, a stable operation ID, and the
  next writer identity. It increments epoch and replaces the fence token in one
  transaction. Exact replay returns the already committed active lease.
- `get_operation`, `authority_snapshot`, `authority_digest`, and
  `projection_status` are read-only.
- `freshness_token` and `assert_fresh` provide the same exact epoch/context/
  requirement/envelope/graph/dependency/base/tree comparison at dispatch,
  result, review, commit, integration, and resume boundaries.
- The fence token is excluded from object repr and file projections. Only its
  digest appears in authority snapshots.

## Start Authorization

- A generated request must contain exactly the complete start-envelope fields
  defined by the master PRD. Missing or unknown fields fail before a record is
  written.
- The request digest binds its stable identity, complete canonical envelope,
  and envelope digest. Reusing its identity with changed content is rejected.
- Approval requires that exact request digest, a direct-user-action assertion,
  and a durable response identity/time. Tone, a worker message, prior approval,
  or a changed digest cannot approve it.
- The original request is immutable. A normative envelope change uses a new
  request identity instead of editing or silently rebinding the old request.

## Canonical Context And Graph

- Each context revision records a monotonic sequence, previous digest, reason,
  approved envelope digest, requirement digest, graph digest, dependency
  digest, canonical JSON, and timestamp.
- Required/optional classification, acceptance, dependencies, base branch, and
  base HEAD remain bound to the approved envelope. Changes to them require
  intervention rather than an autonomous revision.
- A semantic child-graph change inside approved requirements, touches, and
  resources is committed without another gate and gets a separate visible
  graph-revision record. Out-of-envelope changes fail without mutation.
- The exhaustive routine intervention signals are scope/resource expansion;
  dependency/schema/security/secret boundary change; unknown effects, data
  loss, or overlapping user dirt; exhaustion of three same-problem rounds; and
  unresolved product semantics. Explicit pause/resume/cancel remains separate.

## Child Packets And Results

- `issue_child_packet` derives acceptance, dependencies, resources, scope,
  base/tree identity, prohibitions, and freshness identities from authority.
  Its input cannot add arbitrary context fields.
- Context slices are selected by ID. `secret_ref` slices contain fingerprints
  only and cannot enter a child packet; unknown fields and transcript-like
  additions are rejected.
- A child result has an exact schema for actual touches, diff/result tree,
  commands and outcomes, coverage, risks/findings, artifacts, and all freshness
  digests. Required packet tests must be present and passed.
- Result acceptance rechecks freshness and scope inside the fenced transaction.
  Stale, incomplete, unknown-field, forbidden-path, or over-scope input rolls
  back without an authority change. Exact accepted-result replay is durable,
  but later commit/integration must run its own current freshness check.

## Requirement Scheduling And Resources

- Product progress and final readiness are derived from canonical requirement
  coverage. Required/optional classification never belongs to a child ID, so
  an in-envelope context revision may replace, split, or merge child nodes
  without changing the requirement contract or requesting another approval.
- Required coverage cannot be marked omitted. Optional omission first requires
  a canonical context revision, then a stable omission operation containing its
  rationale and final-disclosure impact; optional requirements with dependents
  cannot be omitted.
- Ready order uses required rank, dependency count, requirement ID, and child ID
  as stable tie-breaks. Deterministic integration selection uses the committed
  graph and child states, never result arrival or worker completion time.
- Requirement dependencies assigned to the same child are co-delivered work,
  not pre-dispatch prerequisites. Dependencies outside that child remain
  blocking until their canonical requirement coverage is `covered`.
- Effective dispatch is capped by the minimum of three, the approved parent
  cap, approved worker capacity, supplied live capacity, and conflict-free ready
  work. Existing acquired claims and active child work consume that capacity.
- Path claims are normalized as exclusive path resources. Approved non-path
  resource aliases normalize case-insensitively to one canonical key; resources
  are either exclusive with capacity one or shared with a positive capacity.
  Overlapping paths, exclusive claims, and exhausted shared capacity conflict.
- A missing touch/resource declaration or an ambiguous path/resource claim
  reduces scheduling to one child and records a serial reservation. Claims are
  released only through a stable, fenced release operation.
- Dependency cycles, uncovered required requirements without a child, and
  no-progress deadlocks produce durable pause decisions. Safe graph repair may
  remove only dependency edges whose dependency requirements are already
  covered; the resulting graph is recorded as a normal canonical context
  revision. Other cases remain paused for later reconciliation or intervention.

## Scheduler API Contract

### 1. Scope / Trigger

Use these APIs only after the exact start envelope is approved and canonical
context exists in the same parent ledger. They calculate and record scheduling,
coverage, resource, omission, integration-selection, and graph-repair decisions;
they do not execute workers or mutate Git.

### 2. Signatures

```python
schedule_ready(ledger, lease, *, decision_id: str, live_capacity: int) -> dict
release_child_resources(
    ledger, lease, *, operation_id: str, child_id: str, reason: str
) -> dict
record_optional_omission(
    ledger,
    lease,
    *,
    operation_id: str,
    requirement_id: str,
    rationale: str,
    impact: str,
) -> dict
requirement_progress(ledger) -> dict
deterministic_integration_selection(ledger) -> dict
repair_or_pause_graph(
    ledger, lease, *, request_id: str, revision_id: str
) -> dict
```

`schedule_ready`, release, omission, repair, and pause writes require the active
`WriterLease`. Progress and integration selection are read-only.

### 3. Contracts

- Schedule input binds the stable decision ID, current freshness token, supplied
  live capacity, and source ledger position. Its outcome includes status/reason,
  stable ready order, selected children, conflicts, declaration issues, cycle
  evidence, effective/hard capacity, coverage progress, and source position.
- Acquired claims persist canonical `path:<touch>`,
  `resource:<canonical-key>`, or `scheduler:serial` keys with mode, total
  capacity, child identity, state, and timestamp. Release returns exact claim
  IDs changed to `released`.
- Omission returns the requirement, rationale, disclosure impact, bound context
  digest, and `omitted` status. Graph repair returns removed dependency edges and
  the committed context revision; an unsafe repair returns a durable pause.
- There are no scheduler environment variables or alternate authority stores.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Missing approved envelope or canonical context | `FreshnessError` |
| Required coverage marked omitted | `InterventionRequired` |
| Alias collision, invalid mode/capacity, or invalid coverage state | `ContextError` |
| Stable decision/operation/revision ID reused with different input | `OperationConflict` |
| Required or dependency-bearing optional omission | `SchedulerError` |
| Cycle, uncovered required coverage without a child, or no-progress deadlock | Durable `paused` outcome |
| Missing/ambiguous declarations | At most one selected child plus serial claim |

All write-side errors roll back the fenced SQLite transaction.

### 5. Good / Base / Bad Cases

- Good: three disjoint ready children with current context and sufficient live
  capacity are selected in stable order and receive canonical claims.
- Base: ready work exceeds capacity or conflicts with acquired claims; the
  scheduler selects the deterministic conflict-free prefix and records why the
  rest was not selected.
- Bad: a child uses a wildcard claim that cannot be normalized safely; the
  scheduler never dispatches it in parallel and records serial fallback.

### 6. Tests Required

- Assert coverage progress and final readiness from requirement states, including
  required-omission rejection and optional rationale/disclosure.
- Assert stable ready and integration order, replay equality, cap three, live
  capacity, aliases, path/exclusive/shared conflicts, and claim release.
- Assert missing/ambiguous serial fallback, replacement/split/merge coverage,
  safe satisfied-edge repair, and stable cycle/deadlock pause replay.
- Re-run B1/B2 focused tests and the complete current-Trellis suite while Loop
  admission remains disabled.

### 7. Wrong vs Correct

Wrong: infer product progress from completed child count, dispatch by result
arrival time, or retry a paused repair ID against changed context to obtain a
different outcome.

Correct: derive progress from requirement coverage, select from committed graph
order, and replay the first durable schedule/repair outcome for every stable ID.

## Worker Commit API Contract

### 1. Scope / Trigger

Use these APIs only after the start envelope is approved, canonical context and
a child packet are current, and the parent holds the active `WriterLease`. The
parent owns every worktree/branch, validation, review receipt, staging action,
and local child commit. Workers receive neither the writer lease nor permission
to stage, commit, update refs, use canonical `main`, use an integration
worktree, or access a sibling worktree.

### 2. Signatures

```python
scan_repository_dirt(repo_root: Path) -> dict
create_child_worktree(
    ledger,
    lease,
    *,
    operation_id: str,
    child_id: str,
    packet_id: str,
    worktree: Path,
    branch: str,
    integration_worktree: Path | None = None,
    sibling_worktrees: Sequence[Path] = (),
) -> dict
observe_worker_candidate(worktree: Path, *, base_head: str) -> dict
validate_child_candidate(
    ledger,
    lease,
    *,
    validation_id: str,
    result: Mapping[str, object],
    worktree: Path,
) -> dict
record_precommit_review(
    ledger,
    lease,
    *,
    review_id: str,
    validation_id: str,
    reviewer_identity: str,
    verdict: str,
    required_findings: Sequence[Mapping[str, object]],
    advisory_findings: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
) -> dict
commit_reviewed_candidate(
    ledger,
    lease,
    *,
    operation_id: str,
    review_id: str,
    message: str,
    author_name: str,
    author_email: str,
) -> dict
```

There are no worker-commit environment keys or alternate authority stores.
Declared parent checks execute in the child worktree through `/bin/sh`, have a
300-second per-command timeout, and persist output digests rather than output.

### 3. Contracts

- Dirt evidence contains normalized status/path metadata and a canonical digest;
  it never reads or records file contents. Worktree creation requires the exact
  approved dirt digest, rechecks scope overlap and canonical-branch drift, and
  starts the parent-owned child branch at the packet `execution_base`
  HEAD/tree. The packet retains its immutable source `base` separately.
  Ordinary children execute from that source base; a final-review repair
  executes from the exact current integration HEAD/tree.
- Candidate observation builds the proposed tree with a temporary Git index.
  It records actual tracked/untracked paths, real staged paths, branch/HEAD,
  base/result tree IDs, and a binary-diff digest without staging the real index.
- Parent validation requires the exact accepted structured-result receipt and
  current epoch/context/requirement/envelope/graph/dependency/base identities.
  It independently rejects worker commits or staging, compares actual
  touches/tree/diff/execution-base/coverage, reruns every packet check, and
  re-observes the candidate after checks before recording
  `candidate_validated`. Source-base freshness still protects the canonical
  start envelope; it is not substituted for the repair execution base.
- A pre-commit reviewer identity is `model:<approved-surface>`. Its durable
  verdict binds the validation tree, scope, coverage, worker self-check digest,
  parent-check digest, and result digest. Every advisory finding has exactly one
  `accepted`, `deferred`, or `not_applicable` disposition; required findings
  cannot receive a passing verdict, and a failed verdict must name at least one
  required finding.
- The parent commits only a current passing review. It stages exactly the
  validated paths, requires the staged tree to equal the reviewed tree, creates
  a commit whose parent is the validated base, and advances only the child ref.
  Canonical `main` remains unchanged. The exact commit/tree/ref outcome and
  child `committed` state are recorded in the existing ledger/Git tables. The
  operator then removes the clean child checkout while retaining its branch,
  commit, tree, and ledger authority.
- Stable worktree, validation, review, and commit identities replay only when
  durable authority and current Git state still match. After the child commit
  boundary, replay accepts the checkout only when it is live and exact or when
  both its path and registration are absent and the canonical branch/commit/tree
  still match. Partial path/registration state fails closed. An observed-but-not-
  authoritative Git operation is left intact for B5 reconciliation, never
  guessed, repeated, cleaned, or rolled back.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Worktree overlaps canonical, integration, sibling, or another registration | `GitStateError` |
| Dirt fingerprint changed, or canonical dirt/drift overlaps child scope | `DirtOverlapError` |
| Packet/result/context/base/epoch identity is stale | `FreshnessError` |
| Worker staged/committed, actual scope differs, or parent check fails | `ParentValidationError` |
| Reviewer surface is unapproved, dispositions are incomplete, or tree changed | `ReviewError` |
| Stable operation ID is reused with different input/outcome | `OperationConflict` |
| Git effect is unresolved or no longer matches durable authority | `GitStateError` and later B5 reconciliation |
| Scratch is foreign, partially registered, drifted, secret-like, or has unclassified dirt | Retain it and pause for intervention |

No validation failure stages, commits, stashes, cleans, reverts, moves, deletes,
or rewrites canonical user dirt.

### 5. Good / Base / Bad Cases

- Good: a worker changes one tracked and one untracked file inside `src/**`,
  returns exact structured evidence, parent checks pass, the approved model
  review is green, and the parent creates one child commit matching that tree.
- Base: unrelated canonical dirt existed at approval. Its metadata-only digest
  remains unchanged, so isolated child work proceeds while canonical bytes and
  `main` remain untouched.
- Bad: the worker stages a file, commits, reports a different tree/path, changes
  the candidate after review, or overlaps current canonical dirt. The parent
  records no commit and fails closed at the corresponding boundary.

### 6. Tests Required

- Assert isolated parent-owned worktree/branch creation, protected-worktree
  rejection, worker permission flags, and exact replay.
- Assert metadata-only non-overlap dirt handling and byte-preserving overlap
  rejection after canonical dirt or branch drift changes.
- Assert tracked/untracked candidate observation uses no real staging; reject
  worker staging/commit, stale context, false tree/touch evidence, and failed
  parent reruns without advancing child state.
- Assert review tree/evidence binding, approved model identity, required finding
  blocking, and one disposition per advisory finding.
- Assert post-review edits block commit; a green commit stages exact paths,
  equals the reviewed tree, has the validated base parent, leaves canonical
  `main` unchanged, records durable Git authority, releases the child worktree,
  and replays from canonical Git authority without a second commit or checkout.
- Re-run B1-B3 focused tests and the complete current-Trellis suite while Loop
  admission remains disabled.

### 7. Wrong vs Correct

Wrong: trust the worker's branch name, reported test status, or description;
stage the whole worktree; review a tree that can change; or commit from a worker
process.

Correct: independently observe and test the registered worktree, bind one model
review to its exact tree/evidence, then let the fenced parent stage only the
validated paths and commit exactly that tree from the validated base.

## Serial Integration And Recovery API Contract

### 1. Scope / Trigger

Use these APIs only for the deterministic first child selected by the scheduler
after its exact reviewed commit has durable `git_operations` authority. The
fenced parent owns the candidate worktree, checks, integration ref, recovery,
pause/resume, and cancel records. The authoritative integration ref must be a
full `refs/heads/...` name and must not be checked out in any worktree.

### 2. Signatures

```python
prepare_integration_candidate(
    ledger, lease, *, integration_id, child_id, integration_ref,
    candidate_worktree, candidate_branch, checks, author_name, author_email,
    problem=None,
) -> dict
build_integration_candidate(ledger, lease, *, integration_id) -> dict
advance_integration_ref(ledger, lease, *, integration_id) -> dict
acknowledge_integration(ledger, lease, *, integration_id) -> dict
record_problem_attempt(
    ledger, lease, *, problem_id, round_number, operation_phase, root_condition,
    requirement_ids, diagnosis, action, commands, artifact_ids, result,
) -> dict
open_recovery_generation(
    ledger, lease, *, operation_id, problem_id, diagnosis, action,
    integration_ref, integration_head, integration_tree_id,
    source_context_digest, affected_child_ids,
) -> dict
pause_parent(
    ledger, lease, *, operation_id, reason, requested_by,
    requires_human_resume,
) -> dict
resume_parent(
    ledger, lease, *, operation_id, new_writer_id, authority_identity,
    direct_user_action, tool_receipt, available_resources,
) -> tuple[dict, WriterLease]
cancel_parent(
    ledger, lease, *, operation_id, reason, actor, requested_at,
    direct_user_action, superseded_by=None,
) -> dict
reconcile_integration_history(ledger, lease, *, integration_ref) -> dict
```

There are no integration/recovery environment keys or alternate stores. Git
effects use direct local Git CLI calls and the existing SQLite authority.

### 3. Integration Transaction

- Prepare rechecks current context, approved base/tree/envelope, complete child
  scope against canonical dirt, reviewed commit authority, deterministic order,
  unattached integration ref, and isolated candidate path. Its durable intent
  binds expected-old ref/tree, exact child commit/tree, checks, coverage, and
  candidate identity before a Git effect.
- Candidate construction starts from expected-old, merges the exact child commit
  with `--no-ff`, requires parents `[expected-old, child-commit]`, runs every
  declared check, and records merge/check/tree evidence. A merge/check failure
  commits failed evidence and problem round 0 without moving the integration ref.
- A green candidate remains `effect_observed` until the parent reruns checks and
  advances the unattached ref with `git update-ref <ref> <candidate> <expected>`.
  An unexpected ref pauses for human reconciliation; it is never guessed or
  overwritten.
- Acknowledgement proves the actual ref, ancestry, candidate tree, and durable
  intent before recording one integrated Git authority row, changing only the
  committed child to `integrated`, releasing its resource claims, marking its
  requirements covered in canonical context, and rebuilding projection. An
  acknowledged candidate remains only while final checks still need that exact
  checkout; otherwise it is removed before another candidate is created, and
  the final candidate is removed after its checks are durably recorded.
- Removing an integrated child commit from current integration history retains
  the old Git evidence but marks that child and graph dependents `invalidated`,
  returns their requirements to `uncovered`, and records the observed ref/tree.

### 4. Recovery And Control

- Pause blocks new dispatch, result acceptance, review/commit, candidate, and ref
  work while preserving refs, branches, worktrees, candidates, ledger rows, and
  the exact unresolved-operation list. It performs no cleanup or rollback.
- Routine bounded replacement records the problem and decision, consumes the
  worker dispatch/resources, and then removes the superseded checkout before a
  replacement checkout is issued. Forced removal is limited to a current-run
  worker result whose complete re-observed paths, diff, tree, branch, HEAD, and
  approved touches equal durable evidence and contain no staged or secret-like
  path. A failed parent-only candidate merge may run exact `git merge --abort`
  only when `MERGE_HEAD`, base HEAD/tree, child commit, and failed outcome all
  match, then uses ordinary worktree removal. Unknown dirt or cleanup ambiguity
  pauses and retains the checkout.
- Cleanup never deletes a branch/ref or uses broad prune. It is idempotent only
  when both path and registration are absent and canonical Git objects still
  match. No cleanup table, manifest, background process, or retirement lifecycle
  exists.
- Resume validates direct authority when required, receipt, approved resources,
  and overlapping canonical user dirt before any writer rotation. Overlapping
  committed main drift remains blocked except at a closeout-safe point where
  every unresolved operation kind has a deterministic reconciliation handler,
  every current child is integrated, coverage is final-ready, and the recorded
  integration head is retained as an ancestor of current `main`. The resume
  operation binds that main-state and ancestry proof. When the exact
  qualification-rotation exception below applies, a committed resume preflight
  also binds its canonical proof digest before writer rotation. Resume then
  rotates writer/fence and epoch before reconciliation. Old child leases become stale and acquired
  claims are released. Parent status returns to `authorized` only after every
  old prepared or observed effect resolves to exactly no-effect or the proved
  existing effect. Unknown operation kinds reject before rotation.
- Paused operator status exposes the same read-only safe-point decision:
  eligibility, whether the closeout exception is required, named failed
  predicates, and predicate values. An accepted qualification rotation also
  exposes its canonical proof and digest. Non-applicable or short-circuited
  predicates are `null`. It exposes no dirt paths, operation contents, or
  writer fence and performs no reconciliation or authority write. Sanitized
  unresolved evidence contains only operation ID, kind, phase, and epoch; the
  separate
  `unresolved_operations_clear` and
  `unresolved_operations_reconcilable` predicates distinguish unfinished work
  from handler-backed recovery.
- Child commit recovery accepts only a branch commit with the intended tree and
  exact base parent. Integration recovery reruns candidate checks, advances an
  unchanged expected-old ref at most once, or acknowledges a candidate already
  present. Ambiguity leaves the parent paused.
- Final-local-merge recovery accepts a prepared or observed operation only when
  exactly one approved merge is retained in current canonical history. A branch
  change requires exactly one identical-tree publication merge that directly
  retains the approved merge. Later canonical commits are allowed only when
  their changed paths do not overlap the original envelope touches, except for
  one exact verified `source_release_qualification_rotation` under
  `qualification_rotation_chain_v2`. That proof
  requires an ordered, non-empty chain of descendant binding commits. Every
  config/receipt-touching commit in the interval must be one single-parent link
  whose qualification projection changes only `.trellis/config.yaml` and one
  newly added hash-addressed receipt; only `loop_v1.qualification_receipt` and
  `loop_v1.qualification_receipt_digest` may change in config bytes. Every
  link starts from the preceding config receipt, verifies strictly at its own
  commit, and names its first-parent runtime commit; the terminal link must
  equal current config and verify for both source purposes. A binding commit
  may carry only independently classified `A`/`M`/`D` paths that do not match
  any original envelope touch; their status/path pairs and the exact envelope
  touch patterns are proof-bound. Receipt identities, Git ancestry/blob
  identities, and the immutable non-revoked historical execution binding must
  agree. The same read-only instance proof gates writer rotation and
  post-rotation recovery;
  canonical dirt may be retained only outside the original envelope and its
  path/status digest must remain unchanged through recorded post-merge checks.
  Fresh final merge remains fully clean-only. Recovery records the exact merge,
  publication point, retained canonical HEAD/branch, partitioned descendant
  paths, qualification proof/digest, descendant-drift digest, and retained-dirt
  digest separately. Reconciliation recomputes the preflight proof byte-for-byte;
  final-merge replay and archive do the same. Ambiguous publication, any
  unproved overlap, proof drift, overlapping dirt, dirt path/status mutation,
  or multiple candidates remains unresolved.

#### Scenario: Canonical-descendant final-merge recovery

1. **Scope / Trigger**: Use only for a paused prepared or effect-observed
   `final_local_merge` retained after canonical publication and later commits.
2. **Signatures**:
   `final_merge_recovery_eligible(repo, operation, allowed_touches,
   execution_binding=...) -> bool` gates
   `recover_final_merge(ledger, lease, operation,
   expected_qualification_rotation_proof=...) -> dict`;
   `reconcile_parent(..., expected_qualification_rotation_proof=...)` carries
   the safe-point proof across writer rotation.
3. **Contracts**: The operation supplies exact branch/base/integration/merge
   intent; the approved envelope supplies `allowed_touches`; Git supplies the
   exact merge, optional publication merge, current branch/HEAD, ancestry,
   trees, changed paths, and metadata-only dirt evidence. Recovery records
   `merge_head`, `publication_head`, `retained_main_branch`,
   `retained_main_head`, partitioned descendant paths,
   `qualification_rotation_proof`, `qualification_rotation_proof_digest`,
   `descendant_drift_digest`, and `retained_dirt_digest`. The dirt digest binds
   normalized path/status metadata, never file contents.
4. **Validation & Error Matrix**: Missing or multiple exact merge -> reject;
   changed branch without one direct identical-tree publication merge ->
   reject; publication not retained -> reject; later path matching an allowed
   touch -> reject unless it belongs to the exact verified qualification
   rotation chain; a mixed link with only proof-bound non-overlap attachments
   is accepted; any attached envelope overlap, unclassified status/path,
   non-link config/receipt commit, chain gap or reorder, other config text/key
   change, old receipt mutation, receipt
   identity/strict-verification/source-purpose/predecessor/ancestry mismatch,
   changed or revoked execution binding, or proof drift -> reject; dirt
   overlapping an allowed touch -> reject; changed dirt path/status digest or changed
   branch/HEAD/tree/config/receipt during checks -> reject.
5. **Good / Base / Bad Cases**: Good is PR publication plus non-overlapping
   later commits and unchanged untracked recovery evidence; base is a
   same-branch identical-tree wrapper with unchanged tracked non-overlap dirt;
   bad is overlapping dirt/descendant drift, dirt path/status mutation, or a
   branch rename without publication evidence.
6. **Tests Required**: Exercise prepared and effect-observed windows for good
   and base cases plus single-link, ordered multi-link, and mixed-commit
   non-overlap qualification rotations; assert bad cases fail the
   safe-point before epoch rotation
   with unchanged authority; assert tracked and untracked non-overlap dirt is
   preserved through recovery, replay, and archive; assert a post-check dirt
   path/status mutation blocks acknowledgement.
7. **Wrong vs Correct**: Wrong is treating membership in a handler-kind set as
   proof that this operation instance is recoverable, or applying the fresh
   merge's clean-worktree rule to historical acknowledgement. Correct is
   invoking the same read-only instance proof at safe-point admission and again
   after fence rotation, then binding unchanged non-overlap dirt metadata.

- Problem identity binds root condition and requirements. Round 0 is the initial
  failure; repair rounds are contiguous 1 through 3. Replacing a worker, child,
  or error wording cannot reset the budget. A failed round 3 changes the same
  parent to nonterminal `recovery_waiting`; it retains the ledger, integration
  ref/HEAD/tree, problem lineage, current graph scope, and original start
  authority.
- `open_recovery_generation` accepts only one new diagnosis/action for the
  current exhausted problem and exact current integration/context identity.
  It opens the next additive generation in the same run and returns the parent
  to `authorized`. Changed replay, operation-ID collision, stale identity, or
  scope expansion fails before a replacement revision.
- Cancel requires direct human action and a reconciled safe point. It preserves
  integrated child state, explicitly marks every other child `cancelled`, retains
  Git evidence, records actor/reason/time/supersession, and never cleans, rewrites
  refs, or represents the parent as completed.

### 5. Validation & Error Matrix

| Condition | Result |
|---|---|
| Child lacks current reviewed commit authority or is not deterministic next | `IntegrationStateError` |
| Candidate path/ref is attached, protected, stale, or outside the repository | `GitStateError` / `RecoveryError` |
| Merge/check fails | Durable failed candidate/problem; integration ref unchanged |
| Expected-old CAS differs from actual ref | Durable pause plus `IntegrationCASConflict` |
| Old effect has no single provable Git outcome | Parent remains paused with `RecoveryError` evidence |
| Repair identity/round/result violates 0+3 | `ProblemBudgetError` |
| Round 3 fails | Same parent enters `recovery_waiting`; no cancellation or successor |
| Recovery continuation is stale, colliding, or changes exhausted scope | `ProblemBudgetError` / `OperationConflict`; no new generation |
| Resume receipt/resources/authority/user dirt is stale, overlapping main drift lacks the exact closeout-safe proof, or an unresolved kind has no recovery handler | `FreshnessError` / `ControlError` / `DirtOverlapError` before writer rotation |
| Cancel is automated or an operation is unresolved | `ControlError`; no cancellation mutation |

### 6. Tests Required

- Assert failed and green no-ff candidates, all checks, exact merge parents/tree,
  unchanged ref before CAS, expected-old advance, durable acknowledgement,
  coverage/context/projection, deterministic order, and replay without duplicate
  merge/ref/Git authority.
- Inject crashes after child changes, review, commit effect, candidate prepare,
  green intent, ref advance, and authority before projection; prove exactly one
  no-effect or effect outcome and stale-fence/result rejection after resume.
- Assert overlapping committed main drift passes only when current coverage is
  final-ready, every current child is integrated, every unresolved operation
  kind is handler-backed, and the recorded integration head is an ancestor of
  current `main`. Assert final-local-merge recovery across prepared/observed
  windows and a transparent retained wrapper. User dirt, unsupported
  operations, unfinished work, and unretained integration history must fail
  closed.
- Assert stable 0+3 problem budget, `recovery_waiting` preservation, same-run
  generation continuation/replay rejection, pause/resume gates, history
  invalidation, human-only cancellation, unfinished child disposition,
  integrated evidence retention, bounded routine worktree registrations, and no
  cleanup during exceptional control states or fake completion.
- Re-run B1-B4 focused tests and the complete current-Trellis suite while Loop
  admission remains disabled.

### 7. Wrong vs Correct

Wrong: merge directly into the integration ref, retry a failed CAS against its
new value, infer a commit from child files, flip paused to authorized without a
new fence, reset retry identity with a new child, or delete evidence on cancel.

Correct: persist exact intent, build/test an isolated candidate, use one
expected-old ref CAS, acknowledge observed Git facts, and resume only through a
new fenced epoch with deterministic reconciliation.

## Terminal Task Projection And Evidence Archive

The Loop ledger remains workflow authority. `projection-status` is read-only
and returns `current|stale|missing|conflict` for one exact run/task identity.
`project-terminal` accepts expected authority and task digests, maps ledger
`archived` to task `completed`, maps ledger `cancelled|revoked` to task
`cancelled`, preserves all other evidence bytes, and refreshes BOARD only
through `board.py`. Exact replay is a no-op.

`cancel-safe-point` reads the authority digest and classifies every unresolved
operation as reconcilable, abandonable-preserved, or ambiguous without
consuming an operator action or rotating a fence. `reconcile-for-cancel` is the
only public local-intent reconciliation path. `cancel` commits ledger
terminality before consuming its bound pending action; a later runtime load
repairs operator-state projection if that write was interrupted.

After a current terminal projection and with no pending action,
`project-terminal` also derives every exact task child from its retained ledger
child state. `integrated` maps to completed; `cancelled`, `invalidated`, and
`stale` map to cancelled. `retire-task-evidence` then moves projected children
first and the parent last into one monthly archive through
`.trellis/.runtime/archive-transactions/<id>.json`; replay of a previously
parent-only retirement sweeps the remaining exact children. It retains the
ledger and all task evidence. `archive-pre-admission` applies the same
transaction to one formally cancelled uninitialized parent and removes only
its lock-only run root. Both commands are task/authority-digest bound,
generator-only for BOARD, and idempotent at one unique archived location.
Incomplete archive transactions block every new archive until public
`task.py archive-recover <transaction-id>` proves a committed trailer or
restores exact task paths, BOARD bytes, Git HEAD/index preimages.

## Acceptance And Final Gate API Contract

### 1. Scope / Trigger

Use these APIs only after every required child is integrated, canonical context
matches the unattached integration ref, and the parent remains `authorized`.
Acceptance JSON/Markdown and readiness are derived projections. SQLite remains
the sole workflow authority; the only Git mutation is the exactly authorized
local-main no-ff merge.

### 2. Signatures

```python
record_final_review(
    ledger, lease, *, review_id, integration_ref, reviewer_identity,
    fresh_context_receipt, verdict, required_findings, advisory_findings,
    dispositions, specialist_results, affected_requirement_ids=(),
) -> dict
generate_acceptance_pack(
    ledger, *, pack_id, integration_ref, tool_receipt, environment,
    effect_proofs, skipped_checks,
) -> dict
compute_final_readiness(
    ledger, *, pack_result, integration_ref, tool_receipt, request_id=None,
) -> dict
create_final_request(
    ledger, lease, *, request_id, pack_result, readiness, integration_ref,
    post_merge_checks, author_name, author_email,
) -> dict
approve_final_request(
    ledger, lease, *, request_id, request_digest, pack_result,
    integration_ref, response_identity, response_at, direct_user_action,
    authorization_ref=None,
) -> dict
execute_final_merge(ledger, lease, *, request_id, pack_result) -> dict
archive_parent_run(ledger, lease, *, request_id) -> dict
```

There are no acceptance environment variables, remote calls, alternate stores,
or implicit qualification receipts. B7 still owns Q01-Q12 qualification and
activation.

### 3. Evidence And Readiness

- The final reviewer uses `model:<approved-surface>:<fresh-context>` and must
  differ from recorded implementer reviewers. Review binds the exact current
  integration commit/tree, all applicable requirements, structured findings,
  a fresh-context receipt, and the declared-risk specialist matrix. A failed
  review names a nonempty subset of current `affected_requirement_ids`; a
  passing review names none.
- Specialist reviews exist only for a matching envelope risk, scope, or actual
  touch trigger. An untriggered specialist result is rejected; a triggered
  specialist must be supplied and pass before a passing final verdict.
- Acceptance files live under the ignored parent `acceptance/` directory. They
  contain the fence-redacted authority snapshot, source ledger position,
  envelope/context/graph/requirement digests, Git ancestry/tree, coverage,
  omissions, child/review/integration/problem evidence, projection/dirt/tool/
  input evidence, skipped checks, and local/external-effect proof. No generated
  timestamp enters the pack digest.
- Readiness is recomputed from direct ledger, Git, pack-file, projection, fence,
  resource, problem, finding, receipt, and effect evidence. It is never stored
  as a sticky verdict. Only the exact request and approval records bound to the
  pack may follow its source position before the merge intent; any other change
  makes readiness false.

### 4. Final Authorization And Merge

- The immutable request binds base branch/HEAD/tree, integration ref/HEAD/tree,
  pack/source/readiness digests, exact final review, smoke commands, author
  identity, and only `local_merge`, `verification`, and `parent_archive`.
  Push, tag, release, deploy, and downstream sync are explicitly prohibited.
- In the default `single_user` profile, approval reuses the exact current
  start gate through its durable `local_command_authority`; the final gate has
  no second response identity and allows only the request's local merge,
  verification, and parent archive. The compatible `strict` profile requires
  a direct user action against the exact request and pack digests. Drift before
  either approval form or merge records gate invalidation and prevents the
  merge.
- The parent prepares one durable final-merge intent before Git. Local `main`
  must still be clean, on the approved base branch and HEAD, and the integration
  ref must still resolve to the approved head/tree. The merge uses `--no-ff`.
- Recovery accepts an existing commit only when its parents are exactly
  `[base HEAD, integration HEAD]` and its tree equals the integration tree. It
  never resets, cleans, guesses, or creates a second merge.
- Post-merge verification proves unchanged merge HEAD/tree, clean effect
  boundary, and every smoke command. A failure preserves the merge and evidence
  but blocks archive. The Loop parent becomes `archived` only after passing
  merge authority; this does not move current-Trellis task files or imply push.

### 5. Validation Matrix

| Condition | Result |
|---|---|
| Implementer identity, stale integration artifact, or unresolved required finding | `FinalReviewError` |
| Missing/extra specialist review relative to declared triggers | `FinalReviewError` |
| Base/main/integration/review/pack/receipt/projection drift | Readiness false; approved gate invalidated before merge |
| Missing/invalid retained start authority, non-direct strict response, changed digest, or replayed-as-new approval | `FinalGateError` |
| Dirty/wrong branch, unexpected HEAD, parents, tree, or ref | `FinalMergeError`; no guessed cleanup |
| Smoke failure | Merge and failed verification preserved; archive blocked |
| Archive before verified final merge | `FinalGateError` |

### 6. Tests Required

- Assert byte/digest-stable pack regeneration, complete evidence sections,
  fence-token redaction, and direct evidence for every readiness predicate.
- Assert fresh non-implementer review, trigger-only specialists, required-finding
  blocking, and base/main/integration/review/pack/receipt drift independently.
- Assert exact retained-start and strict direct approval paths, local-only
  allowlist, one single-user start intent, exact no-ff parents/tree, smoke
  checks, failure preservation, crash replay without a second merge, archive
  ordering/replay, user-dirt preservation, and no push effect.

## Orchestrator CLI Contract

### 1. Scope / Trigger

`.trellis/scripts/loop_v1/orchestrator.py` is the durable JSON facade over the
existing Loop primitives. It is valid only for a qualified parent whose
`task.json` declares `tier: parent` and `meta.workflow_mode: loop_v1`. It does
not choose a model/provider, send channel messages, modify current-Trellis task
authority, push, release, deploy, or synchronize a downstream repository.

### 2. Signatures

```bash
PYTHONPATH=.trellis/scripts python3 -m loop_v1.orchestrator \
  --repo-root <repo> --run-id <parent-run-id> \
  [--output <json-path>] <command> [--input <json-path|->]
```

Commands are `init`, `status`, `request-start`, `respond-start`, `advance`,
`ingest`, `request-final`, `respond-final`, `pause`, `resume`,
`guide-replacement`, `recover-final-checks`, `continue-recovery`, `cancel`, and
`revoke`.
Commands that mutate or consume external input require `--input`; `-` reads one
JSON object from stdin. Every success and handled failure emits one sorted JSON
object to stdout or the exact `--output` path. A handled failure exits nonzero
with `status`, `error_type`, and `error` fields. File input/output paths must be
repository-relative and resolve inside `repo_root`; machine-absolute and `..`
paths fail before any runtime or output artifact is created. Output is further
restricted to
`.trellis/.runtime/loop-v1/parents/<run-id>/outputs/*.json`; it cannot replace
source, task, ledger, operator-state, or acceptance files.

The package also exports the corresponding in-process APIs:

```python
initialize_operator(repo_root, run_id, init_input) -> dict
operator_status(repo_root, run_id) -> dict
request_start(repo_root, run_id) -> dict
respond_start(repo_root, run_id, response) -> dict
advance_operator(repo_root, run_id) -> dict
ingest_operator(repo_root, run_id, message) -> dict
guide_replacement(repo_root, run_id, request) -> dict
request_final(repo_root, run_id) -> dict
respond_final(repo_root, run_id, response) -> dict
pause_operator(repo_root, run_id, request) -> dict
resume_operator(repo_root, run_id, request) -> dict
recover_final_checks(repo_root, run_id, request) -> dict
continue_recovery(repo_root, run_id, request) -> dict
cancel_operator(repo_root, run_id, request) -> dict
revoke_operator(repo_root, run_id, request) -> dict
```

### 3. Contracts

#### Initialization And Policy

- `init` accepts exactly `task_dir`, `request_id`, `envelope`, `context`,
  `context_revision_id`, and `context_reason`. Unknown or missing fields fail.
- Before a new ledger creation it validates the unique task identity/mode,
  current role-aware admission qualification, selected base branch/HEAD/tree,
  current branch, and repository dirt fingerprint. `harness_source` uses
  `source_development`; `downstream_project` uses target-local
  `installed_runtime`. A failed preflight leaves no parent ledger. Replaying an
  existing run validates its immutable init identity without re-resolving the
  mutable admission pointer.
- Start approval verifies the current canonical receipt once and records its
  immutable execution binding in `start_request_approved`. Later worker,
  integration, review, recovery, finalization, and ledger writes validate that
  binding, not current config or its admission-disable marker. Canonical receipt
  rotation therefore affects only later admissions.
- Missing `task.json.meta.authorization_profile` means `single_user`.
  Initialization immediately binds the immutable start envelope and records
  one `local_command_authority` operation covering only declared local worker,
  review, child commit, integration, repair, and final merge work. The derived
  final request adds only verification and parent archive to that local
  finalization. `strict` emits the compatible start response action and later
  final response action instead.
- Operator-only execution policy lives at
  `envelope.verification_policy.operator`. It contains exact child result
  deadlines/tests, per-child `integration_checks`, complete-graph
  `final_integration_checks`, post-merge checks, environment evidence, effect
  proofs, and skipped checks. Every command list is required, nonempty, and
  duplicate-free. This policy is part of the approved start envelope; the
  operator cannot add checks or effects later.
- The deterministic sibling worktree root is outside the repository at
  `<repo-parent>/.trellis-loop-v1-worktrees/<repo-name>-<repo-hash>/<run-id>/`.
  The operator never resets, cleans, or rewrites user dirt.

#### Next-Action Protocol

At most one external action is pending. Every action contains
`schema_version`, `action_id`, `action_type`, `authority_digest`, `payload`, and
an `action_digest` over the other fields. A response or ingest message must
bind the exact action ID and digest; changed, stale, unknown-field, or replayed
input fails closed. Once canonical context exists, every payload includes the
ledger-derived `recovery` projection: status, active/exhausted problem attempts,
and current replacement child identities. Operator status exposes the same
projection plus all historical and current-graph child states. While paused it
also exposes the read-only `resume_safe_point` projection used by the resume
guard.

| Action | External responsibility |
|---|---|
| `start_response` | In `strict` only, supply the direct response bound to the exact immutable start request. |
| `dispatch_workers` | Dispatch every listed packet/worktree plus any ledger-derived `recovery_context`, and ingest each structured `worker_result`. |
| `precommit_review` | Supply one independent structured review for the exact validated child tree. |
| `final_review` | Supply one fresh non-implementer review for the exact integration HEAD. |
| `final_response` | In `strict` only, supply the direct response bound to the exact final request and pack. |
| `human_intervention` | Inspect durable evidence; use explicit pause/resume/cancel/revoke authority as applicable. |

`advance` derives the next step from ledger authority and Git facts. It may
perform only already-authorized local effects: issue packets/worktrees, commit
passing reviewed candidates, build/check/advance one integration candidate at a
time, run the complete-graph final integration checks, generate the acceptance
pack/request, execute the exactly approved local merge, run post-merge checks,
and archive the Loop ledger. Worker completion or message arrival order never
selects integration order.

#### Authority, Locking, And Recovery

- SQLite and exact Git identities remain workflow/effect authority.
  `operator-state.json` is an ignored projection used only for the writer lease,
  action handoff, action-consumption receipts, and final pack location.
- `operator-state.json` and `operator.lock` live beside the parent ledger and
  are forced to mode `0600`. A POSIX `flock` serializes every invocation.
- External action records deliberately do not enter the ledger because a
  transport-only write must not change a frozen acceptance pack or final-gate
  source position. Their action digest binds the ledger authority digest seen
  when the action was emitted.
- Final-review action/review identities and acceptance-pack/request identities
  also bind the canonical context receipt. If context changes with the same Git
  HEAD/tree, stale review or response actions are consumed as projection-only
  reconciliation and `advance` recomputes the next action from current authority.
- Final integration verification identities bind the active writer epoch in
  addition to integration, context, and policy identity. A supported writer
  rotation therefore records a new verification operation instead of reusing
  an old-epoch operation ID; committing that verification refreshes the
  summary projection before final readiness is recomputed.
- If operator state is missing after ledger initialization, the CLI rebuilds
  the active lease and task identity from authority. `advance` reconstructs the
  next action from ledger/Git state. A crash-window writer rotation is
  reconciled from its committed resume operation before another write.
- Resume may make unfinished work from the prior writer epoch `stale`. `advance`
  records the stable problem, commits an in-envelope replacement graph revision,
  releases the old claims, and issues new digest-bound child packets. Old
  identities stay stale and reject late results.
- Child worktree creation uses the initial envelope dirt fingerprint until a
  committed `parent_resume` in the active epoch exists. After resume it requires
  that exact resume-bound dirt digest; changed dirt still fails, and the normal
  allowed-touch overlap check remains mandatory. If packet issuance commits
  before worktree creation, the next `advance` completes the same stable
  worktree operation before exposing worker dispatch. Main drift since the
  retained final-merge publication may overlap that child only when an active
  resume exists and the existing receipt-verified
  `source_release_qualification_rotation` proof classifies every overlapping
  path; unclassified or partial overlap retains the original hard stop.
  Replacement worktree creation may anchor a descendant rotation proof at the
  active epoch's committed resume HEAD only after strictly verifying that
  HEAD's configured receipt with the current installed runtime and proving all
  pre-anchor child overlap is limited to qualification config/receipt paths.
  It then verifies only the descendant chain to current main; an earlier
  installed-runtime epoch is not reinterpreted.
- `revoke` accepts exactly `operation_id`, `actor`, `reason`, `revoked_at`,
  `direct_user_action`, and `execution_receipt`. It requires direct user action
  and an exact current run/receipt target, records one append-only
  `execution_binding_revocation` event without cleanup, then changes only that
  parent to terminal `revoked`. Replay is exact; a different target or input
  records no authority. Receipt rotation never invokes this control.
- Worker/candidate validation failure, a required pre-commit finding, a failed
  integration candidate, or a required final-review finding uses the same
  durable 0+3 lineage and deterministic replacement path. Passing siblings,
  commits, integrations, coverage, and failed-attempt evidence remain intact.
- Repair round 3 exhaustion returns the same run in `recovery_waiting` with no
  pending transport action. `continue-recovery` requires an exact operation ID,
  exhausted problem ID, diagnosis, and action, then opens the next same-run
  generation only when integration/context identity and affected scope are
  unchanged.
- Typed `recovery_failure_observed` evidence binds the exact failed child IDs,
  phase, root condition, requirements, graph wave, artifacts, and intended
  problem round. Same-problem sibling failures in one graph wave aggregate at
  one round; different phases or root conditions keep distinct lineages even
  when requirement coverage is identical. Transport result IDs never select a
  sibling or problem lineage.
- A current, in-scope worker result with a required command that is failed or
  skipped records an atomic `rejected` message receipt and failed-result
  operation without advancing the child. Exact replay returns the same
  rejection; changed payload under that result ID raises `OperationConflict`.
- The final failed ingest from a quiescent `worker_required_test_failed`
  dispatch returns the exact problem round, failed result, dispatch, context,
  graph, and complete problem-attempt evidence bindings accepted by
  `guide-replacement`. The command accepts only a nonempty duplicate-free list
  of current integrated transitive ancestors, canonicalizes it in graph order,
  requires every dependency-path intermediate, and rejects a selected
  prerequisite with any current descendant outside the explicit slice.
  Selected coverage must also be disjoint from every unselected current child.
  Validation and the accepted guidance record commit under one writer fence.
- `advance` revalidates accepted guidance against the unchanged problem,
  context, and graph, then reopens each selected prerequisite plus the failed
  child under fresh one-to-one identities. Each node preserves requirements,
  touches, resources, and dependency position; historical evidence remains
  append-only but no longer contributes current coverage. The accepted record
  stores the exact expanded coverage without changing problem identity, and
  the whole graph revision consumes one round of the original shared 0+3
  budget. Exact replay remains available after preparation; changed replay or
  a new late operation fails without mutation. No guidance retains same-child
  behavior.
- Replacement dispatch entries expose only accepted ledger-derived recovery
  diagnosis, failed commands, and required finding summaries. They never depend
  on raw transport text.
- Every replacement preserves exact old/new selected-child coverage. For
  `final_review`, the durable problem requirements must be a nonempty subset of
  that complete coverage; all other recovery phases require exact
  problem-to-coverage equality. Empty, outside-scope, or coverage-changing
  replacements fail before a context revision.
- `integration_checks` run on each deterministic integration candidate and must
  remain valid for every partial graph prefix. Once required coverage is final
  ready and every current graph child is integrated, one
  `final_integration_checks` operation reuses the last candidate worktree and
  existing parent check runner before the first final-review action. Its stable
  identity binds the exact unattached integration ref, HEAD/tree, context and
  graph freshness, policy commands, ordered results, worktree, and clean effect
  boundary through prepared, effect-observed, and authority-committed phases.
  Exact committed replay does not rerun commands.
- A failed final integration check commits its failed operation, preserves the
  integrated children/ref/context, and pauses with
  `ambiguous_product_semantics`. It creates no recovery observation, problem
  attempt, replacement child, context revision, or final-review action because
  no deterministic child attribution exists at that boundary. After an exact
  direct-user resume, `recover-final-checks` may bind the latest failed
  operation to explicit current affected child IDs. Exact replay returns the
  same guidance; changed replay or stale/non-current child attribution fails.
  `advance` converts that durable guidance into the normal bounded problem and
  replacement path while preserving unaffected integrated siblings.
- When that exact replacement is independently reviewed but has an empty diff,
  current validation, and the retained integration HEAD/tree as its execution
  base, `advance` may record one fenced `reviewed_zero_diff_integration`
  authority and move only that child from `reviewed` to `integrated`. This
  transition releases its claims but creates no child commit, integration
  commit, ref update, or `git_operations` row. Ordinary zero-diff children
  remain invalid, and complete-graph final checks still run afterward through
  the ordinary path. It leaves canonical `main` drift untouched for the
  existing final gate instead of treating that drift as transition authority.
  The bound recovery guidance is consumed by that authority and cannot create
  another replacement on a later `advance`. If a prior runtime already
  projected one such replacement but failed before creating its worktree, the
  operator restores the exact preceding integrated context through a normal
  context revision and invalidates only that undispatched replacement.
- A failed final review repairs only current graph children whose requirements
  intersect its declared `affected_requirement_ids`. It resets only that
  coverage, invalidates any extant final request/readiness, and requires a new
  review bound to the repaired integration commit/tree and current canonical-
  context digest. The problem stays active until that exact fresh review passes.
  Advisory findings retain their exact dispositions.
- `human_intervention` may be emitted only after the exhaustive classifier
  returns scope/resource expansion, dependency/schema/security/secret change,
  unknown effect/data loss/user dirt, ambiguous product semantics, or explicit
  user control. Routine failure wording and bounded repair exhaustion are not
  reasons; exhaustion is the distinct `recovery_waiting` state.
- Stable ledger operations and existing primitive replay/CAS checks prevent a
  retry from duplicating worktrees, child commits, integration ref advances,
  gate requests, final merges, or archive effects. Unproved or mismatched
  effects pause or fail; they are never guessed or broadly cleaned up.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Non-parent/non-`loop_v1` task, invalid qualification, stale base/tree/dirt, or policy mismatch | Structured failure before parent-ledger creation |
| Missing or unknown JSON fields, wrong action kind/ID/digest, or out-of-dispatch child | `OrchestratorError`; owning authority mutation does not run |
| Machine-absolute/escaping input, output outside the run `outputs/` directory, or status for an unknown run | Structured failure with no runtime/output artifact or project-file replacement |
| Missing operator state after initialization | Rebuild lease/task identity; `advance` derives the exact next action from ledger/Git |
| Incomplete operation from an obsolete writer epoch | `OperationConflict`; no cross-epoch replay |
| Stale child, worker validation failure, required review finding, or failed integration candidate | Stable problem attempt plus in-envelope replacement action; no direct-user gate |
| Same problem fails repair round 3 | Same run enters `recovery_waiting`; no action, cancellation, or successor |
| Replacement guidance is stale, late, non-ancestor, path-open, overlapping, duplicate, empty, or outside the current graph | `FreshnessError` / `RecoveryError` / `OperationConflict`; no graph, coverage, problem, or budget mutation |
| `continue-recovery` changes replay, identity, or scope | `OperationConflict` / `OrchestratorError`; no new generation |
| Revocation is indirect, stale, mismatched, or reused with different input | `LedgerError` / `OperationConflict`; no revocation authority |
| Parent is explicitly revoked | Later execution fails closed; no worker, integration, review, recovery, finalization, or ledger operation |
| Complete-graph final integration check fails | One durable failed check operation plus `ambiguous_product_semantics` pause; replacement requires resumed direct-user `recover-final-checks` attribution |
| Other exhaustive intervention signal | Durable pause plus its exact classified reason and details |
| Final review, pack, base, receipt, projection, or integration drift | Existing final gate rejects or invalidates; no merge |
| Exact replay of a stable request/action/effect | Same durable result; no duplicate worktree, commit, ref update, merge, or archive |

### 5. Good / Base / Bad Cases

- Good: two conflict-free children are dispatched together, reviewed and
  committed before integration context moves, then integrated in deterministic
  order. Default `single_user` finalization reuses the exact start authority;
  `strict` uses the exact final response. Both end in the same verified local
  archive.
- Base: the process or operator-state projection disappears between commands;
  the next invocation reconstructs the same pending action from ledger/Git and
  continues without duplicating effects.
- Bad: a response changes an action digest, a resumed child belongs to an old
  epoch, or Git/qualification evidence drifts. The CLI rejects or pauses rather
  than reusing identity, rewriting dirt, or guessing recovery.

### 6. Tests Required

- Two-child local Git fixtures must cover default one-start `single_user` and
  compatible two-response `strict` profiles through one parallel dispatch
  action, structured results, per-child review/commit, deterministic serial
  integration, fresh final review, exact final approval, no-ff local merge,
  post-merge check, and Loop runtime archive.
- Wrong action digest and non-Loop task mode must fail before their owning
  authority mutation. Runtime state must be `0600`, public output must not
  expose the writer fence token, and `python -m loop_v1.orchestrator --help`
  must not emit import warnings.
- Absolute/escaping input and output paths plus status for an unknown run must
  fail without creating the target output or an empty runtime directory. A
  repository-relative output outside the run `outputs/` directory must leave
  the existing project file byte-identical.
- Delete operator state after start approval and after final-request creation;
  assert the reconstructed dispatch/final actions are exact-equal. Assert
  worker validation, pre-commit review, resume-stale, integration, and final
  review failures reach bounded replacement actions; round-3 exhaustion enters
  `recovery_waiting`, and exact `continue-recovery` opens the next same-run
  generation without changing integration identity or scope.
- Assert canonical rotation preserves an admitted run's exact binding, an
  explicit targeted revoke is append-only and terminal, and rejected revoke
  input creates neither authority nor a terminal transition.
- A dependent two-child fixture must prove final integration checks do not run
  after the first child, run once after the second child and before final
  review, and replay without a second command effect. Its negative twin must
  preserve both integrated children and the ref while producing one durable
  failed operation and no automatic recovery/replacement/final-review
  authority. After resume, exact explicit attribution must replace only the
  selected child and preserve its integrated sibling.
- The full Loop v1 suite, compile pass, fatal Ruff rules, and diff whitespace
  check remain green. Qualification/receipt activation is a separate delivery
  gate after the orchestrator and transport surfaces are committed.

### 7. Wrong vs Correct

Wrong: write channel/transport actions into the ledger, silently reuse a stale
child after writer rotation, or advance whichever result arrived first. Those
choices either invalidate frozen acceptance evidence or create a second
workflow authority.

Correct: keep one digest-bound action in the locked ignored projection, derive
all transitions, deterministic integration order, problem lineage, and bounded
replacement revisions from ledger/Git. Ask for a human only when the exhaustive
classifier returns a true intervention condition.

## Agent Transport Capture Contract

### 1. Scope / Trigger

Use the project-local `trellis-loop-v1` skill when an admitted A1 operator run
returns an external `next_action`. The skill presents direct-user gates and
uses only an action-approved agent surface for worker or reviewer transport.
Transport messages are untrusted until the A1 CLI accepts their exact ingest
object. This layer must not open Loop authority or mutate Git/task state.

### 2. Signatures

Transport capture command:

```text
python3 .agents/skills/trellis-loop-v1/scripts/capture_transport.py \
  --repo-root <repo> --run-id <run-id> --input <repo-relative-json>
```

The input object has exactly:

```json
{
  "action": {},
  "payload": {},
  "raw_message": "exact transport response text",
  "role": "worker | precommit_reviewer | final_reviewer",
  "surface": "action-approved logical surface",
  "transport_identity": "stable non-secret identity"
}
```

Success emits `status`, `message_id`, `action_digest`, `payload_digest`,
`raw_message_digest`, `record_path`, `ingest_path`, and `replayed`. Failure is
nonzero JSON with `status`, `error_type`, and `error` only.

### 3. Contracts

- Input must be a regular `0600` JSON file directly under
  `.trellis/.runtime/loop-v1/parents/<run-id>/transport/inbox/`. Absolute,
  escaping, nested, loose-permission, or symlinked inputs fail.
- `action` contains exactly the A1 action fields and must reproduce its SHA-256
  action digest. The selected surface must occur in the action's
  `approved_agent_surfaces`.
- Role binding is fixed: `worker -> dispatch_workers -> worker_result`,
  `precommit_reviewer -> precommit_review -> precommit_review`, and
  `final_reviewer -> final_review -> final_review`.
- A worker payload binds one listed child and its exact packet. A reviewer
  payload's `reviewer_identity` equals `transport_identity`; a final review also
  echoes the exact `fresh_context_receipt`, selects affected requirement IDs
  only from the action, uses a nonempty list only for failure, and uses an empty
  list for pass. A failed pre-commit review contains at least one required
  finding; a passing one contains none.
- The helper writes an immutable raw record under `transport/records/` and one
  exact A1 ingest object under `outputs/`, both mode `0600`. Derived parents may
  not contain symlinks or escape the run directory. Exact replay preserves
  bytes; conflicting replay fails before replacement.
- Public output contains paths and digests only. Raw message and structured
  payload bytes stay in ignored runtime evidence.
- The helper does not import Loop/SQLite/task modules, invoke subprocess/Git,
  access a writer lease, dispatch agents, call a network endpoint, or approve a
  gate. A1 `ingest` remains the semantic and authority validator.
- `start_response`, `final_response`, and `human_intervention` never use this
  helper. The skill presents them to the direct user and waits for a new exact
  response or control instruction.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Unknown/missing capture or action field, unsupported schema/role, or changed action digest | Structured failure before record/output creation |
| Surface absent from the current action | Reject; do not fall back to another provider |
| Child/packet outside dispatch | Reject before persistence; A1 authority remains unchanged |
| Reviewer identity or final context differs | Reject before persistence |
| Input or derived parent is loose, escaping, or symlinked | Reject without following the path or replacing project bytes |
| Existing record/ingest bytes differ | Conflict; preserve both existing files |
| Exact capture replay | Return the same paths/digests with `replayed=true` |
| Payload passes capture binding but fails A1 semantic validation | Preserve raw evidence; keep the current action pending |

### 5. Good / Base / Bad Cases

- Good: an action-approved worker returns one packet-bound result; capture
  persists the raw text and A1 accepts the generated ingest file.
- Base: the same response is captured again after process loss; paths and bytes
  are unchanged and the dispatcher reuses the existing ingest file.
- Bad: a stale action, unapproved surface, mismatched reviewer, or symlinked
  outputs directory is supplied. Capture fails without transport output or
  authority mutation.

### 6. Tests Required

- Capture worker, pre-commit review, and final review messages; assert exact A1
  ingest fields, `0600` records/outputs, redacted stdout, and byte-stable replay.
- Send one generated worker ingest file through the real A1 `ingest_operator`;
  assert the pending dispatch and remaining-child projection are preserved.
- Reject changed action digests, unknown fields, unapproved surfaces,
  child/packet drift, reviewer/context drift, loose input permissions, absolute
  or escaping paths, symlinked input/output parents, and conflicting replay.
- Static skill/helper checks reject direct ledger, Git/ref/commit, task,
  provider/network, or inferred-approval instructions.
- Run the focused skill suite, full Loop/full script regressions, compile,
  fatal Ruff rules, skill validation, overlay conformance, and diff checks.

### 7. Wrong vs Correct

Wrong: translate a worker's prose directly into a passing result, dispatch over
an unlisted surface, or write a channel event into the ledger.

Correct: preserve the exact raw response, bind it to the unchanged A1 action
through the capture helper, and submit only the generated ingest file to A1 for
independent semantic and authority validation.

## Writer And CAS Rules

- Every authority mutation checks `run_id`, `writer_id`, `epoch`, and fence
  token inside the same `BEGIN IMMEDIATE` transaction as the write.
- Workers, reviewers, hooks, and channel participants submit validated inputs
  to the parent; they never call mutation APIs or receive `WriterLease`.
- Writer rotation is compare-and-swap against the exact active lease. Once it
  commits, the old lease cannot prepare or advance an operation.
- An operation prepared in an old epoch cannot advance under the new epoch.
  Later recovery work must reconcile it explicitly; B1 never guesses.
- A forged, cross-run, stale, or superseded lease raises `WriterFenceError`.

## Operation Replay

Ordered phases are:

```text
prepared -> effect_observed -> authority_committed -> projected
```

- Every normal state-changing intent has one stable `operation_id`, kind,
  epoch, input fingerprint, current phase, output fingerprint, timestamps, and
  canonical JSON outcome.
- Each phase transition appends an ordered `ledger_events` row containing that
  phase's output fingerprint and outcome, so later phases do not erase earlier
  replay evidence.
- Repeating prepare or the current phase with identical inputs returns the
  durable record without another event.
- Reusing an ID with changed inputs/outcomes, skipping a phase, advancing from
  the wrong phase, or crossing an epoch raises `OperationConflict`.
- Parent initialization and writer rotation are atomic database-only
  operations recorded directly as `authority_committed`; absence means the
  transaction did not commit and the same stable ID may be retried.

## Durable Schema

Schema v2 additively upgrades schema v1 and reserves durable records for:

- parent run and writer-fence history;
- envelope revisions;
- canonical context revisions and semantic graph revisions;
- requirements and coverage;
- child operations and immutable issued packets;
- resource claims;
- message receipts;
- Git operations;
- verification/review evidence;
- effects and gate requests;
- stable operations and ordered ledger events; and
- projection checkpoints.

Later children add validated behavior around these records. They must extend
this one database rather than create another authority store or let projection
files become writable truth.

## Projections

`rebuild_projection(lease, "summary")` writes a deterministic, atomic,
fence-redacted `projections/summary.json` and records its source ledger
position, digest, result, error, and update time.

Effective projection status is:

| Condition | Status |
|---|---|
| No checkpoint or no current file | `missing` |
| Last write failed | `failed` |
| Ledger position advanced after the checkpoint | `stale` |
| File and checkpoint match current authority | `current` |

Projection checkpoints and files are derived state and are excluded from
`authority_digest`. Deleting/rebuilding them cannot change workflow authority.
A file-write failure records `failed` and raises `ProjectionError`; committed
operations remain unchanged.

## Diagnostics And Backup

- Inspect health read-only with `PRAGMA integrity_check`, `get_operation`,
  `authority_digest`, and `projection_status`.
- Preserve the complete parent runtime directory for diagnosis. Do not edit the
  database, WAL, or projection files to repair authority.
- For a backup, first quiesce the parent writer at an atomic boundary and use
  SQLite's backup API or `.backup`; do not copy only `ledger.sqlite3` while WAL
  writes may still be active.
- A missing/corrupt ledger, unresolved operation outcome, failed integrity
  check, or stale epoch is a pause/reconciliation condition, never permission
  to recreate authority or repeat an unproven effect.

## Required Verification

- Deterministic ignored path, one database, and complete schema.
- Active-fence-only writes, exact rotation replay, and old-fence rejection.
- Stable operation identity, ordered phases, idempotent replay, and CAS errors.
- Old-epoch operation rejection after writer rotation.
- Projection deletion/rebuild determinism, freshness visibility, and injected
  write failure without authority rollback.
- Existing Loop admission and full current-Trellis regression suites remain
  green while admission is disabled.
- Immutable start-request replay, one default single-user local authority
  operation, and compatible strict direct-response binding.
- Deterministic context/graph revisions plus additive schema-v1 migration.
- Minimal child packets with secret references excluded.
- Stale epoch/context/base/dependency and over-scope result rejection without
  authority mutation; valid structured result acceptance and exact replay.
- Coverage-based progress, required/optional enforcement, optional omission
  disclosure, and replacement/split/merge graph revisions.
- Deterministic ready/integration order, cap three, live capacity, normalized
  aliases, path/exclusive/shared conflicts, serial fallback, and claim release.
- Stable cycle/deadlock pause replay plus safe satisfied-edge repair.
- Stable acceptance-pack regeneration and non-sticky direct-evidence readiness.
- Fresh exact-integration review, trigger-only specialists, and required-finding
  blocking.
- Exact retained-start and strict direct final approval, drift invalidation,
  crash-safe local no-ff merge, smoke verification, parent archive ordering,
  and no push authorization.
