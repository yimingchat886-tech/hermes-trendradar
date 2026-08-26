from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from hermes_benchmark.stock_runtime import STOCK_TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = ROOT / "hermes_benchmark" / "collector_distribution"
PROFILE_ROOT = DIST_ROOT / "profile"
REPO_PATH = "/home/jym/workspace/Hermes trendradar"
EXPECTED_EXECUTABLE = "/home/jym/.local/bin/hermes-benchmark"
OLD_REPO_PATH = "/home/jym/workspace/Hermes stock"
PREFILL_PATH = "/home/jym/.hermes/profiles/collector/prefill/collector-prefill.json"
EXPECTED_TOOLS = tuple(STOCK_TOOL_SCHEMAS)
GENERIC_DISABLED_TOOLSETS = {
    "terminal",
    "file",
    "code_execution",
    "web",
    "browser",
    "computer_use",
    "delegation",
    "memory",
    "session_search",
    "cronjob",
    "vision",
    "image_gen",
    "tts",
    "skills",
    "clarify",
    "moa",
    "homeassistant",
}
PLATFORM_ENV_PREFIXES = ("FEISHU_", "WEIXIN_", "API_SERVER_")
CREDENTIAL_KEY_PARTS = ("TOKEN", "SECRET", "KEY", "COOKIE", "PASSWORD", "PROXY")


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "{}":
        return {}
    if value == "[]":
        return []
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value in {"''", '""'}:
        return ""
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        content = raw.split(" #", 1)[0].rstrip()
        lines.append((len(content) - len(content.lstrip(" ")), content.strip()))
    return lines


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    lines = _yaml_lines(text)
    for index, (indent, content) in enumerate(lines):
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if content.startswith("- "):
            assert isinstance(parent, list), f"list item without list parent: {content}"
            parent.append(_parse_scalar(content[2:]))
            continue

        key, separator, value = content.partition(":")
        assert separator == ":", f"invalid YAML line: {content}"
        key = key.strip()
        value = value.strip()
        assert isinstance(parent, dict), f"mapping item without dict parent: {content}"
        if value:
            parent[key] = _parse_scalar(value)
            continue

        next_is_list = False
        if index + 1 < len(lines):
            next_indent, next_content = lines[index + 1]
            next_is_list = next_indent > indent and next_content.startswith("- ")
        container: Any = [] if next_is_list else {}
        parent[key] = container
        stack.append((indent, container))
    return root


def _parse_dotenv(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator == "=", f"invalid dotenv line: {raw}"
        env[key] = value.strip().strip('"').strip("'")
    return env


def _distribution_texts() -> dict[Path, str]:
    paths = [
        DIST_ROOT / "collector_config.template.yaml",
        DIST_ROOT / "README.md",
        PROFILE_ROOT / "config.template.yaml",
        PROFILE_ROOT / "env.guardrails.example",
        PROFILE_ROOT / "prefill" / "collector-prefill.json",
    ]
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _assert_real_toolset_contract(config: dict[str, Any], text: str) -> None:
    assert config["plugins"]["enabled"] == ["stock-runtime"]
    assert config["toolsets"] == ["stock_runtime"]
    assert config["platform_toolsets"]["cli"] == ["stock_runtime"]
    disabled = set(config["agent"]["disabled_toolsets"])
    assert disabled >= GENERIC_DISABLED_TOOLSETS
    assert "stock_runtime" not in disabled
    assert "tools" not in config
    assert "enabled_toolsets" not in text


def test_collector_profile_templates_parse_and_pin_blank_local_profile_contract() -> None:
    text = (PROFILE_ROOT / "config.template.yaml").read_text(encoding="utf-8")
    config = _parse_simple_yaml(text)

    assert not (PROFILE_ROOT / "SOUL.md").exists()
    assert config["profile"]["name"] == "collector"
    assert config["profile"]["mode"] == "blank"
    assert config["timezone"] == "America/Denver"
    assert config["providers"] == {}
    assert config["prefill_messages_file"] == PREFILL_PATH
    assert config["terminal"]["cwd"] == REPO_PATH
    assert config["memory"]["memory_enabled"] is False
    assert config["memory"]["user_profile_enabled"] is False
    _assert_real_toolset_contract(config, text)
    assert config["platforms"]["feishu"]["enabled"] is False
    assert config["platforms"]["weixin"]["enabled"] is False
    assert config["platforms"]["api_server"]["enabled"] is False


def test_collector_runtime_fragment_uses_trendradar_paths_and_stock_runtime_only() -> None:
    text = (DIST_ROOT / "collector_config.template.yaml").read_text(encoding="utf-8")
    config = _parse_simple_yaml(text)

    _assert_real_toolset_contract(config, text)
    assert config["platforms"]["feishu"]["enabled"] is False
    assert config["platforms"]["weixin"]["enabled"] is False
    assert config["platforms"]["api_server"]["enabled"] is False
    assert config["stock_runtime"]["executable"] == [EXPECTED_EXECUTABLE]
    assert Path(config["stock_runtime"]["executable"][0]).is_absolute()
    assert config["stock_runtime"]["cwd"] == REPO_PATH
    assert config["stock_runtime"]["profile_ref"] == f"{REPO_PATH}/profiles/local/hermes.v1.4.douyin.local.json"


def test_stock_runtime_plugin_manifest_matches_the_seven_schema_tools() -> None:
    manifest = _parse_simple_yaml((DIST_ROOT / "stock_runtime_plugin" / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["name"] == "stock-runtime"
    assert tuple(manifest["provides_tools"]) == EXPECTED_TOOLS
    assert len(manifest["provides_tools"]) == 7
    assert all(tool.startswith("stock_") for tool in manifest["provides_tools"])

    config_text = (PROFILE_ROOT / "config.template.yaml").read_text(encoding="utf-8")
    readme = (DIST_ROOT / "README.md").read_text(encoding="utf-8")
    prefill = (PROFILE_ROOT / "prefill" / "collector-prefill.json").read_text(encoding="utf-8")
    assert "stock_runtime" in config_text
    for tool in EXPECTED_TOOLS:
        assert f"`{tool}`" in readme
        assert tool in prefill


def test_prefill_is_valid_json_message_array_and_pins_pre_activation_boundaries() -> None:
    prefill = json.loads((PROFILE_ROOT / "prefill" / "collector-prefill.json").read_text(encoding="utf-8"))

    assert isinstance(prefill, list)
    assert prefill
    assert all(set(message) == {"role", "content"} for message in prefill)
    assert all(message["role"] in {"system", "user", "assistant"} for message in prefill)
    assert all(isinstance(message["content"], str) and message["content"].strip() for message in prefill)

    content = "\n".join(message["content"] for message in prefill)
    normalized = content.lower()
    assert "source-of-truth" in content
    assert "pre-activation preparation state" in normalized
    assert "profile_prepared" in content
    assert "cron_prepared_not_activated" in content
    assert "live collector profile" in normalized
    assert "paused cron draft" in normalized
    assert "do not run run-daily" in normalized
    assert "stock_run_daily" in content
    assert "do not resume or activate cron" in normalized
    assert "do not start gateway" in normalized
    assert "platform login" in normalized
    assert "do not write RAG adopted-content outbox data" in content
    assert "do not send or publish externally" in normalized
    assert "validate" in normalized
    assert "health" in normalized
    assert "check-only" in normalized
    assert "structured read-only" in normalized
    assert "Case 4D" in content
    assert "case 4a template state" not in normalized
    assert "do not activate a live profile" not in normalized


def test_env_guardrails_parse_and_leave_platform_credentials_blank_or_false() -> None:
    env = _parse_dotenv((PROFILE_ROOT / "env.guardrails.example").read_text(encoding="utf-8"))

    assert env["FEISHU_APP_ID"] == ""
    assert env["FEISHU_APP_SECRET"] == ""
    assert env["WEIXIN_ACCOUNT_ID"] == ""
    assert env["WEIXIN_TOKEN"] == ""
    assert env["API_SERVER_ENABLED"] == "false"
    assert env["API_SERVER_KEY"] == ""
    for key, value in env.items():
        is_platform_key = key.startswith(PLATFORM_ENV_PREFIXES)
        is_credential_key = any(part in key for part in CREDENTIAL_KEY_PARTS)
        if is_platform_key or is_credential_key:
            assert value in {"", "false"}, f"{key} must remain blank or false"
    serialized = json.dumps(env, sort_keys=True)
    assert not re.search(r"(?i)(xox[baprs]-|sk-[a-z0-9]|secret_[a-z0-9]|eyJ[a-z0-9_-]+)", serialized)


def test_distribution_package_data_includes_profile_templates_without_repo_soul() -> None:
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]

    collector_assets = set(package_data["hermes_benchmark.collector_distribution"])
    assert collector_assets >= {
        "README.md",
        "collector_config.template.yaml",
        "profile/config.template.yaml",
        "profile/env.guardrails.example",
        "profile/prefill/collector-prefill.json",
    }
    assert "profile/SOUL.md" not in collector_assets
    assert package_data["hermes_benchmark.collector_distribution.stock_runtime_plugin"] == ["plugin.yaml"]


def test_distribution_docs_capture_deployment_validation_and_deferred_boundaries() -> None:
    readme = (DIST_ROOT / "README.md").read_text(encoding="utf-8")

    assert "06:00 America/Denver" in readme
    assert "runner-authorized `fixed:300`" in readme
    assert "current v1.4 manifest is max2" in readme
    assert "RAG adopted-content outbox" in readme
    assert "planned, not implemented" in readme
    assert "prepared-not-activated" in readme
    assert "profile preparation" in readme
    assert "live collector profile" in readme
    assert "paused cron draft" in readme
    assert "resume or activate cron" in readme
    assert "start gateway" in readme
    assert "platform login" in readme
    assert "structured read-only" in readme
    assert "Case 4D" in readme
    assert "Case 4B" in readme
    assert "~/.hermes/profiles/collector/SOUL.md" in readme
    assert "profile/SOUL.md" not in readme
    assert "git check-ignore" in readme
    assert "run-daily" in readme
    assert "not run" in readme.lower()
    assert "does not create a live profile" not in readme


def test_old_repo_path_is_absent_from_distribution_templates() -> None:
    for path, text in _distribution_texts().items():
        assert OLD_REPO_PATH not in text, path
    assert OLD_REPO_PATH not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_gitignore_protects_local_and_production_profiles() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("profiles/local/", "*.local.*", "*.production.*"):
        assert pattern in gitignore

    probe_paths = [
        "profiles/local/hermes.v1.4.douyin.local.json",
        "profiles/local/collector/config.yaml",
        "collector.local.yaml",
        "collector.production.yaml",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(probe_paths) + "\n",
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(probe_paths)
