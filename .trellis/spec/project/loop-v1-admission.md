# Loop v1 Admission

## Scope

This contract governs retained Loop v1 lifecycle admission and compatibility.
When `taskrun_v1.new_code_tasks: true`, it is read-only for existing tasks and
archives: new concurrent or unattended code work selects TaskRun strategy
`loop`, not Loop lifecycle mode. The historical selector below remains active
only in repositories that have not enabled TaskRun cutover.

## TaskRun Cutover Precedence

```text
task.py create <title> [--strategy single|loop]
task.py start <task> --taskrun-input <json>
```

- Omitted strategy means TaskRun `single`; explicit `loop` uses the same
  TaskRun SQLite lifecycle authority.
- Any `--workflow-mode` request fails before task, parent, active-pointer,
  Board, ledger, or runtime mutation.
- A new child cannot extend an existing HSM or Loop parent after cutover or
  mutate an already-admitted TaskRun parent projection.
- Existing Loop tasks, ledgers, archives, validation, recovery, and retirement
  remain under their original authority and are not rewritten.

## Legacy Selector

```text
task.py create <title> --tier parent --workflow-mode current_trellis|loop_v1
```

- `--workflow-mode` is the retained pre-cutover selector and is parent-only.
- `current_trellis` stores the existing internal mode `harness_state_machine`.
- `loop_v1` is recognized but cannot be admitted until both configuration and
  runtime qualification gates pass.
- `loop_v4` is historical and cannot create new tasks.
- Unknown selector values fail closed.

## Admission Configuration

```yaml
loop_v1:
  admission_enabled: false
  parent_default: current_trellis
  runtime_mode: harness_source|downstream_project
```

Missing configuration has the same disabled/current-Trellis defaults. Setting
`admission_enabled: true` does not by itself admit Loop v1: a later delivery
must validate an active conformance receipt. During B0, no receipt path exists,
so every Loop v1 parent request remains rejected.

After qualified activation, the enabled configuration must set
`parent_default: loop_v1`. Enabled admission with a Current Trellis default is
invalid because it silently downgrades an implicit new parent. Current Trellis
remains available through an explicit per-parent selector.

`runtime_mode` selects the qualification purpose before mutation.
`harness_source` uses `source_development`, which validates the configured
receipt identity while allowing unfinished source bytes to differ from a
released package. `downstream_project` uses `installed_runtime`, which verifies
only target-local layered receipts and installed managed bytes. Unknown,
missing, or contradictory role/purpose input fails closed; explicit
package/release qualification remains the separate strict `source_release`
boundary.

## Scenario: Qualified Parent Default

### 1. Scope / Trigger

Apply this scenario when `task.py create` creates a parent without an explicit
workflow selector after Loop v1 activation.

### 2. Signatures

```text
task.py create <title> --tier parent
task.py create <title> --tier parent --workflow-mode current_trellis
```

The first command consumes the repository default. The second is the only
compatible override when the user explicitly requests Current Trellis.

### 3. Contracts

- Disabled/bootstrap: implicit parent default is `current_trellis`.
- Enabled/qualified: implicit parent default is exactly `loop_v1`.
- Explicit `current_trellis` stores `harness_state_machine`.
- No rejected request may create a task directory or mutate task projections.

### 4. Validation & Error Matrix

| Admission | Default | Selector | Result |
|---|---|---|---|
| disabled | `current_trellis` | omitted | create Current Trellis parent |
| enabled | `loop_v1` | omitted | require exact-valid receipt, then create Loop parent |
| enabled | `loop_v1` | omitted, stale receipt | `Loop v1 runtime is not qualified`; no mutation |
| enabled | `current_trellis` | omitted | `enabled Loop v1 admission requires parent_default 'loop_v1'`; no mutation |
| enabled | any | explicit `current_trellis` | create Current Trellis parent |

### 5. Good / Base / Bad Cases

- Good: target-local qualification is exact-valid, activation sets the Loop
  default, and the agent creates a new parent without another mode prompt.
- Base: admission is disabled, so an implicit parent retains Current Trellis.
- Bad: admission is enabled while the default remains Current Trellis; creation
  fails closed rather than silently downgrading.

### 6. Tests Required

`test_loop_v1_admission.py` must assert the enabled/default mismatch is visible
through configured qualification, is rejected before mutation for an implicit
parent, and still permits an explicit Current Trellis override.

### 7. Wrong vs Correct

Wrong: ask the user to choose Loop again, or silently append
`--workflow-mode current_trellis`, after exact-valid qualified activation.

Correct: omit `--workflow-mode`, consume the qualified repository default, and
retain the immutable start-envelope authority. The default `single_user`
profile records one local start authority for bounded execution and
finalization; task metadata may opt into compatible `strict` start/final
responses. Neither profile authorizes cancellation or external/destructive
effects.

## Pre-Mutation Gate

Selector, tier, admission, and qualification checks run before task directory
creation, parent linkage, active-task pointer writes, hooks, or BOARD refresh.
A rejected request leaves all of those surfaces unchanged.

## Parent And Child Rules

- With TaskRun cutover enabled, these creation rules are disabled; only
  read-only compatibility and the existing lifecycle's own operations remain.
- A parent without an explicit selector uses `loop_v1.parent_default`.
- The bootstrap default creates the same `harness_state_machine` parent as
  current Trellis.
- After qualified activation, a new parent without a selector resolves to
  `loop_v1` without another workflow-choice prompt.
- Explicit `current_trellis` remains a compatible override.
- Light tasks retain `harness_state_machine` and cannot select a workflow mode.
- A child cannot supply `--workflow-mode`; when its parent exists, the child
  copies `parent.task.json.meta.workflow_mode`.
- Existing tasks and archives are not migrated or rewritten.

## Generic Task CLI Boundary

- `task.py validate` dispatches on the stored workflow mode. Loop v1 validation
  remains read-only, checks the qualified parent boundary, and does not use HSM
  completion or cancellation gates as a substitute for the ledger.
- Only `harness_state_machine` parent and child tasks initialize
  `state-events.jsonl` and HSM metadata during `task.py create`.
- Generic `task.py archive` rejects `loop_v1` task records. `task.py cancel`
  delegates only an exact planning parent with no ledger, operator state,
  event, child/subtask, or Git relationship residue to the atomic
  pre-admission cancellation helper. Initialized and otherwise active Loop
  lifecycle, final merge, and archive decisions remain with the qualified
  operator.
- A formally cancelled pre-admission parent is physically moved only by
  `loop_v1.orchestrator archive-pre-admission`, with an expected task digest,
  one archive transaction, generator-only BOARD refresh, and idempotent replay.
  An initialized terminal parent instead requires a current CAS-bound terminal
  projection and `retire-task-evidence`; generic task archive never substitutes.
- Default `single_user` operator initialization records one start intent for
  declared local worker, review, child commit, integration, repair, and final
  merge actions. The derived final request remains limited to that merge,
  verification, and Loop-ledger archive. `strict` retains distinct direct
  start/final responses. Cancel, push, release, activation, cross-repository
  deploy, force-ref change, and destructive cleanup remain separate commands.

## Verification

The cutover matrix must prove TaskRun `single` default, explicit TaskRun
`loop`, rejected HSM/Loop admission with zero mutation, and byte-stable existing
task/ledger/archive evidence.

Focused tests must cover disabled admission, historical and unknown selectors,
the missing-qualification gate, rejection of enabled admission with a Current
Trellis implicit default, explicit Current Trellis override, no-selector
compatibility, pre-mutation child rejection, and child inheritance from a Loop
v1 parent fixture. They must also cover mode-aware validation, HSM
initialization only for Current Trellis, and generic Loop archive/cancel
rejection without mutation. Existing task, state-machine, hook, list, BOARD,
validation, and archive tests remain the compatibility gate. Cancellation
tests must cover current and non-current eligible parents, public validation,
active-pointer and BOARD follow-through, plus byte-for-byte zero mutation of
task, event, pointer, and BOARD surfaces for every rejected case.
They must also prove pre-admission physical archive, replay, archived
validation, and absence of a per-run ledger/operator residue.
