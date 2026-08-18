# Implementation Plan: MediaCrawler Long-Running Stability

## Scope

- Build the v1.4-compatible CDP stability foundation around the existing external MediaCrawler boundary.
- Keep `validate-config` schema-only.
- Make `healthcheck --profile --json` check-only: it never launches Chrome and never mutates the login/profile directory.
- Add a non-production `smoke-mediacrawler --profile --json` surface for one-account smoke evidence.
- Keep production `run-daily` semantics unchanged: 10 enabled Douyin accounts, serial collection, account failure isolation, no scheduler.

## CLI Surface

```text
hermes-benchmark healthcheck --profile <profile> --json
hermes-benchmark smoke-mediacrawler --profile <profile> --json
hermes-benchmark run-daily --date <date> --profile <profile> --analysis-mode ... --feishu-mode ... --json
```

- No `validate-runtime` command in this task.
- No CLI scheduler/timer.
- No MediaCrawler source vendoring or patching.

## Runtime Flow

`healthcheck`:

1. Load and validate the profile.
2. Resolve runtime refs without printing raw endpoint, login-state path, proxy, cookie, external checkout path, or artifact root.
3. Check DB/artifact path writability.
4. Check CDP `/json/version` once.
5. Check whether runner-owned Chrome would be launchable.
6. Check MediaCrawler external root/venv callability.
7. Return JSON with `run_eligible` and `runtime_effective_status`.

`smoke-mediacrawler` and later `run-daily` runtime preflight:

1. Acquire the runtime CDP port/profile lock before preflight, launch, or MediaCrawler subprocess.
2. Initial preflight:
   - runner-owned Chrome already usable: continue;
   - no listener and port free: launch dedicated Chrome;
   - invalid/non-Chrome service: fail fast.
3. Post-launch preflight must pass `/json/version`, `Browser`, and `webSocketDebuggerUrl`.
4. Enter MediaCrawler only after final preflight passes.

## Error Codes

- CDP: `CDP_HTTP_UNREACHABLE`, `CDP_VERSION_INVALID`, `CDP_WS_ENDPOINT_MISSING`, `CDP_WS_CONNECT_FAILED`, `CDP_BROWSER_DETACHED`
- Runtime lock/Chrome: `CDP_PORT_PROFILE_LOCK_CONFLICT`, `CHROME_LAUNCH_FAILED`
- Post-CDP runtime/page: `LOGIN_REQUIRED`, `LOGIN_UI_TIMEOUT`, `DOUYIN_UI_CHANGED`, `ACCOUNT_GUARD_TIMEOUT`
- Mapping/state/report: `MAPPER_SCHEMA_ERROR`, `DB_WRITE_FAILED`, `NO_NEW_CONTENT`, `UNKNOWN_ERROR`

`CDP_PRECHECK_FAILED` is a report aggregate counter, not the primary low-level error code.

## Lock And Ownership

- Runtime lock key: canonical runtime ref + CDP host + CDP port + Chrome user data dir.
- Lock is non-blocking; conflict maps to exit code 9 for smoke/run paths.
- Never kill an unknown Chrome or process using the CDP port.
- A valid `/json/version` is not enough for safe attach. Smoke/run may attach only when the current runner holds the lock and the endpoint is the current launched browser or has a retained runner owner marker.
- `healthcheck` reports ambiguous ownership as `blocked` or `launch_required`, not as a successful owned runtime.

## Healthcheck JSON Semantics

- `runtime_effective_status` values: `ready`, `launch_required`, `blocked`.
- `run_eligible` can be true when current CDP is unreachable if Chrome is launchable, profile is writable, and the lock is available.
- Endpoint, websocket, login-state path, external root, raw artifact paths, tokens, cookies, and proxy values are never emitted.

## Smoke Report Semantics

- Smoke result values: `passed`, `success_noop`, `failed`.
- `NO_NEW_CONTENT` is `success_noop`, not failure.
- JSON report is the source of truth; Markdown report is derived from JSON.
- Raw MediaCrawler JSONL stays outside the repo. Committed task evidence may contain only redacted refs.

## Expected Files

- `hermes_benchmark/runtime_cdp.py`
- `hermes_benchmark/cli.py`
- `tests/test_runtime_cdp.py`
- `tests/test_cli_contract.py`
- `.trellis/tasks/07-02-mediacrawler-long-running-stability/stage-report.md`

## Ponytail Pass

- Use stdlib `urllib`, `socket`, `fcntl`, `subprocess`, and `pathlib`; no new dependency.
- Reuse existing profile validation, external redaction, MediaCrawler row import, and SQLite ledger helpers.
- Implement the CDP helper directly; no service layer, scheduler, daemon, queue, or broad runner rewrite.

## Oracle

- Required: yes.
- Result: completed by Oracle browser session `mediacrawl-stability-plan-review-browser-3`.
- Required plan fixes accepted: check-only healthcheck, explicit smoke command, runtime lock before launch, runner-owned Chrome identity, broader redaction, JSON-derived Markdown report, no MediaCrawler source rewrite.

## Confirmation Gate

- [x] User requested execution: `执行这个task`
- [x] Fresh branch created: `codex/mediacrawler-long-running-stability`
- [x] Oracle high-risk PLAN review completed.
- [x] This implementation plan resolves Oracle blockers.
