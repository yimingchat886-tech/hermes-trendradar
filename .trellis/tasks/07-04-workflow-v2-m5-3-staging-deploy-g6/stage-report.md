# Stage Report: M5-3 staging deploy and G6

## Acceptance

- [x] Docker staging image builds from the current Python project.
- [x] Local Docker staging container starts and exposes `http://127.0.0.1:18080/healthz`.
- [x] `docker-deploy-staging` workflow exists.
- [x] Deploy job has G6 gate: `needs: ci` and `if: ${{ success() && github.ref == 'refs/heads/main' }}`.
- [x] Deploy job targets local Docker through a `self-hosted` runner instead of a cloud provider.
- [ ] Live GitHub red/green deploy proof is pending explicit push and self-hosted runner availability.

## Verification

- `docker build -t hermes-trendradar:staging .`: pass after retrying a transient Docker Hub EOF while pulling `python:3.12-slim`.
- `docker run -d --name hermes-trendradar-staging-test -p 18080:8080 hermes-trendradar:staging`: pass; temporary test container removed after health check.
- `python3` health probe for `http://127.0.0.1:18080/healthz`: pass; response body `{"ok":true,"service":"hermes-trendradar"}`.
- Persistent local staging container `hermes-trendradar-staging`: pass; running on `0.0.0.0:18080->8080/tcp`.
- `docker inspect -f '{{.State.Health.Status}}' hermes-trendradar-staging`: pass; `healthy`.
- Negative path: workflow text assertion confirms deploy cannot run without the `ci` job: `needs: ci` and `if: ${{ success() && github.ref == 'refs/heads/main' }}`.
- `uvx --from ruff==0.15.20 ruff check --select E9,F63,F7,F82 hermes_benchmark tests .trellis/scripts`: pass.
- `python3 -m compileall -q hermes_benchmark tests .trellis/scripts`: pass.
- `TMPDIR=/tmp python3 -m pytest -q`: pass, 55 tests.
- `git diff --check -- Dockerfile .dockerignore .github/workflows/docker-deploy-staging.yml .trellis/tasks/07-04-workflow-v2-m5-3-staging-deploy-g6 .trellis/tasks/07-04-workflow-v2-m5-remote-ci-staging BOARD.md`: pass.

## Push State

- Push requested: yes, for the current work branch.
- Pushed: pending at commit time. Live GitHub workflow proof still requires a `main` push/merge and self-hosted runner availability.

## User Completion Signal

- Raw signal: 提交git，并push
- Received at: 2026-07-05T02:47:30Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: yes
