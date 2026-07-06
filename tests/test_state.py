from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.state import (
    SCHEMA_VERSION,
    begin_run,
    connect,
    finish_run,
    init_schema,
    record_analysis_package_ref,
    record_analysis_result_ref,
    record_operation_ref,
    record_error,
    record_transcript_state,
    record_write_audit,
    upsert_content_ledger,
)


def memory_db():
    conn = connect()
    init_schema(conn)
    return conn


def content_item(**overrides):
    item = {
        "platform": "douyin",
        "platform_content_id": "aweme-1",
        "normalized_source_url": "https://douyin.example/aweme-1",
        "account_id": "acct-1",
        "publish_at": "2026-07-01T00:00:00Z",
        "normalized_title_or_caption_hash": "sha256:title-1",
        "source_url": "https://douyin.example/aweme-1?share=1",
        "status": "seen",
    }
    item.update(overrides)
    return item


def test_schema_initializes_without_dependencies() -> None:
    conn = memory_db()
    init_schema(conn)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"runs", "content_ledger", "transcripts", "analysis_packages", "analysis_results", "feishu_operations", "write_audit", "errors"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_future_schema_version_fails_closed() -> None:
    conn = connect()
    conn.execute("PRAGMA user_version = 999")
    try:
        init_schema(conn)
    except Exception as exc:
        assert "unsupported future schema version" in str(exc)
    else:
        raise AssertionError("expected future schema version to fail")


def test_same_scope_run_lock_and_terminal_noop() -> None:
    conn = memory_db()
    first = begin_run(conn, "2026-07-01", "sha256:profile", lock_token="a", now="2026-07-01T00:00:00+00:00")
    second = begin_run(conn, "2026-07-01", "sha256:profile", lock_token="b", now="2026-07-01T00:01:00+00:00")
    assert first["status"] == "started"
    assert second == {"status": "locked", "run_id": first["run_id"], "retry_count": 0}

    finish_run(conn, first["run_id"], "succeeded", now="2026-07-01T00:02:00+00:00")
    third = begin_run(conn, "2026-07-01", "sha256:profile", lock_token="c", now="2026-07-01T00:03:00+00:00")
    assert third == {"status": "noop", "run_id": first["run_id"], "retry_count": 0}


def test_failed_or_stale_run_reuses_run_id_for_resume() -> None:
    conn = memory_db()
    first = begin_run(conn, "2026-07-01", "sha256:profile", now="2026-07-01T00:00:00+00:00")
    finish_run(conn, first["run_id"], "failed", now="2026-07-01T00:01:00+00:00")
    resumed = begin_run(conn, "2026-07-01", "sha256:profile", now="2026-07-01T00:02:00+00:00")
    assert resumed == {"status": "resumed", "run_id": first["run_id"], "retry_count": 1}

    stale = begin_run(
        conn,
        "2026-07-02",
        "sha256:profile",
        now="2026-07-02T00:00:00+00:00",
    )
    stale_resume = begin_run(
        conn,
        "2026-07-02",
        "sha256:profile",
        stale_after_seconds=30,
        now="2026-07-02T00:01:00+00:00",
    )
    assert stale_resume == {"status": "resumed", "run_id": stale["run_id"], "retry_count": 1}


def test_reingesting_existing_content_is_noop() -> None:
    conn = memory_db()
    run = begin_run(conn, "2026-07-01", "sha256:profile")
    first = upsert_content_ledger(conn, run["run_id"], content_item())
    second = upsert_content_ledger(conn, run["run_id"], content_item())
    assert first["status"] == "inserted"
    assert second["status"] == "noop"
    assert conn.execute("SELECT COUNT(*) FROM content_ledger").fetchone()[0] == 1


def test_dedup_conflict_writes_deterministic_error() -> None:
    conn = memory_db()
    run = begin_run(conn, "2026-07-01", "sha256:profile")
    upsert_content_ledger(conn, run["run_id"], content_item(platform_content_id="aweme-1", normalized_source_url="https://a.example/1"))
    upsert_content_ledger(
        conn,
        run["run_id"],
        content_item(
            platform_content_id="aweme-2",
            normalized_source_url="https://a.example/2",
            account_id="acct-2",
            publish_at="2026-07-02T00:00:00Z",
            normalized_title_or_caption_hash="sha256:title-2",
        ),
    )

    conflict_item = content_item(platform_content_id="aweme-1", normalized_source_url="https://a.example/2")
    first = upsert_content_ledger(conn, run["run_id"], conflict_item)
    second = upsert_content_ledger(conn, run["run_id"], conflict_item)

    assert first["status"] == "conflict"
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM content_ledger").fetchone()[0] == 2
    error = conn.execute("SELECT * FROM errors WHERE error_id = ?", (first["error_id"],)).fetchone()
    assert error["error_code"] == "dedup_conflict"
    assert conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0] == 1


def test_artifact_and_operation_helpers_are_idempotent_refs_only() -> None:
    conn = memory_db()
    run = begin_run(conn, "2026-07-01", "sha256:profile")
    content = upsert_content_ledger(conn, run["run_id"], content_item())

    transcript_id = record_transcript_state(conn, content["content_id"], "funasr-sensevoice", "done", artifact_ref="file:artifacts/t.json")
    package_id = record_analysis_package_ref(conn, run["run_id"], "hermes-handoff", "ready", "file:artifacts/package.json")
    package_id = record_analysis_package_ref(
        conn,
        run["run_id"],
        "hermes-handoff",
        "ready",
        "file:artifacts/package.json",
        package_id="package_handoff_authoritative",
    )
    result_id = record_analysis_result_ref(
        conn,
        run_id=run["run_id"],
        package_id=package_id,
        content_id=content["content_id"],
        transcript_artifact_ref="file:artifacts/t.json",
        result_ref="file:analysis/results/content-1.json",
        result_hash="sha256:result",
        status="succeeded",
    )
    same_result_id = record_analysis_result_ref(
        conn,
        run_id=run["run_id"],
        package_id=package_id,
        content_id=content["content_id"],
        transcript_artifact_ref="file:artifacts/t.json",
        result_ref="file:analysis/results/content-1.json",
        result_hash="sha256:result",
        status="succeeded",
    )
    operation_id = record_operation_ref(conn, run["run_id"], "status_update", "table_4", content["content_id"], "sha256:op", "pending")
    audit_id = record_write_audit(conn, operation_id, "sha256:op", content["content_id"], "noop")

    assert transcript_id.startswith("transcript_")
    assert package_id == "package_handoff_authoritative"
    assert result_id.startswith("analysis_result_")
    assert same_result_id == result_id
    assert operation_id.startswith("op_")
    assert audit_id.startswith("audit_")
    assert conn.execute("SELECT artifact_ref FROM transcripts").fetchone()[0] == "file:artifacts/t.json"
    result = conn.execute("SELECT * FROM analysis_results").fetchone()
    assert result["result_ref"] == "file:analysis/results/content-1.json"
    assert result["result_hash"] == "sha256:result"
    assert conn.execute("SELECT COUNT(*) FROM write_audit").fetchone()[0] == 1


def test_record_error_is_deterministic_when_called_directly() -> None:
    conn = memory_db()
    first = record_error(conn, "run-1", "runtime", "object-1", "boom", "redacted failure", True)
    second = record_error(conn, "run-1", "runtime", "object-1", "boom", "redacted failure", True)
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0] == 1


if __name__ == "__main__":
    test_schema_initializes_without_dependencies()
    test_future_schema_version_fails_closed()
    test_same_scope_run_lock_and_terminal_noop()
    test_failed_or_stale_run_reuses_run_id_for_resume()
    test_reingesting_existing_content_is_noop()
    test_dedup_conflict_writes_deterministic_error()
    test_artifact_and_operation_helpers_are_idempotent_refs_only()
    test_record_error_is_deterministic_when_called_directly()
