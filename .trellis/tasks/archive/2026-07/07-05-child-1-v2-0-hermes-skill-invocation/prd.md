# Child 1: Hermes Skill Invocation Gate

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-010, P20-REQ-020

## Goal

Define the smallest no-secret Hermes skill invocation contract and prove Hermes can call the repo before deeper analysis work starts.

## Requirements

- C1-REQ-001: Define a no-secret invocation template for Hermes to call the existing `hermes-benchmark` console script.
- C1-REQ-002: Prove `healthcheck --json` can run through the chosen invocation path.
- C1-REQ-003: Prove one handoff-package-producing invocation can run or record an explicit blocker/fallback.
- C1-REQ-004: Record whether Hermes cron is usable for M0; if not, record the systemd fallback decision.
- C1-REQ-005: Keep credentials, tokens, cookies, CDP endpoints, and local secrets out of repo files and logs.

## Out of Scope

- MCP server.
- Scheduler framework beyond the M0 gate/fallback decision.
- Real Hermes analysis.
- Live Feishu/Bitable writes.

## Acceptance Criteria

- [ ] A no-secret skill/config/runbook artifact exists.
- [ ] `hermes-benchmark healthcheck --json` is proven through the invocation path or a blocker is recorded.
- [ ] Handoff invocation is proven or a fallback/blocker is recorded.
- [ ] No new runtime dependency is added.
- [ ] No secret-bearing file is created.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-1-v2-0-hermes-skill-invocation`
- `git diff --check`

## In

- Hermes skill invocation contract, runbook/template, and M0 evidence.

## Out

- MCP, real analysis, feedback intake, and Bitable writes.
