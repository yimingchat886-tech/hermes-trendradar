"""Internal digest payload built from real analysis refs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .runtime_cdp import artifact_ref

ERROR_DIGEST_PAYLOAD_INVALID = "digest_payload_invalid"
DELIVERY_BLOCKER_CODE = "message_channel_not_configured"


class DigestPayloadError(ValueError):
    pass


def build_internal_digest_payload(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    if not run_id:
        raise DigestPayloadError("run_id is required")
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise DigestPayloadError("run not found")

    results = list(conn.execute("SELECT * FROM analysis_results WHERE run_id = ? ORDER BY content_id", (run_id,)))
    if not results:
        raise DigestPayloadError("analysis result refs are required")

    package_ids = sorted({row["package_id"] for row in results})
    content_ids = sorted({row["content_id"] for row in results})
    packages = _rows_by("package_id", _select_in(conn, "analysis_packages", "package_id", package_ids))
    contents = _rows_by("content_id", _select_in(conn, "content_ledger", "content_id", content_ids))
    transcripts = _transcripts_by_content(conn, content_ids)
    errors = list(conn.execute("SELECT * FROM errors WHERE run_id = ? ORDER BY error_code, object_id", (run_id,)))

    items = [_digest_item(row, packages.get(row["package_id"]), contents.get(row["content_id"]), transcripts.get(row["content_id"])) for row in results]
    degraded = _degraded_results(results) + [_degraded_error(row) for row in errors]
    status = "degraded" if degraded else "ready"
    package_refs = sorted({item["trace"]["package_ref"] for item in items if item["trace"].get("package_ref")})

    return {
        "schema_version": "2.0-m1",
        "message_type": "internal_digest",
        "run_id": run_id,
        "status": status,
        "summary": {
            "analyzed_item_count": len(items),
            "degraded_count": len(degraded),
            "failed_result_count": sum(1 for row in results if row["status"] != "succeeded"),
            "error_count": len(errors),
        },
        "items": items,
        "degraded": degraded,
        "trace": {
            "run_id": run_id,
            "package_ids": package_ids,
            "package_refs": package_refs,
            "content_ids": content_ids,
            "analysis_result_ids": [row["analysis_result_id"] for row in results],
        },
        "delivery": {
            "channel": "feishu_internal_group",
            "status": "blocked",
            "blocker_code": DELIVERY_BLOCKER_CODE,
            "reason": "Hermes owns Feishu message delivery; repo emitted the digest payload only.",
        },
    }


def write_internal_digest_payload(storage_dir: Path, run_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    path = storage_dir / run_id / "artifacts" / "internal_digest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    file_bytes = encoded + b"\n"
    path.write_bytes(file_bytes)
    return artifact_ref(storage_dir, path), "sha256:" + hashlib.sha256(file_bytes).hexdigest()


def _select_in(conn: sqlite3.Connection, table: str, column: str, values: list[str]) -> list[sqlite3.Row]:
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    return list(conn.execute(f"SELECT * FROM {table} WHERE {column} IN ({placeholders})", values))


def _rows_by(key: str, rows: list[sqlite3.Row]) -> dict[str, sqlite3.Row]:
    return {row[key]: row for row in rows}


def _transcripts_by_content(conn: sqlite3.Connection, content_ids: list[str]) -> dict[str, sqlite3.Row]:
    rows = _select_in(conn, "transcripts", "content_id", content_ids)
    return {row["content_id"]: row for row in rows}


def _digest_item(
    result: sqlite3.Row,
    package: sqlite3.Row | None,
    content: sqlite3.Row | None,
    transcript: sqlite3.Row | None,
) -> dict[str, Any]:
    content_id = result["content_id"]
    package_ref = package["artifact_ref"] if package else ""
    source_url = content["source_url"] if content else ""
    transcript_status = transcript["status"] if transcript else ""
    return {
        "content_id": content_id,
        "account_id": content["account_id"] if content else "",
        "source_url": source_url,
        "analysis_status": result["status"],
        "result_ref": result["result_ref"],
        "transcript_status": transcript_status,
        "trace": {
            "run_id": result["run_id"],
            "package_id": result["package_id"],
            "package_ref": package_ref,
            "content_id": content_id,
            "transcript_artifact_ref": result["transcript_artifact_ref"],
            "analysis_result_id": result["analysis_result_id"],
            "result_ref": result["result_ref"],
            "result_hash": result["result_hash"],
        },
    }


def _degraded_results(results: list[sqlite3.Row]) -> list[dict[str, str]]:
    return [
        {
            "scope": "analysis_result",
            "object_id": row["content_id"],
            "status": row["status"],
            "summary": "analysis result did not succeed",
            "trace_id": row["analysis_result_id"],
        }
        for row in results
        if row["status"] != "succeeded"
    ]


def _degraded_error(row: sqlite3.Row) -> dict[str, str]:
    return {
        "scope": row["scope"],
        "object_id": row["object_id"],
        "error_code": row["error_code"],
        "summary": row["summary"],
        "retryable": "yes" if row["retryable"] else "no",
    }
