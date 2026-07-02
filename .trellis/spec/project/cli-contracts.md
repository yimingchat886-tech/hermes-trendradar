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
