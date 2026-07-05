# Claim Guard And Ownership

## Scenario: M6 Claim Guard BLOCK And Ownership Commands

### 1. Scope / Trigger

- Trigger: changing `.claude/hooks/claim_guard.py`, `task.py claim`, `task.py release`, task `owner`, or `touches` ownership behavior.
- Scope: local Trellis task ownership gates only; no product runtime behavior.

### 2. Signatures

- `python3 ./.trellis/scripts/task.py claim <task-dir> --owner cc|codex|jym [--override-claim --reason "<why>"]`
- `python3 ./.trellis/scripts/task.py release <task-dir> [--owner cc|codex|jym] [--reason "<why>"]`
- Hook override env: `TRELLIS_OVERRIDE_CLAIM_REASON="<why>"`
- Hook payload override field: `override_claim` or `override_claim_reason` with a non-empty string value.

### 3. Contracts

- `claim_guard.py` runs in `MODE = "BLOCK"` for active task cards where target path matches `touches` and task `owner` differs from current developer.
- Dot-directory paths such as `.claude/hooks/claim_guard.py` must keep their leading dot when normalized.
- `claim` and `release` update only `task.json.owner` and append an audit row to `state-events.jsonl`.
- Override paths must append an `override_claim` audit row before allowing the action.
- `release` defaults ownership to `jym`.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Cross-owner matching target without override | hook exits `2` |
| Matching target with explicit override reason | hook exits `0` and writes `override_claim` |
| `claim --override-claim` without `--reason` | exit `1` |
| Invalid owner | exit `1` |

### 5. Good/Base/Bad Cases

- Good: `TRELLIS_OWNER=cc` editing a `codex`-owned `.claude/hooks/**` card is blocked.
- Base: `task.py claim <dir> --owner codex` records `claim`.
- Bad: stripping `.claude/...` to `claude/...` makes the guard miss the task glob.

### 6. Tests Required

- Hook blocks cross-owner hidden-dot path matches.
- Hook override writes `override_claim`.
- `task.py claim` and `release` update owner and audit events.
- BOARD refreshes after `claim` / `release`.

### 7. Wrong vs Correct

#### Wrong

```bash
printf '{"tool_input":{"file_path":".claude/hooks/claim_guard.py"}}' \
  | TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py
# exits 0 because ".claude" was normalized to "claude"
```

#### Correct

```bash
printf '{"tool_input":{"file_path":".claude/hooks/claim_guard.py"}}' \
  | TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py
# exits 2 when an active codex-owned task touches ".claude/hooks/**"
```
