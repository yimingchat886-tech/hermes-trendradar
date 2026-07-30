"""Versioned source-only wrapper inspection and isolated candidate rendering."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from loop_v1.qualification import digest_json


WRAPPER_SCHEMA_VERSION = 1
WRAPPER_VERSION = "1.0.0"
_SHELL_SOURCE = Path(".trellis/scripts/upstream_release/trellis-upstream-wrapper.sh")
_TEMPLATE_PATHS = (
    Path(".trellis/scripts/upstream_release/templates/trellis-upstream-wrapper.service.template"),
    Path(".trellis/scripts/upstream_release/templates/trellis-upstream-wrapper.timer.template"),
)


class WrapperError(RuntimeError):
    """Raised when wrapper source or isolated rendering is unsafe."""


def _repo_root(value: os.PathLike[str] | str | None) -> Path:
    if value is None:
        return Path(__file__).resolve().parents[3]
    raw = os.fspath(value)
    if "://" in raw:
        raise WrapperError("repository root must be a local path")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise WrapperError("repository root must be an absolute directory")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise WrapperError("repository root must not resolve through a symlink")
    return resolved


def _source_file(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise WrapperError("wrapper source contains a symlink")
    if not current.is_file():
        raise WrapperError("wrapper source file is missing")
    return current


def _digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def wrapper_identity(repo_root: os.PathLike[str] | str | None = None) -> dict[str, object]:
    """Return a path-redacted identity for versioned wrapper source."""
    root = _repo_root(repo_root)
    files = [_SHELL_SOURCE, *_TEMPLATE_PATHS]
    payload = {
        "files": {relative.name: _digest_file(_source_file(root, relative)) for relative in files},
        "wrapper_version": WRAPPER_VERSION,
    }
    return {**payload, "wrapper_id": "sha256:" + digest_json(payload)}


def version(repo_root: os.PathLike[str] | str | None = None) -> dict[str, object]:
    wrapper_identity(repo_root)
    return {
        "schema_version": WRAPPER_SCHEMA_VERSION,
        "wrapper_version": WRAPPER_VERSION,
    }


def status(repo_root: os.PathLike[str] | str | None = None) -> dict[str, object]:
    identity = wrapper_identity(repo_root)
    return {
        "schema_version": WRAPPER_SCHEMA_VERSION,
        "status": "source_only",
        "wrapper_id": identity["wrapper_id"],
        "wrapper_version": WRAPPER_VERSION,
    }


def doctor(repo_root: os.PathLike[str] | str | None = None) -> dict[str, object]:
    identity = wrapper_identity(repo_root)
    return {
        "checks": {"shell_source": "ok", "templates": "ok"},
        "schema_version": WRAPPER_SCHEMA_VERSION,
        "status": "ok",
        "wrapper_id": identity["wrapper_id"],
        "wrapper_version": WRAPPER_VERSION,
    }


def _render_destination(output_dir: os.PathLike[str] | str, repo_root: Path) -> Path:
    raw = os.fspath(output_dir)
    if "://" in raw:
        raise WrapperError("render output must be a local path")
    destination = Path(raw)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise WrapperError("render output must be a new absolute directory")
    temporary_root = Path(tempfile.gettempdir()).resolve()
    resolved = destination.resolve(strict=False)
    if not resolved.is_relative_to(temporary_root) or resolved.is_relative_to(repo_root):
        raise WrapperError("render output must stay under the temporary root")
    parent = resolved.parent
    raw_parent = destination.parent
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or raw_parent.resolve(strict=True) != raw_parent
    ):
        raise WrapperError("render output parent must be a real temporary directory")
    return resolved


def render_isolated_bundle(
    output_dir: os.PathLike[str] | str,
    repo_root: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Render non-activated service/timer candidates beneath the temp root."""
    root = _repo_root(repo_root)
    identity = wrapper_identity(root)
    destination = _render_destination(output_dir, root)
    try:
        destination.mkdir()
        shell_target = destination / _SHELL_SOURCE.name
        shutil.copy2(_source_file(root, _SHELL_SOURCE), shell_target)
        rendered_files = [shell_target]
        for template in _TEMPLATE_PATHS:
            source = _source_file(root, template)
            target = destination / template.name.removesuffix(".template")
            target.write_text(
                source.read_text(encoding="utf-8").replace(
                    "@TRELLIS_HARNESS_ROOT@", str(root)
                ),
                encoding="utf-8",
            )
            rendered_files.append(target)
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise WrapperError("isolated wrapper candidate could not be rendered") from exc
    bundle = {
        "files": sorted(path.name for path in rendered_files),
        "wrapper_id": identity["wrapper_id"],
    }
    return {
        "bundle_id": "sha256:" + digest_json(bundle),
        "schema_version": WRAPPER_SCHEMA_VERSION,
        "status": "rendered_candidate",
        "wrapper_id": identity["wrapper_id"],
        "wrapper_version": WRAPPER_VERSION,
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise WrapperError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--repo-root")
    parser.add_argument("command", choices=("version", "status", "doctor", "render"))
    parser.add_argument("--output-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "version":
            result = version(args.repo_root)
        elif args.command == "status":
            result = status(args.repo_root)
        elif args.command == "doctor":
            result = doctor(args.repo_root)
        else:
            if not args.output_dir:
                raise WrapperError("render requires an isolated output directory")
            result = render_isolated_bundle(args.output_dir, args.repo_root)
        if args.command != "render" and args.output_dir:
            raise WrapperError("only render accepts an output directory")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except WrapperError as exc:
        print(json.dumps({"error": str(exc), "status": "rejected"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
