"""Local v1.4 profile loading and validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAIN_PROFILE_REFS = (
    "account_profile_ref",
    "analysis_profile_ref",
    "transcription_profile_ref",
    "runtime_profile_ref",
    "feishu_profile_ref",
)
PROFILE_FILE_REFS = MAIN_PROFILE_REFS + ("field_mapping_ref",)
ALLOWED_REF_PREFIXES = ("env:", "file:", "secret:", "vault:", "redacted:", "runtime:")
REDACTED_VALUES = {"<redacted>", "<REDACTED>", "REDACTED", "redacted", "***"}
SENSITIVE_KEY_PARTS = (
    "cookie",
    "token",
    "password",
    "login_state",
    "login-state",
    "credential",
    "secret",
    "cdp_endpoint",
    "proxy",
)
URL_RE = re.compile(r"^(https?|wss?|socks5?)://", re.IGNORECASE)


class ProfileError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class LoadedProfile:
    root: dict[str, Any]
    profiles_by_ref: dict[str, dict[str, Any]]
    profiles_by_id: dict[str, dict[str, Any]]


def load_profile(path: str | Path) -> LoadedProfile:
    root_path = Path(path)
    if not root_path.exists():
        raise ProfileError([f"profile file not found: {path}"])

    profiles_by_ref: dict[str, dict[str, Any]] = {}
    profiles_by_id: dict[str, dict[str, Any]] = {}
    profiles_by_path: dict[Path, dict[str, Any]] = {}

    def load_file(file_path: Path, ref: str | None = None) -> dict[str, Any]:
        resolved = file_path.resolve()
        if resolved in profiles_by_path:
            data = profiles_by_path[resolved]
            if ref:
                profiles_by_ref[ref] = data
            return data

        data = _read_json_object(resolved)
        profiles_by_path[resolved] = data
        profile_id = str(data.get("profile_id") or resolved.name)
        profiles_by_id[profile_id] = data
        if ref:
            profiles_by_ref[ref] = data

        for key in PROFILE_FILE_REFS:
            child_ref = data.get(key)
            if isinstance(child_ref, str) and child_ref.startswith("file:"):
                child_path = _resolve_file_ref(child_ref, resolved.parent)
                load_file(child_path, child_ref)
        return data

    root = load_file(root_path)
    return LoadedProfile(root=root, profiles_by_ref=profiles_by_ref, profiles_by_id=profiles_by_id)


def validate_profile(profile: LoadedProfile) -> dict[str, Any]:
    errors: list[str] = []
    root = profile.root

    _validate_profile_header("root", root, "hermes_runtime", errors)
    mode = root.get("profile_mode", "production")
    if mode not in ("production", "sample"):
        errors.append("root profile_mode must be production or sample")

    platforms = _production_platforms(root)
    if platforms != ["douyin"]:
        errors.append("root platform_scope.production_platforms must be ['douyin']")

    schedule = root.get("schedule")
    retry = root.get("retry")
    if not isinstance(schedule, dict) or not schedule:
        errors.append("root schedule must be configured")
    if not isinstance(retry, dict) or not retry:
        errors.append("root retry must be configured")

    for ref_name in MAIN_PROFILE_REFS:
        ref = root.get(ref_name)
        if not isinstance(ref, str) or not ref.startswith("file:"):
            errors.append(f"root {ref_name} must be a file: ref")
        elif ref not in profile.profiles_by_ref:
            errors.append(f"root {ref_name} file target was not loaded")

    for profile_id, data in profile.profiles_by_id.items():
        _validate_profile_header(profile_id, data, None, errors)

    account_profile = _child(profile, root.get("account_profile_ref"))
    enabled_account_count = 0
    required_enabled_accounts = 10
    if account_profile is None:
        errors.append("account profile is missing")
    else:
        _validate_profile_header("account profile", account_profile, "account_profile", errors)
        if account_profile.get("platform") != "douyin":
            errors.append("account profile platform must be douyin")
        required_enabled_accounts = account_profile.get("required_enabled_accounts", 10)
        if required_enabled_accounts != 10:
            errors.append("account profile required_enabled_accounts must be 10")
        accounts = account_profile.get("accounts")
        if not isinstance(accounts, list):
            errors.append("account profile accounts must be a list")
        else:
            enabled_accounts = [account for account in accounts if isinstance(account, dict) and account.get("enabled") is True]
            enabled_account_count = len(enabled_accounts)
            if enabled_account_count != 10:
                errors.append("enabled Douyin account count must be exactly 10")
            for account in enabled_accounts:
                if account.get("platform") != "douyin":
                    errors.append(f"enabled account {account.get('account_id', '<missing>')} must be douyin")

    for ref_name, expected_type in (
        ("analysis_profile_ref", "analysis_profile"),
        ("transcription_profile_ref", "transcription_profile"),
        ("runtime_profile_ref", "runtime_profile"),
    ):
        child = _child(profile, root.get(ref_name))
        if child is not None:
            _validate_profile_header(ref_name, child, expected_type, errors)

    feishu_profile = _child(profile, root.get("feishu_profile_ref"))
    if feishu_profile is None:
        errors.append("feishu profile is missing")
    else:
        if feishu_profile.get("profile_type") not in ("feishu_limited_live_profile", "feishu_allowlist_profile"):
            errors.append("feishu profile type must be feishu_limited_live_profile or feishu_allowlist_profile")
        if "field_mapping_ref" not in feishu_profile:
            errors.append("feishu profile field_mapping_ref is required")

    for profile_id, data in profile.profiles_by_id.items():
        _scan_sensitive_values(profile_id, data, errors)

    if errors:
        raise ProfileError(errors)

    return profile_summary(profile, enabled_account_count, int(required_enabled_accounts))


def profile_hash(profile: LoadedProfile) -> str:
    payload = {
        "root": profile.root,
        "profiles": sorted(profile.profiles_by_id.values(), key=lambda item: str(item.get("profile_id", ""))),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def profile_summary(profile: LoadedProfile, enabled_account_count: int, required_enabled_accounts: int) -> dict[str, Any]:
    root = profile.root
    return {
        "schema_version": root.get("schema_version"),
        "ok": True,
        "profile_id": root.get("profile_id"),
        "profile_mode": root.get("profile_mode", "production"),
        "profile_hash": profile_hash(profile),
        "platforms": _production_platforms(root),
        "enabled_account_count": enabled_account_count,
        "required_enabled_accounts": required_enabled_accounts,
        "schedule_configured": isinstance(root.get("schedule"), dict) and bool(root.get("schedule")),
        "warnings": [],
        "errors": [],
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError([f"profile file could not be read: {path}"]) from exc
    except json.JSONDecodeError as exc:
        raise ProfileError([f"profile file is not valid JSON: {path} line {exc.lineno}"]) from exc
    if not isinstance(data, dict):
        raise ProfileError([f"profile file must contain a JSON object: {path}"])
    return data


def _resolve_file_ref(ref: str, base_dir: Path) -> Path:
    raw = ref.removeprefix("file:")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    local = base_dir / candidate
    if local.exists():
        return local
    return Path.cwd() / candidate


def _validate_profile_header(name: str, data: dict[str, Any], expected_type: str | None, errors: list[str]) -> None:
    if data.get("schema_version") != "1.4":
        errors.append(f"{name} schema_version must be 1.4")
    profile_id = data.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        errors.append(f"{name} profile_id is required")
    profile_type = data.get("profile_type")
    if not isinstance(profile_type, str) or not profile_type:
        errors.append(f"{name} profile_type is required")
    elif expected_type and profile_type != expected_type:
        errors.append(f"{name} profile_type must be {expected_type}")


def _production_platforms(root: dict[str, Any]) -> list[str]:
    platform_scope = root.get("platform_scope")
    if not isinstance(platform_scope, dict):
        return []
    platforms = platform_scope.get("production_platforms")
    if not isinstance(platforms, list):
        return []
    return [platform for platform in platforms if isinstance(platform, str)]


def _child(profile: LoadedProfile, ref: Any) -> dict[str, Any] | None:
    if not isinstance(ref, str):
        return None
    return profile.profiles_by_ref.get(ref)


def _scan_sensitive_values(name: str, value: Any, errors: list[str], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and _is_sensitive_key(key) and _is_plain_sensitive_value(key, child):
                errors.append(f"{name} {child_path} must be a ref or redacted value")
            _scan_sensitive_values(name, child, errors, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_sensitive_values(name, item, errors, f"{path}[{index}]")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _is_plain_sensitive_value(key: str, value: Any) -> bool:
    if value in (None, False, ""):
        return False
    if not isinstance(value, str):
        return True
    if value in REDACTED_VALUES or value.startswith(ALLOWED_REF_PREFIXES):
        return False
    normalized = key.lower().replace("-", "_")
    if ("cdp" in normalized or "proxy" in normalized) and URL_RE.match(value):
        return True
    return any(part in normalized for part in ("cookie", "token", "password", "login_state", "credential", "secret"))
