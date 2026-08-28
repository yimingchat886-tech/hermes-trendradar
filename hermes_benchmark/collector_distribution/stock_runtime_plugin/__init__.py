"""Hermes plugin entry point for the collector stock_runtime toolset."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_OPERATOR_REPO_ROOT = Path("/home/jym/workspace/Hermes trendradar")
_OPERATOR_PACKAGE = "hermes_benchmark"


def _default_repo_root() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / _OPERATOR_PACKAGE / "__init__.py").is_file():
        return source_root
    return _OPERATOR_REPO_ROOT


def _bootstrap_repo_source(repo_root: Path | None = None) -> Path:
    """Make the fixed operator-owned repo source importable, or fail closed."""
    root = Path(repo_root or _default_repo_root())
    package_root = root / _OPERATOR_PACKAGE
    if not root.is_dir() or not package_root.is_dir() or not (package_root / "__init__.py").is_file():
        raise ModuleNotFoundError(f"No module named '{_OPERATOR_PACKAGE}' from fixed repo source")

    root_text = str(root)
    sys.path[:] = [path for path in sys.path if path != root_text]
    sys.path.insert(0, root_text)
    return root


_bootstrap_repo_source()

from hermes_benchmark.stock_runtime import (  # noqa: E402 - requires fixed sys.path bootstrap
    STOCK_TOOL_SCHEMAS,
    StockRuntimeError,
    settings_from_hermes_config,
    stock_build_internal_digest,
    stock_healthcheck,
    stock_read_analysis_package,
    stock_record_analysis_result,
    stock_record_feedback,
    stock_run_content_pipeline,
    stock_run_daily,
    stock_validate_config,
)


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="stock_validate_config",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_validate_config"],
        handler=_handler("stock_validate_config", lambda _args: stock_validate_config(settings_from_hermes_config())),
        description=STOCK_TOOL_SCHEMAS["stock_validate_config"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_healthcheck",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_healthcheck"],
        handler=_handler("stock_healthcheck", lambda _args: stock_healthcheck(settings_from_hermes_config())),
        description=STOCK_TOOL_SCHEMAS["stock_healthcheck"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_run_daily",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_run_daily"],
        handler=_handler("stock_run_daily", lambda args: stock_run_daily(settings_from_hermes_config(), str(args.get("date") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_run_daily"]["description"],
        emoji="📈",
    )
    ctx.register_tool(
        name="stock_run_content_pipeline",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_run_content_pipeline"],
        handler=_handler(
            "stock_run_content_pipeline",
            lambda args: stock_run_content_pipeline(
                settings_from_hermes_config(),
                account_id=str(args.get("account_id") or ""),
                copy_mode=str(args.get("copy_mode") or ""),
                content_ids=args.get("content_ids"),
                published_since=args.get("published_since"),
                max_items=args.get("max_items"),
                all_visible=args.get("all_visible"),
            ),
        ),
        description=STOCK_TOOL_SCHEMAS["stock_run_content_pipeline"]["description"],
        emoji="🎬",
    )
    ctx.register_tool(
        name="stock_read_analysis_package",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_read_analysis_package"],
        handler=_handler("stock_read_analysis_package", lambda args: stock_read_analysis_package(settings_from_hermes_config(), str(args.get("package_ref") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_read_analysis_package"]["description"],
        emoji="📦",
    )
    ctx.register_tool(
        name="stock_record_analysis_result",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_record_analysis_result"],
        handler=_handler(
            "stock_record_analysis_result",
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
        handler=_handler("stock_build_internal_digest", lambda args: stock_build_internal_digest(settings_from_hermes_config(), str(args.get("run_id") or ""))),
        description=STOCK_TOOL_SCHEMAS["stock_build_internal_digest"]["description"],
        emoji="📰",
    )
    ctx.register_tool(
        name="stock_record_feedback",
        toolset="stock_runtime",
        schema=STOCK_TOOL_SCHEMAS["stock_record_feedback"],
        handler=_handler(
            "stock_record_feedback",
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


def _handler(tool: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[..., str]:
    def handle(args: dict[str, Any], **_kwargs: Any) -> str:
        try:
            result = fn(args)
            return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except StockRuntimeError as exc:
            result = _closed_error(tool, exc.code, retryable=exc.retryable)
        except Exception:
            result = _closed_error(tool, "stock_runtime_handler_error")
        return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return handle


def _closed_error(tool: str, code: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "data": None,
        "error": {"code": code, "message": "stock_runtime failed closed"},
        "retryable": retryable,
    }
