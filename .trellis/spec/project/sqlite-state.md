# SQLite State Contracts

## Scenario: v1.4 Run State And Dedup Ledger

### 1. Scope / Trigger

- Trigger: changes to local SQLite schema, run state, dedup ledger, transcript/package/result refs, operations, write-audit, or deterministic error persistence.
- Scope: pure local persistence helpers using stdlib `sqlite3`.
- Out of scope: collection runner, transcription runner, Hermes analysis generation, Feishu authorization, Feishu API I/O, and scheduler behavior.

### 2. Signatures

- `connect(db_path=":memory:") -> sqlite3.Connection`
- `init_schema(conn) -> None`
- `begin_run(conn, run_date, profile_hash, profile_id=None, lock_token=None, stale_after_seconds=None) -> dict`
- `finish_run(conn, run_id, status) -> None`
- `upsert_content_ledger(conn, run_id, item) -> dict`
- `record_error(conn, run_id, scope, object_id, error_code, summary, retryable, redacted_details_ref=None) -> str`
- `record_transcript_state(...) -> str`
- `record_analysis_package_ref(...) -> str`
- `record_analysis_result_ref(...) -> str`
- `record_human_feedback_ref(...) -> str`
- `record_operation_ref(...) -> str`
- `record_write_audit(...) -> str`

### 3. Contracts

- Schema version is `PRAGMA user_version = 3`; future unknown versions fail closed.
- `runs` has `UNIQUE(run_date, profile_hash)` and reuses the same `run_id` for same-scope resume/no-op.
- `content_ledger` stores three independent dedup keys: `p0_key`, `fallback_1_key`, and `fallback_2_key`, each with a partial unique index.
- Transcript, package, analysis-result, operation, audit, and error helpers store refs or hashes only. Do not store raw videos, raw full transcript text, or raw analysis output.
- Analysis result refs are idempotent by `(package_id, content_id)` and must link back to the handoff package, content id, transcript artifact ref, result ref, result hash, and result status.
- Human feedback stores immutable adopt/reject refs only: run/content/result ids, result ref/hash/status snapshot, opaque actor/source-message refs, optional bounded reason code, and a unique `feedback_key_hash`.
- Repeated identical feedback returns the existing `feedback_id`; same key with changed decision, reason, or result snapshot fails as a conflict and must not overwrite the audit row.
- Feedback persistence must not add or mutate rule/RAG promotion, Bitable, operation, or write-audit fields.
- Public helpers that mutate state must be transactional. Run acquisition and content upsert use `BEGIN IMMEDIATE`.

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Same date + profile has active run | return `locked`, no new run row |
| Same date + profile has succeeded run | return `noop`, no new run row |
| Failed/cancelled/partial or stale active run | return `resumed`, reuse `run_id`, increment retry count |
| Existing content matched by any dedup key | return `noop`, update `last_run_id`, no new content row |
| Incoming content matches multiple content rows | return `conflict`, write deterministic `dedup_conflict` error |
| Incoming content matches one row but immutable identity differs | return `conflict`, write deterministic `dedup_conflict` error |
| Missing all dedup keys | raise state error |

### 5. Good/Base/Bad Cases

- Good: repeated ingest of the same content returns `noop` and leaves one ledger row.
- Base: duplicate schema initialization is safe and keeps `user_version = 2`.
- Bad: a content item matching one row by P0 and another by fallback key must not create or overwrite content; it writes one deterministic error.

### 6. Tests Required

- Schema initialization and duplicate initialization.
- Same-scope run start, lock conflict, terminal no-op, and failed/stale resume.
- Content insert, duplicate no-op, split-key dedup conflict, and deterministic error idempotency.
- Transcript/package/analysis-result/operation/write-audit helpers store refs only and are idempotent by key/hash.
- Feedback helper persists traceable refs, rejects mismatched run/content/result refs, treats exact duplicates as no-ops, and treats same-key changed payloads as conflicts.

### 7. Wrong vs Correct

#### Wrong

```python
# read then insert without a transaction
row = conn.execute("SELECT * FROM runs WHERE run_date = ?", (run_date,)).fetchone()
if row is None:
    conn.execute("INSERT INTO runs ...")
```

#### Correct

```python
conn.execute("BEGIN IMMEDIATE")
# read/check/insert or conflict-write in one transaction
```
