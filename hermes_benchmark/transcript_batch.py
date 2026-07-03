"""Serial FunASR batch runner for v1.4 transcript artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from .account_registry import SOURCE_IDS
from .contracts import BenchmarkContent
from .external_runtime import redact_text, run_process
from .profile import LoadedProfile
from .state import record_error, record_transcript_state
from .transcript_pipeline import transcribe_temporary_video

ERROR_CONFIG_MISSING = "transcription_config_missing"
ERROR_VIDEO_MISSING = "transcription_video_missing"
ERROR_VIDEO_UNREADABLE = "transcription_video_unreadable"
ERROR_COMMAND_FAILED = "transcription_command_failed"
ERROR_ARTIFACT_FAILED = "transcription_artifact_failed"

VideoResolver = Callable[[BenchmarkContent], str | Path | None]


class TranscriptRuntime(TypedDict):
    provider: str
    command: list[str]
    model: str
    device: str
    model_profile: str
    artifact_root: str
    database_path: str


class TranscriptBatchItem(TypedDict, total=False):
    content_id: str
    status: str
    queued: bool
    error_code: str
    error_id: str
    transcript_id: str
    artifact_ref: str
    artifact_hash: str
    temp_video_path: str
    temp_video_exists_after: bool
    redacted_command: list[str]
    exit_code: int | None


class TranscriptBatchSummary(TypedDict):
    run_id: str
    status: str
    candidate_count: int
    queued: int
    succeeded: int
    failed: int
    skipped: int
    provider: str
    model: str
    device: str
    items: list[TranscriptBatchItem]


class TranscriptConfigError(ValueError):
    pass


def resolve_transcript_runtime(
    profile: LoadedProfile,
    *,
    env: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
) -> TranscriptRuntime:
    env_map = os.environ if env is None else env
    root = Path(repo_root or Path.cwd())
    transcription = _child_profile(profile, "transcription_profile_ref")
    runtime = _child_profile(profile, "runtime_profile_ref")
    provider = str(transcription.get("provider", ""))
    command = _command(transcription.get("command_ref", transcription.get("command")), env_map)
    model = _value(transcription.get("model_ref", transcription.get("model")), env_map)
    device = _value(transcription.get("device_ref", transcription.get("device")), env_map)
    storage = _value(runtime.get("storage_ref", runtime.get("storage")), env_map)
    database = _value(runtime.get("database_ref", runtime.get("database")), env_map) or ""

    missing = [
        name
        for name, value in (
            ("provider", provider),
            ("command", command),
            ("model", model),
            ("device", device),
            ("storage", storage),
        )
        if not value
    ]
    if provider and provider != "local-funasr":
        missing.append("provider=local-funasr")
    if missing:
        raise TranscriptConfigError(f"missing required transcription runtime config: {', '.join(missing)}")

    artifact_root = _path_from_ref(str(storage), root)
    database_path = str(_path_from_ref(database, root)) if database else ""
    model_profile = f"{transcription.get('profile_id', 'transcription')}:{model}:{device}"
    return {
        "provider": provider,
        "command": command,
        "model": str(model),
        "device": str(device),
        "model_profile": model_profile,
        "artifact_root": str(artifact_root),
        "database_path": database_path,
    }


def content_from_ledger(conn: sqlite3.Connection, *, limit: int | None = None) -> list[BenchmarkContent]:
    sql = """
        SELECT content_id, platform, platform_content_id, source_url, account_id, publish_at, status
        FROM content_ledger
        ORDER BY created_at, content_id
    """
    rows = conn.execute(sql + (" LIMIT ?" if limit is not None else ""), (() if limit is None else (limit,))).fetchall()
    return [_content_from_row(row) for row in rows]


def run_transcript_batch(
    profile: LoadedProfile,
    conn: sqlite3.Connection,
    *,
    run_id: str,
    contents: Iterable[Mapping[str, Any]],
    video_resolver: VideoResolver,
    env: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
    sensitive_values: Iterable[str] = (),
    timeout_seconds: int = 300,
) -> TranscriptBatchSummary:
    normalized = [_normalize_content(content) for content in contents]
    try:
        runtime = resolve_transcript_runtime(profile, env=env, repo_root=repo_root)
    except TranscriptConfigError as exc:
        record_error(conn, run_id, "runtime", run_id, ERROR_CONFIG_MISSING, str(exc), False)
        return _summary(run_id, "blocked", normalized, [], provider="", model="", device="")

    items: list[TranscriptBatchItem] = []
    for content in normalized:
        items.append(
            _process_content(
                conn,
                run_id,
                runtime,
                content,
                video_resolver,
                sensitive_values=sensitive_values,
                timeout_seconds=timeout_seconds,
            )
        )
    status = "success" if items and all(item["status"] == "succeeded" for item in items) else "partial_failed"
    if items and all(item["status"] == "failed" for item in items):
        status = "failed"
    return _summary(run_id, status, normalized, items, provider=runtime["provider"], model=runtime["model"], device=runtime["device"])


def _process_content(
    conn: sqlite3.Connection,
    run_id: str,
    runtime: TranscriptRuntime,
    content: BenchmarkContent,
    video_resolver: VideoResolver,
    *,
    sensitive_values: Iterable[str],
    timeout_seconds: int,
) -> TranscriptBatchItem:
    content_id = content["id"]
    resolved = video_resolver(content)
    if resolved is None:
        return _fail_item(conn, run_id, runtime, content_id, ERROR_VIDEO_MISSING, "temporary video path was not provided", queued=False)

    video_path = Path(resolved)
    if not video_path.is_file() or not os.access(video_path, os.R_OK):
        video_path.unlink(missing_ok=True)
        return _fail_item(
            conn,
            run_id,
            runtime,
            content_id,
            ERROR_VIDEO_UNREADABLE,
            f"temporary video path is not readable: {video_path}",
            queued=True,
            temp_video_path=video_path,
        )

    process: dict[str, Any] = {}

    def transcriber(path: Path) -> Mapping[str, Any]:
        return _run_local_funasr(runtime, run_id, content_id, path, process, sensitive_values, timeout_seconds)

    transcript = transcribe_temporary_video(content, video_path, transcriber)
    exists_after = video_path.exists()
    if transcript["status"] != "done":
        return _fail_item(
            conn,
            run_id,
            runtime,
            content_id,
            ERROR_COMMAND_FAILED,
            transcript.get("error", "funasr command failed"),
            queued=True,
            temp_video_path=video_path,
            temp_video_exists_after=exists_after,
            process=process,
        )

    try:
        artifact_ref, artifact_hash = _write_transcript_artifact(runtime, run_id, content, transcript, process)
        transcript_id = record_transcript_state(
            conn,
            content_id,
            runtime["model_profile"],
            "done",
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
        )
        return {
            "content_id": content_id,
            "status": "succeeded",
            "queued": True,
            "transcript_id": transcript_id,
            "artifact_ref": artifact_ref,
            "artifact_hash": artifact_hash,
            "temp_video_path": str(video_path),
            "temp_video_exists_after": exists_after,
            "redacted_command": list(process.get("redacted_command", [])),
            "exit_code": process.get("exit_code"),
        }
    except Exception as exc:
        return _fail_item(
            conn,
            run_id,
            runtime,
            content_id,
            ERROR_ARTIFACT_FAILED,
            str(exc),
            queued=True,
            temp_video_path=video_path,
            temp_video_exists_after=exists_after,
            process=process,
        )


def _run_local_funasr(
    runtime: TranscriptRuntime,
    run_id: str,
    content_id: str,
    video_path: Path,
    process: dict[str, Any],
    sensitive_values: Iterable[str],
    timeout_seconds: int,
) -> Mapping[str, Any]:
    work_dir = Path(runtime["artifact_root"]) / "transcripts" / run_id / "_funasr" / _safe_name(content_id)
    command = [
        *runtime["command"],
        str(video_path),
        "--model",
        runtime["model"],
        "--device",
        runtime["device"],
        "--output-dir",
        str(work_dir),
        "--output-format",
        "json",
    ]
    result = run_process(
        "funasr",
        command,
        mode="real",
        log_dir=work_dir / "logs",
        sensitive_values=sensitive_values,
        timeout_seconds=timeout_seconds,
    )
    process.update({"redacted_command": result["redacted_command"], "exit_code": result.get("exit_code")})
    if result.get("exit_code") != 0:
        raise RuntimeError(f"funasr command failed with exit {result.get('exit_code')}")
    text, segments_path = _read_funasr_output(work_dir, result)
    return {"text": text, "segments_path": segments_path}


def _read_funasr_output(work_dir: Path, result: Mapping[str, Any]) -> tuple[str, str]:
    json_paths = sorted(path for path in work_dir.glob("*.json") if path.is_file())
    if json_paths:
        data = json.loads(json_paths[0].read_text(encoding="utf-8"))
        return str(data.get("text", "")), str(json_paths[0])
    stdout_path = result.get("stdout_path")
    text = Path(str(stdout_path)).read_text(encoding="utf-8").strip() if stdout_path else ""
    if not text:
        raise RuntimeError("funasr command produced no transcript JSON or stdout text")
    return text, ""


def _write_transcript_artifact(
    runtime: TranscriptRuntime,
    run_id: str,
    content: BenchmarkContent,
    transcript: Mapping[str, Any],
    process: Mapping[str, Any],
) -> tuple[str, str]:
    artifact_dir = Path(runtime["artifact_root"]) / "transcripts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{_safe_name(content['id'])}.transcript.json"
    temp_path = artifact_path.with_suffix(".tmp")
    payload = {
        "content_id": content["id"],
        "provider": runtime["provider"],
        "model": runtime["model"],
        "device": runtime["device"],
        "status": "done",
        "text": transcript.get("text", ""),
        "segments_path": transcript.get("segments_path", ""),
        "redacted_command": process.get("redacted_command", []),
        "exit_code": process.get("exit_code"),
    }
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temp_path.replace(artifact_path)
        digest = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        return f"file:{artifact_path}", digest
    except Exception:
        temp_path.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)
        raise


def _fail_item(
    conn: sqlite3.Connection,
    run_id: str,
    runtime: TranscriptRuntime,
    content_id: str,
    error_code: str,
    summary: str,
    *,
    queued: bool,
    temp_video_path: Path | None = None,
    temp_video_exists_after: bool | None = None,
    process: Mapping[str, Any] | None = None,
) -> TranscriptBatchItem:
    redacted = redact_text(summary)
    error_id = record_error(conn, run_id, "transcript", content_id, error_code, redacted, True)
    transcript_id = record_transcript_state(conn, content_id, runtime["model_profile"], "failed", error_code=error_code)
    item: TranscriptBatchItem = {
        "content_id": content_id,
        "status": "failed",
        "queued": queued,
        "error_code": error_code,
        "error_id": error_id,
        "transcript_id": transcript_id,
    }
    if temp_video_path is not None:
        item["temp_video_path"] = str(temp_video_path)
        item["temp_video_exists_after"] = temp_video_path.exists() if temp_video_exists_after is None else temp_video_exists_after
    if process:
        item["redacted_command"] = list(process.get("redacted_command", []))
        item["exit_code"] = process.get("exit_code")
    return item


def _summary(
    run_id: str,
    status: str,
    candidates: Sequence[BenchmarkContent],
    items: Sequence[TranscriptBatchItem],
    *,
    provider: str,
    model: str,
    device: str,
) -> TranscriptBatchSummary:
    return {
        "run_id": run_id,
        "status": status,
        "candidate_count": len(candidates),
        "queued": sum(1 for item in items if item.get("queued")),
        "succeeded": sum(1 for item in items if item["status"] == "succeeded"),
        "failed": sum(1 for item in items if item["status"] == "failed"),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "provider": provider,
        "model": model,
        "device": device,
        "items": list(items),
    }


def _child_profile(profile: LoadedProfile, ref_name: str) -> Mapping[str, Any]:
    ref = profile.root.get(ref_name)
    child = profile.profiles_by_ref.get(ref) if isinstance(ref, str) else None
    if child is None:
        raise TranscriptConfigError(f"missing child profile ref: {ref_name}")
    return child


def _command(value: Any, env: Mapping[str, str]) -> list[str]:
    resolved = _value(value, env)
    if isinstance(resolved, list):
        return [str(item) for item in resolved if str(item)]
    if not resolved:
        return []
    return shlex.split(str(resolved))


def _value(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return value
    if value.startswith("env:"):
        return env.get(value.removeprefix("env:"), "")
    return value


def _path_from_ref(value: str, repo_root: Path) -> Path:
    raw = value.removeprefix("file:")
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def _content_from_row(row: Mapping[str, Any]) -> BenchmarkContent:
    platform = str(row["platform"])
    content_id = str(row["content_id"])
    return {
        "id": content_id,
        "source_id": SOURCE_IDS.get(platform, platform),
        "account_id": str(_get(row, "account_id") or ""),
        "platform": platform,
        "platform_content_id": str(_get(row, "platform_content_id") or content_id),
        "url": str(_get(row, "source_url") or ""),
        "title": "",
        "caption": "",
        "published_at": str(_get(row, "publish_at") or ""),
        "crawled_at": "",
        "metrics": {},
        "hashtags": [],
        "evidence_state": "sufficient",
    }


def _normalize_content(content: Mapping[str, Any]) -> BenchmarkContent:
    content_id = str(content.get("id") or content.get("content_id") or "")
    platform = str(content.get("platform") or "douyin")
    return {
        "id": content_id,
        "source_id": str(content.get("source_id") or SOURCE_IDS.get(platform, platform)),
        "account_id": str(content.get("account_id") or ""),
        "platform": platform,
        "platform_content_id": str(content.get("platform_content_id") or content_id),
        "url": str(content.get("url") or content.get("source_url") or ""),
        "title": str(content.get("title") or ""),
        "caption": str(content.get("caption") or ""),
        "published_at": str(content.get("published_at") or content.get("publish_at") or ""),
        "crawled_at": str(content.get("crawled_at") or ""),
        "metrics": dict(content.get("metrics") or {}),
        "hashtags": list(content.get("hashtags") or []),
        "evidence_state": str(content.get("evidence_state") or "sufficient"),
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "transcript"


def _get(row: Mapping[str, Any], key: str) -> Any:
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row.get(key)


def _self_check() -> None:
    from .state import begin_run, connect, init_schema

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        helper = root / "fake_funasr.py"
        helper.write_text(
            "import json, pathlib, sys\n"
            "video = pathlib.Path(sys.argv[1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / (video.stem + '.json')).write_text(json.dumps({'text': 'self check transcript'}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        profile = LoadedProfile(
            root={"transcription_profile_ref": "transcription", "runtime_profile_ref": "runtime"},
            profiles_by_ref={
                "transcription": {
                    "profile_id": "self-check-transcription",
                    "provider": "local-funasr",
                    "command": [sys.executable, str(helper)],
                    "model": "tiny",
                    "device": "cpu",
                },
                "runtime": {"storage_ref": f"file:{root / 'artifacts'}", "database_ref": ":memory:"},
            },
            profiles_by_id={},
        )
        conn = connect()
        init_schema(conn)
        run = begin_run(conn, "2026-07-03", "sha256:self-check")
        video = root / "input.mp4"
        video.write_bytes(b"fixture")
        content: BenchmarkContent = {"id": "content-self-check", "source_id": "source-douyin", "platform": "douyin", "url": ""}
        summary = run_transcript_batch(profile, conn, run_id=run["run_id"], contents=[content], video_resolver=lambda _: video)
        assert summary["succeeded"] == 1
        assert summary["queued"] == 1
        assert not video.exists()
        assert "self check transcript" not in json.dumps(summary)


if __name__ == "__main__":
    _self_check()
    print("transcript batch ok")
