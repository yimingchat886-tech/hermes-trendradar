# Parent: Workflow v2 M5 Remote CI Staging

## Source PRD

- `docs/reports/workflow-v2-m2-m6-plan.md`
- `docs/runbooks/workflow-v2-operator-guide.md`

## Stage Scope

- In: M5 only: remote setup, main branch protection, GitHub Actions CI, staging deploy path plus G6, Playwright smoke acceptance, and CI/staging evidence reporting.
- Out: M2-M4 mechanization, M6 parallel-development enablement, product feature work, and any remote/CI/staging implementation before jym makes the required M5 decisions.

## Stage Constraints

- M5 is an independent T2 parent, not a child of `workflow-v2-mechanization`.
- Before implementation, ask jym for: staging host choice and any required secrets or deployment credentials.
- Do not add `origin`, push history, create workflows, change branch protection, or deploy staging until those decisions are explicit.
- Keep private runtime state and secrets out of git; `.trellis/.runtime`, credentials, tokens, and local deployment outputs stay untracked.
- Base branch is `main`; child branches and PR gates should target `main` unless jym explicitly chooses otherwise.

## Current Repo Facts

- No git remote is configured.
- `gh auth status` is logged in as `yimingchat886-tech` with `repo` and `workflow` scopes.
- Target repo is existing private GitHub repo `yimingchat886-tech/hermes-trendradar`.
- Target repo URL is `https://github.com/yimingchat886-tech/hermes-trendradar`.
- Target repo default branch is `main`.
- Remote `main` already exists at `3de7dc25bdd7267547de1a5b85de08c8c1878d19`.
- Project shape is Python-only: `pyproject.toml`, no package lock, no Dockerfile, no existing `.github/workflows/`.
- Existing local test convention is `python3 -m pytest tests -q`.

## Blocking Decision

Repo decision: use existing private repo `yimingchat886-tech/hermes-trendradar` with HTTPS `origin`.

Staging decision: use local Docker for the first staging slice.

Remaining decisions:

- Remote `main` had existing README/LICENSE on unrelated history; user approved preserving it. M5-1 merged and pushed `main` at `2552bba` without force-push.
- GitHub branch protection initially failed while the repo was private; jym made the repo public, and M5-1 enabled `main` protection.
