#!/usr/bin/env python3
"""Validate accepted PRD bindings and build disposable projections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

from common.git import run_git
from common.io import json_bytes, write_bytes_atomic
from common.paths import get_repo_root


REQ_PATTERN = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-REQ-\d+"
REQ_RE = re.compile(REQ_PATTERN)
DECLARATION_RE = re.compile(
    rf"^\s*-\s+`(?P<id>{REQ_PATTERN})`"
    r"\s*(?:\[\s*owner\s*:\s*`?(?P<owner>[^\]`]+)`?\s*\])?\s*[:：]",
    re.MULTILINE,
)
FIELD_RE = re.compile(
    r"^\s*-\s+(Git commit|PRD paths?|REQ IDs):\s*(.*?)"
    r"(?=^\s*-\s+(?:Git commit|PRD paths?|REQ IDs):|\Z)",
    re.MULTILINE | re.DOTALL,
)


class PrdError(Exception):
    """Raised when a PRD binding or projection is invalid."""


def _binding_section(text: str) -> str:
    match = re.search(
        r"^## Accepted PRD Binding\s*$\n(.*?)(?=^##\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise PrdError("binding is missing '## Accepted PRD Binding'")
    return match.group(1)


def _binding_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for label, value in FIELD_RE.findall(_binding_section(text)):
        key = "prd_paths" if label.startswith("PRD path") else label.lower().replace(" ", "_")
        if key in fields:
            raise PrdError(f"binding repeats {label}")
        fields[key] = value.strip()
    return fields


def _binding_text(text: str) -> dict[str, object]:
    fields = _binding_fields(text)
    commit_tokens = re.findall(r"`([^`]+)`", fields.get("git_commit", ""))
    paths = re.findall(r"`([^`]+)`", fields.get("prd_paths", ""))
    req_ids = REQ_RE.findall(fields.get("req_ids", ""))
    if len(commit_tokens) != 1:
        raise PrdError("binding must contain exactly one backticked Git commit")
    if not paths:
        raise PrdError("binding must contain at least one backticked PRD path")
    if not req_ids:
        raise PrdError("binding must contain at least one REQ ID")
    if len(paths) != len(set(paths)):
        raise PrdError("binding contains duplicate PRD paths")
    if len(req_ids) != len(set(req_ids)):
        raise PrdError("binding contains duplicate REQ IDs")
    placeholders = sorted(req_id for req_id in req_ids if req_id.startswith("TODO-"))
    if placeholders:
        raise PrdError(f"binding contains unresolved REQ IDs: {', '.join(placeholders)}")
    for raw in paths:
        candidate = PurePosixPath(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise PrdError(f"binding contains unsafe PRD path: {raw}")
    return {"git_commit": commit_tokens[0], "prd_paths": paths, "req_ids": req_ids}


def _binding(path: Path) -> dict[str, object]:
    try:
        return _binding_text(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PrdError(f"binding is unreadable: {path}") from exc


def _git(repo: Path, args: list[str], failure: str) -> str:
    code, stdout, stderr = run_git(args, cwd=repo)
    if code != 0:
        detail = stderr.strip() or stdout.strip()
        raise PrdError(f"{failure}: {detail}" if detail else failure)
    return stdout


def _source_owner(repo: Path, commit: str, path: str) -> str | None:
    source = PurePosixPath(path)
    if source.parts[:2] != (".trellis", "tasks") or len(source.parts) < 4:
        return None
    task_json = source.with_name("task.json").as_posix()
    try:
        data = json.loads(_git(repo, ["show", f"{commit}:{task_json}"], "task owner is unreadable"))
    except (PrdError, json.JSONDecodeError):
        return None
    owner = data.get("owner") if isinstance(data, dict) else None
    return _clean_owner(owner)


def _clean_owner(value: object) -> str | None:
    owner = str(value or "").strip()
    if not owner or owner.upper() in {"-", "NONE", "TBD", "TODO"}:
        return None
    return owner


def parse_requirement_declarations(
    text: str,
    *,
    default_owner: str | None = None,
    source: str | None = None,
) -> list[dict[str, object]]:
    """Parse the sole canonical requirement declaration grammar."""

    requirements: list[dict[str, object]] = []
    for match in DECLARATION_RE.finditer(text):
        raw_owner = match.group("owner")
        owner = _clean_owner(raw_owner) if raw_owner is not None else _clean_owner(default_owner)
        item: dict[str, object] = {"id": match.group("id"), "owner": owner}
        if source is not None:
            item["path"] = source
        requirements.append(item)

    ids = [str(item["id"]) for item in requirements]
    errors: list[str] = []
    duplicates = sorted(req_id for req_id, count in Counter(ids).items() if count > 1)
    placeholders = sorted(req_id for req_id in ids if req_id.startswith("TODO-"))
    missing_owners = sorted(
        f"{item['id']} ({source})" if source else str(item["id"])
        for item in requirements
        if not item["owner"]
    )
    if duplicates:
        errors.append(f"duplicate REQ IDs: {', '.join(duplicates)}")
    if placeholders:
        errors.append(f"unresolved REQ IDs: {', '.join(placeholders)}")
    if missing_owners:
        errors.append(f"missing owner: {', '.join(missing_owners)}")
    if errors:
        raise PrdError("; ".join(errors))
    return requirements


def _inspect_binding(repo: Path, binding: dict[str, object]) -> dict[str, object]:
    """Return the verified binding and projection-only requirement metadata."""

    commit = _git(
        repo,
        ["rev-parse", "--verify", f"{binding['git_commit']}^{{commit}}"],
        "accepted Git commit is invalid",
    ).strip()
    paths = list(binding["prd_paths"])
    requested = list(binding["req_ids"])
    requirements: list[dict[str, object]] = []

    for path in paths:
        text = _git(repo, ["show", f"{commit}:{path}"], f"accepted PRD ref is missing: {path}")
        default_owner = _source_owner(repo, commit, path)
        requirements.extend(
            parse_requirement_declarations(
                text,
                default_owner=default_owner,
                source=path,
            )
        )

    errors: list[str] = []
    ids = [str(item["id"]) for item in requirements]
    duplicates = sorted(req_id for req_id, count in Counter(ids).items() if count > 1)
    missing_refs = sorted(set(requested) - set(ids))
    if duplicates:
        errors.append(f"duplicate REQ IDs: {', '.join(duplicates)}")
    if missing_refs:
        errors.append(f"missing REQ refs: {', '.join(missing_refs)}")
    if errors:
        raise PrdError("; ".join(errors))

    code, _, stderr = run_git(["diff", "--quiet", commit, "--", *paths], cwd=repo)
    if code == 1:
        raise PrdError("accepted PRD paths drifted; re-propose from the current Git base")
    if code != 0:
        raise PrdError(f"cannot compare accepted PRD paths: {stderr.strip()}")

    bound = set(requested)
    return {
        "binding": {
            "git_commit": commit,
            "prd_paths": sorted(paths),
            "req_ids": sorted(requested),
        },
        "requirements": [
            {**item, "bound": item["id"] in bound}
            for item in sorted(requirements, key=lambda item: (str(item["id"]), str(item["path"])))
        ],
    }


def inspect_binding_text(repo: Path, text: str) -> dict[str, object]:
    """Verify one accepted binding supplied as exact Markdown bytes."""

    return _inspect_binding(repo, _binding_text(text))


def inspect_binding(repo: Path, binding_path: Path) -> dict[str, object]:
    """Verify one accepted binding read from a Markdown file."""

    return _inspect_binding(repo, _binding(binding_path))


def resolve_requirement_ids(
    repo: Path,
    text: str,
    *,
    default_owner: str | None = None,
) -> list[str]:
    """Resolve task requirements from declarations or a reference-only binding."""

    if re.search(r"^## REQ-ID\s*$", text, re.MULTILINE):
        raise PrdError("PRD must not contain the deprecated ## REQ-ID section")
    declarations = parse_requirement_declarations(text, default_owner=default_owner)
    has_binding = bool(re.search(r"^## Accepted PRD Binding\s*$", text, re.MULTILINE))
    if declarations and has_binding:
        raise PrdError(
            "PRD must use canonical declarations or Accepted PRD Binding, not both"
        )
    if has_binding:
        return list(inspect_binding_text(repo, text)["binding"]["req_ids"])
    if not declarations:
        raise PrdError(
            "PRD must contain canonical requirement declarations or Accepted PRD Binding"
        )
    return sorted(str(item["id"]) for item in declarations)


def _readme(projection: dict[str, object]) -> bytes:
    binding = projection["binding"]
    requirements = projection["requirements"]
    lines = [
        "# PRD Index",
        "",
        "Generated projection. Rebuild it from the accepted binding; it is not product truth.",
        "",
        f"- Accepted revision: `{binding['git_commit']}`",
        f"- Source paths: {len(binding['prd_paths'])}",
        f"- Requirements: {len(requirements)}",
        f"- Bound requirements: {len(binding['req_ids'])}",
        "",
        "| PRD path | Requirement count |",
        "|---|---:|",
    ]
    for path in binding["prd_paths"]:
        count = sum(item["path"] == path for item in requirements)
        lines.append(f"| `{path}` | {count} |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _rtm(projection: dict[str, object]) -> bytes:
    lines = [
        "# Generated RTM",
        "",
        "This projection contains no requirement text and grants no authority.",
        "",
        "| REQ ID | Owner | Source | Bound |",
        "|---|---|---|---|",
    ]
    for item in projection["requirements"]:
        lines.append(
            f"| `{item['id']}` | {item['owner']} | `{item['path']}` | "
            f"{'yes' if item['bound'] else 'no'} |"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def generate(repo: Path, binding_path: Path, output: Path) -> dict[str, object]:
    """Validate one binding and atomically rebuild its disposable projections."""

    projection = inspect_binding(repo, binding_path)
    output.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(output / "README.md", _readme(projection))
    write_bytes_atomic(output / "manifest.json", json_bytes(projection))
    write_bytes_atomic(output / "RTM.md", _rtm(projection))
    return projection


def _inside(repo: Path, raw: str, label: str) -> Path:
    path = (repo / raw).resolve()
    if not path.is_relative_to(repo):
        raise PrdError(f"{label} must stay inside the repository")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mechanically check PRD refs, duplicate IDs, owners, and accepted-path drift."
    )
    parser.add_argument("--repo", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--binding", required=True)
    render = subparsers.add_parser("generate")
    render.add_argument("--binding", required=True)
    render.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        repo = Path(args.repo).resolve() if args.repo != "." else get_repo_root()
        binding_path = _inside(repo, args.binding, "binding")
        if args.command == "check":
            projection = inspect_binding(repo, binding_path)
            print(
                f"OK: {projection['binding']['git_commit']} "
                f"({len(projection['binding']['req_ids'])} bound REQ IDs)"
            )
        else:
            output = _inside(repo, args.output, "output")
            generate(repo, binding_path, output)
            print(f"Generated PRD projections: {output.relative_to(repo)}")
    except PrdError as exc:
        print(f"PRD governance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
