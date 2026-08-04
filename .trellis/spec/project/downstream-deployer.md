# Downstream Deployer

## 1. Scope / Trigger

Use this contract when changing the repository-owned downstream deployment
manifest, source/target eligibility, private candidate construction, official
materialization, overlay planning, preservation, intentional deletion, exact
plan identity, promotion, recovery, adoption receipts, exact-scope local Git
transactions, one-off/grant authority, durable updater state, inbox/reminders,
or fixed-order cycle decisions.

Planning is read-only with respect to the source checkout, enrolled target, Git
refs/index, and authority store. Transaction behavior is available only through
an exact plan and target-derived candidate. Authority/state behavior records
durable decisions and never invokes planning or transactions. The public CLI
is the only composition surface. The machine wrapper remains a separate,
semantic-free contract.

## 2. Signatures

```python
plan_target(
    source_root: Path,
    target_root: Path,
    target_id: str,
    *,
    manifest_path: Path | None = None,
    scratch_root: Path | None = None,
    candidate_output: Path | None = None,
    verification_commands: Sequence[Sequence[str]] = (),
) -> dict[str, object]
```

Official materialization inside private scratch:

```text
trellis update --force --migrate
```

When `TRELLIS_CLI` is supplied, it must name one absolute executable file and
the planner invokes that exact resolved executable while retaining the
canonical command identity in evidence. An invalid locator fails before
materialization. Qualification and final-integration fixtures must use this
locator rather than ambient `PATH`; ordinary operator use retains the canonical
`trellis` lookup for compatibility.

The later public CLI may call this function but must not add another planning
implementation.

Exact transaction:

```python
apply_transaction(
    source_root: Path,
    target_root: Path,
    candidate_root: Path,
    plan: Mapping[str, object],
    *,
    verification_commands: Sequence[Sequence[str]],
    recovery_root: Path,
    local_commit_authority: Mapping[str, object] | None = None,
) -> dict[str, object]

verify_transaction(
    source_root: Path,
    target_root: Path,
    plan: Mapping[str, object],
    final_receipt: Mapping[str, object],
    *,
    verification_commands: Sequence[Sequence[str]],
) -> dict[str, object]

recover_transaction(
    target_root: Path,
    recovery_root: Path,
    transaction_id: str,
    recovery_authority: Mapping[str, object],
    recovery_binding: Mapping[str, object],
) -> dict[str, object]
```

`source_root`, `target_root`, `candidate_root`, and `recovery_root` are exact
explicit local paths. None is serialized in receipts or result evidence.

Durable authority/state:

```python
default_state_path(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path

store = AuthorityStore.initialize(
    path: Path,
    *,
    notifier: Sequence[str] | None = None,
    notifier_timeout: int = 10,
)

store.request_one_off(...)
store.approve_one_off(..., direct_user_action=True)
store.consume_one_off(...)
store.record_receipt(...)

store.request_grant(...)
store.capture_grant(..., direct_user_action=True)
store.activate_cohort(..., direct_user_action=True)
store.renew_grant(..., direct_user_action=True)
store.retire_grant(...)
store.pause_target(...)
store.resume_target(..., direct_user_action=True)
store.consume_grant(...)

store.start_cycle(...)
store.record_attempt(...)
store.record_cycle_skip(...)
store.block_cycle_source(...)
store.derive_deadlines(...)
store.snapshot()
store.events()
store.rebuild_projection()
```

Every mutation takes one stable `event_id` and an optional explicit RFC3339
UTC `now`. Authority and grant IDs are `sha256:<64 lowercase hex>`. The public
CLI child composes these methods; it must not add a second state machine.

## 3. Contracts

Inputs:

- `source_root` and `target_root` are disjoint exact Git worktree roots.
- `target_id` is one logical ID in manifest order and must equal the bound
  target root basename.
- Source is the exact canonical Git root on a symbolic branch with stable
  HEAD/tree, an exact-valid configured qualification receipt, the bound
  manifest, and unchanged official/overlay payloads. Unrelated source task,
  `BOARD.md`, project, or generated state is not a source eligibility gate.
- Target is clean, committed, on a symbolic branch, and has no canonically
  classified active task. Verified staged/HSM/Loop terminal evidence-only tasks
  do not block planning; lifecycle conflicts or malformed facts fail closed as
  `TASK_STATE_INVALID`.
- `manifest_path` is a real source-owned path without symlink components.
- `scratch_root` is an existing real directory disjoint from source and target.
- `.trellis/config.yaml` remains target-local project data. Deployment never
  copies source receipt/default settings or `taskrun_v1.new_code_tasks`
  activation into it. New target tasks use TaskRun only after a separate
  target-local change enables cutover against the verified installed payload.
  Existing Loop tasks retain their original qualification and lifecycle.
- A target configured as `downstream_project` uses
  `qualification_layers.admission_mode: installed_runtime` with exact
  repository-relative artifact/local-runtime receipt paths and digests.
  Admission verifies those target-local receipts and installed managed bytes
  without reading the source checkout; project Git and task state are excluded.
- Source planning, apply, transaction verify, and receipt production retain
  strict `source_release` qualification. A pre-cutover target may separately
  activate local Loop admission; after TaskRun cutover only already-recorded
  Loop advance, integration, recovery, and cancellation use that target-local
  runtime identity and remain operable when the canonical source checkout is
  unavailable.

Manifest schema v2 adds:

```json
{
  "deployment": {
    "policy_schema_version": 1,
    "source_id": "trellis-harness",
    "targets": ["RAG v2", "FamiliOS", "Hermes stock", "qivance-music", "xhs-qn-pipeline", "AH-map"],
    "target_only_owner": "project",
    "preserve": ["<exact project/generated path>"],
    "adoption_projection": {
      "path": ".trellis/deploy/adoption.json",
      "owner": "project",
      "source_disposition": "preserve",
      "write": "deployer-replace-only",
      "delete": "never"
    },
    "intentional_deletions": []
  }
}
```

Candidate order is fixed:

1. Capture source and target identities.
2. Copy the exact target worktree, excluding `.git`, into private scratch.
3. Run official materialization there, restore every project/generated/
   target-only path from the target preimage, require exact official
   `.version` bytes, and schema-validate target-local
   `.template-hashes.json` without comparing it to source bytes.
   Updater-created `.trellis/.backup-<timestamp>` paths are removed from the
   candidate and recorded as `.trellis/.backup-TIMESTAMP` in preservation
   evidence; non-timestamped target paths retain their exact names.
4. Record the post-materialization private preimage.
5. Compare/apply overlay-owned source bytes.
6. Apply only exact declared file deletions with matching owner and preimage.
7. Verify project/generated/target-only preservation.
8. Run the plan-bound verification commands against the complete private
   candidate with Python bytecode writes disabled, then prove the candidate
   snapshot is unchanged and record only command/output digests. A command
   failure or any write removes scratch and emits no candidate artifact or
   applicable plan.
9. Emit predicted mutations and delete scratch.
10. Recheck source and target identities and compute `plan_digest`.

Overlay-owned Python payloads must include the runtime dependency closure
needed by the plan-bound smoke command, including top-level providers such as
`state_machine.py` that sit outside a distributed package tree. Candidate
verification is the executable ownership check; target-local patches are not a
compatibility fallback.

Source qualification is valid only when its receipt binds passing evidence
from the pinned oldest supported downstream candidate using the real updater,
complete overlay, task CLI and delayed cancellation-capability smokes. Runtime
or manifest changes invalidate that receipt before planning can create
authority-eligible output.

When `candidate_output` is absent, the result contains only logical IDs,
repository-relative paths, Git/digest identities, ordered phase evidence,
predicted mutations, cleanup evidence, and read-only proof. When it is present,
the same candidate is copied to that new explicit disjoint result directory and
the plan records only `artifact_persisted=true`, never the path. The plan binds
the verification argv digest and the effective commit policy, including the
eligible hook fingerprints. It never serializes absolute machine paths or raw
command output.

Transaction order is fixed:

1. Recompute the plan digest and reject any missing, duplicate, unsorted, or
   non-managed mutation.
2. Revalidate the current qualified source commit/tree/receipt and the target
   branch/HEAD/tree/preimage/instructions/task/check/commit-policy identities.
3. Prove that the persisted candidate payload and exact diff equal the plan.
4. Run the plan-bound checks in the candidate.
5. Persist an exact machine-local preimage for only the predicted mutations and
   `.trellis/deploy/adoption.json`; this recovery artifact is outside source
   and target and is not receipt authority.
6. Recheck source, target, and candidate identities immediately before the
   first target write.
7. Promote only the exact mutation paths with atomic file replacement, write
   the deterministic adoption projection, prove the changed path set, and run
   the same checks in the target. Ignored official metadata remains in the
   exact mutation, preimage, recovery, and final-receipt scopes but is excluded
   from the Git-visible changed-path set.
8. If later authority explicitly enables local commit, recheck branch/ref,
   instructions, hooks/signing policy, stage only explicit paths, prove exact
   staged-set equality, exclude ignored official metadata from the pathspec,
   and invoke normal `git commit`.
9. Emit candidate and final receipts only after verification. Any
   mutation-phase failure restores the exact preimage and reports `recovered`;
   missing restoration proof reports `unknown_outcome`, never success or retry.

The adoption projection is the sole project-owned write exception. It binds
the exact plan, source receipt, source commit, target base/branch, manifest,
overlay version, and pre-commit candidate receipt. The final receipt binds that
receipt, projection, final payload, checks, and optional commit evidence. This
avoids a self-referential commit/receipt digest; the authority store owns
the reverse final-receipt-to-commit record.

Local commit authority is a caller-supplied exact envelope produced by the
authority layer. It must explicitly enable commit and bind one
`one_off` or `grant` ID, the plan, source receipt, target branch/base, and
accepted lineage anchor. Plan/apply authority alone is insufficient. Commit
uses normal hooks/signing and process-local identity
`Trellis Loop Updater <trellis-loop-updater@localhost>`. It never changes Git
config and never pushes, merges, amends, tags, switches branches, resets,
cleans, rebases, or forces history.

The canonical authority database resolves to:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/trellis-loop-updater/state.sqlite3
```

Task C tests always pass an explicit temporary path and never open that live
path. The database file is `0600`; symlink components are rejected. SQLite uses
WAL, foreign keys, `synchronous=FULL`, a five-second busy timeout, and
`BEGIN IMMEDIATE`. Schema v1 contains only immutable `events`, one
`schema_meta` row, and one rebuildable `projections` row. Event insertion and
full projection replay commit atomically. Stable event replay returns the first
result; changed input under the same ID raises `EventConflict`. Event clock
regression, unknown schema/event/field, corrupt JSON/digest, or stale projection
fails closed.

The sole public launcher is:

```text
PYTHONPATH=.trellis/scripts python3 -m downstream_deployer.cli <command> --input <json-file>
```

Supported commands are `plan`, `single-target`, `one-off-request`,
`one-off-approve`, `apply`, `verify`, `recover`, `status`, `inbox`,
`grant-request`, `grant-capture`,
`grant-renew`, `grant-revoke`, `grant-supersede`, `pause`, `resume`,
`cohort-activate`, and `cycle`. `grant-renew` and `grant-supersede` name the
same immutable renewal transition: a new active grant supersedes the old one.
There is no arbitrary supersede-without-renewal operation.

Every command reads one command-specific JSON object from `--input`. Unknown or
missing keys, malformed JSON, symlinked input, stale identities, and mismatched
scope fail closed. Mutating authority commands require explicit RFC3339 `now`
and stable event IDs. `plan` writes one new plan file plus one new private
candidate at explicit disjoint paths. Other commands consume those exact
artifacts; no command infers a target, plan, state path, recovery root, grant,
or authority. Before initializing authority state, `apply`, `recover`, and
`cycle` reject a `state_path` that overlaps any source, target, candidate,
recovery, or scratch root in that command.

`single-target` is an opt-in exact-core envelope with only these keys:

```json
{"operation":"plan|apply|verify|recover","request":{}}
```

The nested request is passed unchanged to the corresponding existing handler,
which retains its planner, transaction, authority, verification, and recovery
semantics. The envelope cannot select `cycle`, grants, cohort activation,
status, or inbox behavior; it neither creates nor owns a second state machine.
The direct commands and the fixed-five fleet `cycle` remain compatible.

Normal stdout is exactly one compact ASCII JSON envelope:

```json
{"command":"plan","result":{},"schema_version":1,"status":"ok"}
```

Input or domain non-success is exactly one redacted error envelope:

```json
{"command":"plan","error":{"code":"SOURCE_DIRTY","detail":"sha256:<digest>"},"schema_version":1,"status":"error"}
```

Exit `0` means success, exit `1` means a truthful domain non-success, and exit
`2` means CLI/input misuse. Absolute paths, secrets, raw subprocess output, and
tracebacks never enter either envelope. Durable transaction/cycle evidence
remains queryable through `status` and `inbox` after a non-success.

`apply` consumes exact apply authority before calling `apply_transaction`; an
optional local commit consumes a second exact commit authority. It records the
final or recovery receipt against the apply consumption. `verify` is read-only
and rechecks the exact source, plan, target, projection, receipt, Git state,
and plan-bound checks. `recover` accepts only consumed one-off recovery
authority bound to the failed transaction and retained preimage. Successful
local commit evidence is retained beside the immutable preimage so standalone
recovery can restore the exact base ref, index, and files.
The retained preimage stores only the target branch, base head, and digest of
the complete pre-transaction identity; it does not persist the full target
file inventory.

`cycle` computes and freezes the current source identity before target planning,
requires exactly the versioned five target entries in order, consumes standing
plan/apply/commit grant authority separately, creates each plan only when its
slot begins, and records every slot durably. A source drift blocks all unstarted
slots. Only one allowlisted, proven-zero-write preflight failure retries the
same plan/candidate. Any non-success keeps the aggregate cycle non-success;
later targets continue only when all five independence dimensions remain true.

The optional machine wrapper keeps one existing timer and runs two sequential,
independently reported stages:

1. `source-maintenance` attempts the official source update only when the
   canonical checkout is clean and its repository-wide task inventory contains
   no planning or in-progress task. Task or dirt gates are safe skips.
2. `downstream-cycle` invokes this repository-owned `cycle` CLI regardless of
   the maintenance gate result. The CLI's qualified-source preflight remains
   the only cycle eligibility decision.

The wrapper may provide explicit enrollment, bounded invocation, redacted
stage logs, and exit propagation only. It must not reproduce source
qualification, planning, target Git, transaction, authority, receipt, grant,
promotion, or recovery semantics. A maintenance skip plus a successful cycle
is overall success. An attempted maintenance failure remains overall
non-success even when the independently evaluated cycle succeeds.

One-off bindings contain the exact common target/policy envelope plus
`base_head`, `plan_digest`, and `source_receipt_id`; standalone recovery also
contains `failed_transaction_id` and `preimage_digest`. `apply`, `commit`, and
`recover` are separate requested/approved/consumed authorities. Approval
requires an exact request digest, durable response identity, and
`direct_user_action=True`; consumption is single-use and must fall inside its
exact time window.

Manifest target enrollment and recurring-cycle membership are distinct.
`AH-map` is accepted by planning, one-off action authority, and transaction
receipts. It is rejected by standing-grant request and therefore cannot enter
cohort activation, pause/resume, or `cycle`. The fixed `TARGET_ORDER` remains
`RAG v2`, `FamiliOS`, `Hermes stock`, `qivance-music`, `xhs-qn-pipeline`;
machine updater enrollment is outside this repository effect.

Plan output is authority-eligible only after candidate verification passes.
The same normalized verification command list is rerun during apply and
read-only verify; the planning run does not replace either transaction-time
check.

Standing-grant common bindings contain exactly:

```text
target_id, repository_id, branch, lineage_anchor, adoption_receipt_id,
deployment_policy_digest, ownership_digest, preservation_digest,
effect_digest, check_policy_digest, recovery_policy_digest,
commit_policy_digest, notification_policy_digest
```

`record_receipt` stores only IDs/digests and requires an already consumed exact
one-off or grant authority; final receipts may bind the resulting local commit
OID. Grant request requires its bound adoption receipt to exist in the same
store as `verified` for the same target.

Grant request and direct-user capture leave the grant `disarmed`. One
direct-user cohort activation requires exactly the versioned target order:
`RAG v2`, `FamiliOS`, `Hermes stock`, `qivance-music`, `xhs-qn-pipeline`.
Every grant shares the common `not_before` and expires exactly 30 days later.
Renewal is a new direct-user active grant that supersedes one unchanged active
lineage without extending the old record. Direct-user revoke and automatic
policy invalidation are distinct. Pause is separate target state; direct-user
resume requires the same still-active grant and lineage and changes no time or
scope. Standing grants authorize only exact `plan`, `apply`, or `commit`
consumptions. The preceding `plan` consumption binds the exact source receipt,
target base, and common policy envelope before a plan digest exists.
Standalone recovery always needs a one-off recovery authority.

Cycle state freezes exact source branch/commit/tree/qualification receipt,
manifest policy, and official/overlay payload identity. The compatibility
`status_digest` binds that qualified source identity, while
`task_fingerprint` deliberately carries no source task gate. Slots use the same
fixed order and must become stable serially. Retry occurs once only for `CHECK_TIMEOUT`,
`LOCK_BUSY`, or `TOOL_TEMPORARILY_UNAVAILABLE` before mutation with a durable
zero-write digest; the retry must reuse source, plan, receipt, and target
identity. Any other non-success pauses. Later slots remain eligible only when
root/state/lock/recovery/grant independence is all true; otherwise all
remaining slots become stable `blocked_by_previous`. Source drift makes every
unstarted slot `source_blocked`. Any failure keeps the aggregate cycle
non-success.

T-7d, T-1d, and expiry events are derived with immutable grant/milestone dedup
and catch-up. Retirement cancels existing reminder projections; expiry pauses
the target. Every business event appears in durable inbox state. Only
exception, recovery, authority-degradation, renewal, and expiry events invoke
the optional notifier. The actionable event commits first; notifier receives
redacted IDs/digests through stdin as one bounded argv subprocess. Absence or
failure creates a separate delivery-status event and never changes authority
or recursively notifies.

## 4. Validation & Error Matrix

| Condition | Required result |
|---|---|
| Source/target overlap or target ID/path mismatch | Reject before target work |
| Non-root/detached source, or invalid receipt/manifest/managed payload | Reject before target planning |
| Dirty, detached, canonically active-task, `TASK_STATE_INVALID`, or non-root target | Reject before scratch |
| Missing/invalid active source receipt | Reject before target planning |
| Unknown manifest schema/policy/owner/scope/target | Reject before scratch |
| Managed path or instruction symlink | Reject before materialization/copy |
| Managed file/tree type conflict | Reject before overlay copy |
| Official command missing, timeout, nonzero, wrong `.version` bytes, or invalid materialization metadata | Clean scratch and reject |
| Overlay tree has an undeclared target descendant | Reject; source absence is not deletion authority |
| Project/generated/target-only bytes still differ after target-preimage restoration | Reject as preservation violation |
| Deletion lacks exact owner/file/preimage disposition | Reject before candidate completion |
| Source or target differs after planning | Reject; never claim read-only success |
| Plan/candidate/check/source/target identity differs at transaction preflight | Reject with zero target writes |
| Recovery root overlaps source/target or transaction ID already exists | Reject before target writes |
| CLI state path overlaps a command source/target/candidate/recovery/scratch root | Reject before state initialization |
| Recovery root or an existing path component is a symlink | Reject before preimage capture or target writes |
| Candidate verification fails | Reject before target writes |
| Promoted path set differs from mutations plus adoption projection | Recover exact preimage or report unknown |
| Target verification fails after mutation | Recover exact preimage; report `recovered`, not success |
| Commit authority is missing/stale/foreign or lineage is invalid | No local commit; reject before target writes |
| Branch/ref/instruction/hook/signing policy drifts | Reject before mutation or recover if mutation began |
| Staged set expands, hook/signing/commit fails, or committed tree mismatches | Recover exact files/index/ref when provable; otherwise `unknown_outcome` |
| Recovery file, byte, mode, index, ref, or final identity cannot be proven | `unknown_outcome`; authority layer must pause |
| State path or an existing component is a symlink; schema is unknown | Reject before authority mutation |
| Stable event ID is replayed with changed type, payload, or time | `EventConflict`; preserve first event |
| Event time moves backward or projection position/digest is stale | Reject; rebuild projection only from immutable events |
| One-off request digest, effect, binding, window, or direct-user approval differs | Reject without consumption |
| Receipt authority/consumption, target, status, plan, source, transaction, or payload identity differs | Reject the receipt event |
| Grant is requested/disarmed/expired/retired/paused or policy/lineage binding drifts | Reject plan/apply/commit authority |
| Cohort is not exactly five disarmed grants in frozen order/common time | Reject activation atomically |
| Revocation lacks direct-user identity, or invalidation claims direct-user revocation | Reject the retirement event |
| Retry reason is not allowlisted, zero-write proof is missing, or retry identity changes | Persist terminal failure/pause; never retry |
| Unknown outcome claims full recovery independence | Reject the cycle result |
| Later slot is recorded before the current slot is stable | Reject fixed-order cycle mutation |
| Notifier is absent, times out, or returns nonzero | Keep authority; record `absent` or `failed` delivery |

## 5. Good / Base / Bad Cases

- Good: an exact qualified source plus one clean enrolled target produces a
  persisted digest-matched candidate; exact promotion, checks, projection, and
  explicitly authorized pathspec commit produce verified receipts and one
  clean child commit.
- Base: local commit authority is absent; exact promotion may complete and
  return verified non-commit evidence, but plan/apply never implies a commit.
- Authority base: five captured grants remain disarmed and have zero
  plan/apply/commit authority until one direct-user cohort activation.
- Bad: a candidate byte, source receipt, target ref, hook, staged path, or
  recovery byte drifts. The transaction either performs zero writes, proves
  exact recovery, or reports unknown outcome without success.
- Authority bad: policy drift, replay with changed input, a second failed
  retry, stale time, or unknown recovery state persists a reject/pause outcome
  and never arms or broadens authority.

## 6. Tests Required

- Execute the exact official subprocess adapter in disposable scratch.
- Prove official materialization evidence precedes the post-materialization
  fingerprint and overlay evidence.
- Prove valid target-specific template-hash bytes need not equal source bytes,
  malformed metadata fails closed, and `.version` remains exact.
- Prove deterministic repeated plans and source/target read-only equality.
- Prove an explicit candidate artifact exactly matches the plan without
  persisting its machine path.
- Prove unrelated source task/`BOARD.md` evidence does not block planning,
  while invalid receipt, manifest/managed-payload drift, managed-payload ABA,
  and every target task/dirt gate still fail closed with zero target writes.
- Cover target binding, managed symlink/type conflict, stale overlay
  descendants, project preservation, exact deletion, deletion drift,
  redaction, and cleanup.
- Prove candidate/target verification, exact promotion, adoption projection,
  non-secret receipts, explicit pathspec, staged equality, deterministic
  trailers/identity, ignored-metadata exclusion from staging, normal hooks, and
  unchanged Git config in disposable repositories.
- Fault candidate/source/authority drift before mutation, target checks after
  mutation, commit hooks, and recovery proof. Assert zero-write rejection,
  `recovered`, and `unknown_outcome` remain distinct and never auto-retry.
- Prove CLI state/root overlap and recovery-root symlinks fail before state or
  target writes, and retained recovery metadata exposes only the target
  identity digest rather than its file inventory.
- Qualification must bind the deployer package plus the exact positive/negative
  deployer test identities and reject omitted, substituted, uncommitted, or
  changed deployer bytes.
- Prove one-off apply/commit/recovery separation, exact request approval and
  single consumption, stable replay, event conflicts, `0600` state, and path
  redaction.
- Prove exactly five disarmed grants, frozen activation order, 30-day windows,
  policy drift rejection, pause/resume, renewal/supersession, direct revoke,
  automatic invalidation, and expiry.
- Prove T-7d/T-1d/expiry catch-up and dedup, actionable-event-before-delivery
  ordering, notifier absence/failure isolation, and reminder cancellation.
- Prove frozen-source/fixed-slot cycles, one zero-write retry, retry identity
  equality, later-target independence, source blocking, aggregate non-success,
  corrupt projection detection, and exact event-only rebuild.

## 7. Wrong vs Correct

Wrong: materialize or compare overlay bytes in the live target, then call the
result a dry run.

Correct: materialize in a private target-derived candidate, record the new
private preimage, apply overlay decisions there, delete scratch, and prove the
live target identity is unchanged.

Wrong: delete an old file because it is absent from the source tree.

Correct: delete only one manifest-declared managed file whose owner and exact
target preimage digest match the plan.

Wrong: rebuild or broaden a candidate during apply, stage with `git add .`,
bypass hooks/signing, or call a recovered transaction successful.

Correct: consume the exact persisted candidate and plan, capture preimage
before the first write, stage the exact verified path set only under separate
commit authority, and report recovery truthfully.

Wrong: treat one-off apply wording as commit/grant/recovery authority, activate
four or reordered grants, retry a mutation failure, or let notifier failure
rewrite authority.

Correct: consume one exact authority effect, activate exactly the frozen
five-grant cohort, retry only one proven zero-write transient under unchanged
identities, and commit notifier delivery as non-authoritative status.
