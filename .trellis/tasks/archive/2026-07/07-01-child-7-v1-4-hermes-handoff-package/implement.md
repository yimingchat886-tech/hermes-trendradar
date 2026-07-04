# Implementation Plan: v1.4 Hermes Handoff Package

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-060

## Scope

- Add handoff package builder and validation.
- Wire package ref into `run-daily` output.

## Expected Files

- `hermes_benchmark/handoff.py`
- Focused handoff contract tests/self-checks.

## Ponytail Pass

- Blocking findings: real LLM/Hermes call is outside this child.
- Advisory findings: plain dict/schema validation is enough.
- Decision: deterministic builder only.

## Oracle

- Required: no.
- Reason: bounded data contract.

## Steps

1. Define package schema from PRD.
2. Build package from state/fixture records.
3. Add validation and artifact write.
4. Wire CLI output ref.
5. Verify invalid package handling.

## Verification

- Command: handoff self-check/test.
- Command: `hermes-benchmark run-daily ... --analysis-mode hermes-handoff --json` fixture path when available.
- Command: invalid package fixture returns exit code `6`.
- Command: `git diff --check`

## Rollback

- Remove handoff module and tests.

## Confirmation Gate

- [x] User confirmed this PLAN.
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
