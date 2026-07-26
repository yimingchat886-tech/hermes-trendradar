from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.handoff import (
    HandoffPackageError,
    build_handoff_package,
    contents_from_state,
    validate_handoff_package,
    write_handoff_package,
)
from hermes_benchmark.state import begin_run, connect, finish_run, init_schema, record_transcript_state, upsert_content_ledger


def item(**overrides):
    value = {
        "content_id": "content-1",
        "platform": "douyin",
        "account_id": "account-1",
        "account_display_name": "Account 1",
        "source_url": "https://www.douyin.com/video/1",
        "title_or_caption_raw": "caption",
        "publish_at": "2026-07-03T00:00:00Z",
        "collected_at": "2026-07-03T01:00:00Z",
        "transcript_status": "success",
        "transcript_artifact_ref": "file:transcripts/1.json",
        "dedup_key": "douyin|1",
    }
    value.update(overrides)
    return value


def test_handoff_package_schema_and_write_ref() -> None:
    package = build_handoff_package("run-1", "sha256:profile", [item()])
    validate_handoff_package(package)

    with tempfile.TemporaryDirectory() as tmp:
        ref, digest = write_handoff_package(Path(tmp), "run-1", package)
        path = Path(tmp) / ref.removeprefix("file:")
        written = json.loads(path.read_text(encoding="utf-8"))

    assert ref == "file:run-1/artifacts/analysis_package.json"
    assert digest.startswith("sha256:")
    assert written["package_id"].startswith("package_")


def test_handoff_package_rejects_invalid_success_transcript_ref() -> None:
    try:
        build_handoff_package("run-1", "sha256:profile", [item(transcript_artifact_ref="")])
    except HandoffPackageError as exc:
        assert "transcript_artifact_ref" in str(exc)
    else:
        raise AssertionError("expected package validation failure")


def test_contents_from_state_is_scoped_to_current_run() -> None:
    conn = connect()
    init_schema(conn)
    first = begin_run(conn, "2026-07-03", "sha256:profile")
    upsert_content_ledger(
        conn,
        first["run_id"],
        ledger_item("content-old", "aweme-old"),
        now="2026-07-03T00:00:00+00:00",
    )
    finish_run(conn, first["run_id"], "succeeded")
    second = begin_run(conn, "2026-07-04", "sha256:profile")
    current = upsert_content_ledger(
        conn,
        second["run_id"],
        ledger_item("content-new", "aweme-new"),
        now="2026-07-04T00:00:00+00:00",
    )
    record_transcript_state(conn, current["content_id"], "funasr:test", "done", artifact_ref="file:transcripts/new.json")

    contents = contents_from_state(conn, {"account-1": "Account 1"}, run_id=second["run_id"])

    assert [content["content_id"] for content in contents] == ["content-new"]
    assert contents[0]["transcript_status"] == "success"


def test_contents_from_state_empty_run_is_explicitly_empty() -> None:
    conn = connect()
    init_schema(conn)
    run = begin_run(conn, "2026-07-05", "sha256:profile")

    contents = contents_from_state(conn, {"account-1": "Account 1"}, run_id=run["run_id"])
    package = build_handoff_package(run["run_id"], "sha256:profile", contents)

    assert contents == []
    assert package["contents"] == []


def ledger_item(content_id: str, platform_content_id: str) -> dict[str, str]:
    return {
        "content_id": content_id,
        "platform": "douyin",
        "platform_content_id": platform_content_id,
        "normalized_source_url": f"https://www.douyin.com/video/{platform_content_id}",
        "account_id": "account-1",
        "publish_at": "2026-07-03T00:00:00Z",
        "normalized_title_or_caption_hash": f"sha256:{platform_content_id}",
        "source_url": f"https://www.douyin.com/video/{platform_content_id}",
        "status": "seen",
    }


if __name__ == "__main__":
    test_handoff_package_schema_and_write_ref()
    test_handoff_package_rejects_invalid_success_transcript_ref()
    test_contents_from_state_is_scoped_to_current_run()
    test_contents_from_state_empty_run_is_explicitly_empty()
