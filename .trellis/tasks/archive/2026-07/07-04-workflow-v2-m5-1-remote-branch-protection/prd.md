# M5-1 remote and branch protection

## Goal

Configure GitHub origin for hermes-trendradar, inspect existing remote main, and prepare branch protection without force-push or data loss.

## REQ-ID

- WV2-M5-REQ-001: Configure `origin` for `https://github.com/yimingchat886-tech/hermes-trendradar.git`, fetch remote `main`, and inspect remote history before any push.
- WV2-M5-REQ-002: Prepare a non-destructive path for publishing local history and enabling `main` branch protection; force-push is out of scope unless jym explicitly authorizes it.

## Verification Commands

- `git remote -v`
- `git fetch origin main`
- `git log --oneline --decorate --graph --left-right --cherry-pick main...origin/main`
- `gh repo view yimingchat886-tech/hermes-trendradar --json nameWithOwner,visibility,defaultBranchRef,url,isPrivate`

## In

- Add HTTPS `origin` if no remote is configured.
- Fetch and inspect remote `main`.
- Report whether remote `main` is empty, compatible, or divergent.
- Prepare branch protection only after the local/remote `main` decision is safe.

## Out

- No commit.
- No push unless jym explicitly says `push` / `推送` / equivalent.
- No force-push.
- No CI workflow, Docker staging, or Playwright acceptance work; those belong to later M5 children.
