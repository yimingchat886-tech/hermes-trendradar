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
    validate_handoff_package,
    write_handoff_package,
)


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


if __name__ == "__main__":
    test_handoff_package_schema_and_write_ref()
    test_handoff_package_rejects_invalid_success_transcript_ref()
