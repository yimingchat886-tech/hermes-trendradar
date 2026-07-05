# Stage Report: M5-4 acceptance automation

## Acceptance

- [x] Playwright staging smoke script exists and checks the local staging top path.
- [x] Workflow head-SHA verification script exists and returns CI run URL/head SHA fields when a workflow run is available.
- [x] v2 child stage-report template includes CI run / staging verification fields.
- [x] Smoke negative path fails when expected staging text is absent.
- [ ] Live green GitHub run head-SHA proof is pending workflow availability on `main` after merge/push.

## Verification

- `uv run --no-project --with playwright==1.56.0 python -m playwright install chromium`: pass; installed Chromium/headless shell in Playwright cache.
- `uv run --no-project --with playwright==1.56.0 python3 ./.trellis/scripts/staging_smoke.py --url http://127.0.0.1:18080/healthz`: pass; status 200, JSON body includes `hermes-trendradar`, root page includes `healthz`.
- Negative path: `uv run --no-project --with playwright==1.56.0 python3 ./.trellis/scripts/staging_smoke.py --url http://127.0.0.1:18080/healthz --expect definitely-not-present`: blocked with exit 1 and `ok=false`.
- `python3 ./.trellis/scripts/verify_workflow_head.py --repo yimingchat886-tech/hermes-trendradar --workflow docker-deploy-staging.yml --branch codex/workflow-v2-m4-board --allow-missing`: pass; reports `run_found=false`, `missing_reason=workflow_not_found` because the workflow is not on default `main` yet.
- Negative path: same command without `--allow-missing`: blocked with GitHub API 404.
- `python3 -m compileall -q .trellis/scripts/staging_smoke.py .trellis/scripts/verify_workflow_head.py`: pass.

## CI Run / Staging Verification

- CI run: pending; workflow not yet present on default `main`.
- Workflow head_sha: pending.
- Expected merge SHA: pending.
- Staging URL: `http://127.0.0.1:18080/healthz`
- Playwright smoke: pass locally.

## Push State

- Push requested: yes, for the current work branch.
- Pushed: pending at commit time. Live GitHub run evidence still requires merge to `main`.

## User Completion Signal

- Raw signal: 提交git并push
- Received at: 2026-07-05T03:32:16Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: yes
