# Merge Collision Protocol

## Scenario: M6 Conflict Checklist

### 1. Scope / Trigger

- Trigger: changing `.trellis/scripts/conflict_checklist.py`, `.trellis/scripts/merge_protocol.md`, or parallel child merge handling.
- Scope: local Git/Trellis workflow checks only; no product runtime behavior.

### 2. Signatures

- `python3 ./.trellis/scripts/conflict_checklist.py --repo <path>`
- `python3 ./.trellis/scripts/conflict_checklist.py --help`

### 3. Contracts

- The script reads conflicted files from `git diff --name-only --diff-filter=U`.
- Active child cards come from `.trellis/tasks/*/task.json`.
- Completed, cancelled, parent, and no-touch cards are ignored.
- File ownership is matched through each child card's `touches` globs.
- For every conflicted file, the report prints matching task card name, owner, status, and `stage-report.md` path.
- The merge protocol requires reading all matching child stage reports before resolving conflict markers.

### 4. Validation & Error Matrix

| Condition | Exit | Result |
|---|---:|---|
| No conflicted files | 0 | `No conflicted files.` |
| Conflicted file has two or more matching child cards and readable reports | 0 | report lists all matches |
| Conflicted file has fewer than two matching child cards | 1 | stderr explains the under-match |
| Matching child stage report is missing or unreadable | 1 | stderr names the missing report |
| Git command fails | 2 | stderr starts with `error:` |

### 5. Good/Base/Bad Cases

- Good: a shared `governance.md` conflict maps to both child cards, and both stage reports are readable.
- Base: no merge conflict exits 0 with a short no-conflict message.
- Bad: resolving a conflict before reading both child stage reports loses the intent of one branch.

### 6. Tests Required

- Assert path matching finds two child cards and skips a parent card.
- Assert a single matching card is a failure.
- Assert a missing `stage-report.md` is a failure.

### 7. Wrong vs Correct

#### Wrong

```bash
git status --short
# edit conflict markers directly, without checking task cards or stage reports
```

#### Correct

```bash
python3 ./.trellis/scripts/conflict_checklist.py --repo .
# read every listed stage-report.md before resolving the conflict
```
