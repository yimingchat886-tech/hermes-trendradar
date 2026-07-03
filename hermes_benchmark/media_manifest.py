"""One-shot Douyin media localization for Child 6a."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

EXTERNAL_RUNS_ROOT = Path("/home/jym/workspace/_external/hermes-stock-runs")
FIRST_ACCOUNT_ID = "douyin_ai_xiaobai_lab"
SCHEMA_VERSION = "1.4"


class MediaManifestError(RuntimeError):
    pass


class RunSummary(TypedDict):
    status: str
    run_root: str
    selection_manifest: str
    media_manifest: str
    error_manifest: str
    selected: int
    downloaded: int
    failed: int
    account_distribution: dict[str, int]


def run_media_manifest(
    source_root: str | Path,
    run_root: str | Path,
    *,
    repo_root: str | Path | None = None,
    timeout_seconds: int = 60,
) -> RunSummary:
    repo = Path(repo_root or Path.cwd()).resolve()
    run = Path(run_root).resolve()
    _assert_external_run_root(run, repo)
    run.mkdir(parents=True, exist_ok=True)

    selection = select_media_rows(source_root)
    selection_manifest = run / "selection-manifest.private.jsonl"
    media_manifest = run / "media-manifest.private.jsonl"
    error_manifest = run / "download-errors.redacted.jsonl"
    summary_path = run / "summary.redacted.json"

    _write_jsonl(selection_manifest, selection)
    media_rows, error_rows = localize_media(selection, run, repo_root=repo, timeout_seconds=timeout_seconds)
    _write_jsonl(media_manifest, media_rows)
    _write_jsonl(error_manifest, error_rows)

    summary: RunSummary = {
        "status": "success" if len(media_rows) == 99 and not error_rows else "failed",
        "run_root": str(run),
        "selection_manifest": str(selection_manifest),
        "media_manifest": str(media_manifest),
        "error_manifest": str(error_manifest),
        "selected": len(selection),
        "downloaded": len(media_rows),
        "failed": len(error_rows),
        "account_distribution": dict(sorted(Counter(row["account_id"] for row in selection).items())),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def select_media_rows(
    source_root: str | Path,
    *,
    quota_by_account: Mapping[str, int] | None = None,
    default_quota: int = 10,
) -> list[dict[str, Any]]:
    source = Path(source_root)
    files = sorted(source.glob("*/douyin/jsonl/creator_contents_*.jsonl"))
    if not files:
        raise MediaManifestError(f"no creator contents JSONL files under {source}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in files:
        account_dir = path.parents[2].name
        account_id = _account_id(account_dir)
        for row in _read_jsonl(path):
            aweme_id = _text(row, "aweme_id", "platform_content_id", "content_id")
            video_url = _text(row, "video_download_url")
            if not aweme_id or not video_url:
                continue
            content_id = f"content-douyin-{_safe_name(aweme_id)}"
            grouped.setdefault(account_id, []).append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "content_id": content_id,
                    "platform": "douyin",
                    "platform_content_id": aweme_id,
                    "account_id": account_id,
                    "account_raw_dir": account_dir,
                    "source_url": _text(row, "aweme_url", "url", "source_url") or f"https://www.douyin.com/video/{aweme_id}",
                    "video_download_url": video_url,
                    "liked_count": _int(row.get("liked_count")),
                    "title": _text(row, "title", "desc"),
                    "published_at": _text(row, "create_time", "published_at", "publish_time"),
                }
            )

    selected: list[dict[str, Any]] = []
    for account_id in sorted(grouped):
        quota = _quota(account_id, quota_by_account, default_quota)
        rows = sorted(grouped[account_id], key=lambda row: (-int(row["liked_count"]), str(row["content_id"])))
        if len(rows) < quota:
            raise MediaManifestError(f"{account_id} has {len(rows)} eligible videos, need {quota}")
        selected.extend(rows[:quota])

    if len({row["content_id"] for row in selected}) != len(selected):
        raise MediaManifestError("selected content IDs are not unique")
    return selected


def localize_media(
    selection: Iterable[Mapping[str, Any]],
    run_root: str | Path,
    *,
    repo_root: str | Path,
    timeout_seconds: int = 60,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run = Path(run_root).resolve()
    repo = Path(repo_root).resolve()
    _assert_external_run_root(run, repo)
    media_root = run / "media"
    media_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []

    for item in selection:
        content_id = str(item["content_id"])
        account_id = str(item["account_id"])
        url = str(item["video_download_url"])
        final_path = media_root / _safe_name(account_id) / f"{_safe_name(content_id)}.mp4"
        try:
            _assert_external_path(final_path.parent, run, repo)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.is_symlink():
                raise MediaManifestError("refusing to overwrite symlink media path")
            if not _usable_file(final_path):
                _download_atomic(url, final_path, timeout_seconds)
            size = final_path.stat().st_size
            digest = _sha256_file(final_path)
            media_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "content_id": content_id,
                    "platform": item.get("platform", "douyin"),
                    "platform_content_id": item.get("platform_content_id", ""),
                    "account_id": account_id,
                    "source_url": item.get("source_url", ""),
                    "video_download_url": url,
                    "liked_count": item.get("liked_count", 0),
                    "local_media_path": str(final_path),
                    "media_size_bytes": size,
                    "media_hash": digest,
                }
            )
        except Exception as exc:
            error_rows.append(_error_row(item, exc))
            final_path.with_suffix(final_path.suffix + ".tmp").unlink(missing_ok=True)

    if len({row["local_media_path"] for row in media_rows}) != len(media_rows):
        raise MediaManifestError("localized media paths are not unique")
    return media_rows, error_rows


def _download_atomic(url: str, final_path: Path, timeout_seconds: int) -> None:
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "HermesBenchmark/1.4", "Accept": "video/*,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response, tmp_path.open("wb") as file:
            content_type = str(response.headers.get("Content-Type", ""))
            if _looks_non_media(content_type):
                raise MediaManifestError(f"non-media response content type: {content_type}")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
        if not _usable_file(tmp_path):
            raise MediaManifestError("downloaded media file is empty or unreadable")
        tmp_path.replace(final_path)
    except (urllib.error.URLError, OSError, MediaManifestError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise MediaManifestError(str(exc)) from exc


def _error_row(item: Mapping[str, Any], exc: Exception) -> dict[str, Any]:
    url = str(item.get("video_download_url", ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "content_id": str(item.get("content_id", "")),
        "account_id": str(item.get("account_id", "")),
        "error_code": "media_download_failed",
        "retryable": True,
        "summary": _redact_text(str(exc))[:300],
        "redacted_video_download_url": _redact_url(url),
        "video_download_url_hash": "sha256:" + hashlib.sha256(url.encode()).hexdigest() if url else "",
    }


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise MediaManifestError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, Mapping):
                raise MediaManifestError(f"JSONL row must be an object at {path}:{line_number}")
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp_path.replace(path)


def _assert_external_run_root(path: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    _assert_under(resolved, EXTERNAL_RUNS_ROOT.resolve())
    if _is_relative_to(resolved, repo_root):
        raise MediaManifestError(f"run root must be outside git repo: {resolved}")


def _assert_external_path(path: Path, run_root: Path, repo_root: Path) -> None:
    resolved = path.resolve()
    _assert_under(resolved, run_root.resolve())
    if _is_relative_to(resolved, repo_root):
        raise MediaManifestError(f"media path must be outside git repo: {resolved}")


def _assert_under(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MediaManifestError(f"path {path} is outside {root}") from exc


def _usable_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and os.access(path, os.R_OK) and path.stat().st_size > 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _quota(account_id: str, quota_by_account: Mapping[str, int] | None, default_quota: int) -> int:
    if quota_by_account and account_id in quota_by_account:
        return quota_by_account[account_id]
    return 9 if account_id == FIRST_ACCOUNT_ID else default_quota


def _account_id(account_dir: str) -> str:
    return account_dir.split("-", 1)[1] if "-" in account_dir else account_dir


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value):
            return str(value)
    return ""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-") or "item"


def _looks_non_media(content_type: str) -> bool:
    lowered = content_type.lower()
    return lowered.startswith("text/") or "json" in lowered or "html" in lowered


def _redact_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme:
        return "<redacted-url>"
    if parsed.scheme == "file":
        return "file://<redacted>"
    host = parsed.netloc or "<redacted-host>"
    return f"{parsed.scheme}://{host}/<redacted>"


def _redact_text(text: str) -> str:
    return re.sub(r"\b(?:https?|wss?|socks5?|file)://\S+", "<redacted-url>", text)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Localize the approved Child 6a Douyin videos into an external manifest.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args(argv)

    summary = run_media_manifest(
        args.source_root,
        args.run_root,
        repo_root=args.repo_root,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "success" else 1


def _self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "raw"
        fixture_media = root / "fixture.mp4"
        fixture_media.write_bytes(b"fake mp4")
        jsonl = source / "01-douyin_ai_xiaobai_lab" / "douyin" / "jsonl" / "creator_contents_2026-07-02.jsonl"
        jsonl.parent.mkdir(parents=True)
        rows = [
            {"aweme_id": f"aweme-{index}", "liked_count": index, "video_download_url": fixture_media.as_uri()}
            for index in range(9)
        ]
        jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        selected = select_media_rows(source)
        assert len(selected) == 9
        assert selected[0]["liked_count"] == 8


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _self_check()
        print("media manifest checks ok")
    else:
        raise SystemExit(main())
