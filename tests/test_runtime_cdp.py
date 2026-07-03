from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark import runtime_cdp
from hermes_benchmark.runtime_cdp import (
    ERROR_CDP_BROWSER_DETACHED,
    ERROR_CDP_HTTP_UNREACHABLE,
    ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT,
    ERROR_CDP_VERSION_INVALID,
    ERROR_CDP_WS_ENDPOINT_MISSING,
    RuntimeCdpConfig,
    RuntimeCdpError,
    RuntimeLock,
    build_runtime_health,
    check_cdp_version,
    ensure_runner_cdp,
)
from hermes_benchmark.profile import load_profile

SAMPLE_DIR = ROOT / "profiles" / "examples"


def test_cdp_preflight_classifies_unreachable_invalid_missing_ws_and_valid() -> None:
    assert check_cdp_version("127.0.0.1", free_port(), timeout=0.1).error_code == ERROR_CDP_HTTP_UNREACHABLE

    invalid = server_with_body(b"not-json")
    try:
        assert check_cdp_version("127.0.0.1", invalid.server_port).error_code == ERROR_CDP_VERSION_INVALID
    finally:
        invalid.shutdown()

    missing_ws = server_with_body(json.dumps({"Browser": "Chrome/test"}).encode())
    try:
        assert check_cdp_version("127.0.0.1", missing_ws.server_port).error_code == ERROR_CDP_WS_ENDPOINT_MISSING
    finally:
        missing_ws.shutdown()

    valid = server_with_body(json.dumps({"Browser": "Chrome/test", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/test"}).encode())
    try:
        result = check_cdp_version("127.0.0.1", valid.server_port)
    finally:
        valid.shutdown()

    assert result.status == "passed"
    assert result.websocket_present is True


def test_runtime_lock_conflict_fails_before_preflight_or_launch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = runtime_config(Path(tmp), free_port())
        lock = RuntimeLock(config.lock_path)
        lock.acquire()
        try:
            try:
                ensure_runner_cdp(config, launcher=lambda _cmd: raise_unreachable())
            except RuntimeCdpError as exc:
                assert exc.code == ERROR_CDP_PORT_PROFILE_LOCK_CONFLICT
            else:
                raise AssertionError("expected lock conflict")
        finally:
            lock.release()


def test_valid_cdp_without_owner_marker_is_blocked() -> None:
    server = server_with_body(json.dumps({"Browser": "Chrome/test", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/test"}).encode())
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config = runtime_config(Path(tmp), server.server_port)
            try:
                ensure_runner_cdp(config)
            except RuntimeCdpError as exc:
                assert exc.code == ERROR_CDP_BROWSER_DETACHED
            else:
                raise AssertionError("expected unowned browser block")
    finally:
        server.shutdown()


def test_healthcheck_reports_launch_required_without_raw_paths_or_endpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile_path = write_temp_profile(root, free_port())
        old_find_chrome = runtime_cdp.find_chrome
        runtime_cdp.find_chrome = lambda: "/usr/bin/google-chrome"
        try:
            payload = build_runtime_health(load_profile(profile_path))
        finally:
            runtime_cdp.find_chrome = old_find_chrome

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert payload["runtime_effective_status"] == "launch_required"
    assert payload["run_eligible"] is True
    assert payload["checks"]["cdp"]["endpoint_ref"] == "redacted"
    assert "http://127.0.0.1" not in encoded
    assert str(root) not in encoded


def runtime_config(root: Path, port: int) -> RuntimeCdpConfig:
    storage = root / "hermes-stock-runs"
    user_data = root / "chrome-profile"
    storage.mkdir(parents=True)
    user_data.mkdir()
    return RuntimeCdpConfig(
        profile_id="test-profile",
        profile_hash="sha256:test",
        storage_dir=storage,
        database_path=storage / "state" / "test.sqlite",
        cdp_host="127.0.0.1",
        cdp_port=port,
        chrome_user_data_dir=user_data,
        lock_path=storage / "_locks" / "test.lock",
        mediacrawler_root=root / "MediaCrawler",
        mediacrawler_python=root / "venvs" / "mediacrawler" / "bin" / "python",
    )


def write_temp_profile(root: Path, port: int) -> Path:
    external = root / "_external"
    storage = external / "hermes-stock-runs"
    chrome_profile = external / "MediaCrawler" / "browser_data" / "cdp_dy_user_data_dir"
    mediacrawler_python = external / "venvs" / "mediacrawler" / "bin" / "python"
    chrome_profile.mkdir(parents=True)
    storage.mkdir(parents=True)
    mediacrawler_python.parent.mkdir(parents=True)
    mediacrawler_python.write_text("#!/bin/sh\n", encoding="utf-8")
    (external / "MediaCrawler").mkdir(exist_ok=True)

    runtime_profile = {
        "schema_version": "1.4",
        "profile_id": "runtime-test",
        "profile_type": "runtime_profile",
        "storage_ref": f"file:{storage}",
        "database_ref": f"file:{storage / 'state' / 'test.sqlite'}",
        "cdp_endpoint_ref": f"runtime:127.0.0.1:{port}",
        "proxy_url_ref": "env:HERMES_PROXY_URL",
        "cookie_ref": "env:HERMES_DOUYIN_COOKIE_REF",
        "login_state_ref": f"file:{chrome_profile}",
    }
    runtime_path = root / "runtime.json"
    runtime_path.write_text(json.dumps(runtime_profile), encoding="utf-8")

    profile = json.loads((SAMPLE_DIR / "hermes.v1.4.douyin.sample.json").read_text(encoding="utf-8"))
    profile["account_profile_ref"] = f"file:{SAMPLE_DIR / 'accounts.douyin.sample.json'}"
    profile["analysis_profile_ref"] = f"file:{SAMPLE_DIR / 'analysis.v1.4.sample.json'}"
    profile["transcription_profile_ref"] = f"file:{SAMPLE_DIR / 'transcription.v1.4.sample.json'}"
    profile["runtime_profile_ref"] = f"file:{runtime_path}"
    profile["feishu_profile_ref"] = f"file:{SAMPLE_DIR / 'feishu.v1.4.allowlist.sample.json'}"
    profile_path = root / "hermes.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return profile_path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def server_with_body(body: bytes) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def raise_unreachable():
    raise AssertionError("launcher must not be called while lock is held")


if __name__ == "__main__":
    test_cdp_preflight_classifies_unreachable_invalid_missing_ws_and_valid()
    test_runtime_lock_conflict_fails_before_preflight_or_launch()
    test_valid_cdp_without_owner_marker_is_blocked()
    test_healthcheck_reports_launch_required_without_raw_paths_or_endpoint()
