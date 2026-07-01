"""Feishu table mapping and dry-run operation planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal, NamedTuple, TypedDict

from .account_registry import benchmark_accounts, registry_source_health
from .contracts import BenchmarkAccount, BenchmarkContent, ContractError, SourceHealth, Transcript
from .decomposition import HermesDecompositionOutput, mock_hermes_output
from .mediacrawler_import import import_mediacrawler_rows, load_mediacrawler_fixture
from .transcript_pipeline import transcript_fixture

RUN_ID = "run-child6-feishu-dry-run-2026-07-01"
SCHEMA_VERSION = "parent1.v1"
TRANSPORT = "lark-cli"

FieldOwner = Literal["script_generated", "hermes_managed", "manual"]
OperationKind = Literal["create", "update"]


class TableConfig(NamedTuple):
    key: str
    number: int
    name: str
    visibility: Literal["internal", "external_card"]
    owners: Mapping[str, FieldOwner]


class FeishuRow(TypedDict):
    table_key: str
    object_id: str
    fields: dict[str, Any]


class DryRunOperation(TypedDict):
    target_table: str
    target_table_name: str
    operation: OperationKind
    object_id: str
    field_mapping: dict[str, Any]
    idempotency_key: str


class DryRunPlan(TypedDict):
    transport: str
    run_id: str
    operations: list[DryRunOperation]
    skipped_noop: int
    diagnostics: list[str]


TABLES: dict[str, TableConfig] = {
    "table_1": TableConfig(
        "table_1",
        1,
        "信息源表",
        "internal",
        {
            "schema_version": "script_generated",
            "信息源 ID": "script_generated",
            "最近抓取时间": "script_generated",
            "最近成功时间": "script_generated",
            "健康状态": "script_generated",
            "信息源名称": "manual",
            "信息源类型": "manual",
            "源地址": "manual",
            "等级": "manual",
            "是否启用": "manual",
        },
    ),
    "table_2": TableConfig(
        "table_2",
        2,
        "数据源健康表",
        "internal",
        {
            "schema_version": "script_generated",
            "运行记录 ID": "script_generated",
            "信息源 ID": "script_generated",
            "任务类型": "script_generated",
            "开始时间": "script_generated",
            "结束时间": "script_generated",
            "状态": "script_generated",
            "失败原因": "script_generated",
            "radar-ops 摘要": "hermes_managed",
            "是否已处理": "manual",
            "处理备注": "manual",
        },
    ),
    "table_3": TableConfig(
        "table_3",
        3,
        "对标账号表",
        "internal",
        {
            "schema_version": "script_generated",
            "对标账号 ID": "script_generated",
            "最近抓取时间": "script_generated",
            "最近新增内容数": "script_generated",
            "最近疑似爆款数": "script_generated",
            "账号状态": "hermes_managed",
            "Hermes 调整建议": "hermes_managed",
            "账号名称": "manual",
            "平台": "manual",
            "主页链接": "manual",
            "等级": "manual",
            "是否启用": "manual",
        },
    ),
    "table_4": TableConfig(
        "table_4",
        4,
        "对标内容拆解表",
        "internal",
        {
            "schema_version": "script_generated",
            "内容 ID": "script_generated",
            "对标账号": "script_generated",
            "平台": "script_generated",
            "原始链接": "script_generated",
            "发布时间": "script_generated",
            "抓取时间": "script_generated",
            "标题 / 文案": "script_generated",
            "视频字幕状态": "script_generated",
            "点赞数": "script_generated",
            "评论数": "script_generated",
            "转发数": "script_generated",
            "证据状态": "script_generated",
            "是否达到粗爆款阈值": "script_generated",
            "卡片 ID": "script_generated",
            "Whisper 转录摘要": "hermes_managed",
            "内容一句话总结": "hermes_managed",
            "开头钩子": "hermes_managed",
            "内容结构": "hermes_managed",
            "可复用角度": "hermes_managed",
            "沉淀建议": "hermes_managed",
            "人工判断": "manual",
            "是否进入素材沉淀": "manual",
            "备注": "manual",
        },
    ),
    "table_6": TableConfig(
        "table_6",
        6,
        "素材沉淀表 / RAG 候选表",
        "internal",
        {
            "schema_version": "script_generated",
            "素材 ID": "script_generated",
            "素材类型": "script_generated",
            "关联对标内容": "script_generated",
            "证据链接": "script_generated",
            "证据链数量": "script_generated",
            "标题": "hermes_managed",
            "摘要": "hermes_managed",
            "核心价值": "hermes_managed",
            "可用角度": "hermes_managed",
            "沉淀等级": "hermes_managed",
            "是否进入 RAG": "manual",
            "人工确认状态": "manual",
            "拒绝沉淀原因": "manual",
        },
    ),
    "table_7": TableConfig(
        "table_7",
        7,
        "选题池表",
        "internal",
        {
            "schema_version": "script_generated",
            "选题 ID": "script_generated",
            "选题来源": "script_generated",
            "关联对标内容": "script_generated",
            "关联素材": "script_generated",
            "推荐理由": "hermes_managed",
            "核心钩子": "hermes_managed",
            "简要大纲": "hermes_managed",
            "证据链": "hermes_managed",
            "Hermes 置信度": "hermes_managed",
            "选题标题": "manual",
            "人工状态": "manual",
            "拒绝原因": "manual",
            "修改说明": "manual",
        },
    ),
    "table_9": TableConfig(
        "table_9",
        9,
        "卡片表",
        "external_card",
        {
            "schema_version": "script_generated",
            "卡片 ID": "script_generated",
            "卡片类型": "script_generated",
            "来源表": "script_generated",
            "来源记录 ID": "script_generated",
            "证据链接": "script_generated",
            "发布时间": "script_generated",
            "卡片标题": "hermes_managed",
            "卡片摘要": "hermes_managed",
            "为什么值得看": "hermes_managed",
            "可行动作建议": "hermes_managed",
            "发布范围": "manual",
            "卡片状态": "manual",
        },
    ),
}

TABLE9_EXTERNAL_FIELDS = frozenset(
    {
        "schema_version",
        "卡片 ID",
        "卡片类型",
        "来源表",
        "来源记录 ID",
        "卡片标题",
        "卡片摘要",
        "为什么值得看",
        "可行动作建议",
        "证据链接",
        "发布时间",
    }
)


def parent1_feishu_dry_run(
    existing_records: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> DryRunPlan:
    accounts = benchmark_accounts()
    import_result = import_mediacrawler_rows(load_mediacrawler_fixture(), accounts)
    contents = import_result["contents"]
    health = registry_source_health(accounts) + import_result["source_health"]
    transcripts = [transcript_fixture(content) for content in contents]
    hermes_outputs = [mock_hermes_output(content, transcript) for content, transcript in zip(contents, transcripts)]
    rows = parent1_feishu_rows(accounts, contents, transcripts, hermes_outputs, health)
    return build_dry_run_plan(rows, existing_records)


def parent1_feishu_rows(
    accounts: Iterable[BenchmarkAccount],
    contents: Iterable[BenchmarkContent],
    transcripts: Iterable[Transcript],
    hermes_outputs: Iterable[HermesDecompositionOutput],
    source_health: Iterable[SourceHealth],
) -> list[FeishuRow]:
    account_records = list(accounts)
    content_records = list(contents)
    transcript_records = {record["content_id"]: record for record in transcripts}
    hermes_records = {record["content_id"]: record for record in hermes_outputs}
    rows: list[FeishuRow] = []
    rows.extend(_source_rows(account_records))
    rows.extend(_health_rows(source_health))
    rows.extend(_account_rows(account_records))
    for content in content_records:
        transcript = transcript_records.get(content["id"])
        hermes = hermes_records.get(content["id"])
        rows.append(_content_row(content, transcript, hermes))
        if hermes:
            rows.append(_material_row(content, hermes))
            rows.append(_topic_row(content, hermes))
            rows.append(_card_row(hermes))
    return rows


def build_dry_run_plan(
    rows: Iterable[FeishuRow],
    existing_records: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> DryRunPlan:
    existing = existing_records or {}
    operations: list[DryRunOperation] = []
    diagnostics: list[str] = []
    skipped_noop = 0
    seen_keys: set[str] = set()

    for row in rows:
        config = table_config(row["table_key"])
        _validate_writable_fields(config, row["fields"])
        current = existing.get((row["table_key"], row["object_id"]))
        if current is not None and dict(current) == row["fields"]:
            skipped_noop += 1
            continue
        operation: OperationKind = "update" if current is not None else "create"
        item = _operation(config, operation, row)
        if item["idempotency_key"] in seen_keys:
            raise ContractError(f"duplicate Feishu idempotency key: {item['idempotency_key']}")
        seen_keys.add(item["idempotency_key"])
        operations.append(item)

    if not any(operation["target_table"] == "table_9" for operation in operations):
        diagnostics.append("table_9 has no card operations")
    return {"transport": TRANSPORT, "run_id": RUN_ID, "operations": operations, "skipped_noop": skipped_noop, "diagnostics": diagnostics}


def validate_live_readiness(
    table_ids: Mapping[str, str],
    credentials: Mapping[str, str],
) -> None:
    missing_tables = [key for key in TABLES if not table_ids.get(key)]
    if missing_tables:
        raise ContractError(f"missing Feishu table ids before live mode: {missing_tables}")
    missing_credentials = [key for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET") if not credentials.get(key)]
    if missing_credentials:
        raise ContractError(f"missing Feishu credentials before live mode: {missing_credentials}")


def table_config(table_key: str) -> TableConfig:
    try:
        return TABLES[table_key]
    except KeyError as exc:
        raise ContractError(f"unsupported parent 1 Feishu table: {table_key}") from exc


def _source_rows(accounts: list[BenchmarkAccount]) -> list[FeishuRow]:
    rows = []
    for source_id in sorted({account["source_id"] for account in accounts}):
        rows.append(
            {
                "table_key": "table_1",
                "object_id": source_id,
                "fields": {
                    "schema_version": SCHEMA_VERSION,
                    "信息源 ID": source_id,
                    "最近抓取时间": _latest_observed_at(accounts, source_id),
                    "最近成功时间": _latest_observed_at(accounts, source_id),
                    "健康状态": "正常",
                },
            }
        )
    return rows


def _health_rows(records: Iterable[SourceHealth]) -> list[FeishuRow]:
    return [
        {
            "table_key": "table_2",
            "object_id": record["id"],
            "fields": {
                "schema_version": SCHEMA_VERSION,
                "运行记录 ID": record["id"],
                "信息源 ID": record["source_id"],
                "任务类型": "飞书同步" if "feishu" in record["id"] else "对标采集",
                "开始时间": record["checked_at"],
                "结束时间": record["checked_at"],
                "状态": _health_status(record["status"]),
                "失败原因": "" if record["status"] == "ok" else record["message"],
            },
        }
        for record in records
    ]


def _account_rows(accounts: Iterable[BenchmarkAccount]) -> list[FeishuRow]:
    return [
        {
            "table_key": "table_3",
            "object_id": account["id"],
            "fields": {
                "schema_version": SCHEMA_VERSION,
                "对标账号 ID": account["id"],
                "最近抓取时间": account["trace"]["observed_at"],
                "最近新增内容数": 0,
                "最近疑似爆款数": 0,
                "账号状态": "正常" if account["enabled"] else "待确认",
                "Hermes 调整建议": "placeholder registry; no automatic level changes",
            },
        }
        for account in accounts
    ]


def _content_row(
    content: BenchmarkContent,
    transcript: Transcript | None,
    hermes: HermesDecompositionOutput | None,
) -> FeishuRow:
    metrics = content.get("metrics", {})
    card_id = hermes["card"]["id"] if hermes else ""
    return {
        "table_key": "table_4",
        "object_id": content["id"],
        "fields": {
            "schema_version": SCHEMA_VERSION,
            "内容 ID": content["id"],
            "对标账号": content["account_id"],
            "平台": content["platform"],
            "原始链接": content["url"],
            "发布时间": content["published_at"],
            "抓取时间": content["crawled_at"],
            "标题 / 文案": content["caption"] or content["title"],
            "视频字幕状态": _transcript_status(transcript),
            "点赞数": metrics.get("likes", 0),
            "评论数": metrics.get("comments", 0),
            "转发数": metrics.get("shares", 0),
            "证据状态": _evidence_label(content["evidence_state"]),
            "是否达到粗爆款阈值": metrics.get("likes", 0) > 1000 or metrics.get("comments", 0) > 200,
            "卡片 ID": card_id,
            **(_hermes_content_fields(hermes) if hermes else {}),
        },
    }


def _material_row(content: BenchmarkContent, hermes: HermesDecompositionOutput) -> FeishuRow:
    material_id = f"material-{content['id'].removeprefix('content-')}"
    return {
        "table_key": "table_6",
        "object_id": material_id,
        "fields": {
            "schema_version": SCHEMA_VERSION,
            "素材 ID": material_id,
            "素材类型": "对标内容",
            "关联对标内容": content["id"],
            "证据链接": content["url"],
            "证据链数量": 1,
            "标题": hermes["topic_one_liner"],
            "摘要": hermes["summary"],
            "核心价值": hermes["sedimentation_suggestion"],
            "可用角度": hermes["reusable_angle"],
            "沉淀等级": "B" if hermes["evidence_state"] == "sufficient" else "C",
        },
    }


def _topic_row(content: BenchmarkContent, hermes: HermesDecompositionOutput) -> FeishuRow:
    supplement = hermes["topic_pool_supplement"]
    topic_id = f"topic-{content['id'].removeprefix('content-')}"
    return {
        "table_key": "table_7",
        "object_id": topic_id,
        "fields": {
            "schema_version": SCHEMA_VERSION,
            "选题 ID": topic_id,
            "选题来源": "对标",
            "关联对标内容": content["id"],
            "关联素材": f"material-{content['id'].removeprefix('content-')}",
            "推荐理由": supplement["rationale"],
            "核心钩子": hermes["hook"],
            "简要大纲": hermes["structure"],
            "证据链": f"{content['id']} -> {hermes['trace_id']}",
            "Hermes 置信度": 70 if hermes["evidence_state"] == "sufficient" else 40,
        },
    }


def _card_row(hermes: HermesDecompositionOutput) -> FeishuRow:
    card = hermes["card"]
    fields = {
        "schema_version": SCHEMA_VERSION,
        "卡片 ID": card["id"],
        "卡片类型": "对标拆解",
        "来源表": "表 4",
        "来源记录 ID": hermes["content_id"],
        "卡片标题": card["headline"],
        "卡片摘要": card["summary"],
        "为什么值得看": hermes["reusable_angle"],
        "可行动作建议": "讨论 / 作为选题候选 / 暂缓",
        "证据链接": card["source_url"],
        "发布时间": hermes["trace"]["observed_at"],
    }
    extra = set(fields) - TABLE9_EXTERNAL_FIELDS
    if extra:
        raise ContractError(f"table_9 card fields are not external-safe: {sorted(extra)}")
    return {"table_key": "table_9", "object_id": card["id"], "fields": fields}


def _hermes_content_fields(hermes: HermesDecompositionOutput) -> dict[str, Any]:
    return {
        "Whisper 转录摘要": hermes["summary"],
        "内容一句话总结": hermes["topic_one_liner"],
        "开头钩子": hermes["hook"],
        "内容结构": hermes["structure"],
        "可复用角度": hermes["reusable_angle"],
        "沉淀建议": hermes["sedimentation_suggestion"],
    }


def _operation(config: TableConfig, operation: OperationKind, row: FeishuRow) -> DryRunOperation:
    return {
        "target_table": config.key,
        "target_table_name": config.name,
        "operation": operation,
        "object_id": row["object_id"],
        "field_mapping": row["fields"],
        "idempotency_key": _idempotency_key(config.key, operation, row["object_id"], row["fields"]),
    }


def _validate_writable_fields(config: TableConfig, fields: Mapping[str, Any]) -> None:
    unknown = sorted(set(fields) - set(config.owners))
    if unknown:
        raise ContractError(f"{config.key} has fields not in mapping: {unknown}")
    manual = sorted(field for field in fields if config.owners[field] == "manual")
    if manual:
        raise ContractError(f"{config.key} dry-run cannot write manual fields: {manual}")
    if config.key == "table_9" and set(fields) - TABLE9_EXTERNAL_FIELDS:
        raise ContractError("table_9 dry-run must use external-safe field allowlist")


def _idempotency_key(table_key: str, operation: str, object_id: str, fields: Mapping[str, Any]) -> str:
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{RUN_ID}:{table_key}:{operation}:{object_id}:{digest}"


def _latest_observed_at(accounts: Iterable[BenchmarkAccount], source_id: str) -> str:
    values = sorted(account["trace"]["observed_at"] for account in accounts if account["source_id"] == source_id)
    return values[-1] if values else ""


def _health_status(status: str) -> str:
    return {"ok": "成功", "warning": "部分成功", "failed": "失败"}.get(status, "部分成功")


def _transcript_status(transcript: Transcript | None) -> str:
    if transcript is None:
        return "待转录"
    return {"done": "已转录", "failed": "转录失败", "pending": "待转录", "not_applicable": "无视频"}.get(transcript["status"], "待转录")


def _evidence_label(state: str) -> str:
    return "证据完整" if state == "sufficient" else "证据不足"


def _self_check() -> None:
    plan = parent1_feishu_dry_run()
    operations = plan["operations"]
    assert operations
    assert plan["transport"] == "lark-cli"
    assert {operation["target_table"] for operation in operations} == {"table_1", "table_2", "table_3", "table_4", "table_6", "table_7", "table_9"}
    assert len({operation["idempotency_key"] for operation in operations}) == len(operations)

    for operation in operations:
        config = table_config(operation["target_table"])
        _validate_writable_fields(config, operation["field_mapping"])
        assert operation["operation"] == "create"
        if operation["target_table"] == "table_9":
            assert set(operation["field_mapping"]) <= TABLE9_EXTERNAL_FIELDS
            assert "Whisper 转录摘要" not in operation["field_mapping"]

    existing = {(operation["target_table"], operation["object_id"]): operation["field_mapping"] for operation in operations}
    noops = parent1_feishu_dry_run(existing)
    assert not noops["operations"]
    assert noops["skipped_noop"] == len(operations)

    one_changed = dict(existing)
    first_key = (operations[0]["target_table"], operations[0]["object_id"])
    one_changed[first_key] = {**one_changed[first_key], "schema_version": "old"}
    updates = parent1_feishu_dry_run(one_changed)
    assert len(updates["operations"]) == 1
    assert updates["operations"][0]["operation"] == "update"

    rows = parent1_feishu_rows(
        benchmark_accounts(),
        import_mediacrawler_rows(load_mediacrawler_fixture())["contents"],
        [],
        [],
        registry_source_health(),
    )
    _expect_contract_error("duplicate Feishu idempotency key", lambda: build_dry_run_plan([rows[0], rows[0]]))
    _expect_contract_error("dry-run cannot write manual fields", lambda: build_dry_run_plan([{"table_key": "table_3", "object_id": "bad", "fields": {"账号名称": "manual"}}]))
    _expect_contract_error("unsupported parent 1 Feishu table", lambda: table_config("table_5"))
    _expect_contract_error("missing Feishu table ids", lambda: validate_live_readiness({}, {"FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"}))
    _expect_contract_error("missing Feishu credentials", lambda: validate_live_readiness({key: key for key in TABLES}, {}))


def _expect_contract_error(fragment: str, action: Any) -> None:
    try:
        action()
    except ContractError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r} in {exc!r}") from exc
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


if __name__ == "__main__":
    _self_check()
    print("Feishu dry-run plan ok")
