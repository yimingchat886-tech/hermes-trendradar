# M5-4 acceptance automation

## Goal

Add minimal acceptance automation for the M5 local-Docker staging path: a Playwright smoke script, a GitHub workflow head-SHA verifier, and a stage-report template slot for CI/staging evidence.

## REQ-ID

- WV2-M5-REQ-004: Add a Playwright smoke script for the staging top path at `/healthz`.
- WV2-M5-REQ-004A: Add a `workflow:head_sha` verification script that reports the latest workflow run URL and compares `headSha` with an expected merge SHA when provided.
- WV2-M5-REQ-004B: Add CI run / staging verification fields to the child stage-report template.

## Verification Commands

- `python3 ./.trellis/scripts/staging_smoke.py --url http://127.0.0.1:18080/healthz`
- `python3 ./.trellis/scripts/verify_workflow_head.py --repo yimingchat886-tech/hermes-trendradar --workflow docker-deploy-staging.yml --branch codex/workflow-v2-m4-board --allow-missing`
- `python3 -m compileall -q .trellis/scripts/staging_smoke.py .trellis/scripts/verify_workflow_head.py`

## In

- Implement scripts under `.trellis/scripts/`.
- Keep Playwright usage optional and local-runner friendly; do not add repo dependencies.
- Use GitHub CLI/API already present in this workflow.
- Update only the v2 child `stage-report.md` template.

## Out

- No Playwright test framework setup.
- No browser snapshot artifacts in the repo.
- No cloud, registry, or production deploy changes.
- No commit or push unless jym explicitly approves it.
