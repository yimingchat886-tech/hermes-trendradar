# M5-2 CI

## Goal

Add the leanest GitHub Actions CI for the current Python project and make its checks required on `main`.

## REQ-ID

- WV2-M5-REQ-002: Add `.github/workflows/ci.yml` for PRs and `main` pushes with lint, typecheck, and test checks matched to the current Python stack.
- WV2-M5-REQ-002A: Update GitHub `main` branch protection so the CI checks are required before merge.

## Verification Commands

- `python3 -m compileall -q hermes_benchmark tests .trellis/scripts`
- `TMPDIR=/tmp python3 -m pytest -q`
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main/protection --jq '{required_status_checks: .required_status_checks}'`

## In

- Create `.github/workflows/ci.yml`.
- Use Python 3.12, install the package plus CI-only test/lint tools, and avoid matrix or deployment scope.
- Keep lint narrow enough to avoid converting existing style debt into this child.
- Configure required checks for the CI job names on protected `main`.

## Out

- No source-code lint cleanup.
- No staging deploy, G6, Playwright, acceptance automation, tag, or release work.
- No git commit or push unless jym explicitly approves it.
