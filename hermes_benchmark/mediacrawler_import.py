"""Import MediaCrawler-style fixture rows into local benchmark content."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urldefrag

from .account_registry import benchmark_accounts, validate_account_registry
from .contracts import BenchmarkAccount, BenchmarkContent, ContractError, SourceHealth, validate_record

RUN_ID = "run-child3-mediacrawler-fixture-2026-06-30"
OBSERVED_AT = "2026-06-30T00:00:00-07:00"
IMPORT_SOURCE_ID = "source-mediacrawler-fixture-import"
DEFAULT_FIXTURE_PATH = Path(__file__).with_name("sample_data") / "mediacrawler_results.json"


class MediaCrawlerImportResult(TypedDict):
    contents: list[BenchmarkContent]
    source_health: list[SourceHealth]


def load_mediacrawler_fixture(path: str | Path = DEFAULT_FIXTURE_PATH) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fixture_file:
        rows = json.load(fixture_file)
    if not isinstance(rows, list):
        raise ContractError("MediaCrawler fixture must be a list")
    if not all(isinstance(row, dict) for row in rows):
        raise ContractError("MediaCrawler fixture rows must be objects")
    return rows


def import_mediacrawler_rows(
    rows: Iterable[Mapping[str, Any]],
    accounts: Iterable[BenchmarkAccount] | None = None,
) -> MediaCrawlerImportResult:
    account_records = list(accounts if accounts is not None else benchmark_accounts())
    validate_account_registry(account_records)
    account_index = {(account["platform"], account["handle"]): account for account in account_records}
    platforms = {account["platform"] for account in account_records}

    contents: list[BenchmarkContent] = []
    source_health: list[SourceHealth] = []
    seen_platform_ids: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()

    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            source_health.append(_health(row_number, "fixture row must be an object"))
            continue

        platform = _text(row, "platform")
        platform_content_id = _text(row, "platform_content_id", "aweme_id", "note_id", "content_id")
        url = _text(row, "url", "source_url", "content_url")
        author_handle = _text(row, "author_handle", "user_id", "sec_uid", "nickname")
        missing = [
            name
            for name, value in (
                ("platform", platform),
                ("platform_content_id", platform_content_id),
                ("url", url),
                ("author_handle", author_handle),
            )
            if not value
        ]
        if missing:
            source_health.append(_health(row_number, f"missing required fields: {', '.join(missing)}", url))
            continue
        if platform not in platforms:
            source_health.append(_health(row_number, f"unsupported platform: {platform}", url))
            continue

        account = account_index.get((platform, author_handle))
        if account is None:
            source_health.append(_health(row_number, f"unknown benchmark account: {platform}/{author_handle}", url))
            continue

        url_key = _normalize_url(url)
        platform_id_key = (platform, platform_content_id)
        if url_key in seen_urls or platform_id_key in seen_platform_ids:
            continue
        seen_urls.add(url_key)
        seen_platform_ids.add(platform_id_key)

        try:
            content = _content(row, row_number, account, platform_content_id, url)
            validate_record("contents", content)
        except (ContractError, TypeError, ValueError) as exc:
            source_health.append(_health(row_number, f"import error: {exc}", url))
            continue
        contents.append(content)

    for health in source_health:
        validate_record("source_health", health)
    return {"contents": contents, "source_health": source_health}


def _content(
    row: Mapping[str, Any],
    row_number: int,
    account: BenchmarkAccount,
    platform_content_id: str,
    url: str,
) -> BenchmarkContent:
    content_id = f"content-{account['platform']}-{_slug(platform_content_id)}"
    metrics = _metrics(row)
    comments_summary = _text(row, "comments_summary", "comments")
    evidence_state = "sufficient" if _has_core_evidence(metrics, comments_summary) else "insufficient"
    return {
        "id": content_id,
        "source_id": account["source_id"],
        "account_id": account["id"],
        "platform": account["platform"],
        "platform_content_id": platform_content_id,
        "url": url,
        "title": _text(row, "title", "desc"),
        "caption": _text(row, "caption", "desc"),
        "published_at": _text(row, "published_at", "publish_time"),
        "crawled_at": _text(row, "crawled_at", "crawl_time"),
        "metrics": metrics,
        "hashtags": _text_list(row.get("hashtags", [])),
        "topics": _text_list(row.get("topics", [])),
        "comments_summary": comments_summary,
        "raw_source_url": url,
        "evidence_state": evidence_state,
        "trace": _trace(content_id, account["source_id"], url, row_number),
    }


def _metrics(row: Mapping[str, Any]) -> dict[str, int]:
    raw_metrics = row.get("metrics")
    metrics = {key: int(value) for key, value in raw_metrics.items()} if isinstance(raw_metrics, Mapping) else {}
    aliases = {"likes": "like_count", "comments": "comment_count", "shares": "share_count"}
    for target, alias in aliases.items():
        if target not in metrics and alias in row:
            metrics[target] = int(row[alias])
    return metrics


def _has_core_evidence(metrics: Mapping[str, int], comments_summary: str) -> bool:
    return all(key in metrics for key in ("likes", "comments", "shares")) and bool(comments_summary)


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value):
            return str(value)
    return ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


def _normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return slug or "unknown"


def _trace(local_id: str, source_id: str, source_url: str, row_number: int) -> dict[str, str]:
    return {
        "local_id": local_id,
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
        "source_id": source_id,
        "source_url": source_url,
        "row_number": str(row_number),
    }


def _health(row_number: int, message: str, source_url: str = "") -> SourceHealth:
    health_id = f"health-mediacrawler-row-{row_number}"
    return {
        "id": health_id,
        "source_id": IMPORT_SOURCE_ID,
        "status": "warning",
        "checked_at": OBSERVED_AT,
        "message": f"row {row_number}: {message}",
        "trace": _trace(health_id, IMPORT_SOURCE_ID, source_url, row_number),
    }


def _self_check() -> None:
    result = import_mediacrawler_rows(load_mediacrawler_fixture())
    contents = result["contents"]
    health = result["source_health"]

    assert len(contents) == 2
    assert len({content["url"] for content in contents}) == len(contents)
    assert len({(content["platform"], content["platform_content_id"]) for content in contents}) == len(contents)
    assert any(content["evidence_state"] == "sufficient" for content in contents)
    assert any(content["evidence_state"] == "insufficient" for content in contents)
    assert any("unknown benchmark account" in item["message"] for item in health)
    assert any("unsupported platform" in item["message"] for item in health)

    for content in contents:
        validate_record("contents", content)
    for item in health:
        validate_record("source_health", item)


if __name__ == "__main__":
    _self_check()
    print("mediacrawler fixture import ok")
