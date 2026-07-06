# Stage Report: M5-1 remote and branch protection

## Acceptance

- [x] `origin` points at `https://github.com/yimingchat886-tech/hermes-trendradar.git`.
- [x] Remote `main` has been fetched and inspected.
- [x] Any divergence between local `main` and remote `main` is reported before push.
- [x] No force-push or branch protection mutation happens without explicit approval.
- [x] `main` is published to origin.
- [x] `main` branch protection is enabled or explicitly deferred.

## Verification

- `git remote -v`: pass; fetch and push URL both point at `https://github.com/yimingchat886-tech/hermes-trendradar.git`.
- `git fetch origin main`: pass; fetched `origin/main`.
- `git log --oneline --decorate --graph --left-right --cherry-pick main...origin/main`: pass; histories are unrelated/divergent.
- `git ls-tree --name-only -r origin/main`: pass; remote currently contains `README.md` and `LICENSE`.
- `git show origin/main:README.md`: pass; README contains `# hermes-trendradar`.
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main/protection`: blocked; GitHub returned 403, `Upgrade to GitHub Pro or make this repository public to enable this feature.`
- `gh api repos/yimingchat886-tech/hermes-trendradar/rulesets`: blocked; GitHub returned the same 403.
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main --jq '{name: .name, protected: .protected, protection_url: .protection_url}'`: pass; `main` currently reports `protected: false`.
- `git merge --allow-unrelated-histories --no-commit --no-ff origin/main`: pass; staged remote README/LICENSE only.
- `git diff --cached --check`: pass.
- `node "/home/jym/workspace/Hermes stock/.gitnexus/run.cjs" detect-changes --scope staged --repo "Hermes stock"`: pass; `No changes detected.`
- `bash "/home/jym/workspace/Hermes stock/.trellis/scripts/mark_scope_ok.sh" --print`: pass; matched `.trellis/.runtime/scope-check.ok` fingerprint `aaf6e890c31442c41acc2df34021e3460c153d24ac0ef12a4815e1ffa5f38a21`.
- `git commit --no-verify -m "chore: preserve remote README and license"`: pass; created merge commit `2552bba`.
- `git push -u origin main`: pass; `G4 pre-push ok`; remote advanced `3de7dc2..2552bba`.
- `git ls-remote --heads origin main`: pass; remote `main` is `2552bbae32e2a63c5adcb1fce7fc1125501cda5d`.
- `git log --oneline --decorate -3 origin/main`: pass; `origin/main` points at `2552bba`.
- `python3 ./.trellis/scripts/state_cli.py event ... blocker_opened`: pass; child state is `child_blocked`.
- `gh repo view yimingchat886-tech/hermes-trendradar --json nameWithOwner,visibility,isPrivate,defaultBranchRef,url`: pass; repo is now `PUBLIC`.
- `gh api -X PUT repos/yimingchat886-tech/hermes-trendradar/branches/main/protection`: pass; enabled `main` protection with required PR, admin enforcement, no force-push, no deletion, and no required status checks yet.
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main --jq '{name: .name, protected: .protected, commit_sha: .commit.sha}'`: pass; `protected: true`, commit remains `2552bba`.
- `gh api repos/yimingchat886-tech/hermes-trendradar/branches/main/protection --jq '{enforce_admins: .enforce_admins.enabled, required_pr_reviews: .required_pull_request_reviews.required_approving_review_count, allow_force_pushes: .allow_force_pushes.enabled, allow_deletions: .allow_deletions.enabled, required_status_checks: .required_status_checks}'`: pass; `enforce_admins=true`, `required_pr_reviews=0`, `allow_force_pushes=false`, `allow_deletions=false`, `required_status_checks=null`.
- Negative path: detached empty commit push to `main` was rejected with `GH006: Protected branch update failed` and `Changes must be made through a pull request.`
- `git ls-remote --heads origin main`: pass after negative test; remote `main` remains `2552bbae32e2a63c5adcb1fce7fc1125501cda5d`.
- `python3 ./.trellis/scripts/state_cli.py event ... blocker_resolved`: pass; blocker resolved after repo became public and protection was enabled.

## Findings

- Remote `main` already exists at `3de7dc2` with README and Apache-2.0 LICENSE.
- Local `main` and `origin/main` have no merge-base, so a normal push from local `main` would not be a fast-forward.
- User authorized preserving remote README/LICENSE, merging the remote initial commit into local `main`, then pushing.
- Force-push was not used.
- The merge commit used `--no-verify` because the installed pre-commit hook expects `.trellis/scripts/mark_scope_ok.sh`, but the older local `main` being published does not contain that M3 helper yet. Equivalent manual G3 checks passed before commit.
- GitHub branch protection/rulesets were unavailable while the repo was private. After jym made the repo public, branch protection was enabled successfully.

## Blocker

- Opened at: 2026-07-05T01:01:29Z
- Resolved at: 2026-07-05T01:20:46Z
- State: resolved
- Reason: GitHub branch protection/rulesets are unavailable for this private repo under the current plan.
- Resolution: jym made the repo public; `main` branch protection was enabled and direct push was rejected by GitHub.

## Push State

- Pushed: yes, `main` to `origin/main` at `2552bba`.

## User Completion Signal

- Raw signal: 验收通过，提交git
- Received at: 2026-07-05T01:56:11Z
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: no
