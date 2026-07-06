# Hermes Benchmark CLI Contracts

## Scenario: v1.4 CLI Contract Skeleton

### 1. Scope / Trigger

- Trigger: any change to `hermes-benchmark` commands, JSON envelopes, or exit codes.
- Scope: command shell only. Real profile parsing, SQLite, collection, transcription, Hermes analysis, and Feishu live writes stay in later child tasks.

### 2. Signatures

- `hermes-benchmark --version`
- `hermes-benchmark validate-config --profile <path> --json`
- `hermes-benchmark healthcheck --json`
- `hermes-benchmark run-daily --profile <path> --json`
- `hermes-benchmark apply-limited-live --profile <path> --json`

### 3. Contracts

Success JSON:

```json
{"ok":true,"command":"validate-config","mode":"stub","data":{},"error":null}
```

Contract error JSON:

```json
{"ok":false,"command":null,"mode":"contract","data":null,"error":{"code":"contract_mismatch","message":"..."},"exit_code":2}
```

### 4. Validation & Error Matrix

| Condition | Exit | JSON error |
|---|---:|---|
| Stub command parses | 0 | none |
| Invalid command or arg with `--json` | 2 | `contract_mismatch` |
| Invalid command or arg without `--json` | 2 | stderr text |

### 5. Good/Base/Bad Cases

- Good: `validate-config --profile missing.yaml --json` returns parseable stub JSON and does not read the profile.
- Base: `healthcheck --json` returns CLI version and a local contract check.
- Bad: unknown commands with `--json` must still return parseable contract JSON.

### 6. Tests Required

- Assert each v1.4 stub command returns parseable JSON.
- Assert invalid args with `--json` return exit code 2 and `error.code = contract_mismatch`.
- Assert installable console script exposes `hermes-benchmark --version`.

### 7. Wrong vs Correct

#### Wrong

```bash
hermes-benchmark validate-config --profile missing.yaml --json
# reads the missing profile or exits with argparse text only
```

#### Correct

```bash
hermes-benchmark validate-config --profile missing.yaml --json
# returns stub JSON with "profile_read": false
```

## Scenario: v1.4 Profile Validation

### 1. Scope / Trigger

- Trigger: `validate-config` reads a local v1.4 runtime profile.
- Scope: local profile parsing, child `file:` refs, summary hash, and validation only.
- Out of scope: resolving secrets, connecting to CDP or Feishu, SQLite state, collection, transcription, scheduler, and live writes.

### 2. Signatures

- `hermes-benchmark validate-config --profile <path> --json`
- `hermes-benchmark validate-config --config <path> --json`

`--config` is a compatibility alias. If both are supplied with different paths, reject the command as `contract_mismatch`.

### 3. Contracts

Valid profile JSON envelope:

```json
{"ok":true,"command":"validate-config","mode":"profile","data":{"ok":true,"profile_hash":"sha256:...","enabled_account_count":10,"required_enabled_accounts":10,"errors":[]},"error":null}
```

Invalid profile JSON envelope:

```json
{"ok":false,"command":"validate-config","mode":"profile","data":{"ok":false,"errors":["..."]},"error":{"code":"config_invalid","message":"profile/config validation failed","errors":["..."]},"exit_code":2}
```

Current implementation uses stdlib JSON profile files. Do not add a YAML dependency unless a future task explicitly approves that dependency.

### 4. Validation & Error Matrix

| Condition | Exit | JSON error |
|---|---:|---|
| Valid v1.4 profile with 10 enabled Douyin accounts | 0 | none |
| Missing `--profile` / `--config` | 2 | `contract_mismatch` |
| Conflicting `--profile` and `--config` | 2 | `contract_mismatch` |
| Missing or invalid profile file | 2 | `config_invalid` |
| Enabled non-Douyin account | 2 | `config_invalid` |
| Plaintext cookie/token/password/CDP endpoint/proxy/login state | 2 | `config_invalid` |

### 5. Good/Base/Bad Cases

- Good: sample profile reports `enabled_account_count = 10` and `profile_hash` starts with `sha256:`.
- Base: `--config <path>` returns the same validation result as `--profile <path>`.
- Bad: plaintext sensitive values fail without echoing the plaintext value in JSON output.

### 6. Tests Required

- Assert valid sample profile returns `mode = profile` and 10 enabled accounts.
- Assert root and child profile sensitive plaintext values fail.
- Assert enabled non-Douyin accounts fail.
- Assert profile hash is deterministic.
- Assert `--config` alias works and conflicting args fail.

### 7. Wrong vs Correct

#### Wrong

```bash
hermes-benchmark validate-config --profile local.production.json --json
# validates by connecting to CDP, reading secrets, or printing plaintext values
```

#### Correct

```bash
hermes-benchmark validate-config --profile profiles/examples/hermes.v1.4.douyin.sample.json --json
# validates local files only, returns a redacted JSON envelope, and never dereferences secrets
```

## Scenario: v1.4 MediaCrawler CDP Healthcheck And Smoke

### 1. Scope / Trigger

- Trigger: `healthcheck` checks runtime dependencies or `smoke-mediacrawler` runs MediaCrawler against a runner-owned Chrome CDP runtime.
- Scope: CDP preflight, runtime lock, runner-owned Chrome launch, one-account non-production smoke, JSON/Markdown report, redaction.
- Out of scope: production scheduler, weekly burn-in, MediaCrawler source edits, Feishu live writes, and changing the 10-account production `run-daily` behavior.

### 2. Signatures

- `hermes-benchmark healthcheck --profile <path> --json`
- `hermes-benchmark smoke-mediacrawler --profile <path> --json`

### 3. Contracts

- `healthcheck` is check-only. It must not launch Chrome, mutate login state, run MediaCrawler, or write smoke artifacts.
- `healthcheck` returns `runtime_effective_status = ready|launch_required|blocked` and `run_eligible = true|false`.
- `CDP_HTTP_UNREACHABLE` can still be `run_eligible=true` when runner Chrome is launchable, profile is writable, lock is available, and MediaCrawler is callable.
- `smoke-mediacrawler` acquires the runtime lock before preflight, launch, or MediaCrawler subprocess.
- Smoke reports use `result = passed|success_noop|failed`; `NO_NEW_CONTENT` maps to `success_noop`.
- JSON report is the source of truth. Markdown report is derived from JSON.
- Committed output must use redacted refs, not raw endpoints, WebSocket URLs, login-state paths, external roots, cookies, tokens, proxies, raw dumps, or raw videos.

### 4. Validation & Error Matrix

| Condition | Exit | JSON error/result |
|---|---:|---|
| healthcheck only needs Chrome launch | 0 | `runtime_effective_status=launch_required`, `run_eligible=true` |
| invalid service on CDP port | 3 | `CDP_VERSION_INVALID` or `CDP_WS_ENDPOINT_MISSING` |
| runtime lock already held | 9 | `CDP_PORT_PROFILE_LOCK_CONFLICT` |
| existing CDP is not runner-owned | 3 | `CDP_BROWSER_DETACHED` |
| post-CDP login UI timeout | 4 | `LOGIN_UI_TIMEOUT` |
| post-CDP Douyin navigation/page timeout | 4 | `DOUYIN_UI_CHANGED` |
| account/process guard timeout | 4 | `ACCOUNT_GUARD_TIMEOUT` |
| no new imported content but mapping succeeds | 0 | `result=success_noop`, no error row |
| mapper failure | 4 | `MAPPER_SCHEMA_ERROR` |

### 5. Good/Base/Bad Cases

- Good: no listener on configured port, healthcheck returns `launch_required`; smoke launches runner Chrome, final CDP preflight passes, then MediaCrawler starts.
- Base: repeated smoke with already-seen rows returns `success_noop` and `NO_NEW_CONTENT`, not failure.
- Bad: a user/default Chrome responds on the CDP port but lacks the runner owner marker; smoke blocks instead of silently attaching.

### 6. Tests Required

- Classify unreachable, invalid JSON, missing `webSocketDebuggerUrl`, and valid `/json/version`.
- Assert runtime lock conflict prevents launch/preflight.
- Assert valid but unowned CDP is blocked.
- Assert healthcheck profile JSON redacts raw endpoint and paths.
- Assert CLI contract still returns parseable JSON for invalid args with `--json`.

### 7. Wrong vs Correct

#### Wrong

```bash
hermes-benchmark healthcheck --profile profiles/local/hermes.v1.4.douyin.local.json --json
# starts Chrome or fails just because no Chrome is currently listening
```

#### Correct

```bash
hermes-benchmark healthcheck --profile profiles/local/hermes.v1.4.douyin.local.json --json
# check-only; returns launch_required + run_eligible=true when smoke/run can launch runner Chrome
```

## Scenario: v2.0 Hermes Analysis Result Recording

### 1. Scope / Trigger

- Trigger: `record-analysis-result` consumes a v1.4 handoff package and a Hermes-owned analysis result file.
- Scope: local package/result validation, SQLite reference persistence, JSON envelope, and deterministic invalid-result errors.
- Out of scope: generating the real analysis text, prompt tuning, digest delivery, feedback intake, and Feishu writes.

### 2. Signatures

- `hermes-benchmark record-analysis-result --profile <path> --package <file-or-file-ref> --result <path> --json`
- `--config <path>` remains a compatibility alias for `--profile`.

### 3. Contracts

Success JSON:

```json
{"ok":true,"command":"record-analysis-result","mode":"runtime","data":{"schema_version":"1.4","analysis_result_id":"analysis_result_...","package_id":"package_...","content_id":"...","transcript_artifact_ref":"file:...","result_ref":"file:...","result_hash":"sha256:...","status":"succeeded"},"error":null}
```

Invalid result JSON:

```json
{"ok":false,"command":"record-analysis-result","mode":"runtime","data":null,"error":{"code":"analysis_result_invalid","message":"..."},"exit_code":6}
```

The command persists only refs, hashes, ids, and status. It must not import or call `mock_hermes_output`, and it must not store raw analysis body text in SQLite.

### 4. Validation & Error Matrix

| Condition | Exit | JSON error |
|---|---:|---|
| Package ref and result match handoff trace refs | 0 | none |
| Package is unreadable, invalid JSON, or invalid handoff package | 6 | `analysis_result_invalid` |
| Result is unreadable, invalid JSON, or missing required fields | 6 | `analysis_result_invalid` |
| Result `package_id`, `run_id`, `content_id`, or `transcript_artifact_ref` does not match the handoff package | 6 | `analysis_result_invalid` |
| Result ref is not a relative `file:` ref | 6 | `analysis_result_invalid` |

### 5. Good/Base/Bad Cases

- Good: a result file for one handoff content row stores one `analysis_results` row keyed by `(package_id, content_id)`.
- Base: repeating the same record command updates the same row and returns the same deterministic `analysis_result_id`.
- Bad: a mismatched package id or transcript ref fails closed with exit 6 and does not persist raw analysis output.

### 6. Tests Required

- Assert valid package/result refs persist an `analysis_results` row linked to package/content/transcript refs.
- Assert invalid result files return exit 6 with `error.code = analysis_result_invalid`.
- Assert the real result-recording path does not import or call mock decomposition.
- Assert persisted rows and JSON envelopes contain no raw analysis body text.

## Scenario: v2.0 Internal Digest Payload

### 1. Scope / Trigger

- Trigger: `build-internal-digest` turns persisted real-analysis refs into an internal-group digest payload.
- Scope: local SQLite reads, traceable digest JSON artifact, JSON envelope, degraded/error reporting, and explicit delivery blocker when no message transport is configured.
- Out of scope: Feishu/Bitable table writes, external group cards, feedback persistence, and implementing Hermes/Feishu credentials or message transport inside this repo.

### 2. Signatures

- `hermes-benchmark build-internal-digest --profile <path> --run-id <run_id> --json`
- `--config <path>` remains a compatibility alias for `--profile`.

### 3. Contracts

Success JSON:

```json
{"ok":true,"command":"build-internal-digest","mode":"runtime","data":{"schema_version":"2.0-m1","run_id":"run_...","digest_payload_ref":"file:.../internal_digest.json","digest_payload_hash":"sha256:...","status":"ready|degraded","summary":{},"trace":{},"delivery":{"channel":"feishu_internal_group","status":"blocked","blocker_code":"message_channel_not_configured"}},"error":null}
```

Invalid digest input JSON:

```json
{"ok":false,"command":"build-internal-digest","mode":"runtime","data":null,"error":{"code":"digest_payload_invalid","message":"..."},"exit_code":6}
```

The digest payload stores refs, ids, hashes, redacted error summaries, and delivery status only. It must not import or call mock decomposition, and it must not write Feishu/Bitable operation rows.

### 4. Validation & Error Matrix

| Condition | Exit | JSON error |
|---|---:|---|
| Run has persisted analysis result refs | 0 | none |
| Run is missing | 6 | `digest_payload_invalid` |
| Run has no analysis result refs | 6 | `digest_payload_invalid` |
| Analysis result or error state is failed/degraded | 0 | none; payload `status=degraded` |
| No repo-local Hermes/Feishu message transport is configured | 0 | none; payload delivery blocker |

### 5. Good/Base/Bad Cases

- Good: payload item trace links run, package, content, transcript artifact, analysis result id, result ref, and result hash.
- Base: a degraded analysis row or persisted error appears in payload `degraded`.
- Bad: building the digest must not create Feishu/Bitable operation rows or include raw analysis text.

### 6. Tests Required

- Assert digest payload contains analyzed items and trace refs.
- Assert degraded/error state appears in payload.
- Assert no Feishu operation rows are written.
- Assert CLI writes a `file:` payload artifact and reports the message-channel blocker.
