# Project Workflow Specs

## Scope

Project specs cover repository-level development workflow rules that are broader than one backend contract.

## Pre-Development Checklist

- Read `staged-delivery-overlay.md` before planning T3/T4 work, parent/child delivery, RTM-tracked work, or harness/tooling changes.
- Read `task-sizing.md` before deciding whether a request uses default Trellis or staged overlay.
- Read `pm-intake-protocol.md` before converting non-trivial user input into PRD requirements.
- Read `oracle-review-policy.md` before marking an Oracle review required or skipped.
- Read `ponytail-boundary.md` before adding dependencies, architecture, abstractions, or broad workflow surface.
- Read `rtm-guidelines.md` before updating requirement traceability.
- Read `git-commit-push-policy.md` before reporting a staged task ready to commit or push.

## Quality Check

- Confirm ordinary low-risk work can still use default Trellis.
- Confirm staged overlay work records `task.json.meta.workflow_mode = "staged_overlay"`.
- Confirm child soft archive does not call built-in `task.py archive`.
- Confirm push is never implied by commit approval.
