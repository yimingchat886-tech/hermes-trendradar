"""Daily benchmark digest and ops alert objects."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from .account_registry import benchmark_accounts, daily_tracking_plan, registry_source_health
from .contracts import BenchmarkAccount, BenchmarkContent, SourceHealth, Transcript
from .decomposition import HermesDecompositionOutput, mock_hermes_output
from .feishu_dry_run import DryRunPlan, parent1_feishu_dry_run
from .mediacrawler_import import IMPORT_SOURCE_ID, import_mediacrawler_rows, load_mediacrawler_fixture
from .transcript_pipeline import build_transcript, transcript_fixture

RUN_ID = "run-child7-daily-digest-alerts-2026-07-01"
OBSERVED_AT = "2026-07-01T00:00:00-07:00"
LIKE_THRESHOLD = 1000
DISCUSSION_THRESHOLD = 200

PerformanceLevel = Literal["normal", "high_like", "high_discussion", "strong_hit"]
AlertCategory = Literal["crawler", "import", "transcript", "feishu", "abnormal_count"]


class BenchmarkDailyDigest(TypedDict, total=False):
    id: str
    target_agent: str
    date: str
    summary: dict[str, int]
    account_updates: list[dict[str, Any]]
    performance_candidates: dict[str, list[dict[str, Any]]]
    evidence_insufficient_content: list[dict[str, Any]]
    trace: dict[str, str]


class OpsAlert(TypedDict, total=False):
    id: str
    target_agent: str
    notification_type: str
    category: AlertCategory
    severity: str
    reason: str
    source_id: str
    source_run_id: str
    source_url: str
    content_id: str
    trace: dict[str, str]


class DailyDigestResult(TypedDict):
    digest: BenchmarkDailyDigest
    alerts: list[OpsAlert]


def parent1_daily_digest_alerts() -> DailyDigestResult:
    accounts = benchmark_accounts()
    import_result = import_mediacrawler_rows(load_mediacrawler_fixture(), accounts)
    contents = import_result["contents"]
    health = registry_source_health(accounts) + import_result["source_health"]
    transcripts = [transcript_fixture(content) for content in contents]
    hermes_outputs = [mock_hermes_output(content, transcript) for content, transcript in zip(contents, transcripts)]
    feishu_plan = parent1_feishu_dry_run()
    return build_daily_digest_alerts(accounts, contents, health, transcripts, hermes_outputs, feishu_plan)


def build_daily_digest_alerts(
    accounts: Iterable[BenchmarkAccount],
    contents: Iterable[BenchmarkContent],
    source_health: Iterable[SourceHealth],
    transcripts: Iterable[Transcript] = (),
    hermes_outputs: Iterable[HermesDecompositionOutput] = (),
    feishu_plan: DryRunPlan | None = None,
) -> DailyDigestResult:
    digest = build_daily_digest(accounts, contents, hermes_outputs, feishu_plan)
    alerts = build_ops_alerts(source_health, transcripts, feishu_plan, digest)
    return {"digest": digest, "alerts": alerts}


def build_daily_digest(
    accounts: Iterable[BenchmarkAccount],
    contents: Iterable[BenchmarkContent],
    hermes_outputs: Iterable[HermesDecompositionOutput] = (),
    feishu_plan: DryRunPlan | None = None,
) -> BenchmarkDailyDigest:
    account_records = list(accounts)
    content_records = list(contents)
    daily_accounts = daily_tracking_plan(account_records)
    content_counts = Counter(content["account_id"] for content in content_records)
    accounts_by_id = {account["id"]: account for account in account_records}
    hermes_by_content = {output["content_id"]: output for output in hermes_outputs}

    candidates: dict[str, list[dict[str, Any]]] = {"high_like": [], "high_discussion": [], "strong_hit": []}
    evidence_insufficient: list[dict[str, Any]] = []
    for content in content_records:
        level = performance_level(content)
        if level != "normal":
            candidates[level].append(_content_brief(content, level, hermes_by_content.get(content["id"])))
        if content.get("evidence_state") == "insufficient":
            evidence_insufficient.append(_content_brief(content, level, hermes_by_content.get(content["id"])))

    account_updates = [
        _account_update(accounts_by_id[item["account_id"]], content_counts[item["account_id"]])
        for item in daily_accounts
        if content_counts[item["account_id"]]
    ]
    summary = {
        "tracked_account_count": len(account_records),
        "daily_tracking_account_count": len(daily_accounts),
        "accounts_with_updates": len(account_updates),
        "new_content_count": len(content_records),
        "evidence_insufficient_count": len(evidence_insufficient),
        "high_like_count": len(candidates["high_like"]),
        "high_discussion_count": len(candidates["high_discussion"]),
        "strong_hit_count": len(candidates["strong_hit"]),
        "hermes_output_count": len(hermes_by_content),
        "feishu_operation_count": len(feishu_plan["operations"]) if feishu_plan else 0,
    }
    digest_id = "digest-parent1-benchmark-daily"
    return {
        "id": digest_id,
        "target_agent": "trend-chief",
        "date": OBSERVED_AT[:10],
        "summary": summary,
        "account_updates": account_updates,
        "performance_candidates": candidates,
        "evidence_insufficient_content": evidence_insufficient,
        "trace": _trace(digest_id, "parent1", source_run_id=RUN_ID),
    }


def build_ops_alerts(
    source_health: Iterable[SourceHealth],
    transcripts: Iterable[Transcript] = (),
    feishu_plan: DryRunPlan | None = None,
    digest: BenchmarkDailyDigest | None = None,
) -> list[OpsAlert]:
    alerts: list[OpsAlert] = []
    for health in source_health:
        if health["status"] != "ok":
            alerts.append(_health_alert(health))
    for transcript in transcripts:
        if transcript["status"] == "failed":
            alerts.append(_transcript_alert(transcript))
    if feishu_plan:
        for index, message in enumerate(feishu_plan["diagnostics"], start=1):
            alerts.append(_feishu_alert(message, feishu_plan["run_id"], index))
    if digest and digest["summary"]["daily_tracking_account_count"] and not digest["summary"]["new_content_count"]:
        alerts.append(_abnormal_count_alert(digest))
    return alerts


def performance_level(content: Mapping[str, Any]) -> PerformanceLevel:
    metrics = content.get("metrics", {})
    likes = int(metrics.get("likes", 0))
    comments = int(metrics.get("comments", 0))
    if likes > LIKE_THRESHOLD and comments > DISCUSSION_THRESHOLD:
        return "strong_hit"
    if comments > DISCUSSION_THRESHOLD:
        return "high_discussion"
    if likes > LIKE_THRESHOLD:
        return "high_like"
    return "normal"


def _account_update(account: BenchmarkAccount, new_content_count: int) -> dict[str, Any]:
    return {
        "account_id": account["id"],
        "source_id": account["source_id"],
        "display_name": account["display_name"],
        "platform": account["platform"],
        "new_content_count": new_content_count,
        "trace": _trace(f"account-update-{account['id']}", account["source_id"], account_id=account["id"]),
    }


def _content_brief(
    content: BenchmarkContent,
    level: PerformanceLevel,
    hermes: HermesDecompositionOutput | None,
) -> dict[str, Any]:
    trace = content.get("trace", {})
    brief = {
        "content_id": content["id"],
        "account_id": content["account_id"],
        "source_id": content["source_id"],
        "source_run_id": trace.get("run_id", ""),
        "source_url": content.get("raw_source_url") or content.get("url", ""),
        "title": content["title"],
        "performance_level": level,
        "metrics": content.get("metrics", {}),
        "evidence_state": content["evidence_state"],
        "trace": _trace(
            f"digest-content-{content['id']}",
            content["source_id"],
            content_id=content["id"],
            account_id=content["account_id"],
            source_run_id=trace.get("run_id", ""),
            source_url=content.get("raw_source_url") or content.get("url", ""),
        ),
    }
    if hermes:
        brief["hermes_output_id"] = hermes["id"]
        brief["card_id"] = hermes["card"]["id"]
    return brief


def _health_alert(health: SourceHealth) -> OpsAlert:
    trace = health.get("trace", {})
    category = _health_category(health)
    alert_id = f"alert-{category}-{health['id']}"
    return {
        "id": alert_id,
        "target_agent": "radar-ops",
        "notification_type": "ops_exception",
        "category": category,
        "severity": health["status"],
        "reason": health["message"],
        "source_id": health["source_id"],
        "source_run_id": trace.get("run_id", ""),
        "source_url": trace.get("source_url", ""),
        "trace": _trace(alert_id, health["source_id"], source_run_id=trace.get("run_id", "")),
    }


def _transcript_alert(transcript: Transcript) -> OpsAlert:
    trace = transcript.get("trace", {})
    alert_id = f"alert-transcript-{transcript['id']}"
    return {
        "id": alert_id,
        "target_agent": "radar-ops",
        "notification_type": "ops_exception",
        "category": "transcript",
        "severity": "failed",
        "reason": transcript.get("error", "transcript failed"),
        "source_id": trace.get("source_id", ""),
        "source_run_id": trace.get("run_id", ""),
        "source_url": trace.get("source_url", ""),
        "content_id": transcript["content_id"],
        "trace": _trace(
            alert_id,
            trace.get("source_id", ""),
            content_id=transcript["content_id"],
            source_run_id=trace.get("run_id", ""),
        ),
    }


def _feishu_alert(message: str, source_run_id: str, index: int) -> OpsAlert:
    alert_id = f"alert-feishu-{source_run_id}-{index}"
    return {
        "id": alert_id,
        "target_agent": "radar-ops",
        "notification_type": "ops_exception",
        "category": "feishu",
        "severity": "warning",
        "reason": message,
        "source_id": "feishu-dry-run",
        "source_run_id": source_run_id,
        "trace": _trace(alert_id, "feishu-dry-run", source_run_id=source_run_id),
    }


def _abnormal_count_alert(digest: BenchmarkDailyDigest) -> OpsAlert:
    alert_id = "alert-abnormal-count-no-new-benchmark-content"
    return {
        "id": alert_id,
        "target_agent": "radar-ops",
        "notification_type": "ops_exception",
        "category": "abnormal_count",
        "severity": "warning",
        "reason": "daily benchmark tracking produced zero new content",
        "source_id": "parent1-benchmark-digest",
        "source_run_id": RUN_ID,
        "trace": _trace(alert_id, "parent1-benchmark-digest", source_run_id=RUN_ID),
    }


def _health_category(health: SourceHealth) -> AlertCategory:
    text = f"{health['id']} {health['message']}".lower()
    if "feishu" in text:
        return "feishu"
    if "transcript" in text or "funasr" in text or "asr" in text or "whisper" in text:
        return "transcript"
    if health["source_id"] == IMPORT_SOURCE_ID or "import" in text or "row" in text:
        return "import"
    return "crawler"


def _trace(
    local_id: str,
    source_id: str,
    *,
    content_id: str = "",
    account_id: str = "",
    source_run_id: str = "",
    source_url: str = "",
) -> dict[str, str]:
    trace = {"local_id": local_id, "run_id": RUN_ID, "observed_at": OBSERVED_AT, "source_id": source_id}
    if content_id:
        trace["content_id"] = content_id
    if account_id:
        trace["account_id"] = account_id
    if source_run_id:
        trace["source_run_id"] = source_run_id
    if source_url:
        trace["source_url"] = source_url
    return trace


def _content_for_threshold(likes: int, comments: int) -> dict[str, Any]:
    return {"metrics": {"likes": likes, "comments": comments}}


def _self_check() -> None:
    result = parent1_daily_digest_alerts()
    digest = result["digest"]
    alerts = result["alerts"]
    summary = digest["summary"]

    assert digest["target_agent"] == "trend-chief"
    assert summary["new_content_count"] == 2
    assert summary["evidence_insufficient_count"] == 1
    assert summary["high_like_count"] == 1
    assert summary["high_discussion_count"] == 0
    assert summary["strong_hit_count"] == 0
    assert summary["hermes_output_count"] == 2
    assert summary["feishu_operation_count"] > 0
    assert digest["performance_candidates"]["high_like"][0]["source_run_id"]
    assert digest["evidence_insufficient_content"][0]["content_id"]

    assert performance_level(_content_for_threshold(1000, 200)) == "normal"
    assert performance_level(_content_for_threshold(1001, 200)) == "high_like"
    assert performance_level(_content_for_threshold(1000, 201)) == "high_discussion"
    assert performance_level(_content_for_threshold(1001, 201)) == "strong_hit"

    assert alerts
    assert any(alert["category"] == "import" and alert["source_run_id"] for alert in alerts)
    assert all(alert["target_agent"] == "radar-ops" for alert in alerts)
    assert all(alert["notification_type"] == "ops_exception" for alert in alerts)
    assert not any("hotspot" in str(alert).lower() for alert in alerts)

    content = import_mediacrawler_rows(load_mediacrawler_fixture())["contents"][0]
    failed_transcript = build_transcript(content, status="failed", error="funasr unavailable")
    transcript_alerts = build_ops_alerts([], [failed_transcript])
    assert transcript_alerts[0]["category"] == "transcript"
    assert transcript_alerts[0]["content_id"] == content["id"]

    feishu_plan: DryRunPlan = {
        "transport": "lark-cli",
        "run_id": "run-feishu",
        "operations": [],
        "skipped_noop": 0,
        "diagnostics": ["write failed", "mapping failed"],
    }
    feishu_alerts = build_ops_alerts([], [], feishu_plan)
    assert [alert["category"] for alert in feishu_alerts] == ["feishu", "feishu"]
    assert len({alert["id"] for alert in feishu_alerts}) == 2
    assert feishu_alerts[0]["source_run_id"] == "run-feishu"

    empty_digest = build_daily_digest(benchmark_accounts(), [])
    abnormal_alerts = build_ops_alerts([], [], digest=empty_digest)
    assert abnormal_alerts[0]["category"] == "abnormal_count"


if __name__ == "__main__":
    _self_check()
    print("benchmark daily digest and ops alerts ok")
