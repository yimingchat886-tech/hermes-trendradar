from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.profile import LoadedProfile
from hermes_benchmark.state import begin_run, connect, init_schema, upsert_content_ledger
from hermes_benchmark.transcript_batch import content_from_ledger, run_transcript_batch


def test_batch_success_persists_artifact_hash_and_cleans_temp() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = profile_for(root, helper_script(root, "success"))
        conn, run_id = memory_run()
        video = root / "input.mp4"
        video.write_bytes(b"fixture video")

        summary = run_transcript_batch(profile, conn, run_id=run_id, contents=[content()], video_resolver=lambda _: video)

        assert summary["status"] == "success"
        assert summary["candidate_count"] == 1
        assert summary["queued"] == 1
        assert summary["succeeded"] == 1
        assert summary["failed"] == 0
        item = summary["items"][0]
        artifact_ref = item.get("artifact_ref")
        artifact_hash = item.get("artifact_hash")
        temp_video_path = item.get("temp_video_path")
        assert isinstance(artifact_hash, str)
        assert artifact_hash.startswith("sha256:")
        serialized = json.dumps(summary)
        assert "fixture transcript" not in serialized
        assert str(root) not in serialized
        assert "/tmp/" not in serialized
        assert "/var/tmp/" not in serialized
        assert isinstance(artifact_ref, str)
        assert artifact_ref.startswith(f"file:transcripts/{run_id}/")
        assert temp_video_path == "temp:input.mp4"
        assert not video.exists()
        artifact_path = root / "artifacts" / artifact_ref.removeprefix("file:")
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert str(root) not in json.dumps(artifact_payload)
        row = conn.execute("SELECT * FROM transcripts").fetchone()
        assert row["status"] == "done"
        assert row["artifact_ref"] == summary["items"][0]["artifact_ref"]
        assert row["artifact_hash"] == summary["items"][0]["artifact_hash"]


def test_missing_video_records_failure_not_skipped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = profile_for(root, helper_script(root, "success"))
        conn, run_id = memory_run()

        summary = run_transcript_batch(profile, conn, run_id=run_id, contents=[content()], video_resolver=lambda _: None)

        assert summary["queued"] == 0
        assert summary["failed"] == 1
        assert summary["skipped"] == 0
        assert summary["items"][0]["error_code"] == "transcription_video_missing"
        assert conn.execute("SELECT error_code FROM transcripts").fetchone()[0] == "transcription_video_missing"
        assert conn.execute("SELECT error_code FROM errors").fetchone()[0] == "transcription_video_missing"


def test_command_failure_cleans_video_and_records_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = profile_for(root, helper_script(root, "fail"))
        conn, run_id = memory_run()
        video = root / "input.mp4"
        video.write_bytes(b"fixture video")

        summary = run_transcript_batch(profile, conn, run_id=run_id, contents=[content()], video_resolver=lambda _: video)

        assert summary["queued"] == 1
        assert summary["failed"] == 1
        assert summary["items"][0]["error_code"] == "transcription_command_failed"
        assert summary["items"][0]["temp_video_exists_after"] is False
        temp_video_path = summary["items"][0].get("temp_video_path")
        serialized = json.dumps(summary)
        assert str(root) not in serialized
        assert "/tmp/" not in serialized
        assert "/var/tmp/" not in serialized
        assert temp_video_path == "temp:input.mp4"
        assert not video.exists()
        assert conn.execute("SELECT error_code FROM transcripts").fetchone()[0] == "transcription_command_failed"
        assert conn.execute("SELECT error_code FROM errors").fetchone()[0] == "transcription_command_failed"


def test_missing_runtime_config_blocks_without_fake_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = profile_for(root, helper_script(root, "success"))
        del profile.profiles_by_ref["transcription"]["command"]
        conn, run_id = memory_run()

        summary = run_transcript_batch(profile, conn, run_id=run_id, contents=[content()], video_resolver=lambda _: None)

        assert summary["status"] == "blocked"
        assert summary["candidate_count"] == 1
        assert summary["queued"] == 0
        assert summary["failed"] == 0
        assert summary["items"] == []
        assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 0
        error = conn.execute("SELECT * FROM errors").fetchone()
        assert error["scope"] == "runtime"
        assert error["error_code"] == "transcription_config_missing"


def test_content_from_ledger_maps_sqlite_rows_to_batch_content() -> None:
    conn, run_id = memory_run()
    upsert_content_ledger(
        conn,
        run_id,
        {
            "platform": "douyin",
            "platform_content_id": "aweme-1",
            "normalized_source_url": "https://douyin.example/video/1",
            "account_id": "account-1",
            "publish_at": "2026-07-03T00:00:00Z",
            "normalized_title_or_caption_hash": "sha256:title",
            "source_url": "https://douyin.example/video/1",
            "status": "seen",
        },
    )

    rows = content_from_ledger(conn)

    assert rows[0]["id"].startswith("content_")
    assert rows[0]["source_id"] == "source-douyin-benchmark"
    assert rows[0]["account_id"] == "account-1"


def memory_run():
    conn = connect()
    init_schema(conn)
    run = begin_run(conn, "2026-07-03", "sha256:test")
    return conn, run["run_id"]


def content():
    return {
        "id": "content-1",
        "source_id": "source-douyin",
        "account_id": "account-1",
        "platform": "douyin",
        "url": "https://douyin.example/video/1",
        "title": "fixture title",
    }


def profile_for(root: Path, helper: Path) -> LoadedProfile:
    return LoadedProfile(
        root={"transcription_profile_ref": "transcription", "runtime_profile_ref": "runtime"},
        profiles_by_ref={
            "transcription": {
                "profile_id": "transcription-test",
                "provider": "local-funasr",
                "command": [sys.executable, str(helper)],
                "model": "tiny",
                "device": "cpu",
            },
            "runtime": {"storage_ref": f"file:{root / 'artifacts'}", "database_ref": ":memory:"},
        },
        profiles_by_id={},
    )


def helper_script(root: Path, mode: str) -> Path:
    helper = root / f"fake_funasr_{mode}.py"
    if mode == "fail":
        helper.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    else:
        helper.write_text(
            "import json, pathlib, sys\n"
            "video = pathlib.Path(sys.argv[1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / (video.stem + '.json')).write_text(json.dumps({'text': 'fixture transcript'}), encoding='utf-8')\n",
            encoding="utf-8",
        )
    return helper


if __name__ == "__main__":
    test_batch_success_persists_artifact_hash_and_cleans_temp()
    test_missing_video_records_failure_not_skipped()
    test_command_failure_cleans_video_and_records_error()
    test_missing_runtime_config_blocks_without_fake_counts()
    test_content_from_ledger_maps_sqlite_rows_to_batch_content()
