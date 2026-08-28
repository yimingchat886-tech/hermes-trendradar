from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import types
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path

from hermes_benchmark.collector_distribution.stock_runtime_plugin import _handler as stock_runtime_plugin_handler
from hermes_benchmark.profile import load_profile
from hermes_benchmark.runtime_cdp import resolve_runtime_config
from hermes_benchmark.stock_runtime import (
    STOCK_TOOL_SCHEMAS,
    StockRuntimeError,
    StockRuntimeSettings,
    settings_from_hermes_config,
    stock_run_content_pipeline,
    stock_read_analysis_package,
    stock_record_analysis_result,
    stock_run_daily,
    stock_validate_config,
)
from hermes_benchmark.stock_runtime_self_check import (
    _seed_handoff_state,
    _write_fixture_profile,
    run_self_check,
)

ROOT = Path(__file__).resolve().parents[1]


def _content_pipeline_receipt(run_id: str = "run-abcdef1234567890abcdef12") -> dict[str, object]:
    return {
        "schema_version": "hermes-content-pipeline.v1",
        "run_id": run_id,
        "request_digest": "a" * 64,
        "status": "success",
        "copy_mode": "original",
        "target": {"platform": "douyin", "account_id": "acct_1"},
        "scope": {"content_ids": ["content-douyin-123"], "published_since": None, "max_items": None, "all_visible": False},
        "summary": {"selected": 1, "completed": 1, "blocked": 0},
        "items": [
            {
                "content_id": "content-douyin-123",
                "source": {"url": "https://www.douyin.com/video/123"},
                "stage": "completed",
                "status": "completed",
                "artifact_refs": [
                    f"file:{run_id}/content-douyin-123/media/original.mp4",
                    f"file:{run_id}/content-douyin-123/transcript.original.md",
                ],
                "media_sha256": "sha256:" + "b" * 64,
                "transcript_original_sha256": "sha256:" + "c" * 64,
                "error_code": None,
            }
        ],
        "error_code": None,
        "artifact_refs": [f"file:{run_id}/receipt.json"],
        "replayed": False,
        "stdout": "raw process detail must be stripped",
    }


def _content_pipeline_envelope(data: dict[str, object] | None = None, *, ok: bool = True, exit_code: int = 0) -> bytes:
    payload = {
        "contract_version": "2.0",
        "ok": ok,
        "command": "content-pipeline",
        "mode": "runtime",
        "data": _content_pipeline_receipt() if data is None else data,
        "error": None if ok else {"code": "content_pipeline_blocked", "message": "content pipeline blocked"},
        "exit_code": exit_code,
        "retryable": False,
    }
    return json.dumps(payload).encode()


def test_stock_runtime_uses_fixed_argv_and_minimal_env_without_raw_process_details() -> None:
    calls: list[tuple[list[str], Path, dict[str, str], float]] = []

    def runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((list(argv), cwd, dict(env), timeout))
        envelope = {
            "contract_version": "2.0",
            "ok": True,
            "command": "validate-config",
            "mode": "profile",
            "data": {
                "schema_version": "1.4",
                "ok": True,
                "profile_id": "collector-fixture",
                "profile_hash": "sha256:" + "a" * 64,
                "enabled_account_count": 10,
                "required_enabled_accounts": 10,
                "validation": "ok",
            },
            "error": None,
            "exit_code": 0,
            "retryable": False,
        }
        return 0, json.dumps(envelope).encode(), b""

    with tempfile.TemporaryDirectory() as tmp:
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=Path(tmp),
            profile_ref=Path(tmp) / "profile.json",
            storage_root=Path(tmp) / "storage",
            timeout_seconds=12,
            env_allowlist=("PATH",),
            runner=runner,
        )
        result = stock_validate_config(settings)

    assert result["ok"] is True
    assert calls[0][0] == ["hermes-benchmark", "validate-config", "--profile", str(settings.profile_ref), "--json"]
    assert calls[0][1] == Path(tmp).resolve()
    assert set(calls[0][2]).issubset({"PATH"})
    assert calls[0][3] == 12
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "command" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded


def test_stock_run_content_pipeline_builds_canonical_request_with_fixed_argv_timeout_and_cleans_request_file() -> None:
    calls: list[tuple[list[str], Path, dict[str, str], float]] = []
    captured: dict[str, object] = {}

    def runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((list(argv), cwd, dict(env), timeout))
        request_path = Path(argv[5])
        captured["request_path"] = request_path
        captured["request"] = json.loads(request_path.read_text(encoding="utf-8"))
        return 0, _content_pipeline_envelope(), b""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=root,
            profile_ref=root / "stock-profile.json",
            content_pipeline_profile_ref=root / "content-pipeline-profile.json",
            storage_root=root / "storage",
            timeout_seconds=12,
            env_allowlist=("PATH",),
            runner=runner,
        )
        result = stock_run_content_pipeline(
            settings,
            account_id="acct_1",
            copy_mode="original",
            content_ids=["0007665571270808931626", "content-douyin-aweme-epoch"],
        )
        request_path = captured["request_path"]

    assert result["ok"] is True
    assert calls[0][0] == [
        "hermes-benchmark",
        "content-pipeline",
        "--profile",
        str(settings.content_pipeline_profile_ref),
        "--request",
        str(request_path),
        "--json",
    ]
    assert calls[0][1] == Path(tmp).resolve()
    assert set(calls[0][2]).issubset({"PATH"})
    assert calls[0][3] == 1800
    assert captured["request"] == {
        "schema_version": "hermes-content-request.v1",
        "target": {"platform": "douyin", "account_id": "acct_1"},
        "scope": {
            "content_ids": ["content-douyin-0007665571270808931626", "content-douyin-aweme-epoch"],
            "published_since": None,
            "max_items": None,
            "all_visible": False,
        },
        "copy_mode": "original",
    }
    assert isinstance(request_path, Path)
    assert not request_path.exists()
    assert not request_path.parent.exists()
    assert result["data"]["run_id"] == "run-abcdef1234567890abcdef12"
    assert result["data"]["artifact_refs"] == ["file:run-abcdef1234567890abcdef12/receipt.json"]
    assert set(result["data"]["items"][0]) == {
        "content_id",
        "stage",
        "status",
        "artifact_refs",
        "media_sha256",
        "transcript_original_sha256",
        "error_code",
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "stdout" not in encoded
    assert "stderr" not in encoded
    assert str(Path(tmp)) not in encoded


def test_stock_run_content_pipeline_rejects_unstructured_selectors_before_runner() -> None:
    calls = []

    def runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((argv, cwd, env, timeout))
        raise AssertionError("runner should not be called for invalid content-pipeline inputs")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=root,
            profile_ref=root / "stock-profile.json",
            content_pipeline_profile_ref=root / "content-pipeline-profile.json",
            storage_root=root / "storage",
            runner=runner,
        )
        cases = [
            {"account_id": "acct_1", "copy_mode": "original"},
            {"account_id": "acct_1", "copy_mode": "original", "content_ids": ["123"], "max_items": 1},
            {"account_id": "acct_1", "copy_mode": "original", "all_visible": False},
            {"account_id": "acct_1", "copy_mode": "raw", "max_items": 1},
            {"account_id": "../acct", "copy_mode": "original", "max_items": 1},
            {"account_id": "acct_1", "copy_mode": "original", "content_ids": []},
            {"account_id": "acct_1", "copy_mode": "original", "content_ids": ["content-1"]},
            {"account_id": "acct_1", "copy_mode": "original", "content_ids": ["766abc"]},
            {"account_id": "acct_1", "copy_mode": "original", "content_ids": [7665571270808931626]},
        ]
        results = [stock_run_content_pipeline(settings, **case) for case in cases]

    assert calls == []
    assert all(result["ok"] is False for result in results)
    assert {result["error"]["code"] for result in results} >= {
        "content_pipeline_scope_selector_required",
        "content_pipeline_scope_selector_conflict",
        "content_request_copy_mode",
        "content_request_target",
        "content_request_content_ids",
    }


def test_stock_run_content_pipeline_schema_allows_only_business_inputs_and_one_selector() -> None:
    parameters = STOCK_TOOL_SCHEMAS["stock_run_content_pipeline"]["parameters"]

    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["account_id", "copy_mode"]
    assert set(parameters["properties"]) == {
        "account_id",
        "content_ids",
        "published_since",
        "max_items",
        "all_visible",
        "copy_mode",
    }
    assert len(parameters["oneOf"]) == 4
    assert parameters["properties"]["content_ids"]["items"]["pattern"] == r"^(?:[0-9]{1,64}|content-douyin-[A-Za-z0-9][A-Za-z0-9_.:-]{0,128})$"
    for forbidden in ("argv", "command", "cwd", "profile", "profile_ref", "request", "output_root", "env", "cookie", "deliver", "feishu"):
        assert forbidden not in parameters["properties"]


def test_stock_run_content_pipeline_preserves_blocked_receipt_without_raw_or_path_leaks() -> None:
    run_id = "run-blocked1234567890abcdef"
    data = _content_pipeline_receipt(run_id)
    data["status"] = "blocked"
    data["summary"] = {"selected": 1, "completed": 0, "blocked": 1}
    data["items"] = [
        {
            "content_id": "content-douyin-123",
            "source": {"url": "https://www.douyin.com/video/123"},
            "stage": "blocked",
            "status": "blocked",
            "artifact_refs": [],
            "media_sha256": None,
            "transcript_original_sha256": None,
            "error_code": "transcription_failed",
        }
    ]
    data["error_code"] = "transcription_failed"

    def runner(_argv: Sequence[str], _cwd: Path, _env: Mapping[str, str], _timeout: float) -> tuple[int, bytes, bytes]:
        return 4, _content_pipeline_envelope(data, ok=False, exit_code=4), b""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=root,
            profile_ref=root / "stock-profile.json",
            content_pipeline_profile_ref=root / "content-pipeline-profile.json",
            storage_root=root / "storage",
            runner=runner,
        )
        result = stock_run_content_pipeline(settings, account_id="acct_1", copy_mode="original", content_ids=["content-douyin-123"])

    assert result["ok"] is False
    assert result["error"]["code"] == "content_pipeline_blocked"
    assert result["data"]["status"] == "blocked"
    assert result["data"]["items"][0] == {
        "content_id": "content-douyin-123",
        "stage": "blocked",
        "status": "blocked",
        "artifact_refs": [],
        "media_sha256": None,
        "transcript_original_sha256": None,
        "error_code": "transcription_failed",
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "stdout" not in encoded
    assert "stderr" not in encoded
    assert str(Path(tmp)) not in encoded


def test_stock_run_content_pipeline_rejects_file_absolute_path_leaks_from_receipt() -> None:
    data = _content_pipeline_receipt()
    data["artifact_refs"] = ["file:/home/jym/private/receipt.json"]

    def runner(_argv: Sequence[str], _cwd: Path, _env: Mapping[str, str], _timeout: float) -> tuple[int, bytes, bytes]:
        return 0, _content_pipeline_envelope(data), b""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=root,
            profile_ref=root / "stock-profile.json",
            content_pipeline_profile_ref=root / "content-pipeline-profile.json",
            storage_root=root / "storage",
            runner=runner,
        )
        result = stock_run_content_pipeline(settings, account_id="acct_1", copy_mode="original", content_ids=["content-douyin-123"])

    assert result["ok"] is False
    assert result["error"]["code"] == "secret_like_output"
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "/home/" not in encoded


def test_stock_run_content_pipeline_is_callable_through_plugin_with_business_only_input(monkeypatch) -> None:
    from hermes_benchmark.collector_distribution import stock_runtime_plugin

    calls: list[list[str]] = []
    captured_requests: list[dict[str, object]] = []

    def runner(argv: Sequence[str], _cwd: Path, _env: Mapping[str, str], _timeout: float) -> tuple[int, bytes, bytes]:
        calls.append(list(argv))
        captured_requests.append(json.loads(Path(argv[5]).read_text(encoding="utf-8")))
        return 0, _content_pipeline_envelope(), b""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=root,
            profile_ref=root / "stock-profile.json",
            content_pipeline_profile_ref=root / "content-pipeline-profile.json",
            storage_root=root / "storage",
            runner=runner,
        )
        monkeypatch.setattr(stock_runtime_plugin, "settings_from_hermes_config", lambda: settings)

        class Ctx:
            def __init__(self) -> None:
                self.tools: list[dict[str, object]] = []

            def register_tool(self, **kwargs: object) -> None:
                self.tools.append(kwargs)

        ctx = Ctx()
        stock_runtime_plugin.register(ctx)
        tool = next(item for item in ctx.tools if item["name"] == "stock_run_content_pipeline")
        result = json.loads(
            tool["handler"](
                {
                    "account_id": "acct_1",
                    "all_visible": True,
                    "copy_mode": "optimized",
                    "cwd": "/tmp/evil",
                    "request": "/tmp/evil/request.json",
                    "argv": ["rm", "-rf", "/"],
                    "deliver": "feishu",
                }
            )
        )

    assert result["ok"] is True
    assert tool["schema"] == STOCK_TOOL_SCHEMAS["stock_run_content_pipeline"]
    assert calls[0][:4] == ["hermes-benchmark", "content-pipeline", "--profile", str(settings.content_pipeline_profile_ref)]
    assert captured_requests[0]["scope"] == {"content_ids": [], "published_since": None, "max_items": None, "all_visible": True}
    combined = json.dumps({"argv": calls, "request": captured_requests, "result": result}, ensure_ascii=False, sort_keys=True)
    assert "/tmp/evil" not in combined
    assert "feishu" not in combined.lower()
    assert all(arg not in {"rm", "-rf", "/"} for arg in calls[0])


def test_stock_runtime_config_parses_optional_content_pipeline_profile_without_requiring_it(monkeypatch) -> None:
    package = types.ModuleType("hermes_cli")
    config_module = types.ModuleType("hermes_cli.config")
    config_module.load_config = lambda: {  # type: ignore[attr-defined]
        "stock_runtime": {
            "executable": ["hermes-benchmark"],
            "cwd": "/operator/repo",
            "profile_ref": "/operator/profiles/stock.json",
            "content_pipeline_profile_ref": "/operator/profiles/content-pipeline.json",
            "content_pipeline_timeout_seconds": 2400,
        }
    }
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config_module)

    settings = settings_from_hermes_config()

    assert settings.profile_ref == Path("/operator/profiles/stock.json")
    assert settings.content_pipeline_profile_ref == Path("/operator/profiles/content-pipeline.json")
    assert settings.content_pipeline_timeout_seconds == 2400


def test_stock_runtime_fails_closed_on_invalid_json_exit_mismatch_oversize_and_secret_like_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def settings_with(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> StockRuntimeSettings:
            return StockRuntimeSettings(
                executable=("hermes-benchmark",),
                cwd=root,
                profile_ref=root / "profile.json",
                storage_root=root / "storage",
                stdout_cap_bytes=1024,
                stderr_cap_bytes=32,
                runner=lambda _argv, _cwd, _env, _timeout: (returncode, stdout, stderr),
            )

        assert stock_validate_config(settings_with(b"not-json"))["error"]["code"] == "invalid_json"

        mismatch = {
            "contract_version": "2.0",
            "ok": True,
            "command": "validate-config",
            "mode": "profile",
            "data": {},
            "error": None,
            "exit_code": 0,
            "retryable": False,
        }
        assert stock_validate_config(settings_with(json.dumps(mismatch).encode(), returncode=1))["error"]["code"] == "contract_mismatch"
        assert stock_validate_config(settings_with(b"{" + b"x" * 2000))["error"]["code"] == "output_too_large"

        missing_contract = dict(mismatch)
        missing_contract.pop("contract_version")
        assert stock_validate_config(settings_with(json.dumps(missing_contract).encode()))["error"]["code"] == "contract_mismatch"

        wrong_contract = dict(mismatch)
        wrong_contract["contract_version"] = "1.0"
        assert stock_validate_config(settings_with(json.dumps(wrong_contract).encode()))["error"]["code"] == "contract_mismatch"

        secret = dict(mismatch)
        secret["data"] = {"profile_id": "token=abcd1234", "schema_version": "1.4"}
        assert stock_validate_config(settings_with(json.dumps(secret).encode()))["error"]["code"] == "secret_like_output"

        path_leak = dict(mismatch)
        path_leak["data"] = {"profile_id": "/tmp/hermes-stock-profile.json", "schema_version": "1.4"}
        assert stock_validate_config(settings_with(json.dumps(path_leak).encode()))["error"]["code"] == "secret_like_output"


def test_stock_runtime_rejects_semantically_invalid_dates_before_runner() -> None:
    calls = []

    def runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> tuple[int, bytes, bytes]:
        calls.append((argv, cwd, env, timeout))
        raise AssertionError("runner should not be called for invalid dates")

    with tempfile.TemporaryDirectory() as tmp:
        settings = StockRuntimeSettings(
            executable=("hermes-benchmark",),
            cwd=Path(tmp),
            profile_ref=Path(tmp) / "profile.json",
            storage_root=Path(tmp) / "storage",
            runner=runner,
        )
        result = stock_run_daily(settings, "2026-99-99")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_date"
    assert calls == []


def test_stock_runtime_plugin_handler_compresses_config_and_unexpected_exceptions() -> None:
    def stock_error(_args: dict[str, object]) -> dict[str, object]:
        raise StockRuntimeError("stock_runtime_config_invalid", "/tmp/profile token=abcd1234")

    stock_result = json.loads(stock_runtime_plugin_handler("stock_validate_config", stock_error)({}))
    assert stock_result["ok"] is False
    assert stock_result["tool"] == "stock_validate_config"
    assert stock_result["error"] == {"code": "stock_runtime_config_invalid", "message": "stock_runtime failed closed"}
    assert "/tmp/" not in json.dumps(stock_result, sort_keys=True)
    assert "token" not in json.dumps(stock_result, sort_keys=True).lower()

    def unexpected_error(_args: dict[str, object]) -> dict[str, object]:
        raise ValueError("/home/jym/profile Cookie=secret")

    unexpected_result = json.loads(stock_runtime_plugin_handler("stock_healthcheck", unexpected_error)({}))
    assert unexpected_result["ok"] is False
    assert unexpected_result["tool"] == "stock_healthcheck"
    assert unexpected_result["error"] == {"code": "stock_runtime_handler_error", "message": "stock_runtime failed closed"}
    encoded = json.dumps(unexpected_result, sort_keys=True)
    assert "/home/" not in encoded
    assert "cookie" not in encoded.lower()


def test_collector_distribution_assets_pin_tool_allowlist_and_generic_denies() -> None:
    dist_root = resources.files("hermes_benchmark.collector_distribution")
    plugin_root = resources.files("hermes_benchmark.collector_distribution.stock_runtime_plugin")
    readme = dist_root.joinpath("README.md").read_text(encoding="utf-8")
    config = dist_root.joinpath("collector_config.template.yaml").read_text(encoding="utf-8")
    plugin = plugin_root.joinpath("plugin.yaml").read_text(encoding="utf-8")
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]

    assert package_data["hermes_benchmark.collector_distribution"] == [
        "README.md",
        "collector_config.template.yaml",
        "profile/config.template.yaml",
        "profile/env.guardrails.example",
        "profile/prefill/collector-prefill.json",
        "profile/skills/trendradar-content-download/SKILL.md",
    ]
    assert package_data["hermes_benchmark.collector_distribution.stock_runtime_plugin"] == ["plugin.yaml"]
    assert "toolsets:\n  - stock_runtime\n" in config
    assert "platform_toolsets:\n  cli:\n    - stock_runtime\n" in config
    assert "agent:\n  disabled_toolsets:\n" in config
    assert "enabled_toolsets" not in config
    assert "\ntools:\n" not in config
    for toolset in ("terminal", "file", "code_execution"):
        assert f"    - {toolset}\n" in config
    assert "plugins:\n  enabled:\n    - stock-runtime\n" in config
    assert "profile_ref: /home/jym/workspace/Hermes trendradar/profiles/local/hermes.v1.4.douyin.local.json" in config
    assert "content_pipeline_profile_ref: /home/jym/workspace/Hermes trendradar/profiles/local/hermes-content-pipeline.v1.local.json" in config
    assert "content_pipeline_timeout_seconds: 1800" in config
    assert "cwd: /home/jym/workspace/Hermes trendradar" in config
    assert "executable:\n    - /home/jym/.local/bin/hermes-benchmark\n" in config
    assert "prepared-not-activated" in readme
    assert "live collector profile" in readme
    assert "paused cron draft" in readme
    assert "does not deploy anything to `~/.hermes/profiles`" not in readme
    assert "profile/SOUL.md" not in readme

    expected_tools = tuple(STOCK_TOOL_SCHEMAS)
    assert plugin.count("  - stock_") == len(expected_tools)
    for tool in expected_tools:
        assert f"  - {tool}\n" in plugin
        assert f"`{tool}`" in readme

    combined = "\n".join((config, plugin)).lower()
    for forbidden in ("token", "cookie", "proxy", "cdp", "127.0.0.1", "localhost", "/tmp/", "\\users\\"):
        assert forbidden not in combined


def test_stock_runtime_rejects_out_of_bounds_and_oversized_package_refs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile_path = _write_fixture_profile(root)
        config = resolve_runtime_config(load_profile(profile_path))
        _seed_handoff_state(profile_path, "2026-07-03")
        settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
            package_cap_bytes=32,
        )
        run = stock_run_daily(settings, "2026-07-03")
        package_ref = run["data"]["analysis_package_ref"]

        assert stock_read_analysis_package(settings, "file:../escape.json")["error"]["code"] == "ref_invalid"
        assert stock_read_analysis_package(settings, package_ref)["error"]["code"] == "ref_too_large"

        normal_settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
        )
        package_path = config.storage_dir / package_ref.removeprefix("file:")
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["contents"][0]["transcript_artifact_ref"] = "file:transcripts/wrong-run/1.json"
        package_path.write_text(json.dumps(package, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        assert stock_read_analysis_package(normal_settings, package_ref)["error"]["code"] == "ref_out_of_bounds"


def test_stock_runtime_records_result_exact_replay_and_conflict_safely() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile_path = _write_fixture_profile(root)
        config = resolve_runtime_config(load_profile(profile_path))
        _seed_handoff_state(profile_path, "2026-07-03")
        settings = StockRuntimeSettings(
            executable=(sys.executable, "-m", "hermes_benchmark.cli"),
            cwd=ROOT,
            profile_ref=profile_path,
            storage_root=config.storage_dir,
            timeout_seconds=30,
        )
        run = stock_run_daily(settings, "2026-07-03")
        package_ref = run["data"]["analysis_package_ref"]
        first = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "same payload"},
        )
        replay = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "same payload"},
        )
        conflict = stock_record_analysis_result(
            settings,
            package_ref=package_ref,
            content_id="content-1",
            status="succeeded",
            analysis={"summary": "changed payload"},
        )

    assert first["ok"] is True
    assert replay["ok"] is True
    assert replay["data"]["analysis_result_id"] == first["data"]["analysis_result_id"]
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "result_ref_conflict"
    assert "changed payload" not in json.dumps(conflict)


def test_collector_stock_runtime_distribution_self_check_runs_full_fixture_loop() -> None:
    result = run_self_check(repo_root=ROOT)

    assert result["ok"] is True
    assert [step["name"] for step in result["steps"]] == [
        "validate",
        "health",
        "run",
        "read_package",
        "record_result",
        "digest",
        "feedback",
    ]
    assert result["analysis_package_ref"].startswith(f"file:{result['run_id']}/")
    assert result["digest_payload_ref"].startswith(f"file:{result['run_id']}/")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "/tmp/" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded
