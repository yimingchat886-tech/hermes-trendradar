from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.media_manifest import localize_media, select_media_rows


def test_select_media_rows_uses_account_quota_and_likes_tiebreak() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "raw"
        write_rows(
            source,
            "01-douyin_first",
            [
                row("a", 10),
                row("b", 20),
                row("c", 20),
            ],
        )
        write_rows(source, "02-douyin_second", [row("d", 1), row("e", 2)])

        selected = select_media_rows(source, quota_by_account={"douyin_first": 2, "douyin_second": 1})

    assert [item["platform_content_id"] for item in selected] == ["b", "c", "e"]
    assert {item["content_id"] for item in selected} == {"content-douyin-b", "content-douyin-c", "content-douyin-e"}


def test_localize_media_writes_hashes_and_redacted_failures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        run = Path("/home/jym/workspace/_external/hermes-stock-runs") / f"test-child6a-media-manifest-{os.getpid()}"
        shutil.rmtree(run, ignore_errors=True)
        media = root / "fixture.mp4"
        media.write_bytes(b"video bytes")
        selection = [
            {
                "content_id": "content-douyin-ok",
                "platform": "douyin",
                "platform_content_id": "ok",
                "account_id": "douyin_first",
                "source_url": "https://www.douyin.com/video/ok",
                "video_download_url": media.as_uri(),
                "liked_count": 9,
            },
            {
                "content_id": "content-douyin-bad",
                "platform": "douyin",
                "platform_content_id": "bad",
                "account_id": "douyin_first",
                "source_url": "https://www.douyin.com/video/bad",
                "video_download_url": "https://media.example/video.mp4",
                "liked_count": 8,
            },
        ]

        rows, errors = localize_media(selection, run, repo_root=repo, timeout_seconds=1)

    assert len(rows) == 1
    assert rows[0]["media_size_bytes"] == len(b"video bytes")
    assert rows[0]["media_hash"].startswith("sha256:")
    assert Path(rows[0]["local_media_path"]).is_file()
    assert len(errors) == 1
    assert "video.mp4" not in json.dumps(errors)
    assert errors[0]["redacted_video_download_url"] == "https://media.example/<redacted>"
    shutil.rmtree(run, ignore_errors=True)


def write_rows(source: Path, account_dir: str, rows: list[dict[str, object]]) -> None:
    path = source / account_dir / "douyin" / "jsonl" / "creator_contents_2026-07-02.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")


def row(aweme_id: str, liked_count: int) -> dict[str, object]:
    return {
        "aweme_id": aweme_id,
        "aweme_url": f"https://www.douyin.com/video/{aweme_id}",
        "liked_count": liked_count,
        "video_download_url": f"https://media.example/{aweme_id}.mp4",
    }


if __name__ == "__main__":
    test_select_media_rows_uses_account_quota_and_likes_tiebreak()
    test_localize_media_writes_hashes_and_redacted_failures()
