"""No-credential benchmark tracking contracts and validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

Platform = Literal["douyin", "xiaohongshu"]
AccountLevel = Literal["S", "A", "B", "C"]
TranscriptStatus = Literal["pending", "done", "failed", "not_applicable"]
EvidenceState = Literal["sufficient", "insufficient", "placeholder"]
HealthStatus = Literal["ok", "warning", "failed"]
SourceStatus = Literal["placeholder", "verified"]


class Trace(TypedDict, total=False):
    local_id: str
    run_id: str
    observed_at: str
    source_id: str
    source_url: str


class Source(TypedDict, total=False):
    id: str
    platform: Platform
    name: str
    kind: str
    url: str
    enabled: bool
    trace: Trace


class BenchmarkAccount(TypedDict, total=False):
    id: str
    source_id: str
    platform: Platform
    handle: str
    display_name: str
    profile_url: str
    level: AccountLevel
    enabled: bool
    owner: str
    daily_tracking: bool
    notes: str
    verified: bool
    source_status: SourceStatus
    trace: Trace


class BenchmarkContent(TypedDict, total=False):
    id: str
    source_id: str
    account_id: str
    platform: Platform
    platform_content_id: str
    url: str
    title: str
    caption: str
    published_at: str
    crawled_at: str
    metrics: dict[str, int]
    hashtags: list[str]
    topics: list[str]
    comments_summary: str
    raw_source_url: str
    evidence_state: EvidenceState
    trace: Trace


class Transcript(TypedDict, total=False):
    id: str
    content_id: str
    status: TranscriptStatus
    provider: str
    language: str
    text: str
    segments_path: str
    error: str
    trace: Trace


class TopicCandidate(TypedDict, total=False):
    id: str
    content_id: str
    summary: str
    hook: str
    title_formula: str
    structure: str
    pain: str
    reusable_angle: str
    evidence_state: EvidenceState
    card_fields: dict[str, str]
    topic_pool_supplement: dict[str, str]
    trace: Trace


class RAGDocument(TypedDict, total=False):
    id: str
    source_object_id: str
    source_object_type: str
    text: str
    metadata: dict[str, str]
    trace: Trace


class SourceHealth(TypedDict, total=False):
    id: str
    source_id: str
    status: HealthStatus
    checked_at: str
    message: str
    trace: Trace


class FixtureLoop(TypedDict, total=False):
    sources: list[Source]
    accounts: list[BenchmarkAccount]
    contents: list[BenchmarkContent]
    transcripts: list[Transcript]
    topic_candidates: list[TopicCandidate]
    rag_documents: list[RAGDocument]
    source_health: list[SourceHealth]


class ContractError(ValueError):
    """Raised when a benchmark fixture violates the local contract."""


TRACE_FIELDS = ("local_id", "run_id", "observed_at")

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "sources": ("id", "platform", "name", "kind", "url", "enabled", "trace"),
    "accounts": (
        "id",
        "source_id",
        "platform",
        "handle",
        "display_name",
        "profile_url",
        "level",
        "enabled",
        "owner",
        "daily_tracking",
        "trace",
    ),
    "contents": (
        "id",
        "source_id",
        "account_id",
        "platform",
        "platform_content_id",
        "url",
        "title",
        "caption",
        "published_at",
        "crawled_at",
        "metrics",
        "hashtags",
        "evidence_state",
        "trace",
    ),
    "transcripts": ("id", "content_id", "status", "provider", "language", "trace"),
    "topic_candidates": (
        "id",
        "content_id",
        "summary",
        "hook",
        "title_formula",
        "structure",
        "pain",
        "reusable_angle",
        "evidence_state",
        "card_fields",
        "topic_pool_supplement",
        "trace",
    ),
    "rag_documents": (
        "id",
        "source_object_id",
        "source_object_type",
        "text",
        "metadata",
        "trace",
    ),
    "source_health": ("id", "source_id", "status", "checked_at", "message", "trace"),
}


def validate_fixture_loop(fixture: Mapping[str, Any]) -> None:
    """Validate the child-1 no-credential fixture loop."""

    seen_ids: set[str] = set()
    for kind, records in _record_groups(fixture):
        for record in records:
            validate_record(kind, record)
            object_id = record["id"]
            if object_id in seen_ids:
                raise ContractError(f"duplicate object id: {object_id}")
            seen_ids.add(object_id)

    source_ids = _ids(fixture, "sources")
    account_ids = _ids(fixture, "accounts")
    content_ids = _ids(fixture, "contents")

    for account in _records(fixture, "accounts"):
        _require_ref("account.source_id", account["source_id"], source_ids)
    for content in _records(fixture, "contents"):
        _require_ref("content.source_id", content["source_id"], source_ids)
        _require_ref("content.account_id", content["account_id"], account_ids)
    for transcript in _records(fixture, "transcripts"):
        _require_ref("transcript.content_id", transcript["content_id"], content_ids)
    for topic in _records(fixture, "topic_candidates"):
        _require_ref("topic.content_id", topic["content_id"], content_ids)
    for health in _records(fixture, "source_health"):
        _require_ref("source_health.source_id", health["source_id"], source_ids)
    for document in _records(fixture, "rag_documents"):
        _require_ref("rag_document.source_object_id", document["source_object_id"], seen_ids)


def validate_record(kind: str, record: Mapping[str, Any]) -> None:
    if kind not in REQUIRED_FIELDS:
        raise ContractError(f"unknown record kind: {kind}")

    missing = [field for field in REQUIRED_FIELDS[kind] if field not in record]
    if missing:
        raise ContractError(f"{kind} {record.get('id', '<missing id>')} missing: {missing}")

    object_id = record["id"]
    if not isinstance(object_id, str) or not object_id:
        raise ContractError(f"{kind} has empty object id")

    trace = record["trace"]
    if not isinstance(trace, Mapping):
        raise ContractError(f"{kind} {object_id} trace must be an object")
    for field in TRACE_FIELDS:
        if not trace.get(field):
            raise ContractError(f"{kind} {object_id} missing trace.{field}")
    if trace["local_id"] != object_id:
        raise ContractError(f"{kind} {object_id} trace.local_id must match id")


def _record_groups(fixture: Mapping[str, Any]) -> Iterable[tuple[str, list[Mapping[str, Any]]]]:
    for kind in REQUIRED_FIELDS:
        records = fixture.get(kind)
        if not isinstance(records, list):
            raise ContractError(f"{kind} must be a list")
        if not records:
            raise ContractError(f"{kind} must include at least one fixture record")
        for record in records:
            if not isinstance(record, Mapping):
                raise ContractError(f"{kind} fixture record must be an object")
        yield kind, records


def _records(fixture: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    records = fixture.get(kind)
    if not isinstance(records, list):
        raise ContractError(f"{kind} must be a list")
    return records


def _ids(fixture: Mapping[str, Any], kind: str) -> set[str]:
    return {record["id"] for record in _records(fixture, kind)}


def _require_ref(label: str, value: str, valid_ids: set[str]) -> None:
    if value not in valid_ids:
        raise ContractError(f"{label} references unknown id: {value}")
