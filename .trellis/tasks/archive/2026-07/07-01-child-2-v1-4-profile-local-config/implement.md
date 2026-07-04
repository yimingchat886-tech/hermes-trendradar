# Implementation Plan: v1.4 Profile And Local Config

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-020, P14-REQ-040

## Scope

- Add profile parsing/validation and local config examples.
- Preserve manual-edit local profile flow.

## Expected Files

- `hermes_benchmark/profile.py`
- `profiles/*.example.yaml` or test fixtures with redacted/sample values.
- Focused profile validation tests or self-checks.

## Ponytail Pass

- Blocking findings: new YAML dependency requires justification; stdlib lacks YAML parser.
- Advisory findings: if PyYAML is already unavailable, consider JSON-compatible YAML subset only if it does not make profile UX worse.
- Decision: choose the smallest parser approach during PLAN confirmation.

## Oracle

- Required: conditional.
- Reason: security/profile boundary.

## Steps

1. Define profile schema shape from v1.4 PRDs.
2. Parse main profile and referenced child profile paths.
3. Validate Douyin-only 10 enabled accounts.
4. Add sensitive value scanning.
5. Add profile hash and JSON summary.

## Verification

- Command: `hermes-benchmark validate-config --profile <valid fixture> --json`
- Command: plaintext-secret fixture exits with config invalid.
- Command: `git diff --check`

## Rollback

- Remove profile module and sample fixtures.

## Confirmation Gate

- [x] User confirmed this PLAN.
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
