"""Durable downstream authority, inbox, reminder, and cycle state."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from loop_v1.qualification import canonical_json, digest_json


SCHEMA_VERSION = 1
TARGET_ORDER = (
    "RAG v2",
    "FamiliOS",
    "Hermes stock",
    "qivance-music",
    "xhs-qn-pipeline",
)
ENROLLED_TARGETS = (*TARGET_ORDER, "AH-map")

_ACTION_FIELDS = {
    "adoption_receipt_id",
    "base_head",
    "branch",
    "check_policy_digest",
    "commit_policy_digest",
    "deployment_policy_digest",
    "effect_digest",
    "lineage_anchor",
    "notification_policy_digest",
    "ownership_digest",
    "plan_digest",
    "preservation_digest",
    "recovery_policy_digest",
    "repository_id",
    "source_receipt_id",
    "target_id",
}
_COMMON_FIELDS = _ACTION_FIELDS - {
    "base_head",
    "plan_digest",
    "source_receipt_id",
}
_PLAN_FIELDS = _COMMON_FIELDS | {"base_head", "source_receipt_id"}
_INDEPENDENCE_FIELDS = {"grant", "lock", "recovery", "root", "state"}
_SKIP_RESULTS = {"expired", "ineligible", "no_change", "paused"}
_ATTEMPT_RESULTS = {
    "failed",
    "preflight_failed",
    "recovered",
    "succeeded",
    "unknown_outcome",
}
_TRANSIENT_PREWRITE_REASONS = {
    "CHECK_TIMEOUT",
    "LOCK_BUSY",
    "TOOL_TEMPORARILY_UNAVAILABLE",
}
_BAD_SLOT_RESULTS = {
    "blocked_by_previous",
    "expired",
    "failed",
    "paused",
    "recovered",
    "source_blocked",
    "unknown_outcome",
}
_STABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]+\Z")
_PROJECTION_NAME = "state"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projections (
    name TEXT PRIMARY KEY,
    source_position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class AuthorityError(RuntimeError):
    """Raised when downstream authority or state would fail closed."""


class EventConflict(AuthorityError):
    """Raised when a stable event identity is reused with changed input."""


class ProjectionError(AuthorityError):
    """Raised when the rebuildable projection is stale or inconsistent."""


def default_state_path(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return the canonical state path without creating it."""
    values = os.environ if env is None else env
    configured = values.get("XDG_STATE_HOME", "").strip()
    if configured:
        base = Path(configured).expanduser()
        if not base.is_absolute():
            raise AuthorityError("XDG_STATE_HOME must be absolute")
    else:
        base = Path(home or Path.home()).expanduser().absolute() / ".local/state"
    return base / "trellis-loop-updater/state.sqlite3"


def _error(code: str, detail: str) -> AuthorityError:
    return AuthorityError(f"{code}: {detail}")


def _keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise _error(
            "INPUT_INVALID",
            f"{label} fields must be exactly {','.join(sorted(expected))}",
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise _error("INPUT_INVALID", f"{label} must be non-empty text")
    return value.strip()


def _stable_id(value: object, label: str) -> str:
    text = _text(value, label)
    if not _STABLE_ID.fullmatch(text):
        raise _error("INPUT_INVALID", f"{label} is not a stable ID")
    return text


def _digest(value: object, label: str, *, prefixed: bool = False) -> str:
    text = _text(value, label)
    raw = text.removeprefix("sha256:") if prefixed else text
    if (
        (prefixed and not text.startswith("sha256:"))
        or len(raw) != 64
        or not _HEX.fullmatch(raw)
    ):
        raise _error("INPUT_INVALID", f"{label} must be a SHA-256 digest")
    return text


def _git_oid(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) not in {40, 64} or not _HEX.fullmatch(text):
        raise _error("INPUT_INVALID", f"{label} must be a Git object ID")
    return text


def _time(value: datetime | str | None = None, *, label: str = "time") -> str:
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        if value.tzinfo is None:
            raise _error("INPUT_INVALID", f"{label} must include UTC offset")
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _error("INPUT_INVALID", f"{label} is not RFC3339") from exc
        if moment.tzinfo is None:
            raise _error("INPUT_INVALID", f"{label} must include UTC offset")
    else:
        raise _error("INPUT_INVALID", f"{label} must be RFC3339")
    normalized = (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if isinstance(value, str) and value != normalized:
        raise _error("INPUT_INVALID", f"{label} must be canonical UTC seconds")
    return normalized


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _authority_id(value: object, label: str = "authority_id") -> str:
    return _digest(value, label, prefixed=True)


def _common_binding(value: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error("INPUT_INVALID", "binding must be an object")
    _keys(value, _COMMON_FIELDS, "binding")
    target_id = _text(value["target_id"], "target_id")
    if target_id not in ENROLLED_TARGETS:
        raise _error("TARGET_NOT_ENROLLED", target_id)
    result = {
        "adoption_receipt_id": _digest(
            value["adoption_receipt_id"],
            "adoption_receipt_id",
            prefixed=True,
        ),
        "branch": _text(value["branch"], "branch"),
        "lineage_anchor": _git_oid(value["lineage_anchor"], "lineage_anchor"),
        "repository_id": _text(value["repository_id"], "repository_id"),
        "target_id": target_id,
    }
    for field in sorted(
        _COMMON_FIELDS
        - {
            "adoption_receipt_id",
            "branch",
            "lineage_anchor",
            "repository_id",
            "target_id",
        }
    ):
        result[field] = _digest(value[field], field)
    return result


def _action_binding(
    value: Mapping[str, object],
    *,
    effect: str,
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error("INPUT_INVALID", "action binding must be an object")
    extra = (
        {"failed_transaction_id", "preimage_digest"} if effect == "recover" else set()
    )
    _keys(value, _ACTION_FIELDS | extra, "action binding")
    result = _common_binding({field: value[field] for field in _COMMON_FIELDS})
    result.update(
        {
            "base_head": _git_oid(value["base_head"], "base_head"),
            "plan_digest": _digest(value["plan_digest"], "plan_digest"),
            "source_receipt_id": _digest(
                value["source_receipt_id"],
                "source_receipt_id",
                prefixed=True,
            ),
        }
    )
    if effect == "recover":
        result["failed_transaction_id"] = _digest(
            value["failed_transaction_id"],
            "failed_transaction_id",
            prefixed=True,
        )
        result["preimage_digest"] = _digest(
            value["preimage_digest"],
            "preimage_digest",
        )
    return result


def _plan_binding(value: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error("INPUT_INVALID", "plan binding must be an object")
    _keys(value, _PLAN_FIELDS, "plan binding")
    result = _common_binding({field: value[field] for field in _COMMON_FIELDS})
    result.update(
        {
            "base_head": _git_oid(value["base_head"], "base_head"),
            "source_receipt_id": _digest(
                value["source_receipt_id"],
                "source_receipt_id",
                prefixed=True,
            ),
        }
    )
    return result


def _source_identity(value: Mapping[str, object]) -> dict[str, str]:
    expected = {
        "commit",
        "policy_digest",
        "qualification_receipt_id",
        "status_digest",
        "task_fingerprint",
        "tree",
    }
    if not isinstance(value, Mapping):
        raise _error("INPUT_INVALID", "source identity must be an object")
    _keys(value, expected, "source identity")
    return {
        "commit": _git_oid(value["commit"], "source commit"),
        "policy_digest": _digest(value["policy_digest"], "policy_digest"),
        "qualification_receipt_id": _digest(
            value["qualification_receipt_id"],
            "qualification_receipt_id",
            prefixed=True,
        ),
        "status_digest": _digest(value["status_digest"], "status_digest"),
        "task_fingerprint": _digest(
            value["task_fingerprint"],
            "task_fingerprint",
        ),
        "tree": _git_oid(value["tree"], "source tree"),
    }


def _independence(value: Mapping[str, object]) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise _error("INPUT_INVALID", "independence must be an object")
    _keys(value, _INDEPENDENCE_FIELDS, "independence")
    if any(type(value[field]) is not bool for field in _INDEPENDENCE_FIELDS):
        raise _error("INPUT_INVALID", "independence values must be booleans")
    return {field: bool(value[field]) for field in sorted(_INDEPENDENCE_FIELDS)}


def _empty_state() -> dict[str, object]:
    return {
        "authorities": {},
        "cycles": {},
        "inbox": {},
        "notifier": {},
        "receipts": {},
        "reminders": {},
        "schema_version": SCHEMA_VERSION,
        "targets": {},
    }


def _require_authority(
    state: dict[str, object],
    authority_id: str,
    kind: str,
) -> dict[str, object]:
    authority = state["authorities"].get(authority_id)
    if not isinstance(authority, dict) or authority.get("kind") != kind:
        raise _error("AUTHORITY_NOT_FOUND", authority_id)
    return authority


def _request_digest(payload: Mapping[str, object]) -> str:
    return digest_json(
        {key: value for key, value in payload.items() if key != "request_digest"}
    )


def _window_contains(authority: Mapping[str, object], created_at: str) -> bool:
    moment = _moment(created_at)
    return (
        _moment(str(authority["not_before"]))
        <= moment
        < _moment(str(authority["expires_at"]))
    )


def _actionable(event_type: str, payload: Mapping[str, object]) -> bool:
    if event_type in {
        "cycle_source_blocked",
        "deadline_due",
        "grant_retired",
        "target_paused",
    }:
        return True
    if event_type == "receipt_recorded":
        return payload.get("status") in {"recovered", "unknown_outcome"}
    return event_type == "cycle_slot_recorded" and payload.get("result") in {
        "failed",
        "paused",
        "preflight_failed",
        "recovered",
        "unknown_outcome",
    }


def _update_cycle_status(cycle: dict[str, object]) -> None:
    slots = cycle["slots"]
    if all(slot["stable"] for slot in slots):
        cycle["status"] = (
            "completed_non_success"
            if any(slot["result"] in _BAD_SLOT_RESULTS for slot in slots)
            else "completed"
        )
    elif any(slot["stable"] and slot["result"] in _BAD_SLOT_RESULTS for slot in slots):
        cycle["status"] = "non_success"
    else:
        cycle["status"] = "running"


def _apply_authority_event(
    state: dict[str, object],
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> bool:
    event_type = event["event_type"]
    created_at = event["created_at"]
    authorities = state["authorities"]
    targets = state["targets"]

    if event_type == "one_off_requested":
        _keys(
            payload,
            {
                "authority_id",
                "binding",
                "effect",
                "expires_at",
                "not_before",
                "request_digest",
            },
            event_type,
        )
        authority_id = _authority_id(payload["authority_id"])
        effect = _text(payload["effect"], "effect")
        if effect not in {"apply", "commit", "recover"}:
            raise _error("INPUT_INVALID", f"unsupported one-off effect: {effect}")
        binding = _action_binding(payload["binding"], effect=effect)
        not_before = _time(payload["not_before"], label="not_before")
        expires_at = _time(payload["expires_at"], label="expires_at")
        if (
            authority_id in authorities
            or _moment(created_at) > _moment(not_before)
            or _moment(not_before) >= _moment(expires_at)
            or payload["request_digest"] != _request_digest(payload)
        ):
            raise _error("AUTHORITY_INVALID", authority_id)
        authorities[authority_id] = {
            "approval": None,
            "binding": binding,
            "consumption": None,
            "created_at": created_at,
            "effect": effect,
            "expires_at": expires_at,
            "kind": "one_off",
            "not_before": not_before,
            "request_digest": payload["request_digest"],
            "state": "requested",
        }
        return True

    if event_type == "receipt_recorded":
        _keys(
            payload,
            {
                "authority_consumption_id",
                "authority_id",
                "commit_id",
                "payload_digest",
                "plan_digest",
                "receipt_id",
                "receipt_kind",
                "source_receipt_id",
                "status",
                "target_id",
                "transaction_id",
            },
            event_type,
        )
        receipt_id = _authority_id(payload["receipt_id"], "receipt_id")
        if receipt_id in state["receipts"]:
            raise _error("RECEIPT_EXISTS", receipt_id)
        receipt_kind = _text(payload["receipt_kind"], "receipt_kind")
        status_value = _text(payload["status"], "status")
        combinations = {
            "candidate": {"candidate_verified"},
            "final": {"verified"},
            "recovery": {"recovered", "unknown_outcome"},
        }
        if (
            receipt_kind not in combinations
            or status_value not in combinations[receipt_kind]
        ):
            raise _error("RECEIPT_INVALID", receipt_id)
        target_id = _text(payload["target_id"], "target_id")
        if target_id not in ENROLLED_TARGETS:
            raise _error("TARGET_NOT_ENROLLED", target_id)
        authority_id = _authority_id(payload["authority_id"])
        consumption_id = _stable_id(
            payload["authority_consumption_id"],
            "authority_consumption_id",
        )
        authority = state["authorities"].get(authority_id)
        if not isinstance(authority, dict):
            raise _error("RECEIPT_AUTHORITY_INVALID", receipt_id)
        if authority["kind"] == "one_off":
            valid_consumption = (
                authority.get("state") == "consumed"
                and authority.get("consumption", {}).get("event_id") == consumption_id
            )
        else:
            valid_consumption = any(
                item["event_id"] == consumption_id
                for item in authority.get("consumptions", [])
            )
        if not valid_consumption:
            raise _error("RECEIPT_AUTHORITY_INVALID", receipt_id)
        commit_id = payload["commit_id"]
        if commit_id is not None:
            commit_id = _git_oid(commit_id, "commit_id")
        state["receipts"][receipt_id] = {
            "authority_consumption_id": consumption_id,
            "authority_id": authority_id,
            "commit_id": commit_id,
            "event_id": event["event_id"],
            "payload_digest": _digest(
                payload["payload_digest"],
                "payload_digest",
            ),
            "plan_digest": _digest(
                payload["plan_digest"],
                "plan_digest",
            ),
            "receipt_kind": receipt_kind,
            "source_receipt_id": _digest(
                payload["source_receipt_id"],
                "source_receipt_id",
                prefixed=True,
            ),
            "status": status_value,
            "target_id": target_id,
            "transaction_id": _digest(
                payload["transaction_id"],
                "transaction_id",
                prefixed=True,
            ),
        }
        return True

    if event_type == "one_off_approved":
        _keys(
            payload,
            {"authority_id", "request_digest", "response_identity"},
            event_type,
        )
        authority_id = _authority_id(payload["authority_id"])
        authority = _require_authority(state, authority_id, "one_off")
        if (
            authority["state"] != "requested"
            or payload["request_digest"] != authority["request_digest"]
            or _moment(created_at) >= _moment(authority["expires_at"])
        ):
            raise _error("AUTHORITY_STALE", authority_id)
        authority["approval"] = {
            "event_id": event["event_id"],
            "response_identity": _stable_id(
                payload["response_identity"],
                "response_identity",
            ),
        }
        authority["state"] = "approved"
        return True

    if event_type == "one_off_consumed":
        _keys(payload, {"authority_id", "binding", "effect"}, event_type)
        authority_id = _authority_id(payload["authority_id"])
        authority = _require_authority(state, authority_id, "one_off")
        effect = _text(payload["effect"], "effect")
        binding = _action_binding(payload["binding"], effect=effect)
        if (
            authority["state"] != "approved"
            or authority["effect"] != effect
            or authority["binding"] != binding
            or not _window_contains(authority, created_at)
        ):
            raise _error("AUTHORITY_MISMATCH", authority_id)
        authority["consumption"] = {
            "event_id": event["event_id"],
            "created_at": created_at,
        }
        authority["state"] = "consumed"
        return True

    if event_type == "grant_requested":
        _keys(
            payload,
            {
                "binding",
                "expires_at",
                "grant_id",
                "not_before",
                "request_digest",
            },
            event_type,
        )
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        binding = _common_binding(payload["binding"])
        if binding["target_id"] not in TARGET_ORDER:
            raise _error("GRANT_INVALID", grant_id)
        adoption = state["receipts"].get(binding["adoption_receipt_id"])
        not_before = _time(payload["not_before"], label="not_before")
        expires_at = _time(payload["expires_at"], label="expires_at")
        if (
            grant_id in authorities
            or not isinstance(adoption, dict)
            or adoption.get("status") != "verified"
            or adoption.get("target_id") != binding["target_id"]
            or _moment(created_at) > _moment(not_before)
            or _moment(expires_at) - _moment(not_before) != timedelta(days=30)
            or payload["request_digest"] != _request_digest(payload)
        ):
            raise _error("GRANT_INVALID", grant_id)
        authorities[grant_id] = {
            "binding": binding,
            "capture": None,
            "consumptions": [],
            "created_at": created_at,
            "expires_at": expires_at,
            "kind": "grant",
            "not_before": not_before,
            "request_digest": payload["request_digest"],
            "state": "requested",
            "supersedes": None,
        }
        return True

    if event_type == "grant_captured":
        _keys(
            payload,
            {"grant_id", "request_digest", "response_identity"},
            event_type,
        )
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        grant = _require_authority(state, grant_id, "grant")
        if (
            grant["state"] != "requested"
            or payload["request_digest"] != grant["request_digest"]
            or _moment(created_at) >= _moment(grant["expires_at"])
        ):
            raise _error("GRANT_STALE", grant_id)
        grant["capture"] = {
            "event_id": event["event_id"],
            "response_identity": _stable_id(
                payload["response_identity"],
                "response_identity",
            ),
        }
        grant["state"] = "disarmed"
        return True

    if event_type == "cohort_activated":
        _keys(
            payload,
            {
                "common_not_before",
                "grant_ids",
                "response_identity",
            },
            event_type,
        )
        grant_ids = list(payload["grant_ids"])
        common_not_before = _time(
            payload["common_not_before"],
            label="common_not_before",
        )
        if len(grant_ids) != len(TARGET_ORDER):
            raise _error("COHORT_INVALID", "exactly five grants are required")
        grants = [
            _require_authority(
                state,
                _authority_id(grant_id, "grant_id"),
                "grant",
            )
            for grant_id in grant_ids
        ]
        if (
            [grant["binding"]["target_id"] for grant in grants] != list(TARGET_ORDER)
            or any(grant["state"] != "disarmed" for grant in grants)
            or any(grant["not_before"] != common_not_before for grant in grants)
            or len(set(grant_ids)) != len(grant_ids)
        ):
            raise _error("COHORT_INVALID", "grant order or state is invalid")
        response_identity = _stable_id(
            payload["response_identity"],
            "response_identity",
        )
        for grant_id, grant in zip(grant_ids, grants, strict=True):
            grant["activation"] = {
                "event_id": event["event_id"],
                "response_identity": response_identity,
            }
            grant["state"] = "active"
            binding = grant["binding"]
            targets[binding["target_id"]] = {
                "grant_id": grant_id,
                "lineage_anchor": binding["lineage_anchor"],
                "pause_reason": None,
                "paused": False,
            }
        return True

    if event_type == "grant_renewed":
        _keys(
            payload,
            {
                "expires_at",
                "new_grant_id",
                "not_before",
                "old_grant_id",
                "response_identity",
            },
            event_type,
        )
        old_id = _authority_id(payload["old_grant_id"], "old_grant_id")
        new_id = _authority_id(payload["new_grant_id"], "new_grant_id")
        old = _require_authority(state, old_id, "grant")
        not_before = _time(payload["not_before"], label="not_before")
        expires_at = _time(payload["expires_at"], label="expires_at")
        if (
            old["state"] != "active"
            or new_id in authorities
            or _moment(created_at) >= _moment(old["expires_at"])
            or _moment(created_at) > _moment(not_before)
            or _moment(expires_at) - _moment(not_before) != timedelta(days=30)
        ):
            raise _error("GRANT_RENEWAL_INVALID", old_id)
        old["state"] = "superseded"
        old["superseded_by"] = new_id
        new = {
            "activation": {
                "event_id": event["event_id"],
                "response_identity": _stable_id(
                    payload["response_identity"],
                    "response_identity",
                ),
            },
            "binding": old["binding"],
            "capture": old["capture"],
            "consumptions": [],
            "created_at": created_at,
            "expires_at": expires_at,
            "kind": "grant",
            "not_before": not_before,
            "request_digest": None,
            "state": "active",
            "supersedes": old_id,
        }
        authorities[new_id] = new
        target_id = new["binding"]["target_id"]
        target = targets.get(target_id)
        if isinstance(target, dict) and target.get("grant_id") == old_id:
            target["grant_id"] = new_id
        for reminder in state["reminders"].values():
            if reminder["grant_id"] == old_id:
                reminder["status"] = "cancelled"
        return True

    if event_type == "grant_retired":
        _keys(
            payload,
            {"disposition", "grant_id", "reason", "response_identity"},
            event_type,
        )
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        grant = _require_authority(state, grant_id, "grant")
        disposition = _text(payload["disposition"], "disposition")
        if disposition not in {"invalidated", "revoked"} or grant["state"] not in {
            "active",
            "disarmed",
            "requested",
        }:
            raise _error("GRANT_RETIREMENT_INVALID", grant_id)
        response_identity = payload["response_identity"]
        if disposition == "revoked":
            response_identity = _stable_id(
                response_identity,
                "response_identity",
            )
        elif response_identity is not None:
            raise _error(
                "GRANT_RETIREMENT_INVALID",
                "automatic invalidation cannot carry direct-user identity",
            )
        grant["state"] = disposition
        grant["retirement"] = {
            "event_id": event["event_id"],
            "reason": _text(payload["reason"], "reason"),
            "response_identity": response_identity,
        }
        target_id = grant["binding"]["target_id"]
        target = targets.get(target_id)
        if isinstance(target, dict) and target.get("grant_id") == grant_id:
            target["paused"] = True
            target["pause_reason"] = f"grant_{disposition}"
        for reminder in state["reminders"].values():
            if reminder["grant_id"] == grant_id:
                reminder["status"] = "cancelled"
        return True

    if event_type == "grant_action_consumed":
        _keys(payload, {"binding", "effect", "grant_id"}, event_type)
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        grant = _require_authority(state, grant_id, "grant")
        effect = _text(payload["effect"], "effect")
        if effect not in {"apply", "commit", "plan"}:
            raise _error("GRANT_EFFECT_INVALID", effect)
        binding = (
            _plan_binding(payload["binding"])
            if effect == "plan"
            else _action_binding(payload["binding"], effect=effect)
        )
        target = targets.get(grant["binding"]["target_id"])
        if (
            grant["state"] != "active"
            or grant["binding"] != {field: binding[field] for field in _COMMON_FIELDS}
            or not _window_contains(grant, created_at)
            or not isinstance(target, dict)
            or target.get("grant_id") != grant_id
            or target.get("paused") is not False
        ):
            raise _error("GRANT_AUTHORITY_INVALID", grant_id)
        grant["consumptions"].append(
            {
                "binding": binding,
                "effect": effect,
                "event_id": event["event_id"],
            }
        )
        return True

    if event_type == "target_paused":
        _keys(
            payload,
            {"grant_id", "lineage_anchor", "reason", "target_id"},
            event_type,
        )
        target_id = _text(payload["target_id"], "target_id")
        if target_id not in TARGET_ORDER:
            raise _error("TARGET_NOT_ENROLLED", target_id)
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        lineage = _git_oid(payload["lineage_anchor"], "lineage_anchor")
        target = targets.get(target_id)
        if (
            not isinstance(target, dict)
            or target.get("grant_id") != grant_id
            or target.get("lineage_anchor") != lineage
        ):
            raise _error("TARGET_STATE_MISMATCH", target_id)
        target["paused"] = True
        target["pause_reason"] = _text(payload["reason"], "reason")
        return True

    if event_type == "target_resumed":
        _keys(
            payload,
            {
                "grant_id",
                "lineage_anchor",
                "response_identity",
                "target_id",
            },
            event_type,
        )
        target_id = _text(payload["target_id"], "target_id")
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        lineage = _git_oid(payload["lineage_anchor"], "lineage_anchor")
        grant = _require_authority(state, grant_id, "grant")
        target = targets.get(target_id)
        if (
            grant["state"] != "active"
            or not _window_contains(grant, created_at)
            or not isinstance(target, dict)
            or target.get("paused") is not True
            or target.get("grant_id") != grant_id
            or target.get("lineage_anchor") != lineage
        ):
            raise _error("RESUME_INVALID", target_id)
        _stable_id(payload["response_identity"], "response_identity")
        target["paused"] = False
        target["pause_reason"] = None
        return True

    return False


def _apply_cycle_event(
    state: dict[str, object],
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> bool:
    event_type = event["event_type"]
    cycles = state["cycles"]
    targets = state["targets"]

    if event_type == "cycle_started":
        _keys(payload, {"cycle_id", "source"}, event_type)
        cycle_id = _stable_id(payload["cycle_id"], "cycle_id")
        if cycle_id in cycles:
            raise _error("CYCLE_EXISTS", cycle_id)
        cycles[cycle_id] = {
            "created_at": event["created_at"],
            "slots": [
                {
                    "attempts": [],
                    "independent": True,
                    "result": "pending",
                    "stable": False,
                    "target_id": target_id,
                }
                for target_id in TARGET_ORDER
            ],
            "source": _source_identity(payload["source"]),
            "status": "running",
        }
        return True

    if event_type == "cycle_slot_recorded":
        _keys(
            payload,
            {
                "attempt_id",
                "cycle_id",
                "evidence_digest",
                "independence",
                "mode",
                "plan_digest",
                "reason_code",
                "result",
                "source_identity_digest",
                "source_receipt_id",
                "target_id",
                "target_identity_digest",
                "zero_write_proof",
            },
            event_type,
        )
        cycle_id = _stable_id(payload["cycle_id"], "cycle_id")
        cycle = cycles.get(cycle_id)
        if not isinstance(cycle, dict):
            raise _error("CYCLE_NOT_FOUND", cycle_id)
        pending = next(
            (slot for slot in cycle["slots"] if not slot["stable"]),
            None,
        )
        if pending is None:
            raise _error("CYCLE_COMPLETE", cycle_id)
        target_id = _text(payload["target_id"], "target_id")
        if target_id != pending["target_id"]:
            raise _error("CYCLE_ORDER_INVALID", target_id)
        mode = _text(payload["mode"], "mode")
        result = _text(payload["result"], "result")
        independence = _independence(payload["independence"])
        all_independent = all(independence.values())
        if result == "unknown_outcome" and all_independent:
            raise _error(
                "CYCLE_RESULT_INVALID",
                "unknown outcome cannot prove recovery independence",
            )
        evidence_digest = _digest(
            payload["evidence_digest"],
            "evidence_digest",
        )
        source_identity_digest = _digest(
            payload["source_identity_digest"],
            "source_identity_digest",
        )
        if source_identity_digest != digest_json(cycle["source"]):
            raise _error("SOURCE_IDENTITY_DRIFT", cycle_id)
        attempt_id = _stable_id(payload["attempt_id"], "attempt_id")
        if any(
            attempt["attempt_id"] == attempt_id
            for slot in cycle["slots"]
            for attempt in slot["attempts"]
        ):
            raise _error("ATTEMPT_EXISTS", attempt_id)

        attempt: dict[str, object] = {
            "attempt_id": attempt_id,
            "evidence_digest": evidence_digest,
            "independence": independence,
            "result": result,
        }
        if mode == "skip":
            if result not in _SKIP_RESULTS or pending["attempts"]:
                raise _error("CYCLE_RESULT_INVALID", result)
            for field in (
                "plan_digest",
                "source_receipt_id",
                "target_identity_digest",
                "zero_write_proof",
            ):
                if payload[field] is not None:
                    raise _error("CYCLE_RESULT_INVALID", f"{field} must be null")
            reason_code = _text(payload["reason_code"], "reason_code")
            attempt["reason_code"] = reason_code
            pending["attempts"].append(attempt)
            pending["result"] = result
            pending["stable"] = True
        elif mode == "attempt":
            if result not in _ATTEMPT_RESULTS:
                raise _error("CYCLE_RESULT_INVALID", result)
            plan_digest = _digest(payload["plan_digest"], "plan_digest")
            source_receipt = _digest(
                payload["source_receipt_id"],
                "source_receipt_id",
                prefixed=True,
            )
            target_digest = _digest(
                payload["target_identity_digest"],
                "target_identity_digest",
            )
            if source_receipt != cycle["source"]["qualification_receipt_id"]:
                raise _error("SOURCE_RECEIPT_DRIFT", source_receipt)
            if pending["attempts"]:
                first = pending["attempts"][0]
                if (
                    first["plan_digest"] != plan_digest
                    or first["source_receipt_id"] != source_receipt
                    or first["target_identity_digest"] != target_digest
                ):
                    raise _error("RETRY_IDENTITY_DRIFT", target_id)
            reason_code = _text(payload["reason_code"], "reason_code")
            zero_write = payload["zero_write_proof"]
            if zero_write is not None:
                zero_write = _digest(zero_write, "zero_write_proof")
            attempt.update(
                {
                    "plan_digest": plan_digest,
                    "reason_code": reason_code,
                    "source_receipt_id": source_receipt,
                    "target_identity_digest": target_digest,
                    "zero_write_proof": zero_write,
                }
            )
            pending["attempts"].append(attempt)
            retry = (
                len(pending["attempts"]) == 1
                and result == "preflight_failed"
                and reason_code in _TRANSIENT_PREWRITE_REASONS
                and zero_write is not None
            )
            if retry:
                pending["result"] = "retry_pending"
                pending["stable"] = False
            else:
                pending["result"] = "failed" if result == "preflight_failed" else result
                pending["stable"] = True
        else:
            raise _error("CYCLE_RESULT_INVALID", mode)

        pending["independent"] = all_independent
        if pending["stable"] and pending["result"] in _BAD_SLOT_RESULTS:
            target = targets.get(target_id)
            if isinstance(target, dict):
                target["paused"] = True
                target["pause_reason"] = f"cycle_{pending['result']}"
        if pending["stable"] and not all_independent:
            seen = False
            for slot in cycle["slots"]:
                if slot is pending:
                    seen = True
                    continue
                if seen and not slot["stable"]:
                    slot["independent"] = False
                    slot["result"] = "blocked_by_previous"
                    slot["stable"] = True
        _update_cycle_status(cycle)
        return True

    if event_type == "cycle_source_blocked":
        _keys(
            payload,
            {"cycle_id", "observed_source_digest", "reason"},
            event_type,
        )
        cycle_id = _stable_id(payload["cycle_id"], "cycle_id")
        cycle = cycles.get(cycle_id)
        observed_source_digest = _digest(
            payload["observed_source_digest"],
            "observed_source_digest",
        )
        reason = _text(payload["reason"], "reason")
        if cycle is None:
            cycles[cycle_id] = {
                "created_at": event["created_at"],
                "slots": [
                    {
                        "attempts": [],
                        "independent": False,
                        "result": "source_blocked",
                        "stable": True,
                        "target_id": target_id,
                    }
                    for target_id in TARGET_ORDER
                ],
                "source": {
                    "observed_source_digest": observed_source_digest,
                    "reason": reason,
                },
                "status": "source_blocked",
            }
            return True
        if not isinstance(cycle, dict):
            raise _error("CYCLE_INVALID", cycle_id)
        for slot in cycle["slots"]:
            if not slot["stable"]:
                slot["independent"] = False
                slot["result"] = "source_blocked"
                slot["stable"] = True
        cycle["status"] = "source_blocked"
        return True

    return False


def _apply_delivery_event(
    state: dict[str, object],
    event: Mapping[str, object],
    payload: Mapping[str, object],
) -> bool:
    event_type = event["event_type"]
    if event_type == "deadline_due":
        _keys(
            payload,
            {"due_at", "grant_id", "milestone", "target_id"},
            event_type,
        )
        grant_id = _authority_id(payload["grant_id"], "grant_id")
        grant = _require_authority(state, grant_id, "grant")
        milestone = _text(payload["milestone"], "milestone")
        if milestone not in {"expiry", "t-1d", "t-7d"} or grant["state"] not in {
            "active",
            "disarmed",
        }:
            raise _error("REMINDER_INVALID", grant_id)
        due_at = _time(payload["due_at"], label="due_at")
        target_id = _text(payload["target_id"], "target_id")
        key = f"{grant_id}:{milestone}"
        if key in state["reminders"]:
            raise _error("REMINDER_EXISTS", key)
        state["reminders"][key] = {
            "due_at": due_at,
            "event_id": event["event_id"],
            "grant_id": grant_id,
            "milestone": milestone,
            "status": "due",
            "target_id": target_id,
        }
        if milestone == "expiry":
            grant["state"] = "expired"
            target = state["targets"].get(target_id)
            if isinstance(target, dict) and target.get("grant_id") == grant_id:
                target["paused"] = True
                target["pause_reason"] = "grant_expired"
        return True

    if event_type == "notifier_delivery":
        _keys(
            payload,
            {
                "delivery_id",
                "error",
                "output_digest",
                "returncode",
                "source_event_id",
                "status",
            },
            event_type,
        )
        source_event_id = _stable_id(
            payload["source_event_id"],
            "source_event_id",
        )
        if source_event_id not in state["inbox"]:
            raise _error("NOTIFIER_SOURCE_MISSING", source_event_id)
        if source_event_id in state["notifier"]:
            raise _error("NOTIFIER_DELIVERY_EXISTS", source_event_id)
        status_value = _text(payload["status"], "status")
        if status_value not in {"absent", "failed", "sent"}:
            raise _error("NOTIFIER_STATUS_INVALID", status_value)
        output_digest = payload["output_digest"]
        if output_digest is not None:
            output_digest = _digest(
                output_digest,
                "output_digest",
                prefixed=True,
            )
        error = payload["error"]
        if error is not None:
            error = _text(error, "error")
        returncode = payload["returncode"]
        if returncode is not None and type(returncode) is not int:
            raise _error("NOTIFIER_STATUS_INVALID", "returncode")
        state["notifier"][source_event_id] = {
            "delivery_id": _stable_id(payload["delivery_id"], "delivery_id"),
            "error": error,
            "event_id": event["event_id"],
            "output_digest": output_digest,
            "returncode": returncode,
            "status": status_value,
        }
        return True

    return False


def _project(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    state = _empty_state()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProjectionError("event payload is not valid JSON") from exc
        if (
            not isinstance(payload, dict)
            or digest_json(payload) != row["payload_digest"]
        ):
            raise ProjectionError("event payload digest mismatch")
        event = {
            "created_at": _time(str(row["created_at"]), label="created_at"),
            "event_id": _stable_id(row["event_id"], "event_id"),
            "event_type": _stable_id(row["event_type"], "event_type"),
        }
        handled = (
            _apply_authority_event(state, event, payload)
            or _apply_cycle_event(state, event, payload)
            or _apply_delivery_event(state, event, payload)
        )
        if not handled:
            raise ProjectionError(f"unknown event type: {event['event_type']}")
        if event["event_type"] != "notifier_delivery":
            state["inbox"][event["event_id"]] = {
                "actionable": _actionable(event["event_type"], payload),
                "created_at": event["created_at"],
                "event_type": event["event_type"],
                "payload_digest": row["payload_digest"],
                "target_id": payload.get("target_id")
                or (
                    payload.get("binding", {}).get("target_id")
                    if isinstance(payload.get("binding"), Mapping)
                    else None
                ),
            }
    return state


class AuthorityStore:
    """One SQLite event authority with one atomically rebuilt projection."""

    def __init__(
        self,
        path: Path,
        *,
        notifier: Sequence[str] | None = None,
        notifier_timeout: int = 10,
    ) -> None:
        raw = Path(path).expanduser().absolute()
        self._assert_real_path(raw)
        self.path = raw
        if notifier is None:
            self.notifier: tuple[str, ...] = ()
        else:
            if isinstance(notifier, (str, bytes)):
                raise _error("NOTIFIER_INVALID", "notifier must be an argv array")
            argv = tuple(str(part) for part in notifier)
            if not argv or any(not part or "\0" in part for part in argv):
                raise _error("NOTIFIER_INVALID", "notifier argv is invalid")
            self.notifier = argv
        if type(notifier_timeout) is not int or not 1 <= notifier_timeout <= 60:
            raise _error("NOTIFIER_INVALID", "timeout must be 1-60 seconds")
        self.notifier_timeout = notifier_timeout

    @classmethod
    def initialize(
        cls,
        path: Path,
        *,
        notifier: Sequence[str] | None = None,
        notifier_timeout: int = 10,
    ) -> AuthorityStore:
        """Create or open one exact authority store."""
        store = cls(
            path,
            notifier=notifier,
            notifier_timeout=notifier_timeout,
        )
        created_parent = not store.path.parent.exists()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store._assert_real_path(store.path)
        if created_parent:
            store.path.parent.chmod(0o700)
        if not store.path.exists():
            descriptor = os.open(
                store.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
            os.close(descriptor)
        if not store.path.is_file():
            raise _error("STATE_PATH_INVALID", "database path is not a file")
        store.path.chmod(0o600)
        connection = store._connect(configure=False)
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }
            expected = {"events", "projections", "schema_meta"}
            if tables and tables != expected:
                raise _error(
                    "SCHEMA_UNSUPPORTED",
                    ",".join(sorted(tables)),
                )
            if tables:
                row = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None or row["value"] != str(SCHEMA_VERSION):
                    raise _error(
                        "SCHEMA_UNSUPPORTED",
                        str(row["value"] if row is not None else "missing"),
                    )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
            if not tables:
                connection.execute(
                    "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
            connection.execute("BEGIN IMMEDIATE")
            store._refresh_projection(connection, updated_at=_time())
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return store

    @staticmethod
    def _assert_real_path(path: Path) -> None:
        for candidate in (path, *path.parents):
            if candidate.exists() and candidate.is_symlink():
                raise _error("STATE_PATH_INVALID", "symlink path is forbidden")

    def _connect(
        self,
        *,
        read_only: bool = False,
        configure: bool = True,
    ) -> sqlite3.Connection:
        if read_only:
            uri = f"{self.path.as_uri()}?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=5,
            )
        else:
            connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                timeout=5,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        elif configure:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _event_rows(
        self,
        connection: sqlite3.Connection,
    ) -> list[sqlite3.Row]:
        return connection.execute("SELECT * FROM events ORDER BY position").fetchall()

    def _refresh_projection(
        self,
        connection: sqlite3.Connection,
        *,
        updated_at: str,
    ) -> dict[str, object]:
        # ponytail: replay all events per write; add incremental projections
        # only if measured event volume makes authority writes too slow.
        rows = self._event_rows(connection)
        state = _project(rows)
        payload = canonical_json(state)
        position = int(rows[-1]["position"]) if rows else 0
        connection.execute(
            """
            INSERT INTO projections (
                name, source_position, payload_json, payload_digest, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                source_position = excluded.source_position,
                payload_json = excluded.payload_json,
                payload_digest = excluded.payload_digest,
                updated_at = excluded.updated_at
            """,
            (
                _PROJECTION_NAME,
                position,
                payload,
                sha256(payload.encode("utf-8")).hexdigest(),
                updated_at,
            ),
        )
        return state

    def _projection(
        self,
        connection: sqlite3.Connection,
    ) -> dict[str, object]:
        row = connection.execute(
            "SELECT * FROM projections WHERE name = ?",
            (_PROJECTION_NAME,),
        ).fetchone()
        position = connection.execute(
            "SELECT COALESCE(MAX(position), 0) AS position FROM events"
        ).fetchone()["position"]
        if row is None or row["source_position"] != position:
            raise ProjectionError("authority projection is missing or stale")
        if (
            sha256(row["payload_json"].encode("utf-8")).hexdigest()
            != row["payload_digest"]
        ):
            raise ProjectionError("authority projection digest mismatch")
        try:
            value = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise ProjectionError("authority projection is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProjectionError("authority projection is not an object")
        return value

    def snapshot(self) -> dict[str, object]:
        """Return the current deterministic state projection."""
        connection = self._connect(read_only=True)
        try:
            connection.execute("BEGIN")
            value = self._projection(connection)
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def events(self) -> list[dict[str, object]]:
        """Return redacted canonical event evidence."""
        connection = self._connect(read_only=True)
        try:
            return [
                {
                    "created_at": row["created_at"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "payload_digest": row["payload_digest"],
                    "position": row["position"],
                }
                for row in self._event_rows(connection)
            ]
        finally:
            connection.close()

    def rebuild_projection(self) -> dict[str, object]:
        """Rebuild the projection from immutable canonical events."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            value = self._refresh_projection(connection, updated_at=_time())
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _append_event(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, object],
        created_at: str,
    ) -> tuple[dict[str, object], bool]:
        event_id = _stable_id(event_id, "event_id")
        event_type = _stable_id(event_type, "event_type")
        created_at = _time(created_at, label="created_at")
        try:
            payload_json = canonical_json(payload)
        except (TypeError, ValueError) as exc:
            raise _error("INPUT_INVALID", "event payload is not JSON") from exc
        payload_digest = sha256(payload_json.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event_type
                    or existing["payload_digest"] != payload_digest
                    or existing["created_at"] != created_at
                ):
                    raise EventConflict(
                        f"event ID reused with changed input: {event_id}"
                    )
                value = self._projection(connection)
                connection.commit()
                return value, True
            latest = connection.execute(
                "SELECT created_at FROM events ORDER BY position DESC LIMIT 1"
            ).fetchone()
            if latest is not None and _moment(created_at) < _moment(
                latest["created_at"]
            ):
                raise _error(
                    "CLOCK_REGRESSION",
                    f"{created_at} precedes {latest['created_at']}",
                )
            connection.execute(
                """
                INSERT INTO events (
                    event_id, event_type, payload_json, payload_digest, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    payload_json,
                    payload_digest,
                    created_at,
                ),
            )
            value = self._refresh_projection(
                connection,
                updated_at=created_at,
            )
            connection.commit()
            return value, False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _record(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, object],
        created_at: str,
    ) -> dict[str, object]:
        value, _ = self._append_event(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
        )
        if not _actionable(event_type, payload) or event_id in value["notifier"]:
            return value
        delivery = self._deliver(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            created_at=created_at,
        )
        value, _ = self._append_event(
            event_id=f"delivery:{digest_json({'source_event_id': event_id})}",
            event_type="notifier_delivery",
            payload=delivery,
            created_at=created_at,
        )
        return value

    def _deliver(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, object],
        created_at: str,
    ) -> dict[str, object]:
        delivery_id = f"notify:{digest_json({'source_event_id': event_id})}"
        result: dict[str, object] = {
            "delivery_id": delivery_id,
            "error": None,
            "output_digest": None,
            "returncode": None,
            "source_event_id": event_id,
            "status": "absent",
        }
        if not self.notifier:
            return result
        redacted = canonical_json(
            {
                "created_at": created_at,
                "delivery_id": delivery_id,
                "event_id": event_id,
                "event_type": event_type,
                "payload_digest": digest_json(payload),
                "target_id": payload.get("target_id")
                or (
                    payload.get("binding", {}).get("target_id")
                    if isinstance(payload.get("binding"), Mapping)
                    else None
                ),
            }
        ).encode("utf-8")
        try:
            completed = subprocess.run(
                list(self.notifier),
                input=redacted,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.notifier_timeout,
            )
            result["output_digest"] = (
                "sha256:"
                + sha256(completed.stdout + b"\0" + completed.stderr).hexdigest()
            )
            result["returncode"] = completed.returncode
            result["status"] = "sent" if completed.returncode == 0 else "failed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["error"] = type(exc).__name__
            result["status"] = "failed"
        return result

    def request_one_off(
        self,
        *,
        event_id: str,
        authority_id: str,
        effect: str,
        binding: Mapping[str, object],
        not_before: datetime | str,
        expires_at: datetime | str,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        created_at = _time(now)
        effect = _text(effect, "effect")
        normalized = _action_binding(binding, effect=effect)
        payload: dict[str, object] = {
            "authority_id": _authority_id(authority_id),
            "binding": normalized,
            "effect": effect,
            "expires_at": _time(expires_at, label="expires_at"),
            "not_before": _time(not_before, label="not_before"),
        }
        payload["request_digest"] = _request_digest(payload)
        state = self._record(
            event_id=event_id,
            event_type="one_off_requested",
            payload=payload,
            created_at=created_at,
        )
        return state["authorities"][payload["authority_id"]]

    def approve_one_off(
        self,
        *,
        event_id: str,
        authority_id: str,
        request_digest: str,
        response_identity: str,
        direct_user_action: bool,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "one-off approval")
        authority_id = _authority_id(authority_id)
        state = self._record(
            event_id=event_id,
            event_type="one_off_approved",
            payload={
                "authority_id": authority_id,
                "request_digest": _digest(
                    request_digest,
                    "request_digest",
                ),
                "response_identity": _stable_id(
                    response_identity,
                    "response_identity",
                ),
            },
            created_at=_time(now),
        )
        return state["authorities"][authority_id]

    def consume_one_off(
        self,
        *,
        event_id: str,
        authority_id: str,
        effect: str,
        binding: Mapping[str, object],
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        authority_id = _authority_id(authority_id)
        effect = _text(effect, "effect")
        normalized = _action_binding(binding, effect=effect)
        self._record(
            event_id=event_id,
            event_type="one_off_consumed",
            payload={
                "authority_id": authority_id,
                "binding": normalized,
                "effect": effect,
            },
            created_at=_time(now),
        )
        return self._envelope(
            authority_id,
            "one_off",
            effect,
            normalized,
            event_id,
        )

    def record_receipt(
        self,
        *,
        event_id: str,
        receipt_id: str,
        receipt_kind: str,
        status: str,
        target_id: str,
        transaction_id: str,
        plan_digest: str,
        source_receipt_id: str,
        payload_digest: str,
        authority_id: str,
        authority_consumption_id: str,
        commit_id: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        """Bind one redacted transaction receipt to consumed authority."""
        receipt_id = _authority_id(receipt_id, "receipt_id")
        state = self._record(
            event_id=event_id,
            event_type="receipt_recorded",
            payload={
                "authority_consumption_id": _stable_id(
                    authority_consumption_id,
                    "authority_consumption_id",
                ),
                "authority_id": _authority_id(authority_id),
                "commit_id": (
                    _git_oid(commit_id, "commit_id") if commit_id is not None else None
                ),
                "payload_digest": _digest(
                    payload_digest,
                    "payload_digest",
                ),
                "plan_digest": _digest(plan_digest, "plan_digest"),
                "receipt_id": receipt_id,
                "receipt_kind": _text(receipt_kind, "receipt_kind"),
                "source_receipt_id": _digest(
                    source_receipt_id,
                    "source_receipt_id",
                    prefixed=True,
                ),
                "status": _text(status, "status"),
                "target_id": _text(target_id, "target_id"),
                "transaction_id": _digest(
                    transaction_id,
                    "transaction_id",
                    prefixed=True,
                ),
            },
            created_at=_time(now),
        )
        return state["receipts"][receipt_id]

    def request_grant(
        self,
        *,
        event_id: str,
        grant_id: str,
        binding: Mapping[str, object],
        not_before: datetime | str,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        created_at = _time(now)
        start = _time(not_before, label="not_before")
        expires = _time(_moment(start) + timedelta(days=30))
        normalized = _common_binding(binding)
        payload: dict[str, object] = {
            "binding": normalized,
            "expires_at": expires,
            "grant_id": _authority_id(grant_id, "grant_id"),
            "not_before": start,
        }
        payload["request_digest"] = _request_digest(payload)
        state = self._record(
            event_id=event_id,
            event_type="grant_requested",
            payload=payload,
            created_at=created_at,
        )
        return state["authorities"][payload["grant_id"]]

    def capture_grant(
        self,
        *,
        event_id: str,
        grant_id: str,
        request_digest: str,
        response_identity: str,
        direct_user_action: bool,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "grant capture")
        grant_id = _authority_id(grant_id, "grant_id")
        state = self._record(
            event_id=event_id,
            event_type="grant_captured",
            payload={
                "grant_id": grant_id,
                "request_digest": _digest(
                    request_digest,
                    "request_digest",
                ),
                "response_identity": _stable_id(
                    response_identity,
                    "response_identity",
                ),
            },
            created_at=_time(now),
        )
        return state["authorities"][grant_id]

    def activate_cohort(
        self,
        *,
        event_id: str,
        grant_ids: Sequence[str],
        common_not_before: datetime | str,
        response_identity: str,
        direct_user_action: bool,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "cohort activation")
        if isinstance(grant_ids, (str, bytes)):
            raise _error("COHORT_INVALID", "grant IDs must be an array")
        state = self._record(
            event_id=event_id,
            event_type="cohort_activated",
            payload={
                "common_not_before": _time(
                    common_not_before,
                    label="common_not_before",
                ),
                "grant_ids": [
                    _authority_id(grant_id, "grant_id") for grant_id in grant_ids
                ],
                "response_identity": _stable_id(
                    response_identity,
                    "response_identity",
                ),
            },
            created_at=_time(now),
        )
        return {target_id: state["targets"][target_id] for target_id in TARGET_ORDER}

    def renew_grant(
        self,
        *,
        event_id: str,
        old_grant_id: str,
        new_grant_id: str,
        not_before: datetime | str,
        response_identity: str,
        direct_user_action: bool,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "grant renewal")
        start = _time(not_before, label="not_before")
        new_id = _authority_id(new_grant_id, "new_grant_id")
        state = self._record(
            event_id=event_id,
            event_type="grant_renewed",
            payload={
                "expires_at": _time(_moment(start) + timedelta(days=30)),
                "new_grant_id": new_id,
                "not_before": start,
                "old_grant_id": _authority_id(
                    old_grant_id,
                    "old_grant_id",
                ),
                "response_identity": _stable_id(
                    response_identity,
                    "response_identity",
                ),
            },
            created_at=_time(now),
        )
        return state["authorities"][new_id]

    def retire_grant(
        self,
        *,
        event_id: str,
        grant_id: str,
        disposition: str,
        reason: str,
        response_identity: str | None = None,
        direct_user_action: bool = False,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if disposition == "revoked" and direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "grant revocation")
        if disposition == "invalidated" and direct_user_action is True:
            raise _error(
                "GRANT_RETIREMENT_INVALID",
                "policy invalidation is not a direct-user revocation",
            )
        grant_id = _authority_id(grant_id, "grant_id")
        state = self._record(
            event_id=event_id,
            event_type="grant_retired",
            payload={
                "disposition": _text(disposition, "disposition"),
                "grant_id": grant_id,
                "reason": _text(reason, "reason"),
                "response_identity": (
                    _stable_id(response_identity, "response_identity")
                    if response_identity is not None
                    else None
                ),
            },
            created_at=_time(now),
        )
        return state["authorities"][grant_id]

    def pause_target(
        self,
        *,
        event_id: str,
        target_id: str,
        grant_id: str,
        lineage_anchor: str,
        reason: str,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        target_id = _text(target_id, "target_id")
        state = self._record(
            event_id=event_id,
            event_type="target_paused",
            payload={
                "grant_id": _authority_id(grant_id, "grant_id"),
                "lineage_anchor": _git_oid(
                    lineage_anchor,
                    "lineage_anchor",
                ),
                "reason": _text(reason, "reason"),
                "target_id": target_id,
            },
            created_at=_time(now),
        )
        return state["targets"][target_id]

    def resume_target(
        self,
        *,
        event_id: str,
        target_id: str,
        grant_id: str,
        lineage_anchor: str,
        response_identity: str,
        direct_user_action: bool,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        if direct_user_action is not True:
            raise _error("DIRECT_USER_REQUIRED", "target resume")
        target_id = _text(target_id, "target_id")
        state = self._record(
            event_id=event_id,
            event_type="target_resumed",
            payload={
                "grant_id": _authority_id(grant_id, "grant_id"),
                "lineage_anchor": _git_oid(
                    lineage_anchor,
                    "lineage_anchor",
                ),
                "response_identity": _stable_id(
                    response_identity,
                    "response_identity",
                ),
                "target_id": target_id,
            },
            created_at=_time(now),
        )
        return state["targets"][target_id]

    def consume_grant(
        self,
        *,
        event_id: str,
        grant_id: str,
        effect: str,
        binding: Mapping[str, object],
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        grant_id = _authority_id(grant_id, "grant_id")
        effect = _text(effect, "effect")
        normalized = (
            _plan_binding(binding)
            if effect == "plan"
            else _action_binding(binding, effect=effect)
        )
        self._record(
            event_id=event_id,
            event_type="grant_action_consumed",
            payload={
                "binding": normalized,
                "effect": effect,
                "grant_id": grant_id,
            },
            created_at=_time(now),
        )
        return self._envelope(
            grant_id,
            "grant",
            effect,
            normalized,
            event_id,
        )

    @staticmethod
    def _envelope(
        authority_id: str,
        authority_kind: str,
        effect: str,
        binding: Mapping[str, str],
        consumption_id: str,
    ) -> dict[str, object]:
        if effect == "commit":
            return {
                "authority_id": authority_id,
                "authority_kind": authority_kind,
                "base_head": binding["base_head"],
                "branch": binding["branch"],
                "enabled": True,
                "lineage_anchor": binding["lineage_anchor"],
                "plan_digest": binding["plan_digest"],
                "source_receipt_id": binding["source_receipt_id"],
            }
        return {
            "authority_id": authority_id,
            "authority_kind": authority_kind,
            "binding_digest": digest_json(binding),
            "consumption_id": consumption_id,
            "effect": effect,
            "enabled": True,
        }

    def start_cycle(
        self,
        *,
        event_id: str,
        cycle_id: str,
        source: Mapping[str, object],
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        cycle_id = _stable_id(cycle_id, "cycle_id")
        state = self._record(
            event_id=event_id,
            event_type="cycle_started",
            payload={
                "cycle_id": cycle_id,
                "source": _source_identity(source),
            },
            created_at=_time(now),
        )
        return state["cycles"][cycle_id]

    def record_cycle_skip(
        self,
        *,
        event_id: str,
        cycle_id: str,
        target_id: str,
        result: str,
        reason_code: str,
        evidence_digest: str,
        source_identity_digest: str,
        independence: Mapping[str, object],
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        return self._record_cycle_slot(
            event_id=event_id,
            cycle_id=cycle_id,
            target_id=target_id,
            result=result,
            attempt_id=event_id,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
            independence=independence,
            mode="skip",
            plan_digest=None,
            source_receipt_id=None,
            source_identity_digest=_digest(
                source_identity_digest,
                "source_identity_digest",
            ),
            target_identity_digest=None,
            zero_write_proof=None,
            now=now,
        )

    def record_attempt(
        self,
        *,
        event_id: str,
        attempt_id: str,
        cycle_id: str,
        target_id: str,
        result: str,
        reason_code: str,
        plan_digest: str,
        source_receipt_id: str,
        source_identity_digest: str,
        target_identity_digest: str,
        evidence_digest: str,
        independence: Mapping[str, object],
        zero_write_proof: str | None = None,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        return self._record_cycle_slot(
            event_id=event_id,
            cycle_id=cycle_id,
            target_id=target_id,
            result=result,
            attempt_id=attempt_id,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
            independence=independence,
            mode="attempt",
            plan_digest=_digest(plan_digest, "plan_digest"),
            source_receipt_id=_digest(
                source_receipt_id,
                "source_receipt_id",
                prefixed=True,
            ),
            source_identity_digest=_digest(
                source_identity_digest,
                "source_identity_digest",
            ),
            target_identity_digest=_digest(
                target_identity_digest,
                "target_identity_digest",
            ),
            zero_write_proof=(
                _digest(zero_write_proof, "zero_write_proof")
                if zero_write_proof is not None
                else None
            ),
            now=now,
        )

    def _record_cycle_slot(
        self,
        *,
        event_id: str,
        cycle_id: str,
        target_id: str,
        result: str,
        attempt_id: str,
        reason_code: str,
        evidence_digest: str,
        independence: Mapping[str, object],
        mode: str,
        plan_digest: str | None,
        source_receipt_id: str | None,
        source_identity_digest: str,
        target_identity_digest: str | None,
        zero_write_proof: str | None,
        now: datetime | str | None,
    ) -> dict[str, object]:
        cycle_id = _stable_id(cycle_id, "cycle_id")
        state = self._record(
            event_id=event_id,
            event_type="cycle_slot_recorded",
            payload={
                "attempt_id": _stable_id(attempt_id, "attempt_id"),
                "cycle_id": cycle_id,
                "evidence_digest": _digest(
                    evidence_digest,
                    "evidence_digest",
                ),
                "independence": _independence(independence),
                "mode": mode,
                "plan_digest": plan_digest,
                "reason_code": _text(reason_code, "reason_code"),
                "result": _text(result, "result"),
                "source_identity_digest": source_identity_digest,
                "source_receipt_id": source_receipt_id,
                "target_id": _text(target_id, "target_id"),
                "target_identity_digest": target_identity_digest,
                "zero_write_proof": zero_write_proof,
            },
            created_at=_time(now),
        )
        return state["cycles"][cycle_id]

    def block_cycle_source(
        self,
        *,
        event_id: str,
        cycle_id: str,
        observed_source_digest: str,
        reason: str,
        now: datetime | str | None = None,
    ) -> dict[str, object]:
        cycle_id = _stable_id(cycle_id, "cycle_id")
        state = self._record(
            event_id=event_id,
            event_type="cycle_source_blocked",
            payload={
                "cycle_id": cycle_id,
                "observed_source_digest": _digest(
                    observed_source_digest,
                    "observed_source_digest",
                ),
                "reason": _text(reason, "reason"),
            },
            created_at=_time(now),
        )
        return state["cycles"][cycle_id]

    def derive_deadlines(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[dict[str, object]]:
        created_at = _time(now)
        due: list[dict[str, object]] = []
        state = self.snapshot()
        for grant_id, authority in sorted(state["authorities"].items()):
            if authority.get("kind") != "grant" or authority.get("state") not in {
                "active",
                "disarmed",
            }:
                continue
            expiry = _moment(authority["expires_at"])
            milestones = (
                ("t-7d", expiry - timedelta(days=7)),
                ("t-1d", expiry - timedelta(days=1)),
                ("expiry", expiry),
            )
            for milestone, due_at in milestones:
                key = f"{grant_id}:{milestone}"
                if key in state["reminders"] or _moment(created_at) < due_at:
                    continue
                event_id = f"deadline:{digest_json({'key': key})}"
                state = self._record(
                    event_id=event_id,
                    event_type="deadline_due",
                    payload={
                        "due_at": _time(due_at),
                        "grant_id": grant_id,
                        "milestone": milestone,
                        "target_id": authority["binding"]["target_id"],
                    },
                    created_at=created_at,
                )
                due.append(state["reminders"][key])
        return due


__all__ = [
    "AuthorityError",
    "AuthorityStore",
    "EventConflict",
    "ENROLLED_TARGETS",
    "ProjectionError",
    "SCHEMA_VERSION",
    "TARGET_ORDER",
    "default_state_path",
]
