# Implementation Plan: MediaCrawler Collection Runner Dry-Run

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-045, P1-REQ-100

## Scope

- Add a collection runner boundary for account registry inputs.
- Implement dry-run command planning.
- Implement local fake-command/fixture output mode.
- Write raw artifacts and a run manifest.

## Expected Files

- Collection runner module or command.
- Local fake-command/fixture output data.
- Artifact manifest fixture/check.
- Stage report after implementation.

## Ponytail Pass

- Blocking findings: real crawler execution, scheduler, queue, worker, credential management, or new dependency would exceed this child.
- Advisory findings: use `subprocess.run([...], shell=False)` only for fake/local command execution.
- Decision: dry-run/fake output first; real MediaCrawler call belongs to parent acceptance.

## Oracle

- Required: no.
- Reason: child scope has no real external platform call, no credentials, and no live writes.

## Steps

1. Reuse child 2 enabled account tracking output.
2. Build deterministic command-plan records for enabled accounts.
3. Add dry-run output.
4. Add fake-command/fixture mode that writes raw artifacts.
5. Add run manifest and source-health/failure outputs.
6. Add one focused self-check.

## Verification

- Command: collection runner self-check plus `git diff --check`.
- Expected result: dry-run and fake-command modes produce deterministic manifests and artifacts.

## Parent Acceptance Note

After child tasks are complete, parent final acceptance must run the complete chain with 1-2 user-provided accounts and a real external MediaCrawler command. That evidence is parent-level, not child8 completion scope.

## Rollback

- Remove collection runner, fixtures, artifacts, and checks.

## Confirmation Gate

- [ ] User confirmed this PLAN
- [ ] `task.json.meta.staged_delivery.plan_confirmed = true`
