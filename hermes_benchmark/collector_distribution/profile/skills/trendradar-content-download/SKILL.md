---
name: trendradar-content-download
description: Use stock_runtime for local TrendRadar Douyin download and transcription.
---

# TrendRadar content download

Use `stock_run_content_pipeline` when the user asks collector to collect, download, or transcribe configured Douyin accounts through the TrendRadar content pipeline.

Rules:

1. Supply only business inputs: `account_id`, exactly one scope selector (`content_ids`, `published_since`, `max_items`, or `all_visible`), and `copy_mode` (`original` or `optimized`). For selected content ids, pass the exact requested raw numeric aweme ID or canonical `content-douyin-...` token; the adapter normalizes raw numeric IDs to canonical content IDs without repairing the digits. For a visible-account sweep, use the all-visible selector.
2. When the user asks for sequential ordered execution, call `stock_run_content_pipeline` once per single video ID, wait for that structured receipt, then continue sequentially to the next ID in order. Do not batch an ordered list in one tool call, and do not start parallel direct media or ASR commands.
3. Keep outputs under the operator-pinned local pipeline roots configured for `stock_runtime`; do not ask for or choose output roots.
4. Never upload to Feishu/wiki/external channels unless the user gives separate and explicit authorization for that external destination.
5. Report success only from structured receipts and artifact refs returned by `stock_run_content_pipeline`; include run status, item status/error codes, hashes, and opaque `file:` artifact refs when present.
6. Avoid direct wrapper, Agent Reach, yt-dlp, MediaCrawler, browser, shell, or ASR commands for this workflow. Never ask for cookie contents; cookie handling is operator-managed outside collector.
