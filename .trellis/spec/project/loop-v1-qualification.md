# Loop v1 qualification and local activation

## 1. Scope / Trigger

Use this contract when changing Loop v1 qualification, receipt identity,
admission, operation-time receipt checks, rollback, or overlay ownership.
Qualification is local-only. It must not mutate the canonical repository or any
downstream repository by updating the official base, applying an overlay,
starting a pilot, or synchronizing repositories. Qualification must exercise one local
temporary clean-clone fixture that advances between two local official-base Git
revisions and applies the pinned overlay after a complete no-mutation preflight.
It must not use a remote, CI, installed hooks, or network for correctness.

## 2. Signatures

Commands:

```text
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] run --output <json>
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] receipt --qualification <json> --output-dir <dir> [--runtime-commit <commit>]
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] verify --receipt <json> [--expected-digest <sha256>]
python3 -m loop_v1.qualification --repo-root <portable-release-root> artifact-receipt --qualification <json> --output-dir <dir> [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <portable-release-root> artifact-verify --receipt <json> [--expected-digest <sha256>] [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] local-receipt --artifact <json> --output-dir <dir> [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] local-verify --artifact <json> --receipt <json> [--expected-digest <sha256>] [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] target-receipt --artifact <json> --local-receipt <json> --target-root <path> --plan <json> --final-receipt <json> --verification-commands <json> --output-dir <dir> [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] target-verify --artifact <json> --local-receipt <json> --receipt <json> --target-root <path> --plan <json> --final-receipt <json> --verification-commands <json> [--expected-digest <sha256>] [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <runtime> receipt-read --receipt <json>
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] overlay-check [--manifest <json>]
python3 -m loop_v1.qualification --repo-root <repo> disable --reason <text>
python3 -m loop_v1.qualification --repo-root <runtime> [--installed-root <installed>] enable
```

For `target-receipt` and `target-verify`, `--verification-commands` names one
UTF-8 JSON object with exactly `{"commands":[["argv0","arg1"]]}`. The
commands are passed unchanged to the established read-only transaction verifier;
the layer CLI never parses shell text or creates target authority.

Runtime entry points:

```python
configured_qualification(repo_root, envelope_receipt=None, installed_root=None) -> QualificationStatus
capture_execution_binding(repo_root, qualification) -> dict
execution_qualification(repo_root, binding) -> QualificationStatus
legacy_execution_binding(receipt_id) -> dict
verify_conformance_receipt(repo_root, receipt_path, expected_digest=None, installed_root=None) -> dict
generate_conformance_receipt(repo_root, qualification_result, output_dir, runtime_commit="HEAD", installed_root=None) -> Path
generate_artifact_qualification_receipt(repo_root, qualification_result, output_dir, manifest_path=None) -> Path
verify_artifact_qualification_receipt(repo_root, receipt_path, expected_digest=None, manifest_path=None) -> dict
generate_local_runtime_receipt(repo_root, artifact_receipt_path, output_dir, manifest_path=None, installed_root=None) -> Path
verify_local_runtime_receipt(repo_root, artifact_receipt_path, receipt_path, expected_digest=None, manifest_path=None, installed_root=None) -> dict
generate_target_verification_receipt(source_root, target_root, artifact_receipt_path, local_runtime_receipt_path, plan, final_receipt, verification_commands, output_dir, manifest_path=None, installed_root=None) -> Path
verify_target_verification_receipt(source_root, target_root, artifact_receipt_path, local_runtime_receipt_path, receipt_path, plan, final_receipt, verification_commands, expected_digest=None, manifest_path=None, installed_root=None) -> dict
read_qualification_receipt(receipt_path) -> dict
disable_loop_v1_admission(repo_root, reason) -> dict
ParentLedger.assert_runtime_qualification(lease) -> None
```

Config keys under `loop_v1`:

```yaml
admission_enabled: true|false
parent_default: current_trellis|loop_v1
qualification_receipt: <repository-relative JSON path>
qualification_receipt_digest: <64 lowercase hex characters>
```

Config keys under `qualification_layers`:

```yaml
admission_mode: legacy|installed_runtime
artifact_receipt: <repository-relative JSON path>
artifact_receipt_digest: <64 lowercase hex characters>
local_runtime_receipt: <repository-relative JSON path>
local_runtime_receipt_digest: <64 lowercase hex characters>
```

`harness_source` uses `legacy`. A `downstream_project` may use
`installed_runtime`; all four receipt fields are then required and bind
target-local artifact and local-runtime receipts. Unknown, incomplete, or
role-contradictory modes fail closed without falling back to legacy.

`parent_default: current_trellis` is valid only for disabled/bootstrap state.
An enabled qualified activation requires `parent_default: loop_v1`; Current
Trellis remains available as an explicit selector for one parent.

## 3. Contracts

- `run` emits ordered Q01-Q12 rows. Every row has one positive and one negative
  test identity, exact command, return code, test-source digest, and normalized
  stdout/stderr digests. It also emits the fixed positive/negative downstream
  deployer planning test identities and their result digest. It additionally
  materializes the pinned RAG v2 `0.6.5` compatibility fixture with the real
  `trellis update --force --migrate` command, applies the complete overlay, and
  runs both `python3 .trellis/scripts/task.py --help` and a bytecode-disabled
  `state_machine.cancel_task` import smoke in that candidate. Evidence omits
  elapsed time so two runs against the same committed runtime are byte-identical.
- Q05 exercises resume-stale replacement and directly rejects a late old-epoch
  result without authority mutation; Q06 exercises accepted path-closed
  cross-child worker-test replacement plus missing-path and passing-branch
  rejection without authority mutation; Q07 binds
  failed-integration ref preservation, exact round-3 transition to
  `recovery_waiting`, same-run generation continuation, and the complete-graph
  final integration check's once-only pass and non-attributing failure paths.
- Qualification subprocesses pass the explicit installed-root locator and may
  bind an exact `TRELLIS_CLI` executable. Parent checks preserve only those
  locators plus a minimal process environment, force isolated Git config, and
  remove ambient user-only variables and executable search paths.
  Q11 binds a final-review problem naming a strict subset of a
  multi-requirement child to a complete one-to-one child replacement, preserved
  dependent graph identity, carried required findings, and a fresh
  exact-integration review. A runtime that narrows replacement coverage,
  executes final checks on a partial graph, reruns a committed final check, or
  unconditionally emits `human_intervention` for routine attributable failures
  is not qualified.
- A receipt filename is `<receipt_digest>.json`. `receipt_id` is
  `sha256:<receipt_digest>`. The payload binds the committed runtime files and
  Git commit/tree, ledger schema and migration generation, Q evidence, exact
  deployer planning evidence, the real oldest-downstream candidate
  compatibility evidence, their test sources, local tool environment, overlay
  manifest/payload, local-only flags, and the required-issue list.
- Layered receipts use a separate schema and are hash-addressed in the same
  way. An `artifact` receipt binds only portable release facts: official and
  overlay payload identities, ownership-manifest and migration identity,
  compact qualification evidence, and minimum tool capabilities. It contains
  no absolute path, host value, installed-runtime byte, target fact, or clock
  input. Its portable release root supplies official release payload; it does
  not accept `--installed-root`. A `local_runtime` receipt first verifies that artifact, then binds its
  ID, installed official/overlay and runtime identities, required local tool
  versions, and read-only doctor evidence. A `target` receipt first verifies
  both dependencies, then delegates to `verify_transaction` and binds one
  target's base, policy, plan, candidate, final receipt, and verification
  result. It is never evidence for another target or a future target state.
- Layer verification is ordered `artifact -> local_runtime -> target`. Any
  changed official/overlay payload, ownership manifest, migration, portable
  result, or minimum capability invalidates the artifact and therefore its
  dependents. A changed installed runtime or local tool invalidates local and
  target receipts without changing the artifact. A changed target branch,
  base, policy, plan, candidate, final receipt, or verification result
  invalidates only that target receipt. Verifiers never regenerate a stale
  receipt and never fall back from a failed layered dependency to legacy.
- `read_qualification_receipt` reports legacy receipts as readable but
  inseparable. It must not mint artifact, local-runtime, or target evidence by
  inference. Source admission remains legacy. Explicit downstream
  `installed_runtime` admission verifies only repository-relative artifact and
  local-runtime receipts plus target-local installed managed bytes; it never
  consults a canonical source checkout or falls back to legacy.
- The committed runtime bundle includes the repository-owned downstream
  deployer package. Receipt generation rejects omitted, changed, uncommitted,
  or differently tested deployer bytes. Receipt activation remains a separate
  post-commit action.
- `--installed-root` is an explicit local read boundary for Git worktrees that
  lack ignored Trellis installation identity. Exact official payload,
  target-local materialization metadata, and the Trellis tool version come
  from that root; committed runtime, overlay, tests, manifest, Git identity,
  receipt output, project, and generated ownership stay rooted at
  `--repo-root`. Omitting the option preserves single-root behavior.
- The installed-root path is never inferred, serialized into the receipt, or
  written. `.trellis/.version` remains exact official byte identity.
  `.trellis/.template-hashes.json` is validated as schema-v2 target-local
  updater metadata; its target-specific bytes are not compared across
  repositories. A final single-root verification in the actual installed
  checkout must reproduce the exact payload digests and metadata policy before
  runtime admission.
- An explicit installed root must be disjoint from the runtime root in both
  directions. Its root path and every manifest payload ancestor must be free
  of symlinks. Qualification and receipt output paths below it are rejected
  before any write; omission of the option retains normal single-root output.
- `QualificationStatus` returns `enforced`, `valid`, `receipt_id`, and `issues`.
  Missing repo qualification config is unenforced only for isolated runtime
  fixtures. An explicit config or rollback marker is enforced and fail-closed.
- Repository-local Loop operations select `source_development` for
  `harness_source` and `installed_runtime` for `downstream_project`. The latter
  excludes project HEAD, dirt, task records, and user files from runtime
  identity while managed official/overlay drift invalidates admission.
  `source_release` remains the full strict package/receipt gate for source
  package, release, and activation work.
- Enabled admission with any implicit parent default other than `loop_v1` is
  invalid before receipt use. It never silently creates a Current Trellis
  parent; explicit `current_trellis` remains available as an override.
- Canonical config and its rollback marker are admission-only. New-run
  preflight and start approval resolve the current selected purpose, verify the
  exact envelope receipt, and persist an immutable execution binding in the
  approved-start authority outcome. `source_development` binds its exact
  hash-addressed receipt; `installed_runtime` additionally binds exact
  artifact/local-runtime receipt paths and digests.
- Every later worker, integration, review, recovery, finalization, and ledger
  write validates that persisted binding without reading the mutable canonical
  pointer or rollback marker. A missing, malformed, or drifted bound receipt
  records `qualification_pause`, changes an authorized parent to `paused`,
  commits that evidence, and rejects the requested operation. Canonical receipt
  rotation alone never pauses, rebinds, or revokes an admitted parent.
  Operator intervention reports that current-epoch durable reason as the
  dependency/schema/security/secret machine boundary; a historical
  qualification pause cannot override a later explicit pause after resume.
- Final-merge recovery may classify canonical receipt rotations separately
  from generic descendant overlap only through the runtime contract's
  `source_release_qualification_rotation` proof under the explicit
  `qualification_rotation_chain_v2` contract. The proof does not rebind the run:
  its `execution_receipt_id` remains the immutable start-envelope receipt,
  while the publication and current canonical receipt identities are derived
  independently from Git/config bytes. Every config/receipt-touching descendant
  commit must form one ordered exact qualification link: its qualification
  projection changes only the two canonical binding fields and adds one
  hash-addressed strict receipt, starts from the prior link's receipt, verifies
  at that link's commit, and names that commit's first parent as its runtime
  commit. Attached `A`/`M`/`D` paths are permitted only when every path is
  independently outside the original final-merge envelope; the proof binds the
  status/path pairs and envelope touch patterns. The terminal receipt must
  verify for both `source_development` and `source_release`. No old receipt
  mutation, chain gap/reorder, non-link config/receipt commit, other config
  text/key, attached envelope overlap, or generic overlap is permitted.
- The approved start envelope must name the current admission `receipt_id`.
  The durable approved-start outcome is the run's execution-binding record;
  historical outcomes without that field recover only the envelope's original
  hash-addressed receipt, never the current config pointer.
- Rollback writes `.trellis/.runtime/loop-v1/admission-disabled.json`, blocks
  new admission, and does not alter already admitted parents. Enabling removes
  that marker only after the configured receipt verifies and never resumes a
  parent. The only revocation path is an explicit direct-user,
  append-only `execution_binding_revocation` event targeted to one run and its
  bound receipt; it preserves all prior evidence and makes that run terminal.
- Overlay manifest schema v2 assigns every managed scope exactly one of
  `official`, `overlay`, `project`, or `generated`, freezes six logical
  one-off target IDs, preserves the original five-target recurring-cycle
  order, preserves target-only paths as project-owned, declares the narrow
  adoption projection, and carries exact intentional-deletion dispositions.
  Distributable official and overlay payloads are fingerprinted; project,
  generated, and target-local materialization metadata bytes are not
  distributable.
- `.trellis/workflow.md` is overlay-owned because the canonical repository may
  intentionally carry routing rules that differ from the installed official
  bundle. Official materialization runs first; overlay application then restores
  the exact canonical workflow bytes.
- `overlay-check` combines a read-only installed-payload check with a temporary
  official-update/overlay-application proof. The fixture uses only local Git,
  preflights every overlay path before copying any file, and proves project and
  generated sentinel bytes are unchanged.
- The oldest-downstream fixture preserves the exact RAG v2 `0.6.5`
  `task_utils.py` bytes that exposed the dependency-closure failure. It uses the
  real local updater to construct and update the disposable baseline; key
  updater, restoration, overlay, and smoke behavior is never mocked.
- A receipt binds installed exact-payload fingerprints, the materialization
  metadata validation policy, and clean-clone application evidence. Receipt
  verification compares those identities, manifest identity, overlay version,
  and preservation results to the installed runtime without reapplying the
  overlay during authoritative writes.

## 4. Validation & Error Matrix

| Condition | Required result |
|---|---|
| Disabled admission or rollback marker | Reject new Loop admission before task/runtime mutation |
| Missing or malformed receipt path/digest | `valid=false`; do not fall back to selector-only admission |
| Current canonical receipt/marker is invalid | Reject only a new admission before task/runtime mutation |
| Persisted execution receipt, managed runtime, or binding is missing/malformed/drifted | Pause an authorized parent before the requested write |
| Canonical receipt rotates after approval | Existing parent remains bound and executable; only a new admission uses the replacement |
| Retained final merge requires an exact strict canonical rotation chain | Bind the Git-derived ordered-chain proof digest before fence rotation; recompute exact proof bytes during reconciliation, replay, and archive |
| A chain link adds another qualification path/receipt, carries an attached envelope overlap or unclassified status, is gapped/reordered/non-linear, changes other config text, disagrees across purposes, or changes/revokes execution binding | Reject before writer rotation without final-merge or archive authority |
| Start envelope receipt differs | Reject approval; preserve gate and ledger evidence |
| Explicit targeted revocation | Append one revocation event, preserve prior evidence, and reject later execution for that run |
| Q row or deployer test missing, reordered, failed, or using another test | Refuse receipt generation |
| Qualification output contains wall-clock timing or differs across identical committed runs | Reject release qualification as nondeterministic |
| Oldest-downstream materialization, overlay, or candidate smoke missing, forged, or failed | Refuse receipt generation and activation |
| Required issue present | Receipt is not qualified |
| Unknown/prohibited effect | Raise `QualificationError` before execution |
| Unknown owner/scope, overlap, symlink, or source fingerprint drift | Overlay check fails without mutation |
| Missing, non-directory, overlapping, symlinked, or drifted explicit installed root | Reject without copying identity or writing either root |
| Qualification/receipt output below explicit installed root | Reject before creating the output path |
| Enable with invalid config/receipt/root | Preserve the rollback marker byte-for-byte and fail |

## 5. Good / Base / Bad Cases

- Good: Q01-Q12 pass; a committed runtime generates a stable receipt; config
  names that receipt; a new Loop parent and matching start envelope are admitted
  with that exact execution binding.
- Base: no `loop_v1` config exists in an isolated unit-test repository; runtime
  primitives remain testable without claiming production qualification.
- Bad: config sets `admission_enabled: true` but omits the receipt; parent create
  and direct ledger initialize both fail before durable runtime/task mutation.
- Bad: a bound runtime receipt or managed bytes change after admission; the next
  authorized write records a pause and does not execute the requested operation.

## 6. Tests Required

- Run all 24 Q01-Q12 positive/negative targets plus the fixed deployer planning
  targets and the real RAG v2 `0.6.5` candidate compatibility fixture; assert
  ordered IDs, passing statuses, source/output digests, and stable
  matrix/deployer/candidate evidence.
- Remove the top-level `state_machine.py` overlay entry in the fixture manifest
  and assert the real candidate capability smoke reproduces `CHECK_FAILED`.
- Assert Q11's positive target dispatches the complete multi-requirement child
  contract for a nonempty final-review subset, preserves dependent current
  graph identity, and reaches final response only after a fresh review bound to
  the repaired integration identity.
- Mutate the operator back to unconditional intervention for worker, review,
  resume-stale, integration, or final-review failure and assert the mapped Q05,
  Q06, Q07, or Q11 target fails. Also assert Q07 fails if complete-graph checks
  run before every current child is integrated, rerun after committed replay,
  create a replacement for a non-attributing final-check failure, cancel the
  exhausted run, or open a successor parent instead of one same-run recovery
  generation.
- Regenerate a receipt from the same committed fixture and assert identical path
  and bytes; mutate runtime/test/receipt/environment identity and assert failure.
- Run qualification twice against one exact committed runtime and assert the
  complete evidence files are byte-identical with no duration fields.
- Generate artifact, local-runtime, and target receipts from disposable
  fixtures; prove portable artifact output excludes roots and target facts,
  local-tool and installed-runtime drift invalidates only the local dependency
  chain, target base/policy/plan drift invalidates only that target receipt,
  dependency verification is ordered, CLI/API readers agree, and legacy
  receipt readability never becomes layered admission by inference.
- Assert current configured admission, original-binding survival after unrelated
  canonical rotation, legacy original-binding recovery, explicit targeted
  revocation, direct-init rejection when disabled, writer/projection/transaction
  guard coverage, admission-only rollback preservation, and no auto-resume.
- Exercise clean-clone official/overlay equality plus unknown, overlapping,
  symlinked, missing, and drifted ownership failures without target mutation.
- Require exact `.version` bytes, accept valid target-local template-hash
  history differences, and reject malformed schema, unsafe paths, symlinks,
  self entries, and invalid digests.
- Exercise a runtime root with no ignored installation identity against a
  separate installed root; require dual-root receipt verification, later
  single-root verification in an installed clone, no serialized absolute path,
  no root mutation, disjoint-root and symlink-ancestor enforcement, marker
  preservation, fail-closed wrong-root behavior, and CLI/API parity for
  `run`, `overlay-check`, `receipt`, `verify`, and `enable`.
- Run current-Trellis, historical `loop_v4`, full Loop, cancellation, and v3
  regressions before activation.

## 7. Wrong vs Correct

Wrong: enable `loop_v1` and let runtime code certify the same uncommitted change.

```yaml
loop_v1:
  admission_enabled: true
  parent_default: loop_v1
```

Correct: commit the runtime first, generate a receipt from that exact commit,
verify it, then add the hash-addressed receipt and activation config in a later
local commit.

For an isolated Git worktree, pass the real installed checkout explicitly for
qualification evidence. Do not copy `.trellis/.version` or
`.trellis/.template-hashes.json` into the worktree and do not persist the
installed path in project configuration.

```yaml
loop_v1:
  admission_enabled: true
  parent_default: loop_v1
  qualification_receipt: .trellis/spec/project/receipts/loop-v1/<digest>.json
  qualification_receipt_digest: <digest>
```
