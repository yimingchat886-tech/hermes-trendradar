# MediaCrawler long-running stability

## Goal

建立一个独立的 MediaCrawler 长线稳定性基础任务，使 v1.4 Douyin 采集链路在进入 MediaCrawler 前先具备可重复的 CDP runtime preflight、runner-owned Chrome 生命周期、错误分层、smoke report 和最小可观测性。第一阶段只落地稳定性计划的 Phase 0 / Phase 1，后续 daily / weekly burn-in 以本任务产出的报告和错误码为基础再扩展。

## Source Of Truth

- 本地 repo PRD：`docs/PRD/releases/PRD_v1.4_split_index.md`
- 本地 repo PRD：`docs/PRD/releases/PRD_v1.4_Codex_CLI_Implementation.md`
- 本地 repo PRD：`docs/PRD/releases/PRD_v1.4_Hermes_Runtime_and_Profiles.md`
- 外部计划：`/mnt/c/Users/Jym/Downloads/tele/mediacrawler_stability_plan.md`
- 已完成基础：`07-01-child-4-v1-4-douyin-collection-runner` 已提供 10 账号串行 collection runner、MediaCrawler JSONL normalization、SQLite content ledger/error record、real smoke/full-comments evidence。

## Local PRD Constraints

- v1.4 production scope is Douyin only, with 10 enabled Douyin accounts from local profile.
- CLI must remain profile/ref driven: schedule, timezone, retry, account list, runtime refs, transcription refs, and Feishu mapping are not hard-coded in production code.
- `validate-config` proves profile/schema safety only; runtime reachability belongs in `healthcheck` and run-time preflight.
- `healthcheck --profile --json` must be machine-readable JSON and check DB/artifact/runtime dependencies, including Chrome CDP reachability and MediaCrawler callability.
- `run-daily` is a single-run orchestrator. The CLI must not implement a production scheduler; Hermes/runtime or ops automation decides when to call it.
- External MediaCrawler source, cookies, tokens, login state, proxy settings, raw videos, and model caches must stay outside this repo and out of committed artifacts.
- Single-account collection failure must not fail the entire batch by default; every failed/skipped account needs a deterministic error code.

## Plan Compatibility Decisions

- Use the plan's default stable mode for this task: `CDP_CONNECT_EXISTING=True`, endpoint `runtime:127.0.0.1:9222`, browser source = runner-owned dedicated Chrome.
- Do not add a new `validate-runtime` command in the MVP. Fold runtime validation into the existing `healthcheck` contract and reuse the same helper from smoke/run-daily paths.
- Keep MediaCrawler itself external. Do not patch or vendor MediaCrawler unless a later review explicitly accepts that external-runtime edit.
- Use a dedicated non-default Chrome user data dir for Douyin CDP login state; do not use the daily Chrome profile.
- Bind runner-owned Chrome CDP to `127.0.0.1`, not `0.0.0.0`.
- Treat the current direct `ws://localhost:9222/devtools/browser` 404 as a noisy first-hop behavior to eliminate or bypass. The stable path is `/json/version` -> `webSocketDebuggerUrl`, or Playwright `connect_over_cdp("http://127.0.0.1:9222/")` if verified.
- Keep long-running daily/weekly timers out of this first implementation unless explicitly approved. This task must produce the stable smoke/report foundation first.

## Current Local Facts

- Current runtime profile points to `runtime:127.0.0.1:9222`.
- Current machine has Google Chrome and the external MediaCrawler checkout/venv available.
- Current machine did not have a live listener on `127.0.0.1:9222` during task creation.
- `systemd --user` is available with linger enabled, but production scheduling remains outside the CLI per v1.4 PRD.
- The working tree already had unrelated untracked child 6a files when this task was created; this task must not touch them unless the user explicitly changes scope.

## Requirements

- R1: Add a CDP runtime helper that can check `http://127.0.0.1:9222/json/version` with a short timeout and classify failures before MediaCrawler starts.
- R2: Add runner-owned Chrome lifecycle support for smoke/run paths: launch dedicated Chrome with non-default profile when no usable CDP endpoint exists, then re-run preflight.
- R3: Add a port/profile lock so concurrent runs cannot share the same CDP port and Chrome profile.
- R4: Use explicit CDP error codes at minimum: `CDP_HTTP_UNREACHABLE`, `CDP_VERSION_INVALID`, `CDP_WS_ENDPOINT_MISSING`, `CDP_WS_CONNECT_FAILED`, `CDP_BROWSER_DETACHED`, `LOGIN_REQUIRED`, `LOGIN_UI_TIMEOUT`, `DOUYIN_UI_CHANGED`, `ACCOUNT_GUARD_TIMEOUT`, `MAPPER_SCHEMA_ERROR`, `DB_WRITE_FAILED`, `NO_NEW_CONTENT`, `UNKNOWN_ERROR`.
- R5: Extend `healthcheck --profile --json` to include runtime profile parsing, DB/artifact path checks, CDP preflight result, and MediaCrawler executable/path callability without leaking endpoint/token/cookie values.
- R6: Provide a smoke scenario using 1 stable Douyin account, 1-3 content items, no comments by default, JSONL raw artifact retention outside repo, adapter mapping check, ledger upsert, and machine-readable JSON report.
- R7: Produce a human-readable Markdown smoke report next to the JSON report with summary, CDP status, account result, error breakdown, artifact refs, and next action.
- R8: Preserve current child 4 behavior: 10-account production collection remains serial and failure-isolated; this task should wrap runtime stability around it, not rewrite it.
- R9: Keep security/redaction gates: no raw CDP websocket URL, cookie, token, proxy secret, login state, raw video, or external source tree enters committed repo files.
- R10: Before implementation starts, add `design.md` or `implement.md` covering the exact CLI surface and whether `healthcheck` is check-only while smoke/run paths can launch Chrome.

## Acceptance Criteria

- [ ] Given no Chrome is listening on the configured CDP endpoint, when the smoke scenario runs, then the runner starts dedicated Chrome, verifies `/json/version`, and enters MediaCrawler only after preflight passes.
- [ ] Given a non-Chrome service or invalid response is present on the configured port, when preflight runs, then the command returns `CDP_VERSION_INVALID` or `CDP_WS_ENDPOINT_MISSING` and does not enter MediaCrawler.
- [ ] Given another run holds the same port/profile lock, when a second run starts, then it fails fast with a clear lock-conflict error and no MediaCrawler subprocess starts.
- [ ] Given `healthcheck --profile profiles/local/hermes.v1.4.douyin.local.json --json`, when runtime dependencies are present, then the JSON envelope includes profile, DB/artifact, CDP, and MediaCrawler checks with redacted fields.
- [ ] Given the smoke account has no new content, when smoke completes cleanly, then the result is success-noop / `NO_NEW_CONTENT`, not failure.
- [ ] Given MediaCrawler writes JSONL output, when adapter mapping runs, then mapper success rate is 100% or failures are reported as `MAPPER_SCHEMA_ERROR` with artifact refs.
- [ ] Given smoke runs 3 consecutive times, then each run writes JSON and Markdown reports and has `CDP_PRECHECK_FAILED = 0` and `UNKNOWN_ERROR = 0`.
- [ ] Given a login or page selector problem happens after CDP connection succeeds, then it is classified as `LOGIN_REQUIRED`, `LOGIN_UI_TIMEOUT`, or `DOUYIN_UI_CHANGED`, not CDP failure.
- [ ] No committed file contains cookies, login state, proxy secrets, raw CDP websocket URL, raw videos, raw crawler dumps, external source trees, or model caches.

## Out Of Scope

- Full daily/weekly scheduler implementation inside the CLI.
- Enabling a user-level `systemd` timer by default.
- Weekly long run, 20-account expansion, or 4-week burn-in evidence.
- Comment cursor checkpoint schema and resume implementation.
- MediaCrawler source vendoring or broad MediaCrawler refactor.
- Xiaohongshu or other platform support.
- Feishu live writes, Hermes LLM analysis, transcription, or media download.

## Required Planning Before Start

- Decide whether Oracle PLAN review is required before implementation. Default: required because the task touches external runtime, Chrome CDP, login-state profile, and real Douyin smoke.
- Write `design.md` or `implement.md` with the final minimal execution shape.
- Confirm whether this task should start from a fresh branch instead of the current child 6a branch.

## Technical Notes

- This is a standalone Trellis task related to v1.4 productionization requirements P14-REQ-040 and P14-REQ-080, but it is not added as a child under the parent task yet.
- Prefer reusing `hermes_benchmark.collection_runner`, `hermes_benchmark.profile`, `hermes_benchmark.state`, existing redaction helpers, and existing external runtime boundaries before adding new modules.
- Prefer `healthcheck` over a new `validate-runtime` command for the MVP.
- If later timer automation is approved, use user-level `systemd` as ops/deployment surface, not as CLI scheduler logic.
