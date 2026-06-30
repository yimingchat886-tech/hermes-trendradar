"""Manual benchmark account registry and daily tracking plan."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any, TypedDict
from urllib.parse import urlparse

from .contracts import BenchmarkAccount, ContractError, SourceHealth, validate_record

RUN_ID = "run-child2-registry-2026-06-29"
OBSERVED_AT = "2026-06-29T00:00:00-07:00"

SOURCE_IDS = {
    "douyin": "source-douyin-benchmark",
    "xiaohongshu": "source-xiaohongshu-benchmark",
}

FORBIDDEN_SECRET_KEYS = {"cookie", "cookies", "credential", "password", "secret", "session", "token"}
LEVELS = {"S", "A", "B", "C"}
PLATFORMS = set(SOURCE_IDS)

ACCOUNT_ROWS = (
    ("douyin", "market_watch_s", "Market Watch S", "S", True, True, "jym", "Daily priority account."),
    ("douyin", "sector_alpha", "Sector Alpha", "A", True, True, "jym", "Strong hook reference."),
    ("douyin", "macro_note", "Macro Note", "A", True, True, "jym", "Macro explainer reference."),
    ("douyin", "trading_room", "Trading Room", "B", True, True, "jym", "Useful format baseline."),
    ("douyin", "finance_cards", "Finance Cards", "B", True, True, "jym", "Card structure reference."),
    ("douyin", "chart_story", "Chart Story", "B", True, True, "jym", "Chart narrative reference."),
    ("douyin", "retail_voice", "Retail Voice", "C", True, False, "jym", "Manual watch only."),
    ("douyin", "daily_close", "Daily Close", "A", True, True, "jym", "End-of-day pattern."),
    ("douyin", "disabled_lab", "Disabled Lab", "C", False, True, "jym", "Disabled fixture."),
    ("douyin", "risk_brief", "Risk Brief", "B", True, True, "jym", "Risk framing reference."),
    (
        "xiaohongshu",
        "value_notes",
        "Value Notes",
        "S",
        True,
        True,
        "jym",
        "High-signal Xiaohongshu account.",
    ),
    (
        "xiaohongshu",
        "fundamental_lab",
        "Fundamental Lab",
        "A",
        True,
        True,
        "jym",
        "Fundamental explainer reference.",
    ),
    ("xiaohongshu", "sector_cards", "Sector Cards", "A", True, True, "jym", "Card format reference."),
    ("xiaohongshu", "young_investor", "Young Investor", "B", True, True, "jym", "Retail language reference."),
    ("xiaohongshu", "portfolio_note", "Portfolio Note", "B", True, True, "jym", "Portfolio framing."),
    ("xiaohongshu", "news_digest", "News Digest", "B", True, True, "jym", "Digest format reference."),
    ("xiaohongshu", "watch_only", "Watch Only", "C", True, False, "jym", "Not in daily plan."),
    ("xiaohongshu", "disabled_test", "Disabled Test", "C", False, True, "jym", "Disabled fixture."),
    ("xiaohongshu", "theme_tracker", "Theme Tracker", "A", True, True, "jym", "Theme tracking reference."),
    ("xiaohongshu", "risk_notes", "Risk Notes", "B", True, True, "jym", "Risk note reference."),
)


class TrackingPlanItem(TypedDict):
    account_id: str
    source_id: str
    platform: str
    handle: str
    profile_url: str
    level: str
    owner: str
    frequency: str
    verified: bool
    source_status: str
    trace: dict[str, str]


def benchmark_accounts() -> list[BenchmarkAccount]:
    return [_account(*row) for row in ACCOUNT_ROWS]


def daily_tracking_plan(accounts: Iterable[BenchmarkAccount] | None = None) -> list[TrackingPlanItem]:
    records = list(accounts if accounts is not None else benchmark_accounts())
    validate_account_registry(records)
    return [
        {
            "account_id": account["id"],
            "source_id": account["source_id"],
            "platform": account["platform"],
            "handle": account["handle"],
            "profile_url": account["profile_url"],
            "level": account["level"],
            "owner": account["owner"],
            "frequency": "daily",
            "verified": account["verified"],
            "source_status": account["source_status"],
            "trace": _trace(f"plan-{account['id']}", account["source_id"], account["profile_url"]),
        }
        for account in records
        if account["enabled"] and account["daily_tracking"]
    ]


def registry_source_health(accounts: Iterable[BenchmarkAccount] | None = None) -> list[SourceHealth]:
    records = list(accounts if accounts is not None else benchmark_accounts())
    validate_account_registry(records)
    return [
        {
            "id": f"health-registry-{platform}",
            "source_id": source_id,
            "status": "ok",
            "checked_at": OBSERVED_AT,
            "message": f"{sum(1 for account in records if account['platform'] == platform)} placeholder accounts configured.",
            "trace": _trace(f"health-registry-{platform}", source_id),
        }
        for platform, source_id in SOURCE_IDS.items()
    ]


def validate_account_registry(accounts: Iterable[Mapping[str, Any]]) -> None:
    seen_ids: set[str] = set()
    for account in accounts:
        if not isinstance(account, Mapping):
            raise ContractError("account registry record must be an object")
        _reject_secret_keys(account)
        validate_record("accounts", account)

        account_id = account["id"]
        if account_id in seen_ids:
            raise ContractError(f"duplicate account id: {account_id}")
        seen_ids.add(account_id)

        if account["platform"] not in PLATFORMS:
            raise ContractError(f"unsupported account platform: {account['platform']}")
        if account["source_id"] != SOURCE_IDS[account["platform"]]:
            raise ContractError(f"account {account_id} source_id does not match platform")
        if account["level"] not in LEVELS:
            raise ContractError(f"account {account_id} has invalid level: {account['level']}")
        if not isinstance(account["enabled"], bool):
            raise ContractError(f"account {account_id} enabled must be bool")
        if not isinstance(account["daily_tracking"], bool):
            raise ContractError(f"account {account_id} daily_tracking must be bool")
        if not isinstance(account.get("verified"), bool):
            raise ContractError(f"account {account_id} verified must be bool")
        if account.get("source_status") not in {"placeholder", "verified"}:
            raise ContractError(f"account {account_id} source_status must be placeholder or verified")
        if account["source_status"] == "placeholder" and account["verified"]:
            raise ContractError(f"account {account_id} placeholder account cannot be verified")
        if not _is_http_url(account["profile_url"]):
            raise ContractError(f"account {account_id} profile_url must be an http(s) URL")


def _account(
    platform: str,
    handle: str,
    display_name: str,
    level: str,
    enabled: bool,
    daily_tracking: bool,
    owner: str,
    notes: str,
) -> BenchmarkAccount:
    registry_handle = f"placeholder_{handle}"
    account_id = f"placeholder-account-{platform}-{handle.replace('_', '-')}"
    profile_url = f"https://fixture.invalid/{platform}/{registry_handle}"
    source_id = SOURCE_IDS[platform]
    return {
        "id": account_id,
        "source_id": source_id,
        "platform": platform,
        "handle": registry_handle,
        "display_name": f"Placeholder {display_name}",
        "profile_url": profile_url,
        "level": level,
        "enabled": enabled,
        "owner": owner,
        "daily_tracking": daily_tracking,
        "notes": f"Placeholder only; not a verified real account. {notes}",
        "verified": False,
        "source_status": "placeholder",
        "trace": _trace(account_id, source_id, profile_url),
    }


def _trace(local_id: str, source_id: str, source_url: str = "") -> dict[str, str]:
    trace = {"local_id": local_id, "run_id": RUN_ID, "observed_at": OBSERVED_AT, "source_id": source_id}
    if source_url:
        trace["source_url"] = source_url
    return trace


def _reject_secret_keys(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in FORBIDDEN_SECRET_KEYS):
                raise ContractError(f"forbidden secret field in registry: {path}{key}")
            _reject_secret_keys(nested, f"{path}{key}.")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_keys(nested, f"{path}{index}.")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _self_check() -> None:
    accounts = benchmark_accounts()
    before_levels = {account["id"]: account["level"] for account in accounts}
    validate_account_registry(accounts)
    assert len(accounts) == 20
    assert all(not account["verified"] and account["source_status"] == "placeholder" for account in accounts)
    assert all(account["id"].startswith("placeholder-account-") for account in accounts)
    assert all(account["handle"].startswith("placeholder_") for account in accounts)
    assert all(account["profile_url"].startswith("https://fixture.invalid/") for account in accounts)

    plan = daily_tracking_plan(accounts)
    assert plan
    assert all(item["frequency"] == "daily" for item in plan)
    assert all(not item["verified"] and item["source_status"] == "placeholder" for item in plan)
    assert {item["account_id"] for item in plan} == {
        account["id"] for account in accounts if account["enabled"] and account["daily_tracking"]
    }
    assert before_levels == {account["id"]: account["level"] for account in accounts}

    for health in registry_source_health(accounts):
        validate_record("source_health", health)

    duplicate = deepcopy(accounts)
    duplicate.append(dict(duplicate[0]))
    _expect_contract_error("duplicate account id", duplicate)

    secret = deepcopy(accounts)
    secret[0]["cookies"] = "not allowed"
    _expect_contract_error("forbidden secret field", secret)

    bad_level = deepcopy(accounts)
    bad_level[0]["level"] = "VIP"
    _expect_contract_error("invalid level", bad_level)


def _expect_contract_error(fragment: str, accounts: list[Mapping[str, Any]]) -> None:
    try:
        validate_account_registry(accounts)
    except ContractError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r} in {exc!r}") from exc
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


if __name__ == "__main__":
    _self_check()
    print("benchmark account registry ok")
