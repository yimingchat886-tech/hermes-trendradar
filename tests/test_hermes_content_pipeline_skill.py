from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

import pytest

from hermes_benchmark.content_pipeline import validate_request


DEFAULT_SKILL = (
    Path.home()
    / ".hermes/hermes-agent/skills/autonomous-ai-agents/hermes-content-pipeline/SKILL.md"
)
SECTION_ORDER = [
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Quick Reference",
    "Procedure",
    "Pitfalls",
    "Verification",
]


def _content() -> str:
    path = Path(os.environ.get("HERMES_CONTENT_PIPELINE_SKILL", DEFAULT_SKILL))
    if not path.is_file():
        pytest.skip(f"external Hermes content skill is not installed: {path}")
    return path.read_text(encoding="utf-8")


def _frontmatter(content: str) -> dict[str, str]:
    assert content.startswith("---\n")
    raw, _body = content[4:].split("\n---\n", 1)
    return {
        key.strip(): value.strip().strip('"')
        for line in raw.splitlines()
        if line and not line.startswith(" ")
        for key, value in [line.split(":", 1)]
    }


def _route_matrix(content: str) -> dict[tuple[str, str], tuple[str, str]]:
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    for mode, route, body, submit in re.findall(
        r"^\| (original|optimized) \| (wiki|hotspot|none) \| (.*?) \| (yes|no) \|$",
        content,
        re.MULTILINE,
    ):
        rows[(mode, route)] = (body.strip("`"), submit)
    return rows


def _fenced(content: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)\n```", content, re.DOTALL)


def _subsection(content: str, title: str) -> str:
    match = re.search(
        rf"^### \d+\. {re.escape(title)}\n(.*?)(?=^### \d+\.|^## )",
        content,
        re.MULTILINE | re.DOTALL,
    )
    assert match, title
    return match.group(1)


def _command_tokens(content: str, command: str) -> list[str]:
    block = next(block for block in _fenced(content, "bash") if command in block)
    return shlex.split(block.replace("\\\n", " "))


def test_skill_contract() -> None:
    content = _content()
    compact = " ".join(content.split())
    lower = compact.lower()
    frontmatter = _frontmatter(content)
    description = frontmatter["description"]

    assert frontmatter["name"] == "hermes-content-pipeline"
    assert len(description) <= 60 and description.endswith(".")
    assert re.findall(r"^## (.+)$", content, re.MULTILINE) == SECTION_ORDER
    assert all(f"`{tool}`" in content for tool in ("terminal", "read_file", "search_files"))

    request_template = next(
        json.loads(block)
        for block in _fenced(content, "json")
        if '"schema_version": "hermes-content-request.v1"' in block
    )
    request_template["target"]["account_id"] = "configured-account-id"
    request_template["scope"]["content_ids"] = ["content-douyin-123456"]
    request = validate_request(request_template)
    assert request["copy_mode"] == "original"
    assert request["scope"] == {
        "content_ids": ["content-douyin-123456"],
        "published_since": None,
        "max_items": None,
        "all_visible": False,
    }
    for internal_name in (
        "load_pipeline_profile",
        "resolve_content_pipeline_root",
        "validate_request",
        "request_digest",
    ):
        assert internal_name not in content

    matrix = _route_matrix(content)
    assert set(matrix) == {
        (mode, route)
        for mode in ("original", "optimized")
        for route in ("wiki", "hotspot", "none")
    }
    assert matrix[("original", "wiki")] == ("transcript.original.md", "yes")
    assert matrix[("optimized", "wiki")] == ("copy.final.md", "yes")
    assert all(
        matrix[(mode, route)] == ("—", "no")
        for mode in ("original", "optimized")
        for route in ("hotspot", "none")
    )

    bind = _subsection(content, "Bind the conversation request").lower()
    assert all(token in bind for token in ("content_pipeline_root", "requests/", "git repositories"))
    assert all(token in bind for token in ("symlink", "escape", "fail closed"))
    assert "never place the request in a source repository" in bind
    assert "do not impose a default maximum" in lower

    envelope = _subsection(content, "Validate the TrendRadar envelope")
    for field in ("contract_version", "command", "mode", "ok", "exit_code", "data", "error"):
        assert f"`{field}`" in envelope
    assert '`receipt = envelope["data"]`' in envelope
    assert "hermes-content-pipeline.v1" in envelope
    assert all(status in envelope for status in ("success", "partial", "no_op"))
    assert all(field in envelope for field in ("items", "artifact_refs", "media_sha256"))

    routing = next(
        json.loads(block)
        for block in _fenced(content, "json")
        if '"schema_version": "hermes-content-routing.v1"' in block
    )
    assert routing["reason"].strip()
    assert routing["route"] == "<wiki-or-hotspot-or-none>"
    route_section = _subsection(content, "Choose exactly one route in Hermes").lower()
    assert all(token in route_section for token in ("durable", "timely", "ask the user", "non-empty"))

    copy_manifest = next(
        json.loads(block)
        for block in _fenced(content, "json")
        if '"schema_version": "hermes-content-copy.v1"' in block
    )
    assert set(copy_manifest) == {
        "schema_version",
        "run_id",
        "content_id",
        "copy_mode",
        "source_transcript_sha256",
        "final_copy_sha256",
        "byte_count",
    }
    assert copy_manifest["schema_version"] == "hermes-content-copy.v1"
    assert copy_manifest["copy_mode"] == "optimized"
    assert all(
        isinstance(copy_manifest[field], str) and copy_manifest[field]
        for field in ("run_id", "content_id")
    )
    assert all(
        isinstance(copy_manifest[field], str)
        and copy_manifest[field].startswith("sha256:")
        for field in ("source_transcript_sha256", "final_copy_sha256")
    )
    assert isinstance(copy_manifest["byte_count"], int)
    assert not isinstance(copy_manifest["byte_count"], bool)
    assert copy_manifest["byte_count"] > 0

    prepare = _command_tokens(content, "hermes_content_ingress.py prepare")
    submit = _command_tokens(content, "hermes_content_ingress.py submit")
    assert content.index("hermes_content_ingress.py prepare") < content.index(
        "hermes_content_ingress.py submit"
    )
    assert set(("--staging-root", "--metadata-json", "--body-md", "--output-json")) <= set(prepare)
    assert set(
        (
            "--knowledge-root",
            "--runtime-root",
            "--audit-db",
            "--operation-id",
            "--package-json",
            "--body-md",
        )
    ) <= set(submit)
    prepare_section = _subsection(content, "Prepare the strict wiki intake")
    assert all(status in prepare_section for status in ("created", "duplicate", "rejected"))
    assert "Continue to submit only" in prepare_section
    assert "hermes_content_package_id" not in content
    assert "hermes_content_package_hash" not in content

    assert "artifact_refs=[]" in content
    assert "exactly one `.md` body" in compact
    assert "Media is never a RAG input" in compact
    assert "symlink in any parent" in compact and "hard-link count" in compact
    assert "Never overwrite" in compact and "fail closed" in compact
    assert "there is no automatic cleanup" in compact
    assert "no skill was deployed, activated, or disabled" in compact
