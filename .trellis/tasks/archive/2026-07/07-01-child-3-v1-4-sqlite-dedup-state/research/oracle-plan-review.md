# Oracle PLAN Review: Child 3 v1.4 SQLite Run State And Dedup Ledger

## Result

- API mode: failed, missing `OPENAI_API_KEY`.
- Browser mode: completed.
- Session: `hermes-v14-child3-sqlite-plan-2`.

## Blockers To Absorb

- Define run-lock and resume semantics before coding: active, terminal, stale/crashed lock, and stable `run_id` reuse for the same `date + profile_hash`.
- Add concrete idempotency keys for non-content tables: transcript state, analysis package refs, operations, write-audit, and deterministic errors.
- Persist all three dedup levels, not just one `dedup_key`.
- Define dedup conflict cases: split matches across different content rows, or immutable identity mismatch on one matched row.
- Use transactional `BEGIN IMMEDIATE` for run acquisition and content ledger upsert.

## Minimal Shape

- One module: `hermes_benchmark/state.py`.
- Stdlib `sqlite3`; no ORM, migration framework, service layer, or repository abstraction.
- `PRAGMA user_version = 1`; fail closed on unknown future versions.
- Tables: `runs`, `content_ledger`, `transcripts`, `analysis_packages`, `feishu_operations`, `write_audit`, `errors`.
- Helpers stay pure persistence: no collection runner, no transcription runner, no authorization validation, no Feishu I/O.
