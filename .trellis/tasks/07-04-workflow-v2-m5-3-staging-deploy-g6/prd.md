# M5-3 staging deploy and G6

## Goal

Add the smallest local-Docker staging path and a GitHub Actions deploy workflow whose deploy job cannot run unless its CI gate succeeds.

## REQ-ID

- WV2-M5-REQ-003: Add a Docker staging image for the current Python CLI project with a reachable local health endpoint.
- WV2-M5-REQ-003A: Add `.github/workflows/docker-deploy-staging.yml` with a CI gate and deploy job `needs: ci` plus `if: success()`.
- WV2-M5-REQ-003B: Keep deployment scoped to local Docker/self-hosted runner; no cloud provider, secrets, or production deployment.

## Verification Commands

- `docker build -t hermes-trendradar:staging .`
- `docker run -d --name hermes-trendradar-staging-test -p 18080:8080 hermes-trendradar:staging`
- `python3 - <<'PY' ... urllib.request.urlopen("http://127.0.0.1:18080/healthz") ... PY`
- `python3 - <<'PY' ... assert workflow contains deploy needs ci and if success ... PY`

## In

- Add `Dockerfile` and `.dockerignore`.
- Add `docker-deploy-staging` workflow for `workflow_dispatch` and `main` push.
- Reuse M5-2 CI commands inside the deploy workflow instead of inventing a new quality gate.
- Use Docker only; no new Python package dependency.

## Out

- No cloud staging host.
- No secrets, SSH, registry push, domain, TLS, or production deploy.
- No Playwright acceptance automation; that is M5-4.
- No git commit or push unless jym explicitly approves it.
