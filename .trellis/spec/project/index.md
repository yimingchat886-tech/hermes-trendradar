# Project Workflow Specs

## Scope

Project specs cover repository-level development workflow rules that are broader than one backend contract.

## Pre-Development Checklist

- Read `staged-delivery-overlay.md` before touching legacy staged-overlay compatibility or parent/child closeout language.
- Read `task-sizing.md` before deciding whether a request uses default Trellis or harness state-machine parent/child work.
- Read `pm-intake-protocol.md` before converting non-trivial user input into PRD requirements.
- Read `prd-governance.md` whenever an Agent creates, revises, accepts, or binds
  a PRD. It owns product truth, acceptance, execution binding, successor, and
  conditional PM-method rules.
- Read `oracle-review-policy.md` before planning independent review; it records
  the repository-wide Oracle stop and the local replacement contract.
- Read `ponytail-boundary.md` before adding dependencies, architecture, abstractions, or broad workflow surface.
- Read `protocol-phrases.md` before writing user-facing completion, commit, archive, limit, or push gate wording.
- Read `claim-guard-ownership.md` before changing claim guard, task ownership, `task.py claim`, or `task.py release`.
- Read `task-cancel-lifecycle.md` before changing `task.py cancel`, cancelled-task validation, RTM cancellation disposition, or cancelled archive behavior.
- Read `loop-v1-admission.md` before changing workflow-mode selection, Loop v1 admission, or parent-to-child mode inheritance.
- Read `loop-v1-qualification.md` and `loop-v1-overlay-manifest.json` before changing Loop v1 qualification, activation, rollback, or overlay ownership.
- Read `downstream-deployer.md` before changing downstream enrollment,
  ownership, private candidate planning, official materialization, preservation,
  deletion, or exact plan identity.
- Read `upstream-release.md` before changing pinned upstream candidate planning,
  release identity, repository wrapper source, or isolated wrapper rendering.
- Read `loop-v1-runtime.md` before changing the Loop v1 SQLite ledger, writer fencing, operation replay, or projections.
- Read `taskrun-runtime.md` before changing TaskRun new-task admission, SQLite
  authority, legacy dry-run importer, event replay, projections, or status-only
  close.
- Read `merge-collision-protocol.md` before changing conflict checklist, merge protocol, or parallel child merge handling.
- Read `rtm-guidelines.md` before updating requirement traceability.
- Read `git-commit-push-policy.md` before reporting a staged task ready to commit or push.

## Quality Check

- When `taskrun_v1.new_code_tasks: true`, confirm new code tasks use TaskRun
  `single` by default and explicit `loop` only for concurrent or unattended
  execution.
- Confirm existing Current Trellis parent/child work retains
  `task.json.meta.workflow_mode = "harness_state_machine"`.
- Confirm an existing qualified Loop v1 parent records `task.json.meta.workflow_mode =
  "loop_v1"` and does not acquire HSM lifecycle metadata.
- Confirm existing HSM child commit approval is paired with child completion unless the user explicitly limits it.
- Confirm existing HSM child completion does not call built-in `task.py archive`.
- Confirm existing HSM parent acceptance commits parent evidence and runs built-in archive unless the user explicitly limits it.
- Confirm user-facing gate wording references `protocol-phrases.md` instead of copying a separate phrase list.
- Confirm claim guard changes keep cross-owner matches blocked and explicit overrides audited.
- Confirm cancelled tasks retain authorization, event, RTM, relationship, and evidence truth and never archive as completed.
- Confirm TaskRun cutover rejects new HSM/Loop lifecycle selection before
  mutation while retained Loop admission remains available only when cutover
  is disabled.
- Confirm downstream planning materializes official bytes only in private
  scratch, records the new preimage before overlay work, and leaves source and
  target identities unchanged.
- Confirm an active receipt mismatch pauses authorized Loop parents before the requested operation and preserves evidence.
- Confirm the Loop v1 ledger remains single-writer authority, rejects stale epoch/fence/phase inputs, and keeps projections derived and rebuildable.
- Confirm the TaskRun create/start adapter replays exact operation input, keeps
  projections non-authoritative, and reports ambiguous close outcomes without
  changing legacy authority.
- Confirm merge collision changes list conflicted files, matching child cards, owners, and required stage reports before conflict resolution.
- Confirm push is never implied by commit approval.
- Confirm implementation binds an accepted Git commit, PRD path(s), and REQ IDs;
  generated PRD views never become parallel truth.
