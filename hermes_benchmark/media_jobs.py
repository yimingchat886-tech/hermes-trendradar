"""Agent-facing video download jobs with file-backed orchestration."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Formatter
from typing import Any

from .external_runtime import run_process

SCHEMA_VERSION = "2.0"
RETENTION = timedelta(days=7)
PLATFORMS = {"douyin", "youtube", "bilibili", "direct"}
PAGE_PLATFORMS = PLATFORMS - {"direct"}
TERMINAL_STATUSES = {"succeeded", "partial", "failed"}
JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
DIRECT_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm"}
TOMBSTONE_RE = re.compile(r"\.deleting-([A-Za-z0-9][A-Za-z0-9._-]{0,63})-[0-9]+\Z")

EXIT_OK = 0
EXIT_CONTRACT = 2
EXIT_BACKEND = 3
EXIT_FAILED = 4
EXIT_NOT_FOUND = 6
EXIT_LOCKED = 9


class MediaJobError(RuntimeError):
    def __init__(self, code: str, message: str, *, exit_code: int = EXIT_CONTRACT, retryable: bool = False):
        self.code = code
        self.exit_code = exit_code
        self.retryable = retryable
        super().__init__(message)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise MediaJobError("contract_mismatch", message)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        _assert_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_media_profile(path: str | Path, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    profile_path = Path(path)
    if profile_path.is_symlink() or not profile_path.is_file():
        raise MediaJobError("config_invalid", "media profile is unavailable")
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaJobError("config_invalid", "media profile is not valid JSON") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
        raise MediaJobError("config_invalid", "media profile schema_version must be 2.0")

    repo = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    run_root = Path(str(raw.get("run_root") or ""))
    if not run_root.is_absolute():
        raise MediaJobError("config_invalid", "run_root must be an absolute path")
    if run_root.is_symlink():
        raise MediaJobError("config_invalid", "run_root must not be a symlink")
    run_root = run_root.resolve()
    if _inside(run_root, repo) or _inside(repo, run_root):
        raise MediaJobError("config_invalid", "run_root must not overlap the Git repository")

    timeout = _bounded_int(raw.get("timeout_seconds", 600), "timeout_seconds", 1, 86_400)
    max_bytes = _bounded_int(raw.get("max_bytes", 2 * 1024**3), "max_bytes", 1, 20 * 1024**3)
    backends_raw = raw.get("backends", {})
    if not isinstance(backends_raw, Mapping):
        raise MediaJobError("config_invalid", "backends must be an object")
    backends: dict[str, dict[str, list[str]]] = {}
    for platform, value in backends_raw.items():
        if platform not in PAGE_PLATFORMS or not isinstance(value, Mapping):
            raise MediaJobError("config_invalid", "backend keys must be douyin, youtube, or bilibili")
        argv = _command(value.get("argv"), required_fields={"url", "output"})
        probe = _command(value.get("probe_argv"), required_fields=set())
        backends[str(platform)] = {"argv": argv, "probe_argv": probe}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_root": run_root,
        "timeout_seconds": timeout,
        "max_bytes": max_bytes,
        "backends": backends,
    }


def healthcheck(profile: Mapping[str, Any]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {"direct": {"status": "available", "backend": "stdlib"}}
    for platform in sorted(PAGE_PLATFORMS):
        backend = profile["backends"].get(platform)
        if not backend:
            checks[platform] = {"status": "unavailable", "error_code": "backend_unavailable"}
            continue
        if not _resolve_executable(backend["argv"][0]) or not _resolve_executable(backend["probe_argv"][0]):
            checks[platform] = {"status": "unavailable", "error_code": "backend_unavailable"}
            continue
        try:
            completed = subprocess.run(
                backend["probe_argv"],
                capture_output=True,
                text=True,
                timeout=min(int(profile["timeout_seconds"]), 30),
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            checks[platform] = {"status": "unavailable", "error_code": "backend_unavailable"}
            continue
        version = (completed.stdout.strip() or completed.stderr.strip()).splitlines()[:1]
        checks[platform] = {
            "status": "available" if completed.returncode == 0 else "unavailable",
            "error_code": "" if completed.returncode == 0 else "backend_unavailable",
            "version": version[0][:120] if version else "",
        }
    available = sum(item["status"] == "available" for item in checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "healthcheck",
        "status": "succeeded" if available == len(checks) else "partial",
        "checks": checks,
        "available": available,
        "unavailable": len(checks) - available,
        "exit_code": EXIT_OK if available == len(checks) else EXIT_BACKEND,
    }


def fetch(
    profile: Mapping[str, Any],
    request_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = _load_request(request_path)
    job_dir = _job_dir(profile, request["job_id"], create=True)
    with _job_lock(job_dir):
        state_path = job_dir / "job.json"
        if state_path.exists():
            state = _read_json(state_path, "job state")
            _validate_state(state, request["job_id"])
            if state.get("request_digest") != _digest(request):
                raise MediaJobError("job_conflict", "job_id already binds a different request")
            if state.get("status") in TERMINAL_STATUSES:
                expires = _parse_time(str(state.get("expires_at") or ""))
                if expires and _utc(now) >= expires:
                    raise MediaJobError("job_expired", "job has reached its retention deadline", exit_code=EXIT_NOT_FOUND)
                invalid = _invalid_successes(state, job_dir)
                if not invalid:
                    if not _manifest_matches(state, job_dir):
                        _write_jsonl_atomic(job_dir / "media-manifest.private.jsonl", state["items"])
                    return _envelope(state, job_dir)
        else:
            state = _new_state(request)
            _write_json_atomic(job_dir / "request.private.json", request)
        targets = {
            item["asset_id"]
            for item in state["items"]
            if item["download_status"] in {"pending", "running"}
            or (item["download_status"] == "failed" and item["retryable"] is True)
        }
        return _run_job(profile, job_dir, state, targets, now=now)


def status(profile: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    job_dir = _job_dir(profile, job_id)
    state = _read_json(job_dir / "job.json", "job state")
    _validate_state(state, job_id)
    if state["status"] in TERMINAL_STATUSES and (not _manifest_matches(state, job_dir) or _invalid_successes(dict(state), job_dir)):
        raise MediaJobError("job_state_invalid", "terminal job artifacts are missing or corrupt")
    return _envelope(state, job_dir)


def retry(profile: Mapping[str, Any], job_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    job_dir = _job_dir(profile, job_id)
    with _job_lock(job_dir):
        state = _read_json(job_dir / "job.json", "job state")
        _validate_state(state, job_id)
        current = _utc(now)
        expires = _parse_time(str(state.get("expires_at") or ""))
        if expires and current >= expires:
            raise MediaJobError("job_expired", "job has reached its retention deadline", exit_code=EXIT_NOT_FOUND)
        _invalid_successes(state, job_dir)
        targets = {
            item["asset_id"]
            for item in state["items"]
            if item["download_status"] in {"pending", "running"}
            or (item["download_status"] == "failed" and item["retryable"] is True)
        }
        return _run_job(profile, job_dir, state, targets, now=current)


def cleanup_expired(profile: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    root = Path(profile["run_root"])
    if root.is_symlink():
        raise MediaJobError("boundary_violation", "run_root must not be a symlink")
    if not root.exists():
        return _cleanup_envelope([], 0)
    current = _utc(now)
    deleted: list[str] = []
    locked = 0
    for candidate in sorted(root.iterdir(), key=lambda item: item.name):
        tombstone_match = TOMBSTONE_RE.fullmatch(candidate.name)
        if tombstone_match and candidate.is_dir() and not candidate.is_symlink():
            state = _read_json(candidate / "job.json", "job state")
            _validate_state(state, tombstone_match.group(1))
            expires = _parse_time(str(state.get("expires_at") or ""))
            if state["status"] in TERMINAL_STATUSES and expires and current >= expires:
                shutil.rmtree(candidate)
            continue
        if candidate.is_symlink() or not candidate.is_dir() or not JOB_ID_RE.fullmatch(candidate.name):
            continue
        try:
            with _job_lock(candidate, create=False):
                state = _read_json(candidate / "job.json", "job state")
                _validate_state(state, candidate.name)
                expires = _parse_time(str(state.get("expires_at") or ""))
                if state.get("status") not in TERMINAL_STATUSES or not expires or current < expires:
                    continue
                tombstone = root / f".deleting-{candidate.name}-{os.getpid()}"
                if tombstone.exists() or tombstone.is_symlink():
                    raise MediaJobError("boundary_violation", "cleanup tombstone already exists")
                candidate.rename(tombstone)
            shutil.rmtree(tombstone)
            deleted.append(candidate.name)
        except MediaJobError as exc:
            if exc.code == "job_locked":
                locked += 1
                continue
            raise
    return _cleanup_envelope(deleted, locked)


def _run_job(
    profile: Mapping[str, Any],
    job_dir: Path,
    state: dict[str, Any],
    target_asset_ids: set[str],
    *,
    now: datetime | None,
) -> dict[str, Any]:
    if not target_asset_ids and state["status"] in TERMINAL_STATUSES:
        if not _manifest_matches(state, job_dir):
            _write_jsonl_atomic(job_dir / "media-manifest.private.jsonl", state["items"])
        return _envelope(state, job_dir)
    state["status"] = "running"
    state["completed_at"] = ""
    state["expires_at"] = ""
    _write_json_atomic(job_dir / "job.json", state)
    for item in state["items"]:
        if item["asset_id"] not in target_asset_ids:
            continue
        item["download_status"] = "running"
        item["error_code"] = ""
        item["retryable"] = False
        _write_json_atomic(job_dir / "job.json", state)
        try:
            _download_item(profile, job_dir, item)
        except MediaJobError as exc:
            item.update(
                download_status="failed",
                local_media_path="",
                media_size_bytes=0,
                media_hash="",
                error_code=exc.code,
                retryable=exc.retryable,
            )
        _write_json_atomic(job_dir / "job.json", state)

    succeeded = sum(item["download_status"] == "succeeded" for item in state["items"])
    failed = sum(item["download_status"] == "failed" for item in state["items"])
    completed = _utc(now)
    state.update(
        status="succeeded" if not failed else "partial" if succeeded else "failed",
        succeeded=succeeded,
        failed=failed,
        completed_at=_format_time(completed),
        expires_at=_format_time(completed + RETENTION),
    )
    _write_jsonl_atomic(job_dir / "media-manifest.private.jsonl", state["items"])
    _write_json_atomic(job_dir / "job.json", state)
    return _envelope(state, job_dir)


def _download_item(profile: Mapping[str, Any], job_dir: Path, item: dict[str, Any]) -> None:
    media_dir = job_dir / "media"
    _mkdir_private(media_dir)
    final_path = _media_path(job_dir, item)
    if final_path.is_symlink():
        raise MediaJobError("boundary_violation", "media path must not be a symlink")
    if _verified_file(final_path, item.get("media_hash")):
        _mark_success(item, final_path)
        return
    final_path.unlink(missing_ok=True)
    temp_path = media_dir / f".{item['asset_id']}.part{final_path.suffix}"
    temp_path.unlink(missing_ok=True)
    if item["platform"] == "unsupported":
        raise MediaJobError("unsupported_source", "source platform is not supported")
    if item["platform"] == "direct":
        _download_direct(item["source_url"], temp_path, int(profile["timeout_seconds"]), int(profile["max_bytes"]))
    else:
        _download_external(profile, job_dir, item, temp_path)
    if not _usable_file(temp_path):
        temp_path.unlink(missing_ok=True)
        raise MediaJobError("invalid_media", "backend did not produce a readable media file", retryable=True)
    if temp_path.stat().st_size > int(profile["max_bytes"]):
        temp_path.unlink(missing_ok=True)
        raise MediaJobError("media_too_large", "media exceeds configured max_bytes")
    temp_path.replace(final_path)
    _mark_success(item, final_path)


def _download_external(profile: Mapping[str, Any], job_dir: Path, item: Mapping[str, Any], output: Path) -> None:
    backend = profile["backends"].get(item["platform"])
    if not backend or not _resolve_executable(backend["argv"][0]):
        raise MediaJobError("backend_unavailable", "configured backend is unavailable", exit_code=EXIT_BACKEND, retryable=True)
    command = [token.format(url=item["source_url"], output=str(output)) for token in backend["argv"]]
    log_dir = job_dir / "logs"
    _mkdir_private(log_dir)
    try:
        result = run_process(
            f"download-{item['asset_id']}",
            command,
            mode="real",
            log_dir=log_dir,
            sensitive_values=[str(item["source_url"])],
            timeout_seconds=int(profile["timeout_seconds"]),
        )
    except FileNotFoundError as exc:
        raise MediaJobError("backend_unavailable", "configured backend is unavailable", exit_code=EXIT_BACKEND, retryable=True) from exc
    except subprocess.TimeoutExpired as exc:
        output.unlink(missing_ok=True)
        raise MediaJobError("download_timeout", "backend exceeded timeout", exit_code=EXIT_BACKEND, retryable=True) from exc
    if result.get("exit_code") != 0:
        output.unlink(missing_ok=True)
        raise MediaJobError("download_failed", "backend returned a non-zero exit code", exit_code=EXIT_BACKEND, retryable=True)


def _download_direct(url: str, output: Path, timeout: int, max_bytes: int) -> None:
    _assert_public_http_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "TrendRadarMedia/2.0", "Accept": "video/*,*/*;q=0.8"})
    opener = urllib.request.build_opener(PublicRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response, output.open("xb") as handle:
            _assert_public_http_url(response.geturl())
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type.startswith("text/") or "json" in content_type or "html" in content_type:
                raise MediaJobError("invalid_media", "direct URL returned a non-media response")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise MediaJobError("media_too_large", "media exceeds configured max_bytes")
            total = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaJobError("media_too_large", "media exceeds configured max_bytes")
                handle.write(chunk)
    except MediaJobError:
        output.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        output.unlink(missing_ok=True)
        raise MediaJobError("download_failed", "direct media download failed", retryable=True) from exc


def _load_request(path: str | Path) -> dict[str, Any]:
    value = _read_json(Path(path), "request")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise MediaJobError("request_invalid", "request schema_version must be 2.0")
    job_id = str(value.get("job_id") or "")
    if not JOB_ID_RE.fullmatch(job_id):
        raise MediaJobError("request_invalid", "job_id must use 1-64 safe characters")
    sources = value.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
        raise MediaJobError("request_invalid", "sources must contain 1-100 items")
    normalized = []
    source_ids: set[str] = set()
    for index, raw in enumerate(sources, start=1):
        if not isinstance(raw, Mapping):
            raise MediaJobError("request_invalid", "every source must be an object")
        source_id = str(raw.get("source_id") or f"source-{index:03d}")
        if not JOB_ID_RE.fullmatch(source_id) or source_id in source_ids:
            raise MediaJobError("request_invalid", "source_id values must be unique safe identifiers")
        source_ids.add(source_id)
        url = str(raw.get("url") or "")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise MediaJobError("request_invalid", "source URLs must be credential-free HTTP(S) URLs")
        declared = str(raw.get("platform") or "").lower()
        platform = _classify_source(url, declared)
        normalized.append(
            {
                "source_id": source_id,
                "asset_id": "asset-" + hashlib.sha256(f"{source_id}\0{url}".encode()).hexdigest()[:20],
                "platform": platform,
                "source_url": url,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "job_id": job_id, "sources": normalized}


def _new_state(request: Mapping[str, Any]) -> dict[str, Any]:
    items = []
    for source in request["sources"]:
        items.append(
            {
                **source,
                "media_type": "video",
                "local_media_path": "",
                "media_size_bytes": 0,
                "media_hash": "",
                "download_status": "pending",
                "error_code": "",
                "retryable": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": request["job_id"],
        "request_digest": _digest(request),
        "status": "running",
        "completed_at": "",
        "expires_at": "",
        "succeeded": 0,
        "failed": 0,
        "items": items,
    }


def _envelope(state: Mapping[str, Any], job_dir: Path) -> dict[str, Any]:
    job_status = str(state.get("status") or "failed")
    exit_code = EXIT_OK if job_status == "succeeded" else EXIT_FAILED if job_status in TERMINAL_STATUSES else EXIT_OK
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": state.get("job_id", ""),
        "status": job_status,
        "manifest_ref": str((job_dir / "media-manifest.private.jsonl").resolve()) if job_status in TERMINAL_STATUSES else "",
        "completed_at": state.get("completed_at", ""),
        "expires_at": state.get("expires_at", ""),
        "succeeded": int(state.get("succeeded", 0)),
        "failed": int(state.get("failed", 0)),
        "exit_code": exit_code,
        "error": None,
    }


def _cleanup_envelope(deleted: list[str], locked: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "cleanup",
        "status": "succeeded",
        "deleted_job_ids": deleted,
        "deleted": len(deleted),
        "locked": locked,
        "exit_code": EXIT_OK,
        "error": None,
    }


def _error_envelope(exc: MediaJobError, job_id: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": "failed",
        "manifest_ref": "",
        "completed_at": "",
        "expires_at": "",
        "succeeded": 0,
        "failed": 0,
        "exit_code": exc.exit_code,
        "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
    }


def _classify_source(url: str, declared: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    inferred = "unsupported"
    if _host_matches(host, "youtu.be") or _host_matches(host, "youtube.com"):
        inferred = "youtube"
    elif _host_matches(host, "b23.tv") or _host_matches(host, "bilibili.com"):
        inferred = "bilibili"
    elif _host_matches(host, "douyin.com") or _host_matches(host, "iesdouyin.com"):
        inferred = "douyin"
    elif Path(parsed.path).suffix.lower() in DIRECT_SUFFIXES:
        inferred = "direct"
    if not declared:
        return inferred
    if declared == "direct":
        return "direct"
    return declared if declared in PAGE_PLATFORMS and declared == inferred else "unsupported"


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _job_dir(profile: Mapping[str, Any], job_id: str, *, create: bool = False) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise MediaJobError("request_invalid", "job_id must use 1-64 safe characters")
    root = Path(profile["run_root"])
    if root.is_symlink():
        raise MediaJobError("boundary_violation", "run_root must not be a symlink")
    if create:
        _mkdir_private(root)
    job_dir = root / job_id
    if job_dir.is_symlink():
        raise MediaJobError("boundary_violation", "job directory must not be a symlink")
    if create:
        _mkdir_private(job_dir)
    elif not job_dir.is_dir():
        raise MediaJobError("job_not_found", "job does not exist", exit_code=EXIT_NOT_FOUND)
    if not _inside(job_dir.resolve(), root.resolve()):
        raise MediaJobError("boundary_violation", "job directory escaped run_root")
    return job_dir


@contextlib.contextmanager
def _job_lock(job_dir: Path, *, create: bool = True) -> Iterator[None]:
    if create:
        _mkdir_private(job_dir)
    lock_path = job_dir / ".lock"
    if lock_path.is_symlink():
        raise MediaJobError("boundary_violation", "job lock must not be a symlink")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MediaJobError("job_locked", "job already has an active writer", exit_code=EXIT_LOCKED, retryable=True) from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _mark_success(item: dict[str, Any], path: Path) -> None:
    path.chmod(0o600)
    item.update(
        download_status="succeeded",
        local_media_path=str(path.resolve()),
        media_size_bytes=path.stat().st_size,
        media_hash=_sha256_file(path),
        error_code="",
        retryable=False,
    )


def _invalid_successes(state: dict[str, Any], job_dir: Path) -> set[str]:
    invalid: set[str] = set()
    for item in state["items"]:
        if item["download_status"] != "succeeded":
            continue
        expected = _media_path(job_dir, item)
        expected_path = str(expected.resolve()) if not expected.parent.is_symlink() else ""
        if (
            not expected_path
            or item.get("local_media_path") != expected_path
            or item.get("media_size_bytes") != (expected.stat().st_size if _usable_file(expected) else 0)
            or not _verified_file(expected, item.get("media_hash"))
        ):
            item.update(
                download_status="failed",
                local_media_path="",
                media_size_bytes=0,
                media_hash="",
                error_code="media_missing_or_corrupt",
                retryable=True,
            )
            invalid.add(item["asset_id"])
    return invalid


def _manifest_matches(state: Mapping[str, Any], job_dir: Path) -> bool:
    path = job_dir / "media-manifest.private.jsonl"
    if path.is_symlink() or not path.is_file():
        return False
    expected = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in state["items"])
    try:
        return path.read_text(encoding="utf-8") == expected
    except (OSError, UnicodeError):
        return False


def _media_path(job_dir: Path, item: Mapping[str, Any]) -> Path:
    suffix = Path(urllib.parse.urlsplit(str(item["source_url"])).path).suffix.lower()
    if item["platform"] != "direct" or suffix not in DIRECT_SUFFIXES:
        suffix = ".mp4"
    return job_dir / "media" / f"{item['asset_id']}{suffix}"


def _validate_state(state: Mapping[str, Any], job_id: str) -> None:
    items = state.get("items")
    valid_items = isinstance(items, list) and all(
        isinstance(item, Mapping)
        and JOB_ID_RE.fullmatch(str(item.get("asset_id") or ""))
        and item.get("platform") in {*PLATFORMS, "unsupported"}
        and item.get("download_status") in {"pending", "running", "succeeded", "failed"}
        and isinstance(item.get("retryable"), bool)
        for item in items
    )
    if (
        state.get("schema_version") != SCHEMA_VERSION
        or state.get("job_id") != job_id
        or state.get("status") not in {"running", *TERMINAL_STATUSES}
        or not valid_items
    ):
        raise MediaJobError("job_state_invalid", "job state does not match the v2.0 contract")
    assert isinstance(items, list)
    asset_ids = [str(item["asset_id"]) for item in items]
    if len(asset_ids) != len(set(asset_ids)):
        raise MediaJobError("job_state_invalid", "job state contains duplicate assets")
    if state["status"] in TERMINAL_STATUSES:
        completed = _parse_time(str(state.get("completed_at") or ""))
        expires = _parse_time(str(state.get("expires_at") or ""))
        succeeded = sum(item["download_status"] == "succeeded" for item in items)
        failed = sum(item["download_status"] == "failed" for item in items)
        expected_status = "succeeded" if not failed else "partial" if succeeded else "failed"
        if (
            not completed
            or not expires
            or expires - completed != RETENTION
            or succeeded + failed != len(items)
            or state.get("succeeded") != succeeded
            or state.get("failed") != failed
            or state["status"] != expected_status
        ):
            raise MediaJobError("job_state_invalid", "terminal job state is inconsistent")


def _verified_file(path: Path, expected_hash: object) -> bool:
    return _usable_file(path) and isinstance(expected_hash, str) and expected_hash and _sha256_file(path) == expected_hash


def _usable_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and os.access(path, os.R_OK) and path.stat().st_size > 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _assert_public_http_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise MediaJobError("request_invalid", "direct URL must be credential-free HTTP(S)")
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or default_port, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise MediaJobError("download_failed", "direct URL host could not be resolved", retryable=True) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise MediaJobError("unsafe_source", "direct URL resolved to a non-public address")


def _command(value: object, *, required_fields: set[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise MediaJobError("config_invalid", "backend commands must be non-empty string arrays")
    fields: list[str] = []
    try:
        for token in value:
            for _literal, field, format_spec, conversion in Formatter().parse(token):
                if field:
                    if format_spec or conversion:
                        raise MediaJobError("config_invalid", "backend placeholders cannot use formatting")
                    fields.append(field)
    except ValueError as exc:
        raise MediaJobError("config_invalid", "backend command contains an invalid placeholder") from exc
    if set(fields) != required_fields or len(fields) != len(required_fields):
        raise MediaJobError("config_invalid", f"backend command placeholders must be {sorted(required_fields)}")
    return list(value)


def _resolve_executable(value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else ""
    return shutil.which(value) or ""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        code = "job_not_found" if label == "job state" else f"{label.replace(' ', '_')}_invalid"
        raise MediaJobError(code, f"{label} is unavailable", exit_code=EXIT_NOT_FOUND if label == "job state" else EXIT_CONTRACT)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaJobError(f"{label.replace(' ', '_')}_invalid", f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MediaJobError(f"{label.replace(' ', '_')}_invalid", f"{label} must be an object")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _write_bytes_atomic(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def _write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    data = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
    _write_bytes_atomic(path, data)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise MediaJobError("boundary_violation", "refusing to replace a symlink")
    _mkdir_private(path.parent)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise MediaJobError("boundary_violation", "runtime directory must be a regular directory")
    path.chmod(0o700)


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise MediaJobError("config_invalid", f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MediaJobError("config_invalid", f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise MediaJobError("config_invalid", f"{name} is outside the allowed range")
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise MediaJobError("clock_invalid", "timestamps must be timezone-aware")
    return current.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MediaJobError("job_state_invalid", "job state contains an invalid timestamp") from exc
    return _utc(parsed)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="trendradar-media")
    parser.add_argument("--profile", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_command = commands.add_parser("fetch")
    fetch_command.add_argument("--request", required=True)
    for name in ("status", "retry"):
        command = commands.add_parser(name)
        command.add_argument("--job-id", required=True)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--expired", action="store_true", required=True)
    commands.add_parser("healthcheck")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    job_id = ""
    try:
        args = build_parser().parse_args(args_list)
        job_id = str(getattr(args, "job_id", "") or "")
        profile = load_media_profile(args.profile)
        if args.command == "fetch":
            payload = fetch(profile, args.request)
        elif args.command == "status":
            payload = status(profile, args.job_id)
        elif args.command == "retry":
            payload = retry(profile, args.job_id)
        elif args.command == "cleanup":
            payload = cleanup_expired(profile)
        else:
            payload = healthcheck(profile)
    except MediaJobError as exc:
        payload = _error_envelope(exc, job_id)
    except OSError:
        payload = _error_envelope(
            MediaJobError("runtime_io_error", "runtime file operation failed", exit_code=EXIT_FAILED, retryable=True),
            job_id,
        )
    except Exception:
        payload = _error_envelope(MediaJobError("internal_error", "unexpected runtime failure", exit_code=EXIT_FAILED), job_id)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
