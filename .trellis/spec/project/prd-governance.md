# PRD Governance

## Scope

Use this contract whenever an Agent creates, revises, accepts, or binds a PRD.
The user controls the work through conversation; the Agent owns file updates,
checks, and Git operations within the user's exact authority.

## Six Runtime Rules

### 1. One Logical Product Contract

- Strict Markdown product/capability sources are the only product truth.
- Capability shards are optional storage boundaries, not independently
  accepted or versioned contracts.
- A Release delta is Draft input. Acceptance merges it into the logical
  contract instead of leaving parallel current truth.
- README, manifest, feature, release comparison, and global RTM views are
  generated projections. They do not grant authority and may be rebuilt.

### 2. One Promotion Question

For task-local work, ask only whether the task adds, changes, or retires an
accepted product REQ. Promote that delta into the product contract before
implementation; otherwise keep the PRD task-local.

Default routing:

| Work | PRD source |
|---|---|
| New product, empty repository, major version | Logical product contract |
| Small request, customer feedback, ordinary bug | Task-local PRD unless it changes an accepted product REQ |
| Next version with research input | Draft Release delta merged into the logical contract on acceptance |

### 3. One Acceptance Transaction

1. Read the accepted Git base and propose the smallest PRD delta.
2. Mechanically check references, duplicate REQ IDs, and missing owners.
   The sole requirement declaration is
   ``- `REQ-ID` [owner: owner]: text`` under `## Requirements`; a task-owned
   contract may inherit its source owner from the accepted adjacent `task.json`.
   A child task may instead use `## Accepted PRD Binding` as a reference to
   accepted declarations. `## REQ-ID` and binding rows are not parallel
   declarations or compatibility indexes.
3. Use model review for semantic conflicts and let the user resolve them.
   Deferral removes the conflicting delta from the accepted revision.
4. `接受并提交 PRD revision` accepts the shown delta and authorizes one scoped
   local PRD commit. That commit SHA is the accepted revision.

Baseline aliases and content digests are optional display fields. Acceptance
does not imply implementation start, another local commit, push, archive,
deployment, installation, activation, or downstream effect.

### 4. One Execution Binding

Implementation consumes only:

```text
Git commit + PRD path(s) + REQ IDs
```

Later edits and regenerated views do not change a running binding. Task and
stage evidence cite REQ IDs rather than copying requirement text. Traceability
views are generated from bindings and evidence when needed. At acceptance,
start, closeout, or an explicit refresh, use `prd.py check` or rebuild disposable
`README.md`, `manifest.json`, and `RTM.md` with `prd.py generate`.

`prd.py` is the shared declaration and binding parser. For an unadmitted
TaskRun plan, `task.py validate` and `task.py start` execute the same read-only
task-PRD preflight before any TaskRun, pointer, Board, or task-status write.
Already-admitted and historical task evidence is not reparsed or migrated.

### 5. Reuse First, Successor On Drift

- Reuse the design task for implementation only when an accepted binding
  exists, Current Trellis mode and scope remain valid, and the user explicitly
  authorizes start.
- Otherwise create a successor. Loop v1 work requires a new admission.
- A material post-start PRD revision defaults to a successor; do not rebind or
  patch a historical runtime in place.

### 6. Silent Governance And Conditional PM Methods

- `采纳` and `确认` update Draft only. Other commit, start, archive, push, and
  external-effect authority follows the repository protocol contracts.
- Enforce routine rules internally. Surface only proposals, semantic findings,
  state changes, and blockers; do not spend normal replies proving compliance.
- Run PM methods zero by default and at most one per unresolved decision:
  assumption scan or OST for major-work unknowns, feature-request analysis for
  multiple feedback items, and outcome-roadmap or assumption scan for unclear
  release outcomes. WWA and test scenarios are writing transforms, not phases.
- Run one compact model red-team before accepting a T3/T4 PRD.
- Concentrate checks at acceptance, start, and closeout boundaries.
