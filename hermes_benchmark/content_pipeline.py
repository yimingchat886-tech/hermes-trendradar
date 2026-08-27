"""Strict request parsing and safe content-run directory binding."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urldefrag, urlsplit

from . import media_jobs, transcript_batch
from .collection_runner import enabled_douyin_accounts, load_mediacrawler_jsonl
from .external_runtime import run_process
from .mediacrawler_import import import_mediacrawler_rows
from .profile import load_profile
from .runtime_cdp import RuntimeCdpError, ensure_runner_cdp, resolve_runtime_config
from .transcript_pipeline import transcribe_temporary_video

REQUEST_SCHEMA_VERSION = "hermes-content-request.v1"
PIPELINE_SCHEMA_VERSION = "hermes-content-pipeline.v1"
PIPELINE_PROFILE_SCHEMA_VERSION = "hermes-content-pipeline-profile.v1"
PLATFORM = "douyin"
TARGET_FIELDS = frozenset({"platform", "account_id"})
SCOPE_FIELDS = frozenset({"content_ids", "published_since", "max_items", "all_visible"})
COPY_MODES = frozenset({"original", "optimized"})
PIPELINE_PROFILE_FIELDS = frozenset(
    {"schema_version", "content_pipeline_root", "hermes_profile_ref", "media_profile_ref"}
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class ContentPipelineError(ValueError):
    """A safe, user-facing content pipeline contract error."""

    def __init__(self, code: str, message: str = "content pipeline request invalid") -> None:
        self.code = code
        super().__init__(message)


def load_request_file(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentPipelineError("content_request_unreadable") from exc
    return validate_request(value)


def load_pipeline_profile(path: str | Path, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    wrapper = Path(path)
    try:
        info = wrapper.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ContentPipelineError("content_pipeline_profile_unreadable")
        value = json.loads(wrapper.read_text(encoding="utf-8"))
    except ContentPipelineError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentPipelineError("content_pipeline_profile_unreadable") from exc
    return validate_pipeline_profile(value, wrapper.parent, repo_root=repo_root)


def validate_pipeline_profile(
    value: Any,
    base_dir: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PIPELINE_PROFILE_FIELDS:
        raise ContentPipelineError("content_pipeline_profile_schema")
    if value.get("schema_version") != PIPELINE_PROFILE_SCHEMA_VERSION:
        raise ContentPipelineError("content_pipeline_profile_schema")
    if not isinstance(value.get("content_pipeline_root"), str) or not value["content_pipeline_root"]:
        raise ContentPipelineError("content_pipeline_profile_root")
    normalized = {
        "schema_version": PIPELINE_PROFILE_SCHEMA_VERSION,
        "content_pipeline_root": value["content_pipeline_root"],
    }
    base = Path(base_dir or Path.cwd())
    for key in ("hermes_profile_ref", "media_profile_ref"):
        ref = value.get(key)
        if not isinstance(ref, str) or not ref.startswith("file:") or not ref[5:]:
            raise ContentPipelineError("content_pipeline_profile_ref")
        candidate = Path(ref[5:])
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ContentPipelineError("content_pipeline_profile_ref")
            candidate = candidate.resolve()
        except ContentPipelineError:
            raise
        except (OSError, ValueError) as exc:
            raise ContentPipelineError("content_pipeline_profile_ref") from exc
        normalized[key] = "file:" + str(candidate)
    resolve_content_pipeline_root(normalized, repo_root)
    return normalized


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContentPipelineError("content_request_invalid")
    if set(value) - {"schema_version", "target", "scope", "copy_mode"}:
        raise ContentPipelineError("content_request_extra_field")
    if value.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ContentPipelineError("content_request_schema_version")

    target = value.get("target")
    if not isinstance(target, Mapping) or set(target) != TARGET_FIELDS:
        raise ContentPipelineError("content_request_target")
    if target.get("platform") != PLATFORM or not _safe_id(target.get("account_id")):
        raise ContentPipelineError("content_request_target")

    scope = value.get("scope")
    if not isinstance(scope, Mapping) or not scope or set(scope) - SCOPE_FIELDS:
        raise ContentPipelineError("content_request_scope")

    normalized_scope: dict[str, Any] = {
        "content_ids": [],
        "published_since": None,
        "max_items": None,
        "all_visible": False,
    }
    active: list[str] = []
    if "content_ids" in scope:
        content_ids = scope["content_ids"]
        if not isinstance(content_ids, list) or any(not _safe_id(item) for item in content_ids):
            raise ContentPipelineError("content_request_content_ids")
        if len(set(content_ids)) != len(content_ids):
            raise ContentPipelineError("content_request_content_ids")
        normalized_scope["content_ids"] = list(content_ids)
        if content_ids:
            active.append("content_ids")

    if "published_since" in scope:
        published_since = scope["published_since"]
        if published_since is not None and (not isinstance(published_since, str) or not _parse_time(published_since)):
            raise ContentPipelineError("content_request_published_since")
        if published_since is not None:
            normalized_scope["published_since"] = published_since
            active.append("published_since")

    if "max_items" in scope:
        max_items = scope["max_items"]
        if max_items is not None and (isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1):
            raise ContentPipelineError("content_request_max_items")
        if max_items is not None:
            normalized_scope["max_items"] = max_items
            active.append("max_items")

    if "all_visible" in scope:
        all_visible = scope["all_visible"]
        if not isinstance(all_visible, bool):
            raise ContentPipelineError("content_request_scope")
        if all_visible:
            normalized_scope["all_visible"] = True
            active.append("all_visible")

    if not active:
        raise ContentPipelineError("content_request_scope_selector_required")
    if "all_visible" in active and len(active) != 1:
        raise ContentPipelineError("content_request_scope_selector_conflict")

    copy_mode = value.get("copy_mode", "original")
    if not isinstance(copy_mode, str) or copy_mode not in COPY_MODES:
        raise ContentPipelineError("content_request_copy_mode")
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "target": {"platform": PLATFORM, "account_id": str(target["account_id"])},
        "scope": normalized_scope,
        "copy_mode": copy_mode,
    }


def request_digest(value: Any) -> str:
    """Return the SHA-256 digest of the validated canonical request."""

    return hashlib.sha256(_canonical_json(validate_request(value)).encode("utf-8")).hexdigest()


def deterministic_run_id(value: Any) -> str:
    return f"run-{request_digest(value)[:24]}"


def resolve_content_pipeline_root(
    profile: Any,
    repo_root: str | Path | None = None,
) -> Path:
    """Read only the injected absolute profile root and create it safely."""

    injected = profile if isinstance(profile, Mapping) else getattr(profile, "root", None)
    raw = injected.get("content_pipeline_root") if isinstance(injected, Mapping) else None
    if not isinstance(raw, str) or not raw:
        raise ContentPipelineError("content_pipeline_root_missing")
    try:
        raw_path = Path(raw)
        if not raw_path.is_absolute():
            raise ContentPipelineError("content_pipeline_root_not_absolute")
        root = Path(os.path.abspath(raw))
        repo = Path(os.path.abspath(os.fspath(repo_root or Path.cwd())))
        if _paths_overlap(root, repo) or _paths_overlap(
            Path(os.path.realpath(root)), Path(os.path.realpath(repo))
        ):
            raise ContentPipelineError("content_pipeline_root_inside_repo")
        _ensure_directory(root)
    except ContentPipelineError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ContentPipelineError("content_pipeline_root_invalid") from exc
    return root


Collector = Callable[[Mapping[str, Any], Mapping[str, Any]], Iterable[Mapping[str, Any]]]
MediaFetcher = Callable[[Mapping[str, Any]], Any]
Transcriber = Callable[[Path], Mapping[str, Any]]


def execute_content_pipeline(
    profile: Any,
    request: Mapping[str, Any],
    collector: Collector,
    media_fetcher: MediaFetcher,
    transcriber: Transcriber,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Collect injected rows and write deterministic, replay-safe artifacts."""

    normalized = validate_request(request)
    root = resolve_content_pipeline_root(profile, repo_root)
    digest = _digest_normalized(normalized)
    run_id = f"run-{digest[:24]}"
    run_dir, _ = _bind_request(root, run_id, digest, normalized)
    receipt_path = run_dir / "receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _read_json(receipt_path)
        _verify_receipt(root, receipt, request=normalized, digest=digest, run_id=run_id)
        if receipt["status"] in {"success", "no_op"}:
            replay = dict(receipt)
            replay["replayed"] = True
            return replay
        return _resume_content_pipeline(
            root,
            run_dir,
            run_id,
            normalized,
            digest,
            receipt,
            collector,
            media_fetcher,
            transcriber,
        )

    try:
        rows = list(collector(normalized["target"], normalized["scope"]))
        selected, missing = _select_rows(rows, normalized)
    except ContentPipelineError:
        raise
    except Exception:
        return _write_receipt(root, run_dir, run_id, normalized, digest, [], "collection_failed")

    items = [
        _blocked_item(run_dir, run_id, normalized, content_id, "content_not_found", None)
        for content_id in missing
    ]
    for row in selected:
        items.append(_process_item(run_dir, run_id, normalized, row, media_fetcher, transcriber))
    return _write_receipt(root, run_dir, run_id, normalized, digest, items, None)


def _resume_content_pipeline(
    root: Path,
    run_dir: Path,
    run_id: str,
    request: Mapping[str, Any],
    digest: str,
    receipt: Mapping[str, Any],
    collector: Collector,
    media_fetcher: MediaFetcher,
    transcriber: Transcriber,
) -> dict[str, Any]:
    try:
        rows = list(collector(request["target"], request["scope"]))
        selected, missing = _select_rows(rows, request)
    except ContentPipelineError:
        raise
    except Exception:
        resumed = _write_receipt(
            root,
            run_dir,
            run_id,
            request,
            digest,
            [dict(item) for item in receipt["items"]],
            "collection_failed",
        )
        resumed["replayed"] = True
        return resumed

    selected_by_id = {_row_content_id(row): row for row in selected}
    missing_ids = set(missing)
    previous_ids = {item["content_id"] for item in receipt["items"]}
    items: list[dict[str, Any]] = []
    processed_ids: set[str] = set()
    for previous in receipt["items"]:
        content_id = previous["content_id"]
        if previous["status"] == "completed":
            items.append(dict(previous))
            continue
        row = selected_by_id.get(content_id)
        if row is not None:
            items.append(_process_item(run_dir, run_id, request, row, media_fetcher, transcriber))
            processed_ids.add(content_id)
        elif content_id in missing_ids:
            items.append(_blocked_item(run_dir, run_id, request, content_id, "content_not_found", None))
        else:
            items.append(dict(previous))

    for content_id in missing:
        if content_id not in previous_ids:
            items.append(_blocked_item(run_dir, run_id, request, content_id, "content_not_found", None))
    for row in selected:
        content_id = _row_content_id(row)
        if content_id not in previous_ids and content_id not in processed_ids:
            items.append(_process_item(run_dir, run_id, request, row, media_fetcher, transcriber))
    resumed = _write_receipt(root, run_dir, run_id, request, digest, items, None)
    resumed["replayed"] = True
    return resumed


def build_mediacrawler_command(
    account: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    python_executable: str | Path,
    output_dir: str | Path,
) -> list[str]:
    content_ids = scope.get("content_ids")
    detail = isinstance(content_ids, list) and bool(content_ids)
    command = [
        str(python_executable),
        "main.py",
        "--platform",
        "dy",
        "--lt",
        "qrcode",
        "--type",
        "detail" if detail else "creator",
    ]
    if detail:
        platform_ids = []
        for content_id in content_ids:
            prefix = "content-douyin-"
            if not isinstance(content_id, str) or not content_id.startswith(prefix) or not _safe_id(content_id[len(prefix) :]):
                raise ContentPipelineError("content_id_not_collectable")
            platform_ids.append(content_id[len(prefix) :])
        command.extend(("--specified_id", ",".join(platform_ids)))
    else:
        command.extend(("--creator_id", str(account.get("profile_url") or account.get("handle") or account["id"])))
    command.extend(
        (
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(output_dir),
            "--max_concurrency_num",
            "1",
            "--get_comment",
            "false",
            "--get_sub_comment",
            "false",
            "--headless",
            "false",
        )
    )
    return command


def run_real_content_pipeline(
    pipeline_profile: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run the production adapters behind the deterministic content executor."""

    normalized = validate_request(request)
    repo = Path(repo_root or Path(__file__).resolve().parents[1])
    wrapper = validate_pipeline_profile(pipeline_profile, repo_root=repo)
    hermes_path = Path(str(wrapper["hermes_profile_ref"])[5:])
    media_path = Path(str(wrapper["media_profile_ref"])[5:])

    hermes = load_profile(hermes_path)
    accounts = [
        account
        for account in enabled_douyin_accounts(hermes)
        if account.get("platform") == PLATFORM and account.get("id") == normalized["target"]["account_id"]
    ]
    if len(accounts) != 1:
        raise ContentPipelineError("target_not_configured")

    config = resolve_runtime_config(hermes)
    media_profile = media_jobs.load_media_profile(media_path, repo_root=repo)
    transcript_runtime = transcript_batch.resolve_transcript_runtime(hermes, repo_root=repo)
    root = resolve_content_pipeline_root(wrapper, repo)
    run_id = deterministic_run_id(normalized)
    context: dict[str, Any] = {"content_id": "", "item_dir": None}

    def collector(target: Mapping[str, Any], scope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw_dir = root / run_id / "_collection" / "raw"
        log_dir = root / run_id / "_collection" / "logs"
        _ensure_directory(raw_dir)
        _ensure_directory(log_dir)
        lock = None
        try:
            cdp = ensure_runner_cdp(config)
            lock = cdp.get("lock")
            command = build_mediacrawler_command(
                accounts[0],
                scope,
                python_executable=config.mediacrawler_python,
                output_dir=raw_dir,
            )
            process = run_process(
                "mediacrawler-content",
                command,
                mode="real",
                cwd=config.mediacrawler_root,
                log_dir=log_dir,
                sensitive_values=(str(config.chrome_user_data_dir), str(config.mediacrawler_root)),
                timeout_seconds=600,
            )
            if process.get("exit_code") not in (0, None):
                raise ContentPipelineError("collection_failed")
            rows = load_mediacrawler_jsonl(raw_dir, accounts[0])
            imported = import_mediacrawler_rows(rows, [accounts[0]])
            return list(imported["contents"])
        except ContentPipelineError:
            raise
        except RuntimeCdpError as exc:
            raise ContentPipelineError("collection_runtime_unavailable") from exc
        except Exception as exc:
            raise ContentPipelineError("collection_failed") from exc
        finally:
            if lock is not None:
                lock.release()

    def media_fetcher(row: Mapping[str, Any]) -> Path:
        content_id = str(row.get("id") or row.get("content_id") or row.get("platform_content_id") or "")
        context.update(content_id=content_id, item_dir=root / run_id / content_id)
        return _fetch_media_job(media_profile, row, context["item_dir"], run_id)

    def transcriber(path: Path) -> Mapping[str, Any]:
        content_id = str(context.get("content_id") or "")
        item_dir = context.get("item_dir")
        if not content_id or not isinstance(item_dir, Path):
            raise ContentPipelineError("transcription_context_invalid")
        runtime = dict(transcript_runtime)
        runtime["artifact_root"] = str(item_dir)
        process: dict[str, Any] = {}
        return transcript_batch._run_local_funasr(runtime, run_id, content_id, path, process, (), 600)

    return execute_content_pipeline(
        wrapper,
        normalized,
        collector,
        media_fetcher,
        transcriber,
        repo_root=repo,
    )


def _fetch_media_job(
    profile: Mapping[str, Any],
    row: Mapping[str, Any],
    item_dir: Path,
    run_id: str,
) -> Path:
    content_id = str(row.get("id") or row.get("content_id") or row.get("platform_content_id") or "")
    url = next(
        (row.get(key) for key in ("raw_source_url", "url", "source_url") if row.get(key) not in (None, "")),
        "",
    )
    canonical_url = _canonical_source_url(url)
    if not content_id or canonical_url is None:
        raise ContentPipelineError("media_source_invalid")
    source_id = "source-" + hashlib.sha256(f"{content_id}\0{canonical_url}".encode()).hexdigest()[:24]
    job_id = "pipeline-" + hashlib.sha256(f"{run_id}\0{content_id}\0{canonical_url}".encode()).hexdigest()[:32]
    request_path = item_dir / "media-request.private.json"
    _atomic_write_json(
        request_path,
        {
            "schema_version": "2.0",
            "job_id": job_id,
            "sources": [{"source_id": source_id, "platform": PLATFORM, "url": canonical_url}],
        },
    )
    media_jobs.fetch(profile, request_path)
    return _read_media_manifest(profile, job_id, source_id)


def _read_media_manifest(
    profile: Mapping[str, Any],
    job_id: str,
    source_id: str,
) -> Path:
    run_root = Path(profile["run_root"])
    job_dir = run_root / job_id
    manifest = job_dir / "media-manifest.private.jsonl"
    try:
        _assert_no_symlink_parents(manifest.parent)
        info = manifest.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContentPipelineError("media_manifest_boundary")
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except ContentPipelineError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ContentPipelineError("media_manifest_invalid") from exc
    items: list[Mapping[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContentPipelineError("media_manifest_invalid") from exc
        if not isinstance(value, Mapping):
            raise ContentPipelineError("media_manifest_invalid")
        items.append(value)
    succeeded = [item for item in items if item.get("download_status") == "succeeded"]
    if len(succeeded) != 1 or succeeded[0].get("source_id") != source_id:
        raise ContentPipelineError("media_manifest_invalid")
    raw_path = succeeded[0].get("local_media_path")
    expected_hash = succeeded[0].get("media_hash")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash):
        raise ContentPipelineError("media_manifest_boundary")
    if not isinstance(raw_path, str) or not raw_path:
        raise ContentPipelineError("media_manifest_invalid")
    media_path = Path(raw_path)
    try:
        media_path.relative_to(job_dir.resolve())
        _assert_no_symlink_parents(media_path.parent)
        info = media_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < 1:
            raise ContentPipelineError("media_manifest_boundary")
        if _hash_file(media_path) != expected_hash:
            raise ContentPipelineError("media_manifest_boundary")
    except ContentPipelineError:
        raise
    except (OSError, ValueError) as exc:
        raise ContentPipelineError("media_manifest_boundary") from exc
    return media_path


def _bind_request(
    root: Path,
    run_id: str,
    digest: str,
    request: Mapping[str, Any],
) -> tuple[Path, bool]:
    run_dir = root / run_id
    binding_path = run_dir / "request.json"
    replayed = run_dir.exists()
    if replayed:
        _ensure_directory(run_dir)
        stored = _read_binding(binding_path)
        if stored.get("request_digest") != digest or stored.get("request") != request:
            raise ContentPipelineError("content_pipeline_run_conflict")
    else:
        _ensure_directory(run_dir)
        _atomic_write_json(
            binding_path,
            {
                "schema_version": PIPELINE_SCHEMA_VERSION,
                "request_digest": digest,
                "request": request,
            },
        )
    return run_dir, replayed


def _select_rows(
    rows: Iterable[Mapping[str, Any]],
    request: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    target = request["target"]
    scope = request["scope"]
    candidates: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("platform") != target["platform"] or row.get("account_id") != target["account_id"]:
            continue
        raw_id = _row_content_id(row)
        if not _safe_id(raw_id):
            continue
        content_id = str(raw_id)
        if content_id in candidates:
            raise ContentPipelineError("content_pipeline_duplicate_content")
        if scope["content_ids"] and content_id not in scope["content_ids"]:
            continue
        if scope["published_since"] is not None:
            published = _parse_time(row.get("published_at"))
            since = _parse_time(scope["published_since"])
            if not published or not since or published < since:
                continue
        candidates[content_id] = row

    requested = scope["content_ids"]
    if requested:
        selected = [candidates[item] for item in requested if item in candidates]
        missing = [item for item in requested if item not in candidates]
    else:
        selected = sorted(candidates.values(), key=_row_sort_key)
        missing = []
    max_items = scope["max_items"]
    if max_items is not None:
        selected = selected[:max_items]
        if requested:
            missing = missing[: max(0, max_items - len(selected))]
    return selected, missing


def _row_content_id(row: Mapping[str, Any]) -> str:
    return str(row.get("content_id") or row.get("platform_content_id") or row.get("id") or "")


def _process_item(
    run_dir: Path,
    run_id: str,
    request: Mapping[str, Any],
    row: Mapping[str, Any],
    media_fetcher: MediaFetcher,
    transcriber: Transcriber,
) -> dict[str, Any]:
    content_id = str(row.get("content_id") or row.get("platform_content_id") or row.get("id"))
    item_dir = run_dir / content_id
    source, source_error = _source_identity(row, request["target"], content_id)
    if source_error:
        return _blocked_item(run_dir, run_id, request, content_id, source_error, source)
    try:
        _ensure_directory(item_dir)
        media, suffix = _fetch_media(row, media_fetcher)
        media_name = f"original{suffix}"
        media_path = item_dir / "media" / media_name
        media_hash, _ = _copy_media(media, media_path)
        media_ref = _ref(run_id, content_id, f"media/{media_name}")
        metadata = _item_metadata(
            run_id,
            request,
            source,
            "media",
            "pending",
            [media_ref],
            media_hash,
            None,
            None,
        )
        _atomic_write_json(item_dir / "metadata.json", metadata)

        temp_dir = run_dir / ".transcription-tmp"
        temp_path = temp_dir / f"{content_id}{suffix}"
        temp_hash, _ = _copy_media(media_path, temp_path)
        if temp_hash != media_hash:
            raise ContentPipelineError("media_hash_mismatch")
        transcript = transcribe_temporary_video(
            _transcript_content(row, request["target"], source, run_id),
            temp_path,
            transcriber,
        )
        if temp_path.exists() or temp_path.is_symlink():
            temp_path.unlink(missing_ok=True)
        if _hash_file(media_path) != media_hash:
            raise ContentPipelineError("media_hash_mismatch")
        text = str(transcript.get("text", ""))
        if transcript.get("status") != "done" or not text.strip():
            return _blocked_item(
                run_dir,
                run_id,
                request,
                content_id,
                "transcription_failed",
                source,
                [media_ref],
                media_hash,
                None,
            )

        transcript_bytes = text.encode("utf-8")
        transcript_path = item_dir / "transcript.original.md"
        _atomic_write_bytes(transcript_path, transcript_bytes)
        transcript_hash = _sha256(transcript_bytes)
        transcript_ref = _ref(run_id, content_id, "transcript.original.md")
        _atomic_write_json(
            item_dir / "metadata.json",
            _item_metadata(
                run_id,
                request,
                source,
                "completed",
                "completed",
                [media_ref, transcript_ref],
                media_hash,
                transcript_hash,
                None,
            ),
        )
        _atomic_write_json(
            item_dir / "state.json",
            {
                "schema_version": PIPELINE_SCHEMA_VERSION,
                "run_id": run_id,
                "content_id": content_id,
                "stage": "completed",
                "status": "completed",
                "copy_mode": request["copy_mode"],
                "artifact_refs": [media_ref, transcript_ref],
                "media_sha256": media_hash,
                "transcript_original_sha256": transcript_hash,
                "error_code": None,
            },
        )
        return {
            "content_id": content_id,
            "source": source,
            "stage": "completed",
            "status": "completed",
            "artifact_refs": [media_ref, transcript_ref],
            "media_sha256": media_hash,
            "transcript_original_sha256": transcript_hash,
            "error_code": None,
        }
    except ContentPipelineError as exc:
        return _blocked_item(run_dir, run_id, request, content_id, _safe_error_code(exc.code, "item_failed"), source)
    except Exception:
        return _blocked_item(run_dir, run_id, request, content_id, "item_failed", source)


def _fetch_media(row: Mapping[str, Any], media_fetcher: MediaFetcher) -> tuple[bytes | Path, str]:
    result = media_fetcher(row)
    suffix = _media_suffix(row.get("media_path"))
    if isinstance(result, Mapping):
        if "bytes" in result:
            result = result["bytes"]
        elif "path" in result:
            result = result["path"]
    if isinstance(result, (bytes, bytearray)):
        data = bytes(result)
    elif isinstance(result, (str, Path)):
        source_path = Path(result).absolute()
        _assert_no_symlink_parents(source_path.parent)
        info = source_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContentPipelineError("media_invalid_boundary")
        if source_path.stat().st_size < 1:
            raise ContentPipelineError("media_empty")
        data = source_path
        suffix = _media_suffix(source_path)
    else:
        raise ContentPipelineError("media_missing")
    if isinstance(data, bytes) and not data:
        raise ContentPipelineError("media_empty")
    return data, suffix


def _write_receipt(
    root: Path,
    run_dir: Path,
    run_id: str,
    request: Mapping[str, Any],
    digest: str,
    items: list[dict[str, Any]],
    error_code: str | None,
) -> dict[str, Any]:
    completed = sum(item.get("status") == "completed" for item in items)
    blocked = sum(item.get("status") == "blocked" for item in items)
    if error_code or (blocked and not completed):
        status = "blocked"
    elif not items:
        status = "no_op"
    elif blocked:
        status = "partial"
    else:
        status = "success"
    receipt = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "request_digest": digest,
        "status": status,
        "copy_mode": request["copy_mode"],
        "target": dict(request["target"]),
        "scope": dict(request["scope"]),
        "summary": {"selected": len(items), "completed": completed, "blocked": blocked},
        "items": items,
        "error_code": error_code,
        "artifact_refs": [_ref(run_id, "", "receipt.json")],
        "replayed": False,
    }
    _atomic_write_json(run_dir / "receipt.json", receipt)
    return receipt


def _blocked_item(
    run_dir: Path,
    run_id: str,
    request: Mapping[str, Any],
    content_id: str,
    error_code: str,
    source: Mapping[str, Any] | None,
    artifact_refs: list[str] | None = None,
    media_hash: str | None = None,
    transcript_hash: str | None = None,
) -> dict[str, Any]:
    item_dir = run_dir / content_id
    _ensure_directory(item_dir)
    refs = list(artifact_refs or [])
    safe_code = _safe_error_code(error_code, "item_failed")
    item_source = dict(source or _missing_source(request["target"], content_id))
    _atomic_write_json(
        item_dir / "metadata.json",
        _item_metadata(
            run_id,
            request,
            item_source,
            "blocked",
            "blocked",
            refs,
            media_hash,
            transcript_hash,
            safe_code,
        ),
    )
    _atomic_write_json(
        item_dir / "state.json",
        {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "run_id": run_id,
            "content_id": content_id,
            "stage": "blocked",
            "status": "blocked",
            "copy_mode": request["copy_mode"],
            "artifact_refs": refs,
            "media_sha256": media_hash,
            "transcript_original_sha256": transcript_hash,
            "error_code": safe_code,
        },
    )
    return {
        "content_id": content_id,
        "source": item_source,
        "stage": "blocked",
        "status": "blocked",
        "artifact_refs": refs,
        "media_sha256": media_hash,
        "transcript_original_sha256": transcript_hash,
        "error_code": safe_code,
    }


def _missing_source(target: Mapping[str, Any], content_id: str) -> dict[str, Any]:
    return {
        "platform": str(target["platform"]),
        "account_id": str(target["account_id"]),
        "content_id": content_id,
        "platform_content_id": content_id,
        "source_id": "source-douyin-benchmark",
        "url": "",
        "published_at": None,
    }


def _item_metadata(
    run_id: str,
    request: Mapping[str, Any],
    source: Mapping[str, Any],
    stage: str,
    status: str,
    artifact_refs: list[str],
    media_hash: str | None,
    transcript_hash: str | None,
    error_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "content_id": source["content_id"],
        "source": dict(source),
        "stage": stage,
        "status": status,
        "copy_mode": request["copy_mode"],
        "artifact_refs": list(artifact_refs),
        "media_sha256": media_hash,
        "transcript_original_sha256": transcript_hash,
        "error_code": error_code,
    }


def _source_identity(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
    content_id: str,
) -> tuple[dict[str, Any], str | None]:
    source_url = next(
        (row.get(key) for key in ("raw_source_url", "url", "source_url") if row.get(key) not in (None, "")),
        "",
    )
    canonical_url = _canonical_source_url(source_url)
    published_at = _canonical_time(row.get("published_at"))
    source_id = row.get("source_id")
    source = {
        "platform": str(target["platform"]),
        "account_id": str(target["account_id"]),
        "content_id": content_id,
        "platform_content_id": str(row.get("platform_content_id") or content_id),
        "source_id": str(source_id) if isinstance(source_id, str) and source_id else "source-douyin-benchmark",
        "url": canonical_url or "",
        "published_at": published_at if isinstance(published_at, str) else None,
    }
    if canonical_url is None or published_at is None:
        return source, "source_invalid"
    return source, None


def _transcript_content(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
    source: Mapping[str, Any],
    run_id: str,
) -> dict[str, Any]:
    crawled_at = _canonical_time(row.get("crawled_at")) or source["published_at"]
    return {
        "id": source["content_id"],
        "source_id": source["source_id"],
        "account_id": target["account_id"],
        "platform": target["platform"],
        "platform_content_id": source["platform_content_id"],
        "url": source["url"],
        "title": str(row.get("title") or source["content_id"]),
        "caption": str(row.get("caption") or ""),
        "published_at": source["published_at"],
        "crawled_at": crawled_at,
        "metrics": {},
        "hashtags": [],
        "evidence_state": "sufficient",
        "trace": {
            "local_id": source["content_id"],
            "run_id": run_id,
            "observed_at": crawled_at,
            "source_id": source["source_id"],
            "source_url": source["url"],
        },
    }


def _verify_receipt(
    root: Path,
    receipt: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    digest: str | None = None,
    run_id: str | None = None,
) -> None:
    receipt_fields = {
        "schema_version",
        "run_id",
        "request_digest",
        "status",
        "copy_mode",
        "target",
        "scope",
        "summary",
        "items",
        "error_code",
        "artifact_refs",
        "replayed",
    }
    if set(receipt) != receipt_fields or receipt.get("schema_version") != PIPELINE_SCHEMA_VERSION:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    stored_run_id = receipt.get("run_id")
    if (
        not isinstance(stored_run_id, str)
        or not stored_run_id.startswith("run-")
        or not _safe_id(stored_run_id.removeprefix("run-"))
        or (run_id is not None and stored_run_id != run_id)
        or receipt.get("replayed") is not False
    ):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    stored_digest = receipt.get("request_digest")
    if not isinstance(stored_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_digest):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    if digest is not None and stored_digest != digest:
        raise ContentPipelineError("content_pipeline_run_conflict")
    if request is not None and (
        receipt.get("target") != request["target"]
        or receipt.get("scope") != request["scope"]
        or receipt.get("copy_mode") != request["copy_mode"]
    ):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    if receipt.get("copy_mode") not in COPY_MODES:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    target = receipt.get("target")
    scope = receipt.get("scope")
    if not isinstance(target, Mapping) or set(target) != TARGET_FIELDS or target.get("platform") != PLATFORM or not _safe_id(target.get("account_id")):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    if not isinstance(scope, Mapping) or set(scope) != SCOPE_FIELDS:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    if not isinstance(receipt.get("artifact_refs"), list) or receipt["artifact_refs"] != [_ref(stored_run_id, "", "receipt.json")]:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    _artifact_path(root, receipt["artifact_refs"][0])

    items = receipt.get("items")
    if not isinstance(items, list):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    completed = 0
    blocked = 0
    seen_ids: set[str] = set()
    item_fields = {
        "content_id",
        "source",
        "stage",
        "status",
        "artifact_refs",
        "media_sha256",
        "transcript_original_sha256",
        "error_code",
    }
    source_fields = {
        "platform",
        "account_id",
        "content_id",
        "platform_content_id",
        "source_id",
        "url",
        "published_at",
    }
    for item in items:
        if not isinstance(item, Mapping) or set(item) != item_fields:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        content_id = item["content_id"]
        if not _safe_id(content_id) or content_id in seen_ids:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        seen_ids.add(content_id)
        status = item["status"]
        if status not in {"completed", "blocked"} or item["stage"] != status:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        source = item["source"]
        if not isinstance(source, Mapping) or set(source) != source_fields or source.get("content_id") != content_id:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        refs = item["artifact_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        item_prefix = f"file:{stored_run_id}/{content_id}/"
        if any(not ref.startswith(item_prefix) for ref in refs):
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        for value in (item["media_sha256"], item["transcript_original_sha256"]):
            if value is not None and not _valid_hash(value):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
        if status == "completed":
            completed += 1
            if item["error_code"] is not None or not _valid_hash(item["media_sha256"]) or not _valid_hash(item["transcript_original_sha256"]):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
            if len(refs) != 2 or not any("/media/" in ref for ref in refs) or not any(ref.endswith("/transcript.original.md") for ref in refs):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
        else:
            blocked += 1
            if not _safe_error_code(item["error_code"], ""):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
            if item["transcript_original_sha256"] is not None or any(ref.endswith("/transcript.original.md") for ref in refs):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
            if item["media_sha256"] is not None and not any("/media/" in ref for ref in refs):
                raise ContentPipelineError("content_pipeline_artifact_conflict")
        item_dir = root / stored_run_id / content_id
        metadata = _read_json(item_dir / "metadata.json")
        state = _read_json(item_dir / "state.json")
        expected = {
            "run_id": stored_run_id,
            "content_id": content_id,
            "stage": item["stage"],
            "status": item["status"],
            "artifact_refs": refs,
            "media_sha256": item["media_sha256"],
            "transcript_original_sha256": item["transcript_original_sha256"],
            "error_code": item["error_code"],
        }
        if set(metadata) != item_fields | {"run_id", "schema_version", "copy_mode"} or set(state) != item_fields - {"source"} | {"run_id", "schema_version", "copy_mode"}:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        for manifest in (metadata, state):
            if any(manifest.get(field) != value for field, value in expected.items()) or manifest.get("copy_mode") != receipt["copy_mode"]:
                raise ContentPipelineError("content_pipeline_artifact_conflict")
        if metadata.get("source") != source:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        for ref in refs:
            _artifact_path(root, ref)
        for field, marker in (("media_sha256", "/media/"), ("transcript_original_sha256", "/transcript.original.md")):
            expected_hash = item[field]
            if expected_hash is None:
                continue
            ref = next((ref for ref in refs if marker in ref), None)
            if ref is None or _hash_file(_artifact_path(root, ref)) != expected_hash:
                raise ContentPipelineError("content_pipeline_artifact_conflict")

    summary = receipt.get("summary")
    if not isinstance(summary, Mapping) or set(summary) != {"selected", "completed", "blocked"}:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    if summary != {"selected": len(items), "completed": completed, "blocked": blocked}:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    error_code = receipt.get("error_code")
    if error_code is not None and not _safe_error_code(error_code, ""):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    expected_status = "blocked" if error_code or (blocked and not completed) else "no_op" if not items else "partial" if blocked else "success"
    if receipt.get("status") != expected_status:
        raise ContentPipelineError("content_pipeline_artifact_conflict")


def _artifact_path(root: Path, ref: str) -> Path:
    if not ref.startswith("file:"):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    relative = Path(ref[5:])
    if relative.is_absolute() or ".." in relative.parts:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    path = root / relative
    try:
        path.relative_to(root)
        _assert_no_symlink_parents(path.parent)
        info = path.lstat()
    except (OSError, ValueError) as exc:
        raise ContentPipelineError("content_pipeline_artifact_conflict") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContentPipelineError("content_pipeline_artifact_conflict")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ContentPipelineError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentPipelineError("content_pipeline_artifact_conflict") from exc
    if not isinstance(value, dict):
        raise ContentPipelineError("content_pipeline_artifact_conflict")
    return value


def _row_sort_key(row: Mapping[str, Any]) -> tuple[float, str]:
    published = _parse_time(row.get("published_at"))
    return (-(published.timestamp() if published else 0.0), str(row.get("content_id") or row.get("platform_content_id") or row.get("id") or ""))


def _assert_no_symlink_parents(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.absolute().parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ContentPipelineError("content_pipeline_path_boundary")


def _media_suffix(value: Any) -> str:
    suffix = Path(str(value)).suffix.lower() if value else ".bin"
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"


def _canonical_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return None
    value, _ = urldefrag(value)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return None
    sensitive_keys = ("token", "cookie", "auth", "password", "secret", "api_key", "apikey")
    if any(any(token in key.lower() for token in sensitive_keys) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return None
    return value


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ContentPipelineError("content_pipeline_artifact_conflict") from exc
    return "sha256:" + digest.hexdigest()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _ref(run_id: str, content_id: str, name: str) -> str:
    prefix = f"{run_id}/" + (f"{content_id}/" if content_id else "")
    return f"file:{prefix}{name}"


def _safe_error_code(value: Any, default: str) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value) else default


def _digest_normalized(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()


def _read_binding(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ContentPipelineError("content_pipeline_run_conflict")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ContentPipelineError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentPipelineError("content_pipeline_run_conflict") from exc
    if not isinstance(value, dict):
        raise ContentPipelineError("content_pipeline_run_conflict")
    return value


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_directory(path.parent)
    if path.is_symlink():
        raise ContentPipelineError("content_pipeline_path_boundary")
    descriptor, temporary = tempfile.mkstemp(prefix=".request-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    _ensure_directory(path.parent)
    if path.is_symlink():
        raise ContentPipelineError("content_pipeline_path_boundary")
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _copy_media(source: bytes | Path, destination: Path) -> tuple[str, int]:
    _ensure_directory(destination.parent)
    if destination.is_symlink():
        raise ContentPipelineError("content_pipeline_path_boundary")
    descriptor, temporary = tempfile.mkstemp(prefix=".media-", dir=destination.parent)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            if isinstance(source, bytes):
                view = memoryview(source)
                for offset in range(0, len(view), 1024 * 1024):
                    chunk = view[offset : offset + 1024 * 1024]
                    output.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)
            else:
                source_path = Path(source)
                _assert_no_symlink_parents(source_path.parent)
                info = source_path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ContentPipelineError("media_invalid_boundary")
                with source_path.open("rb") as input_stream:
                    while chunk := input_stream.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
            if not total:
                raise ContentPipelineError("media_empty")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return "sha256:" + digest.hexdigest(), total


def _ensure_directory(path: Path) -> None:
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                info = current.lstat()
            else:
                continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContentPipelineError("content_pipeline_path_boundary")


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_inside(left, right) or _is_inside(right, left)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _epoch_time(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
            return None
        return _epoch_time(float(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _epoch_time(value: int | float) -> datetime | None:
    try:
        epoch = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(epoch) or not 0 <= epoch <= 4_102_444_800:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _canonical_time(value: Any) -> str | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    if isinstance(value, str) and not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
