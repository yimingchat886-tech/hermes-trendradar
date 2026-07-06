from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.analysis_result import (
    AnalysisResultError,
    load_analysis_result,
    load_handoff_package,
    validate_analysis_result,
)
from hermes_benchmark.handoff import build_handoff_package, write_handoff_package


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


def result_for(package, **overrides):
    value = {
        "schema_version": "1.4",
        "package_id": package["package_id"],
        "run_id": package["run_id"],
        "content_id": "content-1",
        "transcript_artifact_ref": "file:transcripts/1.json",
        "result_ref": "file:analysis/results/content-1.json",
        "status": "succeeded",
    }
    value.update(overrides)
    return value


def test_analysis_result_validates_against_handoff_package() -> None:
    package = build_handoff_package("run-1", "sha256:profile", [item()])
    normalized = validate_analysis_result(package, result_for(package), result_hash="sha256:result")

    assert normalized["package_id"] == package["package_id"]
    assert normalized["content_id"] == "content-1"
    assert normalized["transcript_artifact_ref"] == "file:transcripts/1.json"
    assert normalized["result_hash"] == "sha256:result"


def test_analysis_result_rejects_mismatched_trace_refs() -> None:
    package = build_handoff_package("run-1", "sha256:profile", [item()])
    cases = [
        {"package_id": "package_other"},
        {"run_id": "run-other"},
        {"content_id": "content-other"},
        {"transcript_artifact_ref": "file:transcripts/other.json"},
        {"result_ref": "../escape.json"},
    ]
    for overrides in cases:
        try:
            validate_analysis_result(package, result_for(package, **overrides), result_hash="sha256:result")
        except AnalysisResultError:
            pass
        else:
            raise AssertionError(f"expected rejection for {overrides}")


def test_analysis_result_loads_package_ref_under_storage() -> None:
    package = build_handoff_package("run-1", "sha256:profile", [item()])
    with tempfile.TemporaryDirectory() as tmp:
        storage = Path(tmp)
        ref, _digest = write_handoff_package(storage, "run-1", package)
        assert load_handoff_package(ref, storage)["package_id"] == package["package_id"]

        try:
            load_handoff_package("file:../outside.json", storage)
        except AnalysisResultError:
            pass
        else:
            raise AssertionError("expected path traversal to fail")


def test_analysis_result_load_computes_hash_and_rejects_invalid_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "result.json"
        path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        _data, digest = load_analysis_result(path)
        assert digest.startswith("sha256:")

        bad = Path(tmp) / "bad.json"
        bad.write_text("not-json", encoding="utf-8")
        try:
            load_analysis_result(bad)
        except AnalysisResultError:
            pass
        else:
            raise AssertionError("expected invalid JSON to fail")


if __name__ == "__main__":
    test_analysis_result_validates_against_handoff_package()
    test_analysis_result_rejects_mismatched_trace_refs()
    test_analysis_result_loads_package_ref_under_storage()
    test_analysis_result_load_computes_hash_and_rejects_invalid_json()
