"""SQLite run state and dedup ledger helpers."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2
ACTIVE_RUN_STATUS = "running"
TERMINAL_RUN_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}
IMMUTABLE_CONTENT_FIELDS = (
    "platform",
    "platform_content_id",
    "normalized_source_url",
    "account_id",
    "publish_at",
    "normalized_title_or_caption_hash",
)


class StateError(ValueError):
    pass


def connect(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise StateError(f"unsupported future schema version: {version}")
    with _transaction(conn, immediate=False):
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def begin_run(
    conn: sqlite3.Connection,
    run_date: str,
    profile_hash: str,
    *,
    profile_id: str | None = None,
    lock_token: str | None = None,
    stale_after_seconds: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    run_id = _stable_id("run", run_date, profile_hash)
    with _transaction(conn):
        row = conn.execute(
            "SELECT * FROM runs WHERE run_date = ? AND profile_hash = ?",
            (run_date, profile_hash),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO runs (
                  run_id, run_date, profile_hash, profile_id, status, retry_count,
                  started_at, lock_token, lock_heartbeat_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (run_id, run_date, profile_hash, profile_id, ACTIVE_RUN_STATUS, timestamp, lock_token, timestamp, timestamp, timestamp),
            )
            return {"status": "started", "run_id": run_id, "retry_count": 0}

        row_run_id = row["run_id"]
        if row["status"] == "succeeded":
            return {"status": "noop", "run_id": row_run_id, "retry_count": row["retry_count"]}
        if row["status"] == ACTIVE_RUN_STATUS and not _is_stale(row["lock_heartbeat_at"], timestamp, stale_after_seconds):
            return {"status": "locked", "run_id": row_run_id, "retry_count": row["retry_count"]}

        retry_count = int(row["retry_count"]) + 1
        conn.execute(
            """
            UPDATE runs
            SET status = ?, profile_id = COALESCE(?, profile_id), retry_count = ?,
                started_at = ?, ended_at = NULL, lock_token = ?,
                lock_heartbeat_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (ACTIVE_RUN_STATUS, profile_id, retry_count, timestamp, lock_token, timestamp, timestamp, row_run_id),
        )
        return {"status": "resumed", "run_id": row_run_id, "retry_count": retry_count}


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, *, now: str | None = None) -> None:
    if status not in TERMINAL_RUN_STATUSES:
        raise StateError(f"invalid terminal run status: {status}")
    timestamp = now or _now()
    with _transaction(conn):
        updated = conn.execute(
            """
            UPDATE runs
            SET status = ?, ended_at = ?, lock_token = NULL, lock_heartbeat_at = NULL, updated_at = ?
            WHERE run_id = ?
            """,
            (status, timestamp, timestamp, run_id),
        ).rowcount
        if updated != 1:
            raise StateError(f"run not found: {run_id}")


def upsert_content_ledger(
    conn: sqlite3.Connection,
    run_id: str,
    item: dict[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    normalized = _normalize_content_item(item)
    with _transaction(conn):
        matches = _matching_content_rows(conn, normalized)
        content_ids = {row["content_id"] for row in matches}
        if len(content_ids) > 1:
            return _content_conflict(conn, run_id, normalized, "dedup matched multiple content rows", timestamp)

        if len(content_ids) == 1:
            row = matches[0]
            conflict_field = _identity_conflict(row, normalized)
            if conflict_field:
                return _content_conflict(conn, run_id, normalized, f"dedup identity mismatch: {conflict_field}", timestamp)
            _update_content_noop(conn, row, normalized, run_id, timestamp)
            return {"status": "noop", "content_id": row["content_id"], "dedup_key": normalized["dedup_key"]}

        content_id = normalized.get("content_id") or _stable_id("content", normalized["dedup_key"])
        conn.execute(
            """
            INSERT INTO content_ledger (
              content_id, platform, platform_content_id, normalized_source_url, account_id,
              publish_at, normalized_title_or_caption_hash, p0_key, fallback_1_key,
              fallback_2_key, dedup_key, source_url, status, first_run_id,
              last_run_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id,
                normalized["platform"],
                normalized.get("platform_content_id"),
                normalized.get("normalized_source_url"),
                normalized.get("account_id"),
                normalized.get("publish_at"),
                normalized.get("normalized_title_or_caption_hash"),
                normalized.get("p0_key"),
                normalized.get("fallback_1_key"),
                normalized.get("fallback_2_key"),
                normalized["dedup_key"],
                normalized.get("source_url"),
                normalized.get("status", "seen"),
                run_id,
                run_id,
                timestamp,
                timestamp,
            ),
        )
        return {"status": "inserted", "content_id": content_id, "dedup_key": normalized["dedup_key"]}


def record_error(
    conn: sqlite3.Connection,
    run_id: str | None,
    scope: str,
    object_id: str,
    error_code: str,
    summary: str,
    retryable: bool,
    *,
    redacted_details_ref: str | None = None,
    now: str | None = None,
) -> str:
    timestamp = now or _now()
    error_id = _stable_id("err", run_id or "", scope, object_id, error_code)
    with _transaction(conn):
        _insert_error(conn, error_id, run_id, scope, object_id, error_code, summary, retryable, redacted_details_ref, timestamp)
    return error_id


def _insert_error(
    conn: sqlite3.Connection,
    error_id: str,
    run_id: str | None,
    scope: str,
    object_id: str,
    error_code: str,
    summary: str,
    retryable: bool,
    redacted_details_ref: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO errors (
          error_id, run_id, scope, object_id, error_code, summary,
          retryable, redacted_details_ref, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (error_id, run_id, scope, object_id, error_code, summary, int(retryable), redacted_details_ref, created_at),
    )


def record_transcript_state(
    conn: sqlite3.Connection,
    content_id: str,
    model_profile: str,
    status: str,
    *,
    artifact_ref: str | None = None,
    artifact_hash: str | None = None,
    error_code: str | None = None,
    now: str | None = None,
) -> str:
    timestamp = now or _now()
    transcript_id = _stable_id("transcript", content_id, model_profile)
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO transcripts (
              transcript_id, content_id, model_profile, status, artifact_ref,
              artifact_hash, error_code, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_id, model_profile) DO UPDATE SET
              status = excluded.status,
              artifact_ref = excluded.artifact_ref,
              artifact_hash = excluded.artifact_hash,
              error_code = excluded.error_code,
              updated_at = excluded.updated_at
            """,
            (transcript_id, content_id, model_profile, status, artifact_ref, artifact_hash, error_code, timestamp),
        )
    return transcript_id


def record_analysis_package_ref(
    conn: sqlite3.Connection,
    run_id: str,
    mode: str,
    status: str,
    artifact_ref: str,
    *,
    artifact_hash: str | None = None,
    content_count: int | None = None,
    package_id: str | None = None,
    now: str | None = None,
) -> str:
    timestamp = now or _now()
    package_id = package_id or _stable_id("package", run_id, mode)
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO analysis_packages (
              package_id, run_id, mode, status, artifact_ref,
              artifact_hash, content_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, mode) DO UPDATE SET
              package_id = excluded.package_id,
              status = excluded.status,
              artifact_ref = excluded.artifact_ref,
              artifact_hash = excluded.artifact_hash,
              content_count = excluded.content_count,
              updated_at = excluded.updated_at
            """,
            (package_id, run_id, mode, status, artifact_ref, artifact_hash, content_count, timestamp),
        )
    return package_id


def record_analysis_result_ref(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    package_id: str,
    content_id: str,
    transcript_artifact_ref: str,
    result_ref: str,
    result_hash: str,
    status: str,
    now: str | None = None,
) -> str:
    for label, value in {
        "run_id": run_id,
        "package_id": package_id,
        "content_id": content_id,
        "transcript_artifact_ref": transcript_artifact_ref,
        "result_ref": result_ref,
        "result_hash": result_hash,
        "status": status,
    }.items():
        if not value:
            raise StateError(f"analysis result {label} is required")
    if not result_hash.startswith("sha256:"):
        raise StateError("analysis result_hash must be sha256")
    timestamp = now or _now()
    result_id = _stable_id("analysis_result", package_id, content_id)
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO analysis_results (
              analysis_result_id, run_id, package_id, content_id,
              transcript_artifact_ref, result_ref, result_hash, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, content_id) DO UPDATE SET
              run_id = excluded.run_id,
              transcript_artifact_ref = excluded.transcript_artifact_ref,
              result_ref = excluded.result_ref,
              result_hash = excluded.result_hash,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (result_id, run_id, package_id, content_id, transcript_artifact_ref, result_ref, result_hash, status, timestamp),
        )
    return result_id


def record_operation_ref(
    conn: sqlite3.Connection,
    run_id: str,
    mutation_type: str,
    target_table: str,
    target_record_key: str,
    operation_hash: str,
    status: str,
    *,
    artifact_ref: str | None = None,
    now: str | None = None,
) -> str:
    timestamp = now or _now()
    operation_id = _stable_id("op", operation_hash)
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO feishu_operations (
              operation_id, run_id, mutation_type, target_table, target_record_key,
              operation_hash, status, artifact_ref, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operation_hash) DO UPDATE SET
              status = excluded.status,
              artifact_ref = excluded.artifact_ref,
              updated_at = excluded.updated_at
            """,
            (operation_id, run_id, mutation_type, target_table, target_record_key, operation_hash, status, artifact_ref, timestamp),
        )
    return operation_id


def record_write_audit(
    conn: sqlite3.Connection,
    operation_id: str,
    operation_hash: str,
    target_record_key: str,
    result: str,
    *,
    error_code: str | None = None,
    now: str | None = None,
) -> str:
    timestamp = now or _now()
    audit_id = _stable_id("audit", operation_id, result, error_code or "")
    with _transaction(conn):
        conn.execute(
            """
            INSERT OR IGNORE INTO write_audit (
              audit_id, operation_id, operation_hash, target_record_key,
              result, error_code, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (audit_id, operation_id, operation_hash, target_record_key, result, error_code, timestamp),
        )
    return audit_id


@contextmanager
def _transaction(conn: sqlite3.Connection, *, immediate: bool = True) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _normalize_content_item(item: dict[str, Any]) -> dict[str, Any]:
    platform = item.get("platform")
    if not platform:
        raise StateError("content item platform is required")
    normalized = dict(item)
    normalized["p0_key"] = _join_key(platform, item.get("platform_content_id"))
    normalized["fallback_1_key"] = _join_key(platform, item.get("normalized_source_url"))
    normalized["fallback_2_key"] = _join_key(
        platform,
        item.get("account_id"),
        item.get("publish_at"),
        item.get("normalized_title_or_caption_hash"),
    )
    normalized["dedup_key"] = normalized["p0_key"] or normalized["fallback_1_key"] or normalized["fallback_2_key"]
    if not normalized["dedup_key"]:
        raise StateError("content item needs at least one dedup key")
    return normalized


def _matching_content_rows(conn: sqlite3.Connection, item: dict[str, Any]) -> list[sqlite3.Row]:
    clauses: list[str] = []
    values: list[str] = []
    for field in ("p0_key", "fallback_1_key", "fallback_2_key"):
        if item.get(field):
            clauses.append(f"{field} = ?")
            values.append(item[field])
    if not clauses:
        return []
    return list(conn.execute(f"SELECT * FROM content_ledger WHERE {' OR '.join(clauses)}", values))


def _identity_conflict(row: sqlite3.Row, item: dict[str, Any]) -> str | None:
    for field in IMMUTABLE_CONTENT_FIELDS:
        incoming = item.get(field)
        existing = row[field]
        if incoming is not None and existing is not None and incoming != existing:
            return field
    return None


def _update_content_noop(conn: sqlite3.Connection, row: sqlite3.Row, item: dict[str, Any], run_id: str, now: str) -> None:
    conn.execute(
        """
        UPDATE content_ledger
        SET platform_content_id = COALESCE(platform_content_id, ?),
            normalized_source_url = COALESCE(normalized_source_url, ?),
            account_id = COALESCE(account_id, ?),
            publish_at = COALESCE(publish_at, ?),
            normalized_title_or_caption_hash = COALESCE(normalized_title_or_caption_hash, ?),
            p0_key = COALESCE(p0_key, ?),
            fallback_1_key = COALESCE(fallback_1_key, ?),
            fallback_2_key = COALESCE(fallback_2_key, ?),
            source_url = COALESCE(source_url, ?),
            last_run_id = ?,
            updated_at = ?
        WHERE content_id = ?
        """,
        (
            item.get("platform_content_id"),
            item.get("normalized_source_url"),
            item.get("account_id"),
            item.get("publish_at"),
            item.get("normalized_title_or_caption_hash"),
            item.get("p0_key"),
            item.get("fallback_1_key"),
            item.get("fallback_2_key"),
            item.get("source_url"),
            run_id,
            now,
            row["content_id"],
        ),
    )


def _content_conflict(conn: sqlite3.Connection, run_id: str, item: dict[str, Any], summary: str, now: str) -> dict[str, Any]:
    error_id = _stable_id("err", run_id, "content", item["dedup_key"], "dedup_conflict")
    _insert_error(
        conn,
        error_id,
        run_id,
        "content",
        item["dedup_key"],
        "dedup_conflict",
        summary,
        False,
        None,
        now,
    )
    return {"status": "conflict", "dedup_key": item["dedup_key"], "error_id": error_id}


def _is_stale(heartbeat: str | None, now: str, stale_after_seconds: int | None) -> bool:
    if heartbeat is None or stale_after_seconds is None:
        return False
    then = datetime.fromisoformat(heartbeat)
    current = datetime.fromisoformat(now)
    return (current - then).total_seconds() > stale_after_seconds


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts).encode()
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:16]}"


def _join_key(*parts: Any) -> str | None:
    if any(part in (None, "") for part in parts):
        return None
    return "|".join(str(part) for part in parts)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  run_date TEXT NOT NULL,
  profile_hash TEXT NOT NULL,
  profile_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'partial_failed', 'failed', 'cancelled')),
  retry_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  ended_at TEXT,
  lock_token TEXT,
  lock_heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(run_date, profile_hash)
);

CREATE TABLE IF NOT EXISTS content_ledger (
  content_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  platform_content_id TEXT,
  normalized_source_url TEXT,
  account_id TEXT,
  publish_at TEXT,
  normalized_title_or_caption_hash TEXT,
  p0_key TEXT,
  fallback_1_key TEXT,
  fallback_2_key TEXT,
  dedup_key TEXT NOT NULL,
  source_url TEXT,
  status TEXT NOT NULL,
  first_run_id TEXT,
  last_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS content_p0_key_uq ON content_ledger(p0_key) WHERE p0_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS content_fallback_1_uq ON content_ledger(fallback_1_key) WHERE fallback_1_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS content_fallback_2_uq ON content_ledger(fallback_2_key) WHERE fallback_2_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS transcripts (
  transcript_id TEXT PRIMARY KEY,
  content_id TEXT NOT NULL,
  model_profile TEXT NOT NULL,
  status TEXT NOT NULL,
  artifact_ref TEXT,
  artifact_hash TEXT,
  error_code TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(content_id, model_profile)
);

CREATE TABLE IF NOT EXISTS analysis_packages (
  package_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  artifact_ref TEXT NOT NULL,
  artifact_hash TEXT,
  content_count INTEGER,
  updated_at TEXT NOT NULL,
  UNIQUE(run_id, mode)
);

CREATE TABLE IF NOT EXISTS analysis_results (
  analysis_result_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  package_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  transcript_artifact_ref TEXT NOT NULL,
  result_ref TEXT NOT NULL,
  result_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(package_id, content_id)
);

CREATE TABLE IF NOT EXISTS feishu_operations (
  operation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  mutation_type TEXT NOT NULL,
  target_table TEXT NOT NULL,
  target_record_key TEXT NOT NULL,
  operation_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  artifact_ref TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS write_audit (
  audit_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL,
  operation_hash TEXT NOT NULL,
  target_record_key TEXT NOT NULL,
  result TEXT NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
  error_id TEXT PRIMARY KEY,
  run_id TEXT,
  scope TEXT NOT NULL,
  object_id TEXT NOT NULL,
  error_code TEXT NOT NULL,
  summary TEXT NOT NULL,
  retryable INTEGER NOT NULL,
  redacted_details_ref TEXT,
  created_at TEXT NOT NULL
);
"""
