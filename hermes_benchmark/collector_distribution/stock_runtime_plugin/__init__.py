"""Hermes plugin entry point for the collector stock_runtime toolset."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from hermes_benchmark.stock_runtime import (
    STOCK_TOOL_SCHEMAS,
    settings_from_hermes_config,
    stock_build_internal_digest,
    stock_healthcheck,
    stock_read_analysis_package,
    stock_record_analysis_result,
    stock_record_feedback,
    stock_run_daily,
    stock_validate_config,
)


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="stock_validate_config",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_validate_config"],
        handler=_handler(lambda _args: stock_validate_config(settings_from_hermes_config())),
        description=STOCK_TOOL_SCHEMAS["stock_validate_config"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_healthcheck",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_healthcheck"],
        handler=_handler(lambda _args: stock_healthcheck(settings_from_hermes_config())),
        description=STOCK_TOOL_SCHEMAS["stock_healthcheck"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_run_daily",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_run_daily"],
        handler=_handler(lambda args: stock_run_daily(settings_from_hermes_config(), str(args.get("date") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_run_daily"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_read_analysis_package",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_read_analysis_package"],
        handler=_handler(lambda args: stock_read_analysis_package(settings_from_hermes_config(), str(args.get("package_ref") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_read_analysis_package"]["description"],
        emoji="📦",
    )
    ctx.register_tool(
        name="stock_record_analysis_result",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_record_analysis_result"],
        handler=_handler(
            lambda args: stock_record_analysis_result(
                settings_from_hermes_config(),
                package_ref=str(args.get("package_ref") or ""),
                content_id=str(args.get("content_id") or ""),
                status=str(args.get("status") or ""),
                analysis=args.get("analysis") if isinstance(args.get("analysis"), dict) else {},
            )
        ),
        description=STOCK_TOOL_SCHEMAS["stock_record_analysis_result"]["description"],
        emoji="🧾",
    )
    ctx.register_tool(
        name="stock_build_internal_digest",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_build_internal_digest"],
        handler=_handler(lambda args: stock_build_internal_digest(settings_from_hermes_config(), str(args.get("run_id") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_build_internal_digest"]["description"],
        emoji="📰",
    )
    ctx.register_tool(
        name="stock_record_feedback",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_record_feedback"],
        handler=_handler(
            lambda args: stock_record_feedback(
                settings_from_hermes_config(),
                run_id=str(args.get("run_id") or ""),
                content_id=str(args.get("content_id") or ""),
                analysis_result_id=str(args.get("analysis_result_id") or ""),
                decision=str(args.get("decision") or ""),
                actor_ref=str(args.get("actor_ref") or ""),
                source_message_ref=str(args.get("source_message_ref") or ""),
                reason_code=str(args.get("reason_code") or ""),
            )
        ),
        description=STOCK_TOOL_SCHEMAS["stock_record_feedback"]["description"],
        emoji="✅",
    )


def _handler(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[..., str]:
    def handle(args: dict[str, Any], **_kwargs: Any) -> str:
        return json.dumps(fn(args), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return handle
