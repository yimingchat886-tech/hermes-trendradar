"""Child-1 no-credential fixture loop."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import ContractError, FixtureLoop, validate_fixture_loop

RUN_ID = "run-child1-fixture-2026-06-29"
OBSERVED_AT = "2026-06-29T00:00:00-07:00"


def child1_fixture() -> FixtureLoop:
    source_id = "source-douyin-benchmark"
    account_id = "account-douyin-demo"
    content_id = "content-douyin-demo-001"

    return {
        "sources": [
            {
                "id": source_id,
                "platform": "douyin",
                "name": "Douyin benchmark fixture",
                "kind": "benchmark_account_source",
                "url": "https://www.douyin.com/user/demo",
                "enabled": True,
                "trace": _trace(source_id, source_id, "https://www.douyin.com/user/demo"),
            }
        ],
        "accounts": [
            {
                "id": account_id,
                "source_id": source_id,
                "platform": "douyin",
                "handle": "demo_benchmark",
                "display_name": "Demo Benchmark",
                "profile_url": "https://www.douyin.com/user/demo",
                "level": "A",
                "enabled": True,
                "owner": "jym",
                "daily_tracking": True,
                "notes": "No-credential fixture account.",
                "trace": _trace(account_id, source_id, "https://www.douyin.com/user/demo"),
            }
        ],
        "contents": [
            {
                "id": content_id,
                "source_id": source_id,
                "account_id": account_id,
                "platform": "douyin",
                "platform_content_id": "douyin-demo-001",
                "url": "https://www.douyin.com/video/demo-001",
                "title": "Fixture benchmark content",
                "caption": "Local fixture only; no platform request is made.",
                "published_at": "2026-06-28T18:00:00-07:00",
                "crawled_at": OBSERVED_AT,
                "metrics": {"likes": 1200, "comments": 34, "shares": 56},
                "hashtags": ["benchmark", "fixture"],
                "evidence_state": "sufficient",
                "trace": _trace(content_id, source_id, "https://www.douyin.com/video/demo-001"),
            }
        ],
        "transcripts": [
            {
                "id": "transcript-douyin-demo-001",
                "content_id": content_id,
                "status": "pending",
                "provider": "placeholder",
                "language": "zh",
                "text": "",
                "segments_path": "",
                "error": "",
                "trace": _trace("transcript-douyin-demo-001", source_id),
            }
        ],
        "topic_candidates": [
            {
                "id": "topic-douyin-demo-001",
                "content_id": content_id,
                "summary": "Fixture summary placeholder.",
                "hook": "Start from a concrete benchmark observation.",
                "title_formula": "Problem + benchmark move + proof",
                "structure": "hook -> evidence -> reusable angle",
                "pain": "Unclear repeatable angle.",
                "reusable_angle": "Extract the repeatable content mechanic.",
                "evidence_state": "placeholder",
                "card_fields": {"headline": "Fixture card", "evidence": "metrics available"},
                "topic_pool_supplement": {"angle": "benchmark-derived", "confidence": "fixture"},
                "trace": _trace("topic-douyin-demo-001", source_id),
            }
        ],
        "rag_documents": [
            {
                "id": "rag-doc-douyin-demo-001",
                "source_object_id": content_id,
                "source_object_type": "benchmark_content",
                "text": "Placeholder export shape for later RAG indexing.",
                "metadata": {"platform": "douyin", "content_id": content_id},
                "trace": _trace("rag-doc-douyin-demo-001", source_id),
            }
        ],
        "source_health": [
            {
                "id": "health-douyin-benchmark",
                "source_id": source_id,
                "status": "ok",
                "checked_at": OBSERVED_AT,
                "message": "Fixture source available locally.",
                "trace": _trace("health-douyin-benchmark", source_id),
            }
        ],
    }


def _trace(local_id: str, source_id: str, source_url: str = "") -> dict[str, str]:
    trace = {"local_id": local_id, "run_id": RUN_ID, "observed_at": OBSERVED_AT, "source_id": source_id}
    if source_url:
        trace["source_url"] = source_url
    return trace


def _self_check() -> None:
    valid = child1_fixture()
    validate_fixture_loop(valid)

    duplicate_id = deepcopy(valid)
    duplicate_id["accounts"].append(dict(duplicate_id["accounts"][0]))
    _expect_contract_error("duplicate object id", duplicate_id)

    missing_trace = deepcopy(valid)
    del missing_trace["contents"][0]["trace"]["run_id"]
    _expect_contract_error("missing trace.run_id", missing_trace)


def _expect_contract_error(fragment: str, fixture: dict[str, Any]) -> None:
    try:
        validate_fixture_loop(fixture)
    except ContractError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r} in {exc!r}") from exc
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


if __name__ == "__main__":
    _self_check()
    print("benchmark fixture contracts ok")
