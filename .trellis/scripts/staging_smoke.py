#!/usr/bin/env python3
"""Playwright smoke check for the local staging endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Playwright staging smoke check.")
    parser.add_argument("--url", default="http://127.0.0.1:18080/healthz")
    parser.add_argument("--expect", default="hermes-trendradar")
    parser.add_argument("--page-url")
    parser.add_argument("--page-expect", default="healthz")
    parser.add_argument("--timeout-ms", type=int, default=10_000)
    return parser.parse_args()


def default_page_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def main() -> int:
    args = parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. Run: "
            "uv run --no-project --with playwright==1.56.0 python3 ./.trellis/scripts/staging_smoke.py",
            file=sys.stderr,
        )
        return 2

    with sync_playwright() as playwright:
        request = playwright.request.new_context()
        try:
            api_response = request.get(args.url, timeout=args.timeout_ms)
            body = api_response.text().strip()
        finally:
            request.dispose()

        page_url = args.page_url or default_page_url(args.url)
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page_response = page.goto(page_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page_body = page.locator("body").inner_text(timeout=args.timeout_ms).strip()
        finally:
            browser.close()

    payload: dict[str, Any] = {
        "ok": bool(
            api_response.ok
            and args.expect in body
            and page_response
            and page_response.ok
            and args.page_expect in page_body
        ),
        "url": args.url,
        "status": api_response.status,
        "expected_text": args.expect,
        "body": body,
        "page_url": page_url,
        "page_status": page_response.status if page_response else None,
        "page_expected_text": args.page_expect,
    }
    try:
        payload["json"] = json.loads(body)
    except json.JSONDecodeError:
        payload["json"] = None

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
