#!/usr/bin/env python3
"""Persist one Loop v1 transport message and emit its exact ingest handoff."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any


ACTION_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ACTION_FIELDS = frozenset(
    {
        "action_digest",
        "action_id",
        "action_type",
        "authority_digest",
        "payload",
        "schema_version",
    }
)
_CAPTURE_FIELDS = frozenset(
    {
        "action",
        "payload",
        "raw_message",
        "role",
        "surface",
        "transport_identity",
    }
)
_ROLE_ACTIONS = {
    "worker": ("dispatch_workers", "worker_result"),
    "precommit_reviewer": ("precommit_review", "precommit_review"),
    "final_reviewer": ("final_review", "final_review"),
}


class CaptureError(ValueError):
    """Raised when transport evidence cannot be bound safely."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pretty_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _exact_object(
    value: object, expected: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CaptureError(f"{label} must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise CaptureError(f"{label} has invalid fields ({'; '.join(details)})")
    return dict(value)


def _required_text(value: object, field: str, *, preserve: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(f"{field} must be non-empty text")
    if len(value) > 4096 and field != "raw_message":
        raise CaptureError(f"{field} is too long")
    return value if preserve else value.strip()


def _validate_run_id(value: str) -> str:
    run_id = _required_text(value, "run_id")
    if not _SAFE_ID.fullmatch(run_id):
        raise CaptureError("run_id must use only letters, digits, dot, underscore, or dash")
    return run_id


def _repo_relative_path(repo_root: Path, value: str, label: str) -> Path:
    text = _required_text(value, label)
    if "\\" in text:
        raise CaptureError(f"{label} must use repository-relative POSIX syntax")
    pure = PurePosixPath(text)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise CaptureError(f"{label} must be repository-relative without '..'")
    path = repo_root / Path(*pure.parts)
    resolved = path.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise CaptureError(f"{label} escapes repo_root") from exc
    return path


def _relative(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _run_root(repo_root: Path, run_id: str) -> Path:
    return repo_root / ".trellis" / ".runtime" / "loop-v1" / "parents" / run_id


def _validate_action(value: object) -> dict[str, Any]:
    action = _exact_object(value, _ACTION_FIELDS, "action")
    if action["schema_version"] != ACTION_SCHEMA_VERSION or isinstance(
        action["schema_version"], bool
    ):
        raise CaptureError("action schema_version is unsupported")
    for field in ("action_id", "action_type", "authority_digest"):
        action[field] = _required_text(action[field], field)
    digest = _required_text(action["action_digest"], "action_digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise CaptureError("action_digest must be a sha256 digest")
    if not isinstance(action["payload"], Mapping):
        raise CaptureError("action payload must be an object")
    action["payload"] = dict(action["payload"])
    core = {key: action[key] for key in sorted(_ACTION_FIELDS - {"action_digest"})}
    if digest != f"sha256:{_digest_json(core)}":
        raise CaptureError("action digest differs from the supplied action")
    action["action_digest"] = digest
    return action


def _approved_surface(action: Mapping[str, Any], surface: str) -> None:
    surfaces = action["payload"].get("approved_agent_surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise CaptureError("action approved_agent_surfaces must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in surfaces):
        raise CaptureError("action approved_agent_surfaces contains invalid text")
    if surface not in surfaces:
        raise CaptureError("selected surface is not approved by the current action")


def _bind_worker(action: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    child_id = _required_text(payload.get("child_id"), "payload child_id")
    packet_id = _required_text(payload.get("packet_id"), "payload packet_id")
    children = action["payload"].get("children")
    if not isinstance(children, list) or not children:
        raise CaptureError("dispatch action children must be a non-empty list")
    matches = [
        item
        for item in children
        if isinstance(item, Mapping) and item.get("child_id") == child_id
    ]
    if len(matches) != 1:
        raise CaptureError("worker child is outside the pending dispatch")
    packet = matches[0].get("packet")
    if not isinstance(packet, Mapping) or packet.get("packet_id") != packet_id:
        raise CaptureError("worker packet differs from the pending dispatch")


def _bind_reviewer(
    action: Mapping[str, Any], payload: Mapping[str, Any], identity: str, role: str
) -> None:
    if payload.get("reviewer_identity") != identity:
        raise CaptureError("reviewer_identity differs from transport_identity")
    if role == "final_reviewer" and payload.get(
        "fresh_context_receipt"
    ) != action["payload"].get("fresh_context_receipt"):
        raise CaptureError("final review context receipt differs from the action")


def _validate_capture(value: object) -> tuple[dict[str, Any], str]:
    capture = _exact_object(value, _CAPTURE_FIELDS, "capture input")
    action = _validate_action(capture["action"])
    role = _required_text(capture["role"], "role")
    if role not in _ROLE_ACTIONS:
        raise CaptureError(f"unsupported transport role: {role}")
    expected_action, message_type = _ROLE_ACTIONS[role]
    if action["action_type"] != expected_action:
        raise CaptureError(f"role {role} cannot answer {action['action_type']}")
    surface = _required_text(capture["surface"], "surface")
    identity = _required_text(capture["transport_identity"], "transport_identity")
    raw_message = _required_text(capture["raw_message"], "raw_message", preserve=True)
    payload = capture["payload"]
    if not isinstance(payload, Mapping):
        raise CaptureError("payload must be an object")
    payload = dict(payload)
    _approved_surface(action, surface)
    if role == "worker":
        _bind_worker(action, payload)
    else:
        _bind_reviewer(action, payload, identity, role)
    return (
        {
            "action": action,
            "payload": payload,
            "raw_message": raw_message,
            "role": role,
            "surface": surface,
            "transport_identity": identity,
        },
        message_type,
    )


def _preflight_existing(path: Path, payload: bytes, label: str) -> bool:
    if not path.exists():
        return False
    if not path.is_file() or path.is_symlink():
        raise CaptureError(f"existing {label} is not a regular file")
    if path.read_bytes() != payload:
        raise CaptureError(f"existing {label} conflicts with this capture")
    return True


def _assert_destination(path: Path, run_root: Path, label: str) -> None:
    resolved_parent = path.parent.resolve()
    if resolved_parent != path.parent:
        raise CaptureError(f"{label} parent must not contain a symlink")
    try:
        resolved_parent.relative_to(run_root)
    except ValueError as exc:
        raise CaptureError(f"{label} escapes the run directory") from exc


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def capture_transport(
    repo_root: Path, run_id: str, input_path: str
) -> dict[str, object]:
    """Validate and persist one transport capture without opening Loop authority."""
    root = Path(repo_root).resolve()
    if not (root / ".git").exists():
        raise CaptureError("repo_root must contain a Git worktree")
    run = _validate_run_id(run_id)
    path = _repo_relative_path(root, input_path, "input")
    inbox = (_run_root(root, run) / "transport" / "inbox").resolve()
    if path.resolve() != path or path.parent != inbox or path.suffix != ".json":
        raise CaptureError("input must be a JSON file directly under the run transport inbox")
    if not path.is_file() or path.is_symlink():
        raise CaptureError("input must be a regular JSON file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise CaptureError("input permissions must be 0600 or stricter")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"input does not contain valid UTF-8 JSON: {exc}") from exc
    capture, message_type = _validate_capture(value)
    message_digest = _digest_json(capture)
    message_id = f"sha256:{message_digest}"
    run_root = _run_root(root, run)
    record_path = run_root / "transport" / "records" / f"{message_digest}.json"
    ingest_path = run_root / "outputs" / f"transport-{message_digest}.json"
    ingest = {
        "action_digest": capture["action"]["action_digest"],
        "action_id": capture["action"]["action_id"],
        "message_type": message_type,
        "payload": capture["payload"],
    }
    record_bytes = _pretty_json(capture)
    ingest_bytes = _pretty_json(ingest)
    _assert_destination(record_path, run_root, "transport record")
    _assert_destination(ingest_path, run_root, "ingest output")
    record_exists = _preflight_existing(record_path, record_bytes, "transport record")
    ingest_exists = _preflight_existing(ingest_path, ingest_bytes, "ingest output")
    if not record_exists:
        _write_atomic(record_path, record_bytes)
    else:
        os.chmod(record_path, 0o600)
    if not ingest_exists:
        _write_atomic(ingest_path, ingest_bytes)
    else:
        os.chmod(ingest_path, 0o600)
    return {
        "action_digest": capture["action"]["action_digest"],
        "ingest_path": _relative(root, ingest_path),
        "message_id": message_id,
        "payload_digest": f"sha256:{_digest_json(capture['payload'])}",
        "raw_message_digest": f"sha256:{sha256(capture['raw_message'].encode('utf-8')).hexdigest()}",
        "record_path": _relative(root, record_path),
        "replayed": record_exists and ingest_exists,
        "status": "captured",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    try:
        result = capture_transport(Path(args.repo_root), args.run_id, args.input)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (CaptureError, OSError) as exc:
        error = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "status": "failed",
        }
        print(json.dumps(error, ensure_ascii=False, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
