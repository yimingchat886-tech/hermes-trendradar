# Stage Report: MediaCrawler Long-Running Stability

## Scope

- Added a v1.4 CDP runtime preflight and runner-owned Chrome lifecycle helper.
- Extended `healthcheck --profile --json` into a check-only runtime contract.
- Added `smoke-mediacrawler --profile --json` for one-account non-production MediaCrawler smoke.
- Preserved `validate-config` as schema-only.
- Preserved `run-daily` as a production stub and added its v1.4 args without changing 10-account runner behavior.
- Kept MediaCrawler external; no source vendoring or external checkout edits.

## Oracle PLAN Gate

- API Oracle attempt failed because `OPENAI_API_KEY` was not set.
- Browser Oracle attachment upload timed out.
- Browser Oracle text-bundle session succeeded: `mediacrawl-stability-plan-review-browser-3`.
- Accepted blockers: check-only healthcheck, explicit smoke command, runtime lock before launch, runner-owned ownership marker, broader redaction, JSON-derived Markdown report, no MediaCrawler source rewrite.

## Implementation Summary

- Added `hermes_benchmark/runtime_cdp.py`.
- Updated `hermes_benchmark/cli.py`.
- Added `tests/test_runtime_cdp.py`.
- Updated `tests/test_cli_contract.py`.
- Runtime lock conflict maps to `CDP_PORT_PROFILE_LOCK_CONFLICT`.
- CDP preflight checks `/json/version`, `Browser`, and `webSocketDebuggerUrl`.
- Existing valid CDP without runner owner marker is blocked instead of silently attached.
- `NO_NEW_CONTENT` is reported as `success_noop`, not failure.
- MediaCrawler process failures after successful CDP are classified into page/login/account layers instead of always `UNKNOWN_ERROR`.

## Local Healthcheck

Command:

```bash
python3 -m hermes_benchmark.cli healthcheck --profile profiles/local/hermes.v1.4.douyin.local.json --json
```

Result:

- `runtime_effective_status`: `launch_required`
- `run_eligible`: `true`
- CDP status: `launch_required`
- MediaCrawler status: `callable`
- DB/artifact checks: `passed`
- Raw endpoint, WebSocket URL, login-state path, external source path, cookie, token, and proxy values were not printed.

## Real Smoke Evidence

Initial real smoke:

| Run | Result | CDP Final | Contents | Errors |
|---|---|---|---:|---|
| `run-mediacrawler-smoke-20260703T075003Z` | `passed` | `passed` | 10 seen, 1 inserted, 9 noop | none |

During verification, one run hit a post-CDP Douyin page navigation timeout:

| Run | Observed layer | Root cause |
|---|---|---|
| `run-mediacrawler-smoke-20260703T075252Z` | page/UI layer | CDP connected, then `Page.goto("https://www.douyin.com/")` timed out after 30s |

Fix applied: classify this class as `DOUYIN_UI_CHANGED` instead of `UNKNOWN_ERROR`.

Final 3 consecutive smoke runs after the classification fix:

| Run | Result | CDP Final | Contents | Errors |
|---|---|---|---:|---|
| `run-mediacrawler-smoke-20260703T075545Z` | `success_noop` | `passed` | 10 seen, 0 inserted, 10 noop | none |
| `run-mediacrawler-smoke-20260703T075650Z` | `success_noop` | `passed` | 10 seen, 0 inserted, 10 noop | none |
| `run-mediacrawler-smoke-20260703T075753Z` | `success_noop` | `passed` | 10 seen, 0 inserted, 10 noop | none |

Summary:

- `CDP_PRECHECK_FAILED`: 0 for final 3 smoke runs.
- `UNKNOWN_ERROR`: 0 for final 3 smoke runs.
- JSON and Markdown reports were written for each smoke under external artifact storage.
- Committed task evidence records only `file:<artifact-root-relative-ref>` report refs.

## Verification

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_runtime_cdp.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/test_cli_contract.py` | pass |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -s tests/test_runtime_cdp.py tests/test_cli_contract.py` | pass, 13 tests |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -s tests` | pass, 33 tests |
| `python3 -m hermes_benchmark.cli healthcheck --profile profiles/local/hermes.v1.4.douyin.local.json --json` | pass, `run_eligible=true` |
| `python3 -m hermes_benchmark.cli smoke-mediacrawler --profile profiles/local/hermes.v1.4.douyin.local.json --json` x final 3 | pass, all `success_noop` |
| `git diff --check` | pass |
| `npx gitnexus detect-changes --repo "Hermes stock" --scope all` | medium risk; affected flow `Main -> Add_command` |

## Ponytail Review

- Removed an unused exit constant.
- Removed duplicate DB parent mkdir.
- Replaced a tautological noop condition.
- Reused the report-ref helper for successful and failed reports.
- No new dependency, scheduler, daemon, queue, or MediaCrawler source patch added.

## Remaining Risk

- `gitnexus detect-changes` reports medium risk because `hermes_benchmark/cli.py` is the CLI entrypoint and the new helper symbols are not in the current index. Focused impact checks before editing were LOW, and runtime tests plus real smoke covered the changed behavior.
- Real Douyin smoke still depends on local login state and Douyin page availability. One transient page navigation timeout occurred after CDP success and is now classified outside CDP.
- The CLI still does not implement production `run-daily`; this task adds the CDP/smoke foundation and keeps the existing production stub unchanged except for accepting v1.4 args.

## Commit / Push

- Completion signal received: yes.
- User completion signal: `可以提交，告诉我有没有未完成的地方（对比Trellis task）`.
- Received at: `2026-07-03T01:11:43-07:00`.
- Commit allowed: yes.
- Soft archive allowed: no.
- Soft archive completed: no.
- Built-in Trellis archive called: no.
- Pushed: no.
