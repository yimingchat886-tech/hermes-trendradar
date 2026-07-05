# Stage Report: M5-2 CI

## Acceptance

- [x] `.github/workflows/ci.yml` exists and runs on PRs plus `main` pushes.
- [x] CI has one required `ci` job with lint, typecheck, and test steps.
- [x] Local CI-equivalent checks pass.
- [x] `main` branch protection requires the `ci` status check and still blocks force-push/delete.
- [x] Lint negative path turns red.
- [ ] Live GitHub PR red/green merge-button proof is pending explicit push/PR approval.

## Verification

- `python3 -m compileall -q hermes_benchmark tests .trellis/scripts`: pass.
- `TMPDIR=/tmp python3 -m pytest -q`: pass, 55 tests.
- `python -m ruff check --select E9,F63,F7,F82 hermes_benchmark tests .trellis/scripts`: pass in a temporary venv with CI-pinned `ruff==0.15.20`.
- Negative path: `printf 'print(missing_name)\n' | python -m ruff check --select E9,F63,F7,F82 --stdin-filename bad.py -`: blocked with `F821 Undefined name`.
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main/protection --jq '{...}'`: pass; `required_status_checks.strict=true`, `contexts=["ci"]`, `enforce_admins=true`, `allow_force_pushes=false`, `allow_deletions=false`.
- `ruby -e 'require "yaml"; YAML.load_file(".github/workflows/ci.yml")'`: skipped; `ruby` is not installed in this WSL environment.

## Push State

- Pushed: no. M5-2 live PR proof requires explicit push/PR approval.

## User Completion Signal

- Raw signal: 验证通过，提交git
- Received at: 2026-07-05T02:26:53Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: no
