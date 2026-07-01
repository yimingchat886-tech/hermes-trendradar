# Implementation Plan: Hermes Benchmark Decomposition Outputs

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-020, P1-REQ-070, P1-REQ-090, P1-REQ-100

## Scope

- Define Hermes decomposition output schema.
- Add mock output examples.
- Protect manual fields and evidence state.

## Expected Files

- Hermes output schema/module or prompt contract.
- Mock fixtures.
- Validation check.

## Ponytail Pass

- Blocking findings: do not build a general agent framework in this child.
- Advisory findings: keep the LLM boundary as input/output schema first.
- Decision: mockable schema first.

## Oracle

- Required: decide before implementation.
- Reason: LLM output can shape product behavior.

## Steps

1. Reuse child 1/3/4 outputs.
2. Define decomposition output shape.
3. Add card and topic supplement outputs.
4. Add validation and manual-field protection checks.

## Verification

- Command: decomposition schema check plus `git diff --check`.
- Expected result: valid mock output passes; manual overwrite attempts fail.

## Rollback

- Remove schema, fixtures, and checks.

## Confirmation Gate

- [x] User confirmed this PLAN via "确认child task 5，在新branch上执行"
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
