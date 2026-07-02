# Stage Report: v1.4 CLI Contract Skeleton

## Scope

- Child task: `.trellis/tasks/07-01-child-1-v1-4-cli-contract-skeleton`
- Requirement IDs: P14-REQ-010, P14-REQ-080

## Completed Work

- Added minimal Python package metadata and `hermes-benchmark` console entrypoint.
- Added stdlib `argparse` command skeleton for `validate-config`, `healthcheck`, `run-daily`, and `apply-limited-live`.
- Added centralized success/error JSON envelopes and contract-mismatch exit handling.
- Added a no-dependency CLI contract check.
- Added the project-level CLI contract spec required for future child tasks that modify command signatures or JSON envelopes.

## Unfinished Work

- Real profile parsing, SQLite, collection, transcription, Hermes handoff, Feishu writes, scheduler, and MCP server remain outside this child.

## Changed Files

- `pyproject.toml`
- `hermes_benchmark/cli.py`
- `tests/test_cli_contract.py`
- `.trellis/spec/project/cli-contracts.md`
- `.trellis/spec/project/index.md`
- `.trellis/tasks/07-01-child-1-v1-4-cli-contract-skeleton/`

## Scope Compliance

- Missed work: none for this child.
- Extra work: none.
- Deviations from PLAN: none.

## Ponytail Review

- Blocking findings: none.
- Advisory findings: use stdlib `argparse`; no CLI framework.
- Accepted cuts: no real runtime behavior in this child; removed unnecessary dataclass from the CLI error type.
- Rejected cuts and reason: none.

## Oracle

- Required: no.
- Result: skipped.
- Output path: none.
- Skip reason: child PRD marks high-risk trial PLAN as no; this is a low-risk contract shell.

## Verification

| Command | Result | Evidence |
|---|---|---|
| `python3 tests/test_cli_contract.py` | pass | Stub command JSON and invalid-args JSON contract assertions passed. |
| `set -e; tmpdir=$(mktemp -d); trap 'rm -rf "$tmpdir"' EXIT; python3 -m venv "$tmpdir/venv"; "$tmpdir/venv/bin/python" -m pip install . >/tmp/hermes_child1_pip.log; "$tmpdir/venv/bin/hermes-benchmark" --version; "$tmpdir/venv/bin/hermes-benchmark" validate-config --profile missing.yaml --json` | pass | Console script returned `hermes-benchmark 0.1.0` and parseable stub JSON. |
| `python3 -m compileall -q hermes_benchmark` | pass | Package modules compiled. |
| `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-01-child-1-v1-4-cli-contract-skeleton` | pass | `implement.jsonl` and `check.jsonl` passed. |
| `git diff --check` | pass | No whitespace errors. |
| `npx gitnexus detect-changes --scope staged --repo "Hermes stock"` | pass | Run after staging intended child and v1.4 files. |

Note: an earlier install check using `--no-build-isolation` failed because the fresh Python 3.12 venv did not include `setuptools`; standard PEP 517 build isolation passed without code changes.

## Commit Plan

- Files: current child implementation, child evidence, and current v1.4 PRD/report/runbook files.
- Message: `feat: add v1.4 cli contract skeleton`
- Pushed: no

## User Completion Signal

- Raw signal: `任务完成，并一并提交v1.4文件`
- Received at: 2026-07-01T22:02:26-07:00
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: include current v1.4 files in the commit; no push requested.
- Push allowed: no, unless explicitly requested

## Soft Archive Plan

- [x] Completion signal received
- [ ] Commit hash recorded
- [ ] `task.json.meta.staged_delivery.soft_archive_completed = true`
- [ ] Child directory kept in place
- [ ] Child is no longer the active implementation target

## Completion Signal

Waiting for user to say `任务完成`, `验证通过`, `可以提交`, or equivalent.
