from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.internal_digest import DELIVERY_BLOCKER_CODE, build_internal_digest_payload, write_internal_digest_payload
from hermes_benchmark.state import (
    begin_run,
    connect,
    init_schema,
    record_analysis_package_ref,
    record_analysis_result_ref,
    record_error,
    record_transcript_state,
    upsert_content_ledger,
)


def test_internal_digest_links_result_refs_and_degraded_state() -> None:
    conn = connect()
    init_schema(conn)
    run = begin_run(conn, "2026-07-03", "sha256:profile")
    content = upsert_content_ledger(
        conn,
        run["run_id"],
        {
            "content_id": "content-1",
            "platform": "douyin",
            "platform_content_id": "aweme-1",
            "normalized_source_url": "https://www.douyin.com/video/1",
            "account_id": "douyin_sample_001",
            "publish_at": "2026-07-03T00:00:00Z",
            "normalized_title_or_caption_hash": "sha256:title",
            "source_url": "https://www.douyin.com/video/1",
            "status": "seen",
        },
    )
    record_transcript_state(conn, content["content_id"], "funasr:test", "done", artifact_ref="file:transcripts/1.json")
    package_id = record_analysis_package_ref(
        conn,
        run["run_id"],
        "hermes-handoff",
        "ready",
        "file:run-1/artifacts/analysis_package.json",
        package_id="package_1",
    )
    result_id = record_analysis_result_ref(
        conn,
        run_id=run["run_id"],
        package_id=package_id,
        content_id=content["content_id"],
        transcript_artifact_ref="file:transcripts/1.json",
        result_ref="file:analysis/results/content-1.json",
        result_hash="sha256:result",
        status="failed",
    )
    record_error(conn, run["run_id"], "analysis", content["content_id"], "analysis_timeout", "redacted timeout", True)

    payload = build_internal_digest_payload(conn, run["run_id"])

    assert payload["status"] == "degraded"
    assert payload["summary"] == {"analyzed_item_count": 1, "degraded_count": 2, "failed_result_count": 1, "error_count": 1}
    assert payload["delivery"]["status"] == "blocked"
    assert payload["delivery"]["blocker_code"] == DELIVERY_BLOCKER_CODE
    assert payload["trace"]["package_ids"] == [package_id]
    assert payload["trace"]["analysis_result_ids"] == [result_id]
    item = payload["items"][0]
    assert item["content_id"] == content["content_id"]
    assert item["trace"]["package_id"] == package_id
    assert item["trace"]["transcript_artifact_ref"] == "file:transcripts/1.json"
    assert item["trace"]["result_ref"] == "file:analysis/results/content-1.json"
    assert payload["degraded"][0]["scope"] == "analysis_result"
    assert payload["degraded"][1]["error_code"] == "analysis_timeout"


def test_internal_digest_writes_storage_ref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "schema_version": "2.0-m1",
            "message_type": "internal_digest",
            "run_id": "run-1",
            "status": "ready",
            "summary": {},
            "items": [],
            "degraded": [],
            "trace": {},
            "delivery": {},
        }
        ref, digest = write_internal_digest_payload(Path(tmp), "run-1", payload)

        assert ref == "file:run-1/artifacts/internal_digest.json"
        assert digest.startswith("sha256:")
        assert (Path(tmp) / "run-1" / "artifacts" / "internal_digest.json").is_file()


if __name__ == "__main__":
    test_internal_digest_links_result_refs_and_degraded_state()
    test_internal_digest_writes_storage_ref()
