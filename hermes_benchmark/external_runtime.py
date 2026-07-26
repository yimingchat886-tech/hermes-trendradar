"""External MediaCrawler and FunASR smoke adapter boundary."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict

from .account_registry import SOURCE_IDS
from .contracts import BenchmarkAccount, ContractError, validate_record
from .mediacrawler_import import import_mediacrawler_rows

RUN_ID = "run-child8-external-runtime-smoke-2026-07-01"
OBSERVED_AT = "2026-07-01T00:00:00-07:00"
EXTERNAL_ROOT = Path("/home/jym/workspace/_external")
SECRET_WORDS = (
    "cookie",
    "cookies",
    "credential",
    "login",
    "passport",
    "password",
    "proxy",
    "secret",
    "session",
    "sid",
    "sword",
    "token",
)
LOCAL_URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?[^\s\"']*")
TEMP_PATH_RE = re.compile(r"(?<!\w)/(?:tmp|var/tmp)/[^\s\"']+")
SENSITIVE_PHRASE_RE = re.compile(r"(?i)\b(cookie|cookies|credential|password|proxy|secret|session|token)\b(?:\s*[:=]?\s*[^\s\"']+)?")

Mode = Literal["dry_run", "real"]
FunasrStatus = Literal["done", "fallback_done", "blocked", "failed"]


class RuntimeLayout(TypedDict):
    external_root: str
    mediacrawler_root: str
    mediacrawler_venv: str
    funasr_root: str
    funasr_venv: str
    funasr_model_cache: str
    run_root: str
    run_temp_root: str
    log_dir: str
    transcript_dir: str


class ProcessResult(TypedDict, total=False):
    name: str
    mode: Mode
    command: list[str]
    redacted_command: list[str]
    cwd: str
    exit_code: int | None
    stdout_path: str
    stderr_path: str
    blocked_reason: str


def runtime_layout(
    *,
    external_root: Path = EXTERNAL_ROOT,
    run_id: str = RUN_ID,
) -> RuntimeLayout:
    run_root = external_root / "hermes-stock-runs" / run_id
    return {
        "external_root": str(external_root),
        "mediacrawler_root": str(external_root / "MediaCrawler"),
        "mediacrawler_venv": str(external_root / "venvs" / "mediacrawler"),
        "funasr_root": str(external_root / "FunASR"),
        "funasr_venv": str(external_root / "venvs" / "funasr"),
        "funasr_model_cache": str(external_root / "model-cache" / "funasr"),
        "run_root": str(run_root),
        "run_temp_root": str(run_root / "tmp"),
        "log_dir": str(run_root / "logs"),
        "transcript_dir": str(run_root / "transcripts"),
    }


def validate_preflight(
    layout: Mapping[str, str],
    *,
    repo_root: Path | None = None,
    cleanup_targets: Iterable[Path] = (),
    cookie_file: Path | None = None,
    command_shell: bool = False,
) -> dict[str, Any]:
    if command_shell:
        raise ContractError("external runtime commands must use shell=False")

    repo = (repo_root or Path.cwd()).resolve()
    external_root = Path(layout["external_root"]).resolve()
    run_temp_root = Path(layout["run_temp_root"]).resolve()

    if _inside(external_root, repo):
        raise ContractError("external root must stay outside the main repo")
    if _inside(run_temp_root, repo):
        raise ContractError("run temp root must stay outside the main repo")
    if run_temp_root in {repo, external_root}:
        raise ContractError("run temp root cannot be repo root or external root")
    if not _inside(run_temp_root, external_root):
        raise ContractError("run temp root must stay under external root")

    for key in ("mediacrawler_root", "mediacrawler_venv", "funasr_root", "funasr_venv", "funasr_model_cache"):
        value = Path(layout[key]).resolve()
        if _inside(value, repo):
            raise ContractError(f"{key} must stay outside the main repo")

    cleanup_list = []
    for target in cleanup_targets:
        resolved = target.resolve()
        if not _inside(resolved, run_temp_root):
            raise ContractError(f"cleanup target outside run temp root: {target}")
        cleanup_list.append(str(resolved))

    cookie_source = "none"
    if cookie_file is not None:
        resolved_cookie = cookie_file.resolve()
        if _inside(resolved_cookie, repo):
            if _git_tracked(repo, resolved_cookie):
                raise ContractError("cookie file must not be Git-tracked")
            if not _git_ignored(repo, resolved_cookie):
                raise ContractError("cookie file inside repo must be ignored")
        cookie_source = "file:<redacted>"

    return {
        "status": "ok",
        "repo_root": str(repo),
        "external_root": str(external_root),
        "run_temp_root": str(run_temp_root),
        "cleanup_targets": cleanup_list,
        "cookie_source": cookie_source,
    }


def smoke_account(platform: str, handle: str, profile_url: str) -> BenchmarkAccount:
    if platform not in SOURCE_IDS:
        raise ContractError(f"unsupported smoke platform: {platform}")
    account_id = f"smoke-account-{platform}-{_slug(handle)}"
    account: BenchmarkAccount = {
        "id": account_id,
        "source_id": SOURCE_IDS[platform],
        "platform": platform,
        "handle": handle,
        "display_name": f"Smoke {handle}",
        "profile_url": profile_url,
        "level": "C",
        "enabled": True,
        "owner": "jym",
        "daily_tracking": True,
        "notes": "Local-only smoke target; do not commit cookies or login state.",
        "verified": True,
        "source_status": "verified",
        "trace": _trace(account_id, SOURCE_IDS[platform], profile_url),
    }
    validate_record("accounts", account)
    return account


def fake_mediacrawler_row(account: BenchmarkAccount, public_video_url: str) -> dict[str, Any]:
    return {
        "platform": account["platform"],
        "platform_content_id": "smoke-public-video-001",
        "url": public_video_url,
        "title": "Smoke MediaCrawler public video",
        "caption": "Fake row used to verify child 3 import shape before real collection.",
        "published_at": OBSERVED_AT,
        "crawled_at": OBSERVED_AT,
        "author_handle": account["handle"],
        "metrics": {"likes": 1, "comments": 1, "shares": 1},
        "comments_summary": "Smoke import proof.",
    }


def prove_mediacrawler_import(
    rows: Iterable[Mapping[str, Any]],
    account: BenchmarkAccount,
) -> dict[str, Any]:
    result = import_mediacrawler_rows(rows, [account])
    contents = result["contents"]
    if not contents:
        messages = [record["message"] for record in result["source_health"]]
        raise ContractError(f"MediaCrawler smoke artifact was not importable: {messages}")
    return {
        "status": "importable",
        "content_ids": [content["id"] for content in contents],
        "health_count": len(result["source_health"]),
        "source_id": account["source_id"],
        "account_id": account["id"],
    }


def run_process(
    name: str,
    command: Sequence[str],
    *,
    mode: Mode,
    cwd: Path | None = None,
    log_dir: Path | None = None,
    sensitive_values: Iterable[str] = (),
    timeout_seconds: int = 300,
) -> ProcessResult:
    command_list = [str(item) for item in command]
    redacted = redact_command(command_list, sensitive_values)
    if mode == "dry_run":
        return {
            "name": name,
            "mode": mode,
            "command": redacted,
            "redacted_command": redacted,
            "cwd": "<redacted>" if cwd else "",
            "exit_code": None,
            "blocked_reason": "dry_run_not_executed",
        }

    if log_dir is None:
        raise ContractError("real process execution requires log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"
    completed = subprocess.run(
        command_list,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    stdout_path.write_text(redact_text(completed.stdout, sensitive_values), encoding="utf-8")
    stderr_path.write_text(redact_text(completed.stderr, sensitive_values), encoding="utf-8")
    return {
        "name": name,
        "mode": mode,
        "command": redacted,
        "redacted_command": redacted,
        "cwd": "<redacted>" if cwd else "",
        "exit_code": completed.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def detect_gpu() -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"available": False, "selected_device": "cpu", "reason": "nvidia-smi not found"}
    completed = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, shell=False, check=False)
    available = completed.returncode == 0 and bool(completed.stdout.strip())
    return {
        "available": available,
        "selected_device": "cuda" if available else "cpu",
        "reason": completed.stdout.strip() or completed.stderr.strip(),
    }


def funasr_status(
    *,
    transcript_path: Path | None = None,
    blocker: str = "",
    exit_code: int | None = None,
    fallback_from: str = "",
) -> FunasrStatus:
    if transcript_path is not None and transcript_path.exists():
        return "fallback_done" if fallback_from else "done"
    if blocker:
        return "blocked"
    if exit_code not in (None, 0):
        return "failed"
    return "blocked"


def copy_media_to_run_temp(media_path: Path, run_temp_root: Path) -> Path:
    run_temp_root.mkdir(parents=True, exist_ok=True)
    copied = run_temp_root / media_path.name
    shutil.copy2(media_path, copied)
    return copied


def cleanup_run_temp(run_temp_root: Path, targets: Iterable[Path]) -> list[str]:
    removed = []
    root = run_temp_root.resolve()
    for target in targets:
        resolved = target.resolve()
        if not _inside(resolved, root):
            raise ContractError(f"cleanup target outside run temp root: {target}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
        removed.append(str(resolved))
    return removed


def build_manifest(
    *,
    mode: Mode,
    account: BenchmarkAccount,
    layout: Mapping[str, str],
    mediacrawler: ProcessResult,
    import_proof: Mapping[str, Any],
    funasr: Mapping[str, Any],
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
        "mode": mode,
        "account_id": account["id"],
        "source_id": account["source_id"],
        "platform": account["platform"],
        "layout": {key: str(value) for key, value in layout.items()},
        "mediacrawler": {
            "mode": mediacrawler["mode"],
            "command": mediacrawler["redacted_command"],
            "exit_code": mediacrawler.get("exit_code"),
        },
        "import_proof": dict(import_proof),
        "funasr": dict(funasr),
        "cleanup": dict(cleanup),
    }


def write_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def redact_command(command: Sequence[str], sensitive_values: Iterable[str] = ()) -> list[str]:
    values = [value for value in sensitive_values if value]
    redacted = []
    redact_next = False
    for item in command:
        lower = item.lower()
        if redact_next or any(value in item for value in values) or any(word in lower for word in SECRET_WORDS):
            redacted.append("<redacted>")
            redact_next = lower.lstrip("-") in SECRET_WORDS
        else:
            redacted.append(item)
            redact_next = lower in {"--cookie", "--cookies", "--token", "--proxy"}
    return redacted


def redact_text(text: str, sensitive_values: Iterable[str] = ()) -> str:
    redacted = text
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "<redacted>")
    redacted = LOCAL_URL_RE.sub("<redacted>", redacted)
    redacted = TEMP_PATH_RE.sub("<redacted-path>", redacted)
    redacted = SENSITIVE_PHRASE_RE.sub("<redacted>", redacted)
    return redacted


def _trace(local_id: str, source_id: str, source_url: str = "") -> dict[str, str]:
    trace = {"local_id": local_id, "run_id": RUN_ID, "observed_at": OBSERVED_AT, "source_id": source_id}
    if source_url:
        trace["source_url"] = source_url
    return trace


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_tracked(repo_root: Path, path: Path) -> bool:
    if not (repo_root / ".git").exists() or not _inside(path, repo_root):
        return False
    rel = str(path.relative_to(repo_root))
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    return result.returncode == 0


def _git_ignored(repo_root: Path, path: Path) -> bool:
    if not (repo_root / ".git").exists() or not _inside(path, repo_root):
        return False
    rel = str(path.relative_to(repo_root))
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    return result.returncode == 0


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-") or "unknown"


def _expect_contract_error(fragment: str, action: Any) -> None:
    try:
        action()
    except ContractError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r} in {exc!r}") from exc
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


def _self_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        external = root / "_external"
        layout = runtime_layout(external_root=external, run_id="run-self-check")
        run_temp = Path(layout["run_temp_root"])
        cleanup_target = run_temp / "raw-video.mp4"
        cleanup_target.parent.mkdir(parents=True, exist_ok=True)
        cleanup_target.write_bytes(b"raw video")

        preflight = validate_preflight(layout, cleanup_targets=[cleanup_target])
        assert preflight["status"] == "ok"
        _expect_contract_error("shell=False", lambda: validate_preflight(layout, command_shell=True))
        _expect_contract_error(
            "outside run temp",
            lambda: validate_preflight(layout, cleanup_targets=[external / "not-run-temp.txt"]),
        )

        account = smoke_account("douyin", "local_smoke_account", "https://fixture.invalid/user/local-smoke")
        row = fake_mediacrawler_row(account, "https://fixture.invalid/video/smoke")
        proof = prove_mediacrawler_import([row], account)
        assert proof["status"] == "importable"
        assert proof["content_ids"]

        secret = "cookie-value-for-redaction"
        process = run_process(
            "mediacrawler",
            ["python", "main.py", "--cookie", secret, "--output", layout["run_temp_root"]],
            mode="dry_run",
            sensitive_values=[secret],
        )
        assert process["exit_code"] is None
        assert secret not in json.dumps(process["redacted_command"])

        original = root / "input.mp4"
        original.write_bytes(b"sample media")
        copied = copy_media_to_run_temp(original, run_temp)
        assert original.exists()
        assert copied.exists()

        transcript = Path(layout["transcript_dir"]) / "sample.txt"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("transcript", encoding="utf-8")
        assert funasr_status(transcript_path=transcript) == "done"
        assert funasr_status(transcript_path=transcript, fallback_from="cuda") == "fallback_done"
        assert funasr_status(blocker="missing sample") == "blocked"
        assert funasr_status(exit_code=1) == "failed"

        removed = cleanup_run_temp(run_temp, [cleanup_target, copied])
        assert removed
        assert not cleanup_target.exists()
        assert original.exists()

        manifest = build_manifest(
            mode="dry_run",
            account=account,
            layout=layout,
            mediacrawler=process,
            import_proof=proof,
            funasr={"status": "blocked", "blocker": "no real sample supplied", "device": detect_gpu()},
            cleanup={"removed": removed, "retained": [layout["transcript_dir"]]},
        )
        assert manifest["mediacrawler"]["command"]
        assert "<redacted>" in manifest["mediacrawler"]["command"]
        manifest_path = write_manifest(manifest, Path(layout["run_root"]) / "manifest.json")
        assert manifest_path.exists()


if __name__ == "__main__":
    _self_check()
    print("external runtime adapter checks ok")
