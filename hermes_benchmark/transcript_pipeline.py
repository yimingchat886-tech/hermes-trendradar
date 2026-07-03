"""Local FunASR transcript boundary with fixture-friendly checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .contracts import BenchmarkContent, ContractError, Transcript, TranscriptStatus, validate_record

RUN_ID = "run-child4-transcript-fixture-2026-06-30"
OBSERVED_AT = "2026-06-30T00:00:00-07:00"
PROVIDER = "local_funasr"
STATUSES = {"pending", "done", "failed", "not_applicable"}


def transcript_fixture(content: BenchmarkContent) -> Transcript:
    return build_transcript(
        content,
        status="done",
        text=f"Fixture transcript for {content['title']}.",
        segments_path=f"fixture://transcripts/{content['id']}.segments.json",
    )


def build_transcript(
    content: BenchmarkContent,
    *,
    status: TranscriptStatus,
    text: str = "",
    segments_path: str = "",
    error: str = "",
) -> Transcript:
    if status not in STATUSES:
        raise ContractError(f"unsupported transcript status: {status}")
    if status == "done" and not (text or segments_path):
        raise ContractError("done transcript requires text or segments_path")
    if status == "failed" and not error:
        raise ContractError("failed transcript requires error")

    transcript_id = f"transcript-{content['id'].removeprefix('content-')}"
    transcript: Transcript = {
        "id": transcript_id,
        "content_id": content["id"],
        "status": status,
        "provider": PROVIDER,
        "language": "zh",
        "text": text,
        "segments_path": segments_path,
        "error": error,
        "trace": _trace(transcript_id, content),
    }
    validate_record("transcripts", transcript)
    return transcript


def transcribe_temporary_video(
    content: BenchmarkContent,
    video_path: str | Path,
    transcriber: Callable[[Path], Mapping[str, Any]],
) -> Transcript:
    path = Path(video_path)
    try:
        result = transcriber(path)
        return build_transcript(
            content,
            status="done",
            text=str(result.get("text", "")),
            segments_path=str(result.get("segments_path", "")),
        )
    except Exception as exc:
        return build_transcript(content, status="failed", error=str(exc))
    finally:
        path.unlink(missing_ok=True)


def _trace(local_id: str, content: BenchmarkContent) -> dict[str, str]:
    source_url = content.get("raw_source_url") or content.get("url", "")
    return {
        "local_id": local_id,
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
        "source_id": content["source_id"],
        "source_url": source_url,
        "content_id": content["id"],
    }


def _self_check() -> None:
    import tempfile

    from .mediacrawler_import import import_mediacrawler_rows, load_mediacrawler_fixture

    content = import_mediacrawler_rows(load_mediacrawler_fixture())["contents"][0]
    fixture = transcript_fixture(content)
    assert fixture["status"] == "done"
    assert fixture["provider"] == PROVIDER
    assert fixture["content_id"] == content["id"]
    assert fixture["trace"]["content_id"] == content["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        video = Path(tmpdir) / "input.mp4"
        video.write_bytes(b"fixture video")
        transcript = transcribe_temporary_video(
            content,
            video,
            lambda path: {"text": f"stubbed {path.name}", "segments_path": "fixture://segments.json"},
        )
        assert transcript["status"] == "done"
        assert transcript["text"] == "stubbed input.mp4"
        assert not video.exists()

    with tempfile.TemporaryDirectory() as tmpdir:
        video = Path(tmpdir) / "failed.mp4"
        video.write_bytes(b"fixture video")

        def fail(_: Path) -> Mapping[str, Any]:
            raise RuntimeError("funasr unavailable")

        failed = transcribe_temporary_video(content, video, fail)
        assert failed["status"] == "failed"
        assert "funasr unavailable" in failed["error"]
        assert not video.exists()

    build_transcript(content, status="pending")
    build_transcript(content, status="not_applicable", error="no video input")


if __name__ == "__main__":
    _self_check()
    print("transcript pipeline ok")
