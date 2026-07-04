# Implementation Plan: v1.4 Douyin Collection Runner

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-040

## Scope

- Convert child 8 proof boundary into a multi-account production runner.
- Keep all external runtime state outside the repo.

## Expected Files

- `hermes_benchmark/collection_runner.py`
- Runner fixtures/tests.
- Runbook updates if command contract changes.

## Ponytail Pass

- Blocking findings: queue/concurrency/daemon mode is outside this child.
- Advisory findings: serial account loop is enough until runtime evidence proves otherwise.
- Decision: serial runner first.

## Oracle

- Required: yes.
- Reason: real external execution and credential/CDP safety.
- Output path: `research/oracle-plan-review.md`.
- Result: completed by Browser Oracle; implementation may proceed for boundary,
  dry-run, fixture, normalization, ledger, error, and redaction checks. Real
  external Douyin/MediaCrawler execution remains gated.

## Steps

1. Reuse profile account output.
2. Reuse external runtime process/redaction helpers.
3. Add account loop and failure isolation.
4. Normalize rows and call state ledger.
5. Add focused dry-run and fixture checks.

## Verification

- Command: dry-run collection runner fixture check.
- Command: real smoke only when local inputs exist.
- Command: `git diff --check`

## Rollback

- Remove collection runner module and checks.

## Confirmation Gate

- [x] User confirmed this PLAN by requesting child task 4 proceed with the confirmed account list.
- [x] Oracle high-risk PLAN review completed.
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
