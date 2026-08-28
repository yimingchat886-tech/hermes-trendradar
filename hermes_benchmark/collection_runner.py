"""Douyin collection runner boundary for v1.4."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urldefrag

from .account_registry import SOURCE_IDS, validate_account_registry
from .contracts import BenchmarkAccount, BenchmarkContent
from .external_runtime import redact_text
from .mediacrawler_import import import_mediacrawler_rows
from .profile import LoadedProfile, ProfileError, validate_profile
from .state import record_error, upsert_content_ledger

AccountStatus = Literal["attempted_success", "attempted_failed", "skipped_with_error"]

URL_RE = re.compile(r"\b(?:https?|wss?|socks5?)://\S+", re.IGNORECASE)


class CollectionSkipped(Exception):
    def __init__(self, code: str, summary: str, *, retryable: bool = True):
        self.code = code
        self.summary = summary
        self.retryable = retryable
        super().__init__(summary)


class AccountCollectionResult(TypedDict):
    account_id: str
    display_name: str
    status: AccountStatus
    rows_seen: int
    contents_upserted: int
    errors: int


class CollectionRunResult(TypedDict):
    run_id: str
    status: str
    account_count: int
    accounts: list[AccountCollectionResult]
    contents_upserted: int
    errors: int


Collector = Callable[[BenchmarkAccount], Iterable[Mapping[str, Any]]]


def mediacrawler_creator_command(
    account: BenchmarkAccount,
    *,
    python_executable: str,
    output_dir: str | Path,
    max_notes: int = 10,
    comments: bool = False,
    headless: bool = False,
) -> list[str]:
    creator_id = account.get("profile_url") or account.get("handle") or account["id"]
    return [
        python_executable,
        "main.py",
        "--platform",
        "dy",
        "--lt",
        "qrcode",
        "--type",
        "creator",
        "--creator_id",
        str(creator_id),
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(output_dir),
        "--crawler_max_notes_count",
        str(max_notes),
        "--max_concurrency_num",
        "1",
        "--get_comment",
        str(comments).lower(),
        "--get_sub_comment",
        "false",
        "--headless",
        str(headless).lower(),
    ]


def load_mediacrawler_jsonl(path: str | Path, account: BenchmarkAccount) -> list[dict[str, Any]]:
    rows = []
    for jsonl_path in _jsonl_paths(path):
        with jsonl_path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid MediaCrawler JSONL at {jsonl_path}:{line_number}") from exc
                if not isinstance(item, Mapping):
                    raise ValueError(f"MediaCrawler JSONL row must be an object at {jsonl_path}:{line_number}")
                rows.append(mediacrawler_douyin_row(item, account))
    return rows


def mediacrawler_douyin_row(row: Mapping[str, Any], account: BenchmarkAccount) -> dict[str, Any]:
    aweme_id = _text(row, "aweme_id", "platform_content_id", "content_id")
    aweme_url = _text(row, "aweme_url", "url", "source_url") or f"https://www.douyin.com/video/{aweme_id}"
    title = _text(row, "title", "desc")
    desc = _text(row, "desc", "title")
    comment_count = _int(row.get("comment_count"))
    result = {
        "platform": "douyin",
        "platform_content_id": aweme_id,
        "url": aweme_url,
        "title": title,
        "caption": desc,
        "published_at": _text(row, "create_time", "published_at", "publish_time"),
        "crawled_at": _text(row, "last_modify_ts", "crawled_at", "crawl_time"),
        "author_handle": account["handle"],
        "metrics": {
            "likes": _int(row.get("liked_count") or row.get("like_count")),
            "comments": comment_count,
            "shares": _int(row.get("share_count")),
        },
        "comments_summary": f"comment_count={comment_count}",
    }
    video_download_url = _text(row, "video_download_url")
    if video_download_url:
        result["video_download_url"] = video_download_url
    return result


def enabled_douyin_accounts(profile: LoadedProfile) -> list[BenchmarkAccount]:
    validate_profile(profile)
    account_ref = profile.root["account_profile_ref"]
    account_profile = profile.profiles_by_ref[account_ref]
    accounts = [
        _account_from_profile(account)
        for account in account_profile["accounts"]
        if account.get("enabled") is True and account.get("platform") == "douyin"
    ]
    validate_account_registry(accounts)
    return accounts


def run_douyin_collection(
    profile: LoadedProfile,
    conn: sqlite3.Connection,
    *,
    run_id: str,
    collector: Collector | None = None,
    continue_on_account_failure: bool = True,
    sensitive_values: Iterable[str] = (),
) -> CollectionRunResult:
    accounts = enabled_douyin_accounts(profile)
    collect = collector or _skip_external_collection
    account_results: list[AccountCollectionResult] = []
    total_contents = 0
    total_errors = 0

    for account in accounts:
        try:
            rows = list(collect(account))
            imported = import_mediacrawler_rows(rows, [account])
            errors = _record_import_errors(conn, run_id, imported["source_health"], sensitive_values)
            contents = _upsert_contents(conn, run_id, imported["contents"], sensitive_values)
            if rows and contents:
                status: AccountStatus = "attempted_success"
            else:
                errors += _record_error(
                    conn,
                    run_id,
                    "account",
                    account["id"],
                    "no_importable_content",
                    "account produced no importable content rows",
                    True,
                    sensitive_values,
                )
                status = "attempted_failed"
        except CollectionSkipped as exc:
            errors = _record_error(
                conn,
                run_id,
                "account",
                account["id"],
                exc.code,
                exc.summary,
                exc.retryable,
                sensitive_values,
            )
            rows = []
            contents = 0
            status = "skipped_with_error"
        except Exception as exc:
            if not continue_on_account_failure:
                raise
            errors = _record_error(
                conn,
                run_id,
                "account",
                account["id"],
                "collection_failed",
                str(exc),
                True,
                sensitive_values,
            )
            rows = []
            contents = 0
            status = "attempted_failed"

        total_contents += contents
        total_errors += errors
        account_results.append(
            {
                "account_id": account["id"],
                "display_name": account["display_name"],
                "status": status,
                "rows_seen": len(rows),
                "contents_upserted": contents,
                "errors": errors,
            }
        )

    return {
        "run_id": run_id,
        "status": _run_status(total_contents, total_errors),
        "account_count": len(account_results),
        "accounts": account_results,
        "contents_upserted": total_contents,
        "errors": total_errors,
    }


def content_to_ledger_item(content: BenchmarkContent) -> dict[str, Any]:
    title_or_caption = content.get("title") or content.get("caption") or ""
    return {
        "content_id": content["id"],
        "platform": content["platform"],
        "platform_content_id": content.get("platform_content_id"),
        "normalized_source_url": _normalize_url(content["url"]),
        "account_id": content["account_id"],
        "publish_at": content.get("published_at"),
        "normalized_title_or_caption_hash": _hash_text(title_or_caption),
        "source_url": content["url"],
        "status": "seen",
    }


def _account_from_profile(account: Mapping[str, Any]) -> BenchmarkAccount:
    platform = str(account["platform"])
    account_id = str(account["account_id"])
    profile_url = str(account["profile_url"])
    source_id = SOURCE_IDS.get(platform)
    if source_id is None:
        raise ProfileError([f"unsupported account platform: {platform}"])
    return {
        "id": account_id,
        "source_id": source_id,
        "platform": platform,
        "handle": str(account.get("handle") or account.get("display_name") or account_id),
        "display_name": str(account.get("display_name") or account_id),
        "profile_url": profile_url,
        "level": str(account.get("priority") or "B"),
        "enabled": True,
        "owner": str(account.get("owner") or "jym"),
        "daily_tracking": account.get("crawl_frequency", "daily") == "daily",
        "notes": str(account.get("owner_note") or ""),
        "verified": True,
        "source_status": "verified",
        "trace": {
            "local_id": account_id,
            "run_id": "run-child4-v1-4-douyin-collection",
            "observed_at": "2026-07-02T00:00:00-07:00",
            "source_id": source_id,
            "source_url": profile_url,
        },
    }


def _skip_external_collection(account: BenchmarkAccount) -> Iterable[Mapping[str, Any]]:
    raise CollectionSkipped(
        "external_collection_not_configured",
        f"real MediaCrawler execution is gated for account {account['id']}",
        retryable=False,
    )


def _record_import_errors(
    conn: sqlite3.Connection,
    run_id: str,
    health_rows: Iterable[Mapping[str, Any]],
    sensitive_values: Iterable[str],
) -> int:
    count = 0
    for health in health_rows:
        count += _record_error(
            conn,
            run_id,
            "content",
            str(health.get("id") or "unknown-row"),
            "normalization_error",
            str(health.get("message") or "MediaCrawler row normalization failed"),
            True,
            sensitive_values,
        )
    return count


def _upsert_contents(
    conn: sqlite3.Connection,
    run_id: str,
    contents: Iterable[BenchmarkContent],
    sensitive_values: Iterable[str],
) -> int:
    count = 0
    for content in contents:
        try:
            result = upsert_content_ledger(conn, run_id, content_to_ledger_item(content))
        except Exception as exc:
            _record_error(
                conn,
                run_id,
                "content",
                content.get("id", "unknown-content"),
                "ledger_upsert_failed",
                str(exc),
                True,
                sensitive_values,
            )
            continue
        if result["status"] in {"inserted", "noop"}:
            count += 1
    return count


def _record_error(
    conn: sqlite3.Connection,
    run_id: str,
    scope: str,
    object_id: str,
    code: str,
    summary: str,
    retryable: bool,
    sensitive_values: Iterable[str],
) -> int:
    record_error(
        conn,
        run_id,
        scope,
        object_id,
        code,
        _redact_summary(summary, sensitive_values),
        retryable,
    )
    return 1


def _run_status(contents: int, errors: int) -> str:
    if errors == 0:
        return "success"
    if contents == 0:
        return "failed"
    return "partial_success"


def _redact_summary(summary: str, sensitive_values: Iterable[str]) -> str:
    redacted = redact_text(summary, sensitive_values)
    return URL_RE.sub("<redacted-url>", redacted)[:300]


def _jsonl_paths(path: str | Path) -> list[Path]:
    target = Path(path)
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"MediaCrawler output path not found: {path}")
    return sorted(target.rglob("*_contents_*.jsonl"))


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


def _normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _hash_text(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()


def _self_check() -> None:
    item = content_to_ledger_item(
        {
            "id": "content-self-check",
            "source_id": "source-douyin-benchmark",
            "account_id": "account-self-check",
            "platform": "douyin",
            "platform_content_id": "aweme-self-check",
            "url": "https://www.douyin.com/video/self-check#frag",
            "title": "Self Check",
            "caption": "",
            "published_at": "2026-07-02T00:00:00-07:00",
            "crawled_at": "2026-07-02T00:00:00-07:00",
            "metrics": {"likes": 1, "comments": 1, "shares": 1},
            "hashtags": [],
            "evidence_state": "sufficient",
            "trace": {"local_id": "content-self-check", "run_id": "run", "observed_at": "now"},
        }
    )
    assert item["normalized_source_url"] == "https://www.douyin.com/video/self-check"
    assert item["normalized_title_or_caption_hash"].startswith("sha256:")


if __name__ == "__main__":
    _self_check()
    print("collection runner checks ok")
