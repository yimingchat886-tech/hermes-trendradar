"""Hermes handoff package builder for v1.4."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .profile import LoadedProfile
from .runtime_cdp import artifact_ref

REQUIRED_PACKAGE_FIELDS = ("schema_version", "package_id", "run_id", "profile_hash", "mode", "contents")
REQUIRED_CONTENT_FIELDS = (
    "content_id",
    "platform",
    "account_id",
    "account_display_name",
    "source_url",
    "title_or_caption_raw",
    "publish_at",
    "collected_at",
    "transcript_status",
    "transcript_artifact_ref",
    "dedup_key",
)


class HandoffPackageError(ValueError):
    pass


def account_display_names(profile: LoadedProfile) -> dict[str, str]:
    account_ref = profile.root.get("account_profile_ref")
    account_profile = profile.profiles_by_ref.get(str(account_ref), {})
    accounts = account_profile.get("accounts", [])
    if not isinstance(accounts, list):
        return {}
    names: dict[str, str] = {}
    for account in accounts:
        if not isinstance(account, Mapping):
            continue
        account_id = str(account.get("account_id") or "")
        if account_id:
            names[account_id] = str(account.get("display_name") or account_id)
    return names


def contents_from_state(conn: sqlite3.Connection, account_names: Mapping[str, str]) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT
          c.content_id, c.platform, c.account_id, c.source_url, c.publish_at,
          c.dedup_key, c.created_at, c.updated_at,
          t.status AS transcript_status, t.artifact_ref AS transcript_artifact_ref
        FROM content_ledger c
        LEFT JOIN transcripts t ON t.content_id = c.content_id
        ORDER BY c.created_at, c.content_id, t.updated_at DESC
        """
    ).fetchall()
    contents: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        content_id = str(row["content_id"])
        if content_id in seen:
            continue
        seen.add(content_id)
        account_id = str(row["account_id"] or "")
        contents.append(
            {
                "content_id": content_id,
                "platform": str(row["platform"] or ""),
                "account_id": account_id,
                "account_display_name": str(account_names.get(account_id) or account_id),
                "source_url": str(row["source_url"] or ""),
                "title_or_caption_raw": "",
                "publish_at": str(row["publish_at"] or ""),
                "collected_at": str(row["updated_at"] or row["created_at"] or ""),
                "transcript_status": _handoff_transcript_status(row["transcript_status"]),
                "transcript_artifact_ref": str(row["transcript_artifact_ref"] or ""),
                "dedup_key": str(row["dedup_key"] or ""),
            }
        )
    return contents


def build_handoff_package(run_id: str, profile_hash: str, contents: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [_content_item(content) for content in contents]
    package = {
        "schema_version": "1.4",
        "package_id": _stable_id("package", run_id, profile_hash, "hermes-handoff"),
        "run_id": run_id,
        "profile_hash": profile_hash,
        "mode": "hermes-handoff",
        "contents": normalized,
    }
    validate_handoff_package(package)
    return package


def validate_handoff_package(package: Mapping[str, Any]) -> None:
    for field in REQUIRED_PACKAGE_FIELDS:
        if field not in package:
            raise HandoffPackageError(f"handoff package missing {field}")
    if package["schema_version"] != "1.4":
        raise HandoffPackageError("handoff package schema_version must be 1.4")
    if not str(package["package_id"]).startswith("package_"):
        raise HandoffPackageError("handoff package_id must start with package_")
    if not str(package["run_id"]):
        raise HandoffPackageError("handoff run_id is required")
    if not str(package["profile_hash"]).startswith("sha256:"):
        raise HandoffPackageError("handoff profile_hash must be sha256")
    if package["mode"] != "hermes-handoff":
        raise HandoffPackageError("handoff mode must be hermes-handoff")
    contents = package["contents"]
    if not isinstance(contents, list):
        raise HandoffPackageError("handoff contents must be a list")
    for index, item in enumerate(contents):
        _validate_content_item(item, index)


def write_handoff_package(storage_dir: Path, run_id: str, package: Mapping[str, Any]) -> tuple[str, str]:
    validate_handoff_package(package)
    package_dir = storage_dir / run_id / "artifacts"
    package_dir.mkdir(parents=True, exist_ok=True)
    package_path = package_dir / "analysis_package.json"
    temp_path = package_path.with_suffix(".tmp")
    payload = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(package_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        package_path.unlink(missing_ok=True)
        raise
    digest = "sha256:" + hashlib.sha256(package_path.read_bytes()).hexdigest()
    return artifact_ref(storage_dir, package_path), digest


def _content_item(content: Mapping[str, Any]) -> dict[str, str]:
    return {field: str(content.get(field) or "") for field in REQUIRED_CONTENT_FIELDS}


def _validate_content_item(item: Any, index: int) -> None:
    if not isinstance(item, Mapping):
        raise HandoffPackageError(f"handoff contents[{index}] must be an object")
    for field in REQUIRED_CONTENT_FIELDS:
        if field not in item:
            raise HandoffPackageError(f"handoff contents[{index}] missing {field}")
        if not isinstance(item[field], str):
            raise HandoffPackageError(f"handoff contents[{index}].{field} must be a string")
    for field in ("content_id", "platform", "account_id", "source_url", "publish_at", "collected_at", "dedup_key"):
        if not item[field]:
            raise HandoffPackageError(f"handoff contents[{index}].{field} is required")
    if item["transcript_status"] == "success" and not item["transcript_artifact_ref"].startswith("file:"):
        raise HandoffPackageError(f"handoff contents[{index}].transcript_artifact_ref must be a file: ref")


def _handoff_transcript_status(status: Any) -> str:
    if status == "done":
        return "success"
    if status:
        return str(status)
    return "missing"


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts).encode()
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:16]}"


def _self_check() -> None:
    package = build_handoff_package(
        "run_self_check",
        "sha256:self-check",
        [
            {
                "content_id": "content-1",
                "platform": "douyin",
                "account_id": "account-1",
                "account_display_name": "Account 1",
                "source_url": "https://www.douyin.com/video/1",
                "title_or_caption_raw": "caption",
                "publish_at": "2026-07-03T00:00:00Z",
                "collected_at": "2026-07-03T01:00:00Z",
                "transcript_status": "success",
                "transcript_artifact_ref": "file:transcripts/1.json",
                "dedup_key": "douyin|1",
            }
        ],
    )
    validate_handoff_package(package)
    bad = dict(package)
    bad["profile_hash"] = "not-a-hash"
    try:
        validate_handoff_package(bad)
    except HandoffPackageError:
        pass
    else:
        raise AssertionError("expected invalid package to fail")


if __name__ == "__main__":
    _self_check()
    print("handoff package ok")
