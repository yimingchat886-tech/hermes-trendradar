# Project Workflow Specs

## Scope

Project specs cover repository-level development workflow rules that are broader than one backend contract.

## Pre-Development Checklist

- Read `staged-delivery-overlay.md` before touching legacy staged-overlay compatibility or parent/child closeout language.
- Read `task-sizing.md` before deciding whether a request uses default Trellis or harness state-machine parent/child work.
- Read `pm-intake-protocol.md` before converting non-trivial user input into PRD requirements.
- Read `oracle-review-policy.md` before marking an Oracle review required or skipped.
- Read `ponytail-boundary.md` before adding dependencies, architecture, abstractions, or broad workflow surface.
- Read `protocol-phrases.md` before writing user-facing completion, commit, archive, limit, or push gate wording.
- Read `claim-guard-ownership.md` before changing claim guard, task ownership, `task.py claim`, or `task.py release`.
- Read `merge-collision-protocol.md` before changing conflict checklist, merge protocol, or parallel child merge handling.
- Read `rtm-guidelines.md` before updating requirement traceability.
- Read `git-commit-push-policy.md` before reporting a staged task ready to commit or push.
- Read `external-runtime-asr.md` before changing local runtime, profile, smoke evidence, or runtime-test file boundaries.

## Quality Check

- Confirm ordinary low-risk work can still use default Trellis.
- Confirm v3 parent/child work records `task.json.meta.workflow_mode = "harness_state_machine"`.
- Confirm child commit approval is paired with soft archive unless the user explicitly limits it.
- Confirm child soft archive does not call built-in `task.py archive`.
- Confirm parent acceptance commits parent evidence and runs built-in archive unless the user explicitly limits it.
- Confirm user-facing gate wording references `protocol-phrases.md` instead of copying a separate phrase list.
- Confirm claim guard changes keep cross-owner matches blocked and explicit overrides audited.
- Confirm merge collision changes list conflicted files, matching child cards, owners, and required stage reports before conflict resolution.
- Confirm push is never implied by commit approval.
