"""CDP runtime checks and runner-owned Chrome lifecycle helpers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .external_runtime import runtime_layout
from .profile import LoadedProfile, ProfileError, profile_hash, validate_profile

ERROR_CDP_HTTP_UNREACHABLE = "CDP_HTTP_UNREACHABLE"
ERROR_CDP_VERSION_INVALID = "CDP_VERSION_INVALID"
ERROR_CDP_WS_ENDPOINT_MISSING = "CDP_WS_ENDPOINT_MISSING"
ERROR_CDP_BROWSER_DETACHED = "CDP_BROWSER_DETACHED"
ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT = "CDP_PORT_PROFILE_LOCK_CONFLICT"
ERROR_CHROME_LAUNCH_FAILED = "CHROME_LAUNCH_FAILED"

OWNER_MARKER = ".hermes-mediacrawler-cdp-owner.json"


class RuntimeCdpError(RuntimeError):
    def __init__(self, code: str, summary: str):
        self.code = code
        self.summary = summary
        super().__init__(summary)


@dataclass(frozen=True)
class RuntimeCdpConfig:
    profile_id: str
    profile_hash: str
    storage_dir: Path
    database_path: Path
    cdp_host: str
    cdp_port: int
    chrome_user_data_dir: Path
    lock_path: Path
    mediacrawler_root: Path
    mediacrawler_python: Path


@dataclass(frozen=True)
class CdpCheck:
    status: str
    error_code: str | None = None
    browser: str = ""
    websocket_present: bool = False

    def as_redacted_dict(self, *, runner_owned: bool = False) -> dict[str, Any]:
        return {
            "status": self.status,
            "error_code": self.error_code,
            "endpoint_ref": "redacted",
            "websocket_present": self.websocket_present,
            "runner_owned": runner_owned,
            "browser": self.browser if self.status == "passed" else "",
        }


class RuntimeLock:
    def __init__(self, path: Path):
        self.path = path
        self._file: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise RuntimeCdpError(ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT, "CDP runtime lock is already held") from exc

    def release(self) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> RuntimeLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def resolve_runtime_config(profile: LoadedProfile) -> RuntimeCdpConfig:
    summary = validate_profile(profile)
    runtime_ref = profile.root.get("runtime_profile_ref")
    runtime_profile = profile.profiles_by_ref.get(str(runtime_ref))
    if not isinstance(runtime_profile, dict):
        raise ProfileError(["runtime profile is missing"])

    cdp_host, cdp_port = _resolve_runtime_endpoint_ref(str(runtime_profile.get("cdp_endpoint_ref") or ""))
    storage_dir = _resolve_file_ref(str(runtime_profile.get("storage_ref") or ""))
    database_path = _resolve_file_ref(str(runtime_profile.get("database_ref") or ""))
    user_data_dir = _resolve_file_ref(str(runtime_profile.get("login_state_ref") or ""))
    layout = runtime_layout(external_root=_external_root(storage_dir), run_id="runtime-check")
    lock_key = _lock_key(str(runtime_ref), cdp_host, cdp_port, user_data_dir)
    return RuntimeCdpConfig(
        profile_id=str(summary["profile_id"]),
        profile_hash=profile_hash(profile),
        storage_dir=storage_dir,
        database_path=database_path,
        cdp_host=cdp_host,
        cdp_port=cdp_port,
        chrome_user_data_dir=user_data_dir,
        lock_path=storage_dir / "_locks" / f"{lock_key}.lock",
        mediacrawler_root=Path(layout["mediacrawler_root"]),
        mediacrawler_python=Path(layout["mediacrawler_venv"]) / "bin" / "python",
    )


def check_cdp_version(host: str, port: int, *, timeout: float = 2.0) -> CdpCheck:
    url = f"http://{host}:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(65536)
    except urllib.error.HTTPError:
        return CdpCheck("failed", ERROR_CDP_VERSION_INVALID)
    except (OSError, TimeoutError, urllib.error.URLError):
        return CdpCheck("failed", ERROR_CDP_HTTP_UNREACHABLE)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return CdpCheck("failed", ERROR_CDP_VERSION_INVALID)
    if not isinstance(payload, dict) or not payload.get("Browser"):
        return CdpCheck("failed", ERROR_CDP_VERSION_INVALID)
    websocket = payload.get("webSocketDebuggerUrl")
    if not isinstance(websocket, str) or not websocket:
        return CdpCheck("failed", ERROR_CDP_WS_ENDPOINT_MISSING, browser=str(payload.get("Browser") or ""))
    return CdpCheck("passed", browser=str(payload["Browser"]), websocket_present=True)


def build_runtime_health(profile: LoadedProfile) -> dict[str, Any]:
    config = resolve_runtime_config(profile)
    endpoint_local = config.cdp_host == "127.0.0.1"
    cdp = check_cdp_version(config.cdp_host, config.cdp_port)
    runner_owned = _owner_marker(config).exists()
    lock_available = _probe_lock(config.lock_path)
    chrome_path = find_chrome()
    profile_writable = _path_writable(config.chrome_user_data_dir)
    storage_writable = _path_writable(config.storage_dir)
    db_writable = _path_writable(config.database_path.parent)
    mediacrawler_root_ok = config.mediacrawler_root.exists()
    mediacrawler_python_ok = config.mediacrawler_python.exists()

    errors: list[dict[str, str]] = []
    if not endpoint_local:
        errors.append({"code": ERROR_CDP_VERSION_INVALID, "summary": "CDP endpoint must bind to 127.0.0.1"})

    if cdp.status == "passed" and runner_owned and endpoint_local:
        runtime_status = "ready"
        run_eligible = True
        cdp_status = "passed"
    elif (
        cdp.error_code == ERROR_CDP_HTTP_UNREACHABLE
        and endpoint_local
        and is_port_free(config.cdp_host, config.cdp_port)
        and chrome_path
        and profile_writable
        and lock_available
    ):
        runtime_status = "launch_required"
        run_eligible = True
        cdp_status = "launch_required"
    else:
        runtime_status = "blocked"
        run_eligible = False
        cdp_status = "failed" if cdp.status != "passed" else "blocked"
        errors.append({"code": cdp.error_code or ERROR_CDP_BROWSER_DETACHED, "summary": "CDP runtime is not runner-owned or launchable"})

    if not storage_writable:
        run_eligible = False
        runtime_status = "blocked"
        errors.append({"code": "ARTIFACT_PATH_UNWRITABLE", "summary": "artifact storage is not writable"})
    if not db_writable:
        run_eligible = False
        runtime_status = "blocked"
        errors.append({"code": "DB_PATH_UNWRITABLE", "summary": "database parent is not writable"})
    if not mediacrawler_root_ok or not mediacrawler_python_ok:
        run_eligible = False
        runtime_status = "blocked"
        errors.append({"code": "MEDIACRAWLER_UNAVAILABLE", "summary": "MediaCrawler external runtime is not callable"})

    return {
        "schema_version": "1.4",
        "profile_id": config.profile_id,
        "profile_hash": config.profile_hash,
        "run_eligible": run_eligible,
        "runtime_effective_status": runtime_status,
        "checks": {
            "profile": {"status": "passed"},
            "db": {"status": "passed" if db_writable else "failed", "path_ref": "redacted"},
            "artifacts": {"status": "passed" if storage_writable else "failed", "path_ref": "redacted"},
            "cdp": {**cdp.as_redacted_dict(runner_owned=runner_owned), "status": cdp_status},
            "runner_chrome": {
                "launchable": bool(chrome_path),
                "profile_writable": profile_writable,
                "lock_available": lock_available,
                "profile_ref": "redacted",
            },
            "mediacrawler": {
                "status": "callable" if mediacrawler_root_ok and mediacrawler_python_ok else "missing",
                "path_ref": "redacted",
            },
        },
        "errors": errors,
    }


def ensure_runner_cdp(
    config: RuntimeCdpConfig,
    *,
    timeout_seconds: float = 20.0,
    launcher: Callable[[list[str]], subprocess.Popen[Any]] | None = None,
) -> dict[str, Any]:
    lock = RuntimeLock(config.lock_path)
    lock.acquire()
    launched = False
    try:
        if config.cdp_host != "127.0.0.1":
            raise RuntimeCdpError(ERROR_CDP_VERSION_INVALID, "CDP endpoint must bind to 127.0.0.1")
        initial = check_cdp_version(config.cdp_host, config.cdp_port)
        if initial.status == "passed":
            if _owner_marker(config).exists():
                return {"ok": True, "lock": lock, "launched": False, "initial": initial, "final": initial}
            raise RuntimeCdpError(ERROR_CDP_BROWSER_DETACHED, "existing CDP browser is not runner-owned")
        if initial.error_code != ERROR_CDP_HTTP_UNREACHABLE or not is_port_free(config.cdp_host, config.cdp_port):
            raise RuntimeCdpError(initial.error_code or ERROR_CDP_VERSION_INVALID, "CDP endpoint is occupied by an invalid service")

        process = _launch_chrome(config, launcher=launcher)
        launched = True
        _write_owner_marker(config, process.pid)
        final = _wait_for_cdp(config, timeout_seconds)
        return {"ok": True, "lock": lock, "launched": True, "pid": process.pid, "initial": initial, "final": final}
    except Exception:
        if launched:
            _owner_marker(config).unlink(missing_ok=True)
        lock.release()
        raise


def find_chrome() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return ""


def is_port_free(host: str, port: int, *, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) != 0


def artifact_ref(storage_dir: Path, path: Path) -> str:
    try:
        return "file:" + str(path.resolve().relative_to(storage_dir.resolve()))
    except ValueError:
        return "file:<redacted>"


def _launch_chrome(
    config: RuntimeCdpConfig,
    *,
    launcher: Callable[[list[str]], subprocess.Popen[Any]] | None,
) -> subprocess.Popen[Any]:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeCdpError(ERROR_CHROME_LAUNCH_FAILED, "Chrome executable was not found")
    config.chrome_user_data_dir.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        f"--remote-debugging-address={config.cdp_host}",
        f"--remote-debugging-port={config.cdp_port}",
        f"--user-data-dir={config.chrome_user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    try:
        popen = launcher or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return popen(command)
    except OSError as exc:
        raise RuntimeCdpError(ERROR_CHROME_LAUNCH_FAILED, "Chrome launch failed") from exc


def _wait_for_cdp(config: RuntimeCdpConfig, timeout_seconds: float) -> CdpCheck:
    deadline = time.monotonic() + timeout_seconds
    last = CdpCheck("failed", ERROR_CDP_HTTP_UNREACHABLE)
    while time.monotonic() < deadline:
        last = check_cdp_version(config.cdp_host, config.cdp_port)
        if last.status == "passed":
            return last
        time.sleep(0.25)
    raise RuntimeCdpError(last.error_code or ERROR_CDP_HTTP_UNREACHABLE, "Chrome did not expose a valid CDP endpoint")


def _probe_lock(path: Path) -> bool:
    if not path.exists():
        return True
    lock = RuntimeLock(path)
    try:
        lock.acquire()
    except RuntimeCdpError:
        return False
    else:
        lock.release()
        return True


def _owner_marker(config: RuntimeCdpConfig) -> Path:
    return config.chrome_user_data_dir / OWNER_MARKER


def _write_owner_marker(config: RuntimeCdpConfig, pid: int) -> None:
    marker = _owner_marker(config)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"owner": "hermes-benchmark", "pid": pid}, sort_keys=True), encoding="utf-8")


def _resolve_runtime_endpoint_ref(ref: str) -> tuple[str, int]:
    if ref.startswith("env:"):
        env_name = ref.removeprefix("env:")
        env_value = os.environ.get(env_name, "")
        if not env_value:
            return "127.0.0.1", 9222
        ref = env_value
    return _parse_runtime_endpoint(ref)


def _parse_runtime_endpoint(ref: str) -> tuple[str, int]:
    if not ref.startswith("runtime:"):
        raise ProfileError(["runtime cdp_endpoint_ref must be a runtime:host:port ref"])
    target = ref.removeprefix("runtime:")
    host, sep, port_text = target.rpartition(":")
    if not sep or not host:
        raise ProfileError(["runtime cdp_endpoint_ref must be runtime:host:port"])
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ProfileError(["runtime cdp_endpoint_ref port must be an integer"]) from exc
    return host, port


def _resolve_file_ref(ref: str) -> Path:
    if not ref.startswith("file:"):
        raise ProfileError(["runtime storage/database/login refs must be file: refs"])
    raw = ref.removeprefix("file:")
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def _external_root(storage_dir: Path) -> Path:
    # storage_dir is normally <external-root>/hermes-stock-runs.
    return storage_dir.parent if storage_dir.name == "hermes-stock-runs" else storage_dir


def _path_writable(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return target.exists() and target.is_dir() and bool(target.stat().st_mode & 0o200)


def _lock_key(runtime_ref: str, host: str, port: int, user_data_dir: Path) -> str:
    payload = f"{runtime_ref}|{host}|{port}|{user_data_dir.resolve()}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]
