# Project Development Standard

## Before Development

- Read .trellis/workflow.md, this guide, and .trellis/spec/project/index.md.
- Confirm goal, scope, accepted binding, and verification before non-trivial
  edits.
- Run GitNexus impact before editing an indexed symbol. Hidden .trellis paths
  may be absent; treat UNKNOWN as high risk and verify callers, tests, and diff.
- Check git status for each existing file and preserve unrelated work.

## Implementation

- Use Ponytail full mode: delete/reuse first, then standard library and native
  behavior, then the minimum new code.
- One intent stays in one TaskRun. Use actions, not child/successor/repair Tasks.
- Do not add dependencies without explicit approval.
- Keep validation, security, data-loss protection, and separate external gates.
- Check Agent is read-only. Implement/Coordinator owns fixes.

## Verification

- Reproduce or locate a bug before fixing it when practical.
- Non-trivial branches, loops, parsers, lifecycle, release, sync, and security
  paths leave runnable tests.
- Run focused checks while iterating and workflow Phase 2 checks before VERIFIED.
- After correctness, run Ponytail review: delete, stdlib, native, yagni, shrink.
  Never weaken correctness, security, accessibility, data-loss protection, or
  an accepted requirement.
- Run GitNexus detect_changes against main before an authorized commit.

## Finish

- Confirm the diff contains only accepted scope.
- Update reusable specs plus README.md and HANDOFF.md when public workflow,
  authority, release, sync, migration, or closeout changes.
- Stop at VERIFIED until direct closeout authority.
- Push, publication, deployment, activation, and real target mutation are never
  implied.
