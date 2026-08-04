# TaskRun Runtime

## Status And Scope

`.trellis/scripts/taskrun/` is the shared TaskRun authority. Newly created code
tasks use `taskrun_v2` when `taskrun_v2.new_code_tasks: true`; a retained
`taskrun_v1.new_code_tasks` key activates the same v2 creation path only when
the v2 key is absent, for downstream configuration compatibility. `task.py create` records the
non-authoritative planning strategy and `task.py start --taskrun-input <json>`
admits exactly one TaskRun through the existing thin `TaskRunOperator`.
`single` is the default; `loop` is an explicit execution strategy beneath the
same authority. Existing Current Trellis and Loop v1 tasks retain their
original lifecycle authority, including existing `taskrun_v1` runs, and are
never migrated or reinterpreted.

## New-Code Task Admission

- Cutover is explicit repository configuration. When disabled or absent, the
  historical create behavior remains available for downstream compatibility.
- With cutover enabled, new tasks store `meta.workflow_mode = "taskrun_v2"`
  and `meta.taskrun_strategy = "single"|"loop"`. They do not initialize HSM
  metadata, `state-events.jsonl`, or a Loop ledger.
- Parent and light task PRDs declare owned requirements under `## Requirements`
  with the canonical `prd.py` grammar. A child template contains only an
  `## Accepted PRD Binding` reference to accepted declarations. No new template
  emits a parallel `## REQ-ID` index.
- Before admission, `task.py validate` and `task.py start` call the same
  read-only TaskRun task-PRD preflight. It resolves requirements through
  `prd.py`, checks executable verification commands, and rejects deprecated,
  missing, duplicate, unowned, drifted, or placeholder input before TaskRun,
  pointer, Board, or task-status mutation. Existing admitted TaskRuns and
  historical task files retain their recorded authority and bytes; exact
  reopen reuses the immutable envelope's requirement and check binding rather
  than parsing historical PRD syntax as a new admission.
- `--workflow-mode` is a legacy selector and fails before task, parent, pointer,
  Board, ledger, or runtime mutation. A new child cannot extend an HSM or Loop
  parent after cutover, and cannot mutate an already-admitted TaskRun parent
  projection. Any planning-only TaskRun relationship must be established before
  the parent is admitted.
- A TaskRun parent with planned direct children freezes their ordered delivery
  slots at admission. Each slot binds its initial TaskRun identity, exact REQ
  IDs, scope, touches, and a deterministic digest. The child REQ sets must be
  disjoint and exactly cover the parent PRD. A childless task stores no slot
  surface and retains the direct final gate.
- `task.py start` requires one regular UTF-8 JSON input. `single` requires
  `actor`, `authorization_ref`, `worker_id`, and an independent `reviewer_id`.
  `loop` requires `actor`, `authorization_ref`, `actions`, `worker_ids`, and an
  independent `reviewer_id`; concurrency and candidate-commit fields retain the
  existing operator rules. Unknown fields fail closed.
- Start accepts only the repository's direct active task directory; an absolute
  external, archived, aliased, or symlinked same-name path cannot select a
  different repository task.
- Successful start creates or exactly reopens one deterministic SQLite
  authority, publishes its running task projection, then updates only the
  session pointer and Board projections. Replays cannot add a second run.
- TaskRun create, start, finish, cancel, and archive do not invoke configurable legacy lifecycle
  hooks. Configured hooks retain their historical behavior only for legacy
  workflow modes; they cannot smuggle network or other effects into TaskRun
  admission or pointer cleanup.
- `task.py cancel` accepts only an explicit, unadmitted planning TaskRun with
  no SQLite or HSM residue. `task.py archive` accepts only that verified
  cancellation or an admitted terminal, closed TaskRun. `complete-child`
  cannot mutate a TaskRun task. Planning metadata and relationships may be set before start;
  generic owner, branch, scope, and relationship writers reject an admitted
  projection. Terminal disposition and status-only close remain TaskRun-owned;
  commit, push, release, deploy, migration, network, and destructive effects
  retain separate authority.

Final-gate receipt consumption accepts only a complete outer-operator receipt.
`record_final_acceptance()` no longer constructs `accepted=True` from a
caller-supplied identity, and `TaskRun.record_final_response()` no longer
accepts a raw response object. `RDS-EXEC-001` remains blocked until
`07-22-taskrun-direct-final-response-envelope` independently passes acceptance,
is locally committed, and reaches canonical Current Trellis child completion.

## Qualification Gate Isolation

`taskrun.qualification_scope` is a read-only child-acceptance classifier. It
loads one exact hash-addressed Loop receipt, reads that receipt's historical
overlay manifest from its bound Git commit, and intersects one committed diff
with the receipt's runtime files, qualification test sources, and
official/overlay payloads.

```text
PYTHONPATH=.trellis/scripts python3 -m taskrun.qualification_scope \
  --repo-root <repo> --receipt <receipt.json> \
  --base <commit> [--head <commit>]
```

An empty intersection returns `changed_scope_clear`. Any intersection returns
`complete_qualification_required`; missing or inconsistent evidence returns
`changed_scope_unknown`. A clear child scope does not validate a stale receipt,
qualify a release or milestone, enable admission, activate a receipt, or
authorize downstream work. Complete qualification remains the only release and
admission gate.

## Legacy Dry-Run Importer

`.trellis/scripts/legacy_importer.py` is separate from the TaskRun authority.
It accepts only an explicit local source root, an existing empty disposable
output root, `legacy-import-v1`, and a declared logical scope. It reads the
source-owned `legacy-import.json` manifest and only maps terminal legacy task
facts into sealed attachments with `execution_allowed=false`; it does not
open, copy, or reinterpret a legacy ledger, state-event stream, receipt,
recovery root, grant, lease, fence, raw transport payload, or secret.

The canonical report is byte/digest deterministic for the same source snapshot
and mapping version. It contains `entries`, `unsupported`, `ambiguous`,
`orphans`, `corrupt`, a compatibility projection, and a backup-required reverse
map. Unsupported and unsafe classes stay visible with their cold/retention/
reject disposition. Source metadata and the permitted fact-file digests are
checked before and after the run; any drift, symlink, unsafe path, or unreadable
mapped fact fails before a report is written.

There is no apply, backup, restore, selector, pilot, cutover, removal, service,
network, release, deploy, or authority command. The reverse map is plan-only:
any future destination write requires a separately authorized migration task
with a quiesced backup and exact preimage proof.

## Authority

Each new TaskRun has one immutable ASCII `task_run_id` and one repository-local
SQLite authority at:

```text
.trellis/.runtime/taskrun/runs/<task_run_id>/authority.sqlite3
```

The database uses foreign keys, WAL, `synchronous=FULL`, and `BEGIN IMMEDIATE`.
It owns ordered hash-chained events, stable operation IDs/input digests, action
plans, validated results, independent reviews, strategy decisions, bounded
interventions and resumes, final-candidate authority, terminal disposition,
immutable final requests, direct final responses, close facts, current
materialization, and projection checkpoints. Reopen must pass SQLite integrity,
event-chain, operation-digest, and
replay/materialization checks or fail closed.

Task JSON, `BOARD.md`, active-task pointers, directory location, and Git object
IDs are projections or observed evidence. `state-events.jsonl` and Loop ledgers
are never written by this path. `read_legacy_task_evidence()` returns only
non-secret identity and digests and does not mutate the legacy directory.

## Events And Replay

- Initialization, start, action plan/result/review/decision, delivery-slot
  settlement, intervention/resume, final-candidate review, final
  request/response, terminal, close prepare, closed, and projected events are
  immutable and canonically encoded. Legacy physical-close effect events remain
  replayable but are not emitted by the status-only close path.
- The same operation ID and input digest replay the durable outcome; changed
  input is a conflict.
- `completed` and `cancelled` are terminal dispositions. They do not imply a
  commit, physical archive, push, release, deploy, or network effect.
- Deterministic replay is the source for task projections. A projection can
  preview the expected closed state while close is prepared because it remains
  non-authoritative. `closed` is a separate authority fact committed before its
  in-place task projection is checkpointed.

## Bootstrap And Strategies

`bootstrap_task_run()` derives one stable run ID from the exact task ID and
task directory, rejects a stale or duplicate binding before creating another
authority, embeds the accepted strategy/envelope in genesis, and replays the
same initialize/start operations after interruption. The `task.py` adapter
selects it only when repository cutover configuration and the task projection
both name TaskRun; the core still does not create a legacy task or mutate
another lifecycle store.

`plan(snapshot)` and `reduce(snapshot, validated_result)` are pure data
functions. `single` exposes one unresolved action at capacity one. `loop`
selects the sorted dependency-ready set within declared capacity and preserves
accepted siblings when an exact problem slice retries. Both strategies use the
same TaskRun events and terminal gate. The Loop adapter accepts only an already
structured Loop result and converts data; it writes neither the Loop ledger nor
the TaskRun authority.

The TaskRun operator remains the only execution writer through stable action,
result, review, and decision operation IDs. A `single` problem has one initial
attempt plus at most three repairs. An operator-bound `loop` counts all initial
slices as one generation, then shares at most three exact-slice repair
generations. Budget exhaustion, unknown effects, or missing effect receipts
pause the run; replacement never resets the budget.
A single-action run reaches `candidate_ready` only from that action's exact
validated candidate and accepted independent review. A loop never treats its
last accepted child as readiness: every child first remains an accepted slice,
then `record_final_candidate_review()` binds an explicit aggregate candidate,
the exact accepted component identities, and a fresh approved reviewer. A
bootstrapped TaskRun may record `completed` only when terminal evidence exactly
equals the binding derived from its immutable final request and one trusted
outer-operator gate receipt. `build_final_request()` is data-only and binds the
exact candidate-ready event and authority position, accepted requirements,
checks and reviews, actual effects, unresolved risks, and the aggregate final
review when present. `issue_final_request()` records that request under a
stable operation ID. The outer operator alone converts the actual direct-user
interaction into a trusted gate receipt. TaskRun only consumes that receipt,
validates exact run/request/replay bindings, records bounded receipt evidence,
and derives the terminal binding. It never creates the receipt or infers user
acceptance from raw fields. Startup, approval, resume, worker, reviewer,
provider, transport, caller-chosen references, and syntactically valid
non-receipt input cannot substitute for the receipt.

## Operator Golden Paths

`TaskRunOperator.admit_single()` and `TaskRunOperator.reopen()` are the one
writer-facing composition surface for a new disposable `single` run. Admission
reads the direct active task's `task.json` and `prd.md`, derives one action from
the shared task-PRD preflight's requirement IDs, touches, and verification
commands, and publishes the
TaskRun-owned in-place task projection with compare-before-replace semantics.
It does not read or write `implement.jsonl`, `check.jsonl`,
`state-events.jsonl`, a Loop ledger, or another lifecycle store. Empty JSONL
files therefore do not block admission, reopen, or dispatch.

When an admitted seed has an active top-level status plus a valid active
parent/child state-machine identity and state under
`workflow_mode=harness_state_machine`, terminal and closed TaskRun projections
preserve the seed's HSM-owned `status`, `completedAt`, `commit`, and state
machine. The TaskRun disposition remains authoritative in SQLite and appears
only through `meta.task_run`; it does not complete the HSM or write
`state-events.jsonl`. Non-active, contradictory, or malformed HSM seeds keep
the existing TaskRun terminal projection behavior.

The immutable start envelope binds the full base commit, a digest of the local
TaskRun Python runtime plus its direct `common.io` dependency, a digest of this
contract, the semantic task/PRD context, bounded worker/reviewer/provider
identities, one total repair budget, the review policy, and any frozen delivery
slots. Exact admission replay uses those stored slots rather than mutable child
projections. `next_action()`
either replays the one unresulted intent after interruption or plans it through
TaskRun, then emits a just-in-time packet
from the current authority snapshot. The packet carries the exact intent,
identities, authority position/digest, bounded worker/provider role, cumulative
repair-budget position, and review requirement. It is not a second manifest or
authority.

A running `single` may consume one separately authorized local-commit
reconciliation before or after candidate readiness. The host-owned callback
must accept the stored request and return one complete receipt; raw mappings
cannot manufacture authority. The request binds the immutable admission base,
the exact non-empty linear non-merge commit chain to current `HEAD`, its parent
and tree, sorted changed paths, and prior plus observed
base/runtime/contract/context identities. The worktree must be clean, the
receipt must bind the request event and use authority distinct from start,
approval, or resume, and exact replay never calls the callback again.

Accepted observed identities become the drift and action-freshness baseline
without mutating the start envelope. The final request and terminal evidence
bind the reconciliation receipt, and a completed non-HSM task projection names
its reconciled head commit. This is a one-time `single` closeout bridge, not a
general rebase: `loop`, dirty, merge, repeated, stale, or uncommitted boundaries
remain write-closed and retain material-drift successor behavior.

Low-risk actions may select `aggregate` action review only when the immutable
start policy says so; high-risk actions select `independent`. Trust boundaries,
external effects, material concurrency, and final candidates are fixed
independent gates and cannot be removed from that policy. Because a `single`
action is also its final candidate, its effective candidate review remains
independent in both risk tiers and stays bound to the exact candidate digest.

`TaskRunOperator.admit_loop()` uses the same authority and projection path. Its
caller supplies the immutable action graph, ordered worker identities,
independent reviewer, capacity, and shared attempt budget directly; there is no
second manifest or Loop lifecycle ledger. `next_actions()` replays every active
packet and fills only remaining capacity. Planned intents are the durable
claims: overlapping repository paths or case-insensitive resource names are
excluded before dispatch, and each packet binds one worker, epoch, stable
operation ID, and digest fence. A stale fence, worker, input, or operation
replay is rejected before a second authority write. Accepted siblings remain
bound while only the attributable failed slice receives a new attempt and
fence. `submit_final_review()` is still required after every slice is accepted;
the last child result never becomes aggregate readiness by itself.

The loop envelope prohibits `git_commit` by default. Enabling it requires both
an existing full local branch ref and a candidate-commit authorization reference
that is distinct from the run authorization. Fresh admission requires that ref
to equal the authority repository's current `HEAD` object without being that
checkout's symbolic branch; every symbolic candidate ref is rejected, so commit
work uses an independent direct branch/worktree. Admission binds the exact
object as both the runtime base and expected-old commit. A one-commit advance is
accepted only while reopening an already-bound TaskRun when the commit is the
exact pending fenced operation. Admission adds the ref as an implicit resource
claim to every commit-capable action. The existing repository lock scans active
claims in every TaskRun authority before planning, so the ref remains a
repository-wide single-writer resource even across runs. The claim remains
active through independent review and every paused unresolved outcome; only a
non-pause terminal action decision or terminal TaskRun releases it. The
effect operation ID includes the TaskRun identity. TaskRun does not create the
commit: a lower executor must use that ID idempotently and return canonical
`taskrun-git-commit-receipt-v1` JSON through structured ingest. The receipt
binds the run, effect operation, input, fence, authorization, ref, expected-old,
commit, and tree. Ingest independently verifies the current local ref, commit
object, exact parent CAS, tree, candidate digest, and the commit's changed paths
against the structured result. Each expected TaskRun trailer key must occur
exactly once with its bound value, and every other `TaskRun-*` trailer is
rejected. Missing,
fabricated, advanced-ref, or ambiguous receipts fail closed. Push, network,
release, and deploy remain prohibited and require separate authority.

Before dispatch, result/review ingest, or final-request/receipt consumption, the
operator re-observes the exact base/runtime/contract/context identities. Drift
blocks that write. `supersede()` then records one `cancelled` predecessor with
one `superseded_by` link only when requirements, scope, touches, tier, parent,
and any delivery-slot contracts remain exact. A loop successor revokes the
prior candidate-commit binding, authority, effect, and ref claim; commit work
after drift requires new explicit authority rather than inheriting an old-base CAS. A repository-wide
execution lock keeps successor admission and predecessor cancellation ordered.
The shared bootstrap also scans the predecessor authority for that durable
reservation, so a crash cannot let a different admission claim the successor.
When budget remains, only the
persisted seed may bootstrap the separately named successor with that budget and
a cumulative offset; when none remains, the link is durable and later admission
is rejected. Local TaskRun attempt keys remain authority-local, while the
operator packet proves that the family repair budget did not reset.

`final_request()` records the immutable request before any direct-user
interaction. For a parent with slots, it first records one replayable
`delivery_slots_reconciled` event derived from each child TaskRun's terminal
event. A cancelled material-drift attempt must lead through exact successor
bindings to one completed attempt whose final request covers the same slot.
Unresolved, cyclic, shared, widened, or foreign chains fail before a parent
write. The final request binds that settlement event and its child terminal
digests; it never infers completion from task JSON or Markdown. `single` and
`loop` children use the same check. `complete()` accepts only a callable
host-owned receipt producer; raw mappings, flags, identities, names, and strings
are not producer authority.
A per-run lock serializes every public mutation from action dispatch through
terminal projection, and a recorded response is resumed without calling the
producer again after interruption. The host remains responsible for proving
that its callback represents the actual direct-user gate; TaskRun performs
structural and exact-binding validation, not authentication. The operator
exports no receipt-construction helper. The callback is a leaf trust producer:
it must not re-enter the same TaskRun while the operator holds the per-run lock.

The operator itself writes no Git commit. `single` permits repository workspace
writes and can consume only the exact local-commit reconciliation above; `loop`
can consume the separately authorized local candidate commit receipt described
above. Archive, push, release, deploy, network, and other external effects
remain prohibited or separately authorized. `close()` delegates to the
existing status-only close and leaves the task directory, pointers, Board,
Git, and external systems untouched.

## Terminal Final-Gate Receipt Contract

### 1. Scope / Trigger

This contract applies only after a bootstrapped `single` or `loop`
execution has current `candidate_ready` authority. Legacy non-execution
completion and explicit cancellation retain their existing terminal behavior.

### 2. Unique Trust Boundary

- The outer operator is the sole trusted producer of the final-gate receipt.
  It owns the mapping from an actual direct-user interaction to that receipt.
- TaskRun is a consumer and exact binder. It validates receipt structure,
  current-run/request identities, canonical digest, decision, and replay, then
  records only bounded receipt evidence.
- TaskRun never creates a receipt, inserts an accepted decision, or upgrades a
  raw object into the fact that the user accepted.
- `direct_user_action=True`, `responder_id`, response identity, timestamps,
  prefixes, naming formats, and syntactic validity are claims or audit data.
  None is authenticity proof.
- Raw chat, transport text, worker/reviewer output, start/resume authority, and
  provider identities never enter TaskRun as a trusted final-gate receipt.

This contract does not add an authentication service, signature scheme, user
registry, provider SDK, or second lifecycle authority. Receipt trust is
established outside TaskRun; TaskRun proves only exact consumption and binding.

### 3. Public Surface

The public surface includes `build_final_request()`, `issue_final_request()`,
`record_final_acceptance()` or its replacement receipt-consumer export,
`TaskRun.record_final_request()`, `TaskRun.record_final_response()`,
`TaskRun.record_terminal()`, and their `taskrun` package exports. No sibling or
lower-level entry may bypass the same receipt requirement.

`record_final_acceptance(run, actor=..., receipt=...)` consumes the exact
outer-operator receipt. `TaskRun.record_final_response(...,
final_gate_receipt=...)` consumes the same receipt under the stable operation
ID `final-response:<receipt_digest>`. Neither entry accepts `final_request`,
`responder_id`, or an `accepted` boolean as a shortcut. Naming a helper
`accept`, `direct`, `user`, or `receipt` does not establish trust.

### 4. Receipt And Terminal Binding

The immutable final request has exactly bounded structured evidence:
`task_run_id`, `candidate_ready`, `authority_position`, `requirement_ids`,
`checks`, `reviews`, `final_review`, `actual_effects`, `unresolved_risks`, and
optional `delivery_settlement` or `commit_reconciliation`, and
`request_digest`. Recording adds `authority_event_digest` and
`authority_event_position` as its durable identity.

The outer-operator receipt has exactly `task_run_id`,
`final_request_digest`, `final_request_event_digest`, `decision`, `receipt_id`,
`receipt_audit_id`, `receipt_audit_at`, and `receipt_digest`. Its decision must
be `accepted`, audit time uses `YYYY-MM-DDTHH:MM:SSZ`, and its digest is the
canonical SHA-256 of every other receipt field. It binds the exact TaskRun,
final-request content and event digests, stable response audit identity/time,
and its own identity/digest. TaskRun validates those bindings without
interpreting any individual field as proof of user action. The recorded
response and returned terminal binding contain only the bounded receipt
evidence, exact candidate-ready object, and request/response authority event
digests and positions. For completed execution, `authorization_ref` must equal
the response event digest and `evidence` must equal the whole derived binding.

### 5. Unified Public-Entry Negative Matrix

`test_final_gate_public_entry_negative_matrix` exercises the public import
surface and direct class methods. A rejected row asserts the exception,
unchanged authority-event count, unchanged terminal state, and unchanged replay
outcome.

| Public entry | Raw flag, identity, or name | Wrong authority or non-receipt | Stale, mismatched, or changed replay | Required invariant |
|---|---|---|---|---|
| `build_final_request` | cannot encode acceptance | invalid snapshot rejected | deterministic for exact snapshot | zero writes |
| `issue_final_request` | cannot encode acceptance | non-current candidate/position rejected | exact replay only | request event at most; never response/terminal |
| `TaskRun.record_final_request` | response-shaped claims rejected | request must equal current facts | changed input conflicts | no response/terminal |
| `record_final_acceptance` or replacement receipt consumer | reject | reject | reject or conflict | zero response/terminal |
| `TaskRun.record_final_response` | reject raw bypass | reject caller-built response | reject or conflict | zero response/terminal |
| `TaskRun.record_terminal` | reject | reject foreign or partial binding | reject or conflict | zero terminal events |
| `taskrun` re-export surface | same as underlying entry | no alias bypass | same replay rule | same event/state invariant |

Positive control: one trusted outer-operator receipt bound to the current
request records one response fact. Only its exact derived terminal binding can
complete a `single` or `loop` run, and exact replay adds no event.

### 6. Required Regression Gates

The following parent acceptance reproductions are durable child tests:

1. mismatched terminal candidate cannot complete;
2. over-scope output leaves durable intervention and cannot disappear;
3. `changes_required` without findings cannot advance repair;
4. start or approval authority cannot complete with valid readiness evidence;
5. invented responder identity cannot synthesize accepted response authority.

Run the unified matrix and all five adversarial cases before the work commit
and again before child completion. Record the exact command, test names, event-count
assertions, and both results in the child stage report. A broad green suite or
a test that covers only the last layer does not replace either checkpoint.
Keep cancellation, intervention/resume, legacy completion, close, Loop v1, and
V3 regressions green.

### 7. Good / Base / Bad Cases

- Good: the outer operator produces one trusted receipt for the exact current
  request; TaskRun consumes and binds it; the exact derived terminal binding
  completes once.
- Base: a ready run without a trusted receipt remains `running` and may still
  be explicitly cancelled.
- Bad: TaskRun constructs `accepted=True` from a responder ID, accepts a raw
  response mapping through a lower-level method, or completes from a start,
  approval, resume, execution, or invented final-looking reference.

## Structured Ingest And Review

Raw provider text and schema-invalid or stale input fail before an authority
event. Ingest binds exact action/attempt/input/freshness identities,
requirement coverage, touches, effects and receipts,
artifact/tree/diff/candidate evidence, checks, and worker, transport, and
provider identities. An otherwise action-bound over-scope, prohibited,
secret-like, or unapproved execution identity records only a bounded reason and
action identity as a durable intervention; raw rejected content is never
stored. The intervention blocks planning and replacement ingest. Only
`resume_execution()` with a new authorization reference and the exact current
intervention digest can clear it, and the resume itself is replayable
authority. Provider output never selects an action, review verdict, retry,
terminal disposition, resume, or Git effect.

Review binds the exact candidate and must use an approved identity distinct
from the worker. A green result first produces `review_required`; only an
accepted independent review can produce `continue` or `candidate_ready`.
`changes_required` must carry at least one actionable finding; an empty finding
set is rejected before an authority write.
Attributable failures produce `retry_exact_slice` while budget remains.
Unknown outcomes pause rather than redispatch blindly. Git commit, close,
archive, push, release, deploy, network, and provider effects remain separately
authorized or out of scope.

## Status-Only Close

`close_task_run()` accepts one exact terminal TaskRun, stable
`close:<task_run_id>:<attempt>` operation ID, direct actor, and the exact active
task directory. Its intent binds the terminal disposition, repository-relative
task path, and exact pre-close task-projection digest. It accepts no commit or
tree input and runs no Git command.

The operation durably prepares and commits the separate `closed` authority
fact. It then moves the bound `task.json` preimage to one operation-stable
hidden claim and publishes the final projection with a no-overwrite atomic
link. A competing atomic path writer wins instead of being replaced. The claim
is removed after exact readback and is recoverable if execution stops between
claim and publish. Supported projection writers must acquire the per-run
directory lock or publish by atomic path replacement. Long-lived in-place
writes through a pre-claim file descriptor bypass that protocol and are
invalid competing lifecycle writers; a filesystem-only compare-and-swap cannot
make them safe. The directory lock serializes close callers without a
repository-global lock. Close finally checkpoints the resulting projection
digest. Exact replay is idempotent; a changed actor, path, terminal disposition,
or operation input conflicts before another write.

Unreadable, missing, identity-drifted, or concurrently replaced projection
bytes fail closed and are never overwritten on recovery. The operation remains
at its durable prepared or authority-committed phase; replay can continue only
from the exact bound preimage, recoverable claim, or exact final projection. A
projected replay also verifies the final bytes and records projection drift
rather than reporting stale success. The original task directory, session
pointers, `BOARD.md`, PRD and other task files remain untouched after the
operation. Completed and cancelled runs use the same close path.
After close, `task.py finish` may clear only the non-authoritative session
pointer; it does not archive or change TaskRun authority. A separately
authorized `task.py archive` commits the terminal projection and proof, moves
the task through the existing archive transaction, clears pointers, retires
only the bound SQLite/WAL files, and deletes the successful transaction
journal. `--no-commit` is rejected for admitted TaskRuns. Interrupted cleanup
keeps one recovery journal and is replayed by the same archive command.

Commit, archive, push, release, deploy, network, migration, and cleanup remain
separately authorized effects. Optional Git evidence belongs to the operation
that actually observed an authorized Git effect; close neither requires nor
manufactures it. Existing physical-close event streams remain readable under
their original authority, but new status-only close operations emit no legacy
effect or unknown-effect event.

## Retired Authority Evidence

### 1. Scope / Trigger

This path applies only after a completed or cancelled, closed TaskRun's SQLite
authority is deliberately retired. The archived task directory retains one
`terminal-proof.json` as read-only evidence.

### 2. Signatures

- `validate_taskrun_terminal_proof(task_dir, repo_root, task, projection)`
  returns inactive `TaskActivity` or raises `TASK_STATE_INVALID`.
- Task activity and ordinary task validation call the same validator only when
  the bound SQLite path is absent.

### 3. Contracts

- The proof must exist at the exact task-local path, be committed there, and
  match its bytes at `HEAD`.
- It binds `task`, `run`, `terminal_event_chain`, `disposition`, `projection`,
  and `git` evidence under `taskrun-terminal-proof-v1`.
- The task projection must be completed or cancelled and `closed`; task/run identities,
  task projection digest, event summary and tail, disposition, checkpoints,
  and Git commit/tree ancestry must verify exactly.
- Success returns inactive reason `verified_taskrun_evidence_only` and state
  `closed`. It creates no ledger and grants no lifecycle authority.

### 4. Validation & Error Matrix

- SQLite path absent plus exact valid proof -> evidence-only classification.
- SQLite file, symlink, directory, unreadable path, or corrupt database exists
  -> ordinary SQLite handling; proof fallback is forbidden.
- Missing, uncommitted, modified, partial, mismatched, stale, foreign,
  nonterminal, non-closed, broken-chain, or unreachable-Git proof ->
  `TASK_STATE_INVALID`.

### 5. Good / Base / Bad Cases

- Good: the committed proof matches the unchanged closed task projection and
  every referenced Git commit and tree is reachable.
- Base: SQLite still exists, so existing TaskRun authority behavior is
  unchanged and the proof is ignored.
- Bad: status text or a proof filename is treated as terminal truth without
  verifying committed bytes and every bound field.

### 6. Tests Required

- Positive activity, task-validation, and Board-exclusion coverage.
- Table-driven rejection coverage for each invalid proof class.
- An existing corrupt SQLite file must fail instead of falling back.

### 7. Wrong vs Correct

- Wrong: infer a closed TaskRun from `task.json` or reconstruct a database from
  the proof.
- Correct: classify immutable retained evidence only after the SQLite path is
  absent and the exact committed proof verifies; never authorize start,
  resume, terminal, close, or cancellation from that proof. Archive replay may
  only finish pointer, authority, and transaction-journal cleanup already
  bound to the committed archived task.

## Verification

Use disposable roots only until a separately accepted pilot:

```bash
PYTHONPATH=.trellis/scripts python3 -m unittest discover -s .trellis/scripts/tests -p 'test_taskrun*.py'
python3 -m unittest discover -s .trellis/scripts/tests -p 'test_execution_strategies.py'
python3 -m unittest discover -s .trellis/scripts/tests -p 'test_v3_invariants.py'
python3 -m compileall -q .trellis/scripts
```

Fault tests must cover every boundary named by `FAULT_BOUNDARIES`: prepared
close, committed authority, in-place task projection, and projection
checkpoint. Existing Current Trellis task files, non-selected TaskRun roots,
and Loop authority bytes must remain byte-identical outside the selected
disposable TaskRun.
