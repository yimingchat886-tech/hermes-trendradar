# Implementation Plan: v1.4 CLI Contract Skeleton

## Parent Task

- Parent: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Requirement IDs: P14-REQ-010, P14-REQ-080

## Scope

- Add the CLI entrypoint and shared JSON response helpers.
- Keep commands skeletal; later children fill behavior.

## Expected Files

- `pyproject.toml` or equivalent minimal package metadata.
- `hermes_benchmark/cli.py`
- Focused CLI tests or a no-dependency self-check.

## Ponytail Pass

- Blocking findings: adding a CLI framework dependency is unnecessary unless stdlib `argparse` cannot satisfy the contract.
- Advisory findings: use stdlib `argparse` first.
- Decision: smallest stdlib CLI first.

## Oracle

- Required: no.
- Reason: low-risk contract shell.

## Steps

1. Add package metadata with console script.
2. Add `argparse` command parsing.
3. Add shared success/error JSON envelope.
4. Add version output.
5. Add focused verification.

## Verification

- Command: `hermes-benchmark --version`
- Command: `hermes-benchmark validate-config --profile missing.yaml --json`
- Command: `python3 -m compileall -q hermes_benchmark`
- Command: `git diff --check`

## Rollback

- Remove package metadata and CLI module.

## Confirmation Gate

- [x] User confirmed this PLAN via "执行child task 1".
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
