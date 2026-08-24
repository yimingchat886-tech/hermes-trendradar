"""Immutable official Trellis upstream candidates."""

from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from hashlib import sha256, sha512
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from .authority import AuthorityError, digest, git, git_common_dir, utc_now


CLI_PACKAGE = "@mindfoldhq/trellis"
CORE_PACKAGE = "@mindfoldhq/trellis-core"
REGISTRY = "https://registry.npmjs.org"
CANDIDATE_SCHEMA_VERSION = 2
PROFILE_ARGS = {
    "claude-codex-native": (
        "init", "--claude", "--codex", "--yes", "--user", "uil-base",
        "--workflow", "native",
    )
}
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TREE_BYTES = 128 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 160 * 1024 * 1024
Fetch = Callable[[str], bytes]
ProfileGenerator = Callable[[Path, str], Mapping[str, Mapping[str, tuple[bytes, int]]]]


def _fetch(url: str) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "registry.npmjs.org"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AuthorityError(f"Unsafe npm registry URL: {url}")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "trellis-harness"})
    with urlopen(request, timeout=30) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise AuthorityError("npm registry response exceeds the candidate size limit")
    return data


def _registry_document(package: str, fetch: Fetch) -> Mapping[str, object]:
    value = json.loads(fetch(f"{REGISTRY}/{quote(package, safe='')}").decode("utf-8"))
    if not isinstance(value, Mapping) or value.get("name") != package:
        raise AuthorityError(f"Invalid npm metadata for {package}")
    return value


def _stable_version(raw: object) -> str:
    version = str(raw or "")
    if not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version
    ):
        raise AuthorityError(f"Upstream version is not a stable semantic version: {version}")
    return version


def _package_metadata(
    document: Mapping[str, object], selector: str | None, dist_tag: str
) -> dict[str, object]:
    tags = document.get("dist-tags")
    versions = document.get("versions")
    if not isinstance(tags, Mapping) or not isinstance(versions, Mapping):
        raise AuthorityError("npm metadata is missing dist-tags or versions")
    version = _stable_version(selector or tags.get(dist_tag))
    value = versions.get(version)
    if not isinstance(value, Mapping):
        raise AuthorityError(f"npm metadata does not contain version {version}")
    distribution = value.get("dist")
    dependencies = value.get("dependencies", {})
    if not isinstance(distribution, Mapping) or not isinstance(dependencies, Mapping):
        raise AuthorityError(f"npm metadata for {version} is incomplete")
    integrity = distribution.get("integrity")
    tarball = distribution.get("tarball")
    if not isinstance(integrity, str) or not isinstance(tarball, str):
        raise AuthorityError(f"npm distribution metadata for {version} is incomplete")
    return {
        "dependencies": dict(dependencies),
        "dist_tag": dist_tag if selector is None else None,
        "integrity": integrity,
        "name": document["name"],
        "tarball_url": tarball,
        "version": version,
    }


def _verify_integrity(data: bytes, integrity: str) -> None:
    try:
        algorithm, encoded = integrity.split("-", 1)
        hasher = {"sha256": sha256, "sha512": sha512}[algorithm]
        actual = base64.b64encode(hasher(data).digest()).decode("ascii")
    except (KeyError, ValueError) as exc:
        raise AuthorityError(f"Unsupported npm integrity: {integrity}") from exc
    if not hmac.compare_digest(actual, encoded):
        raise AuthorityError("npm tarball integrity mismatch")


def _package_tree(data: bytes) -> dict[str, tuple[bytes, int]]:
    contents: dict[str, tuple[bytes, int]] = {}
    total = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as exc:
        raise AuthorityError("Invalid npm tarball") from exc
    with archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise AuthorityError(f"Unsafe npm archive path: {member.name}")
            if member.issym() or member.islnk():
                raise AuthorityError(f"npm archive links are not allowed: {member.name}")
            if member.isdir():
                continue
            if not member.isfile() or path.parts[0] != "package" or len(path.parts) == 1:
                raise AuthorityError(f"Unsupported npm archive member: {member.name}")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if relative in contents or member.size > MAX_MEMBER_BYTES:
                raise AuthorityError(f"Unsafe npm archive member: {member.name}")
            total += member.size
            if total > MAX_TREE_BYTES:
                raise AuthorityError("npm archive expands beyond the candidate size limit")
            source = archive.extractfile(member)
            if source is None:
                raise AuthorityError(f"Unreadable npm archive member: {member.name}")
            value = source.read(MAX_MEMBER_BYTES + 1)
            if len(value) != member.size:
                raise AuthorityError(f"npm archive member size mismatch: {member.name}")
            contents[relative] = (value, member.mode & 0o777)
    if "package.json" not in contents:
        raise AuthorityError("npm package tree is missing package.json")
    return contents


def _resolve_cli(executable: str, version: str) -> Path:
    resolved = shutil.which(executable) if not Path(executable).is_absolute() else executable
    if not resolved or not Path(resolved).is_file():
        raise AuthorityError("TOOL_ARTIFACT_MISSING: Trellis CLI is unavailable")
    path = Path(resolved).resolve()
    result = subprocess.run(
        [str(path), "--version"], check=False, capture_output=True, text=True
    )
    if result.returncode or result.stdout.strip() != version:
        raise AuthorityError(f"TOOL_ARTIFACT_MISSING: exact Trellis {version} CLI is unavailable")
    return path


def _generate_profiles(cli: Path, version: str) -> Mapping[str, Mapping[str, tuple[bytes, int]]]:
    profiles: dict[str, dict[str, tuple[bytes, int]]] = {}
    for name, arguments in PROFILE_ARGS.items():
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="trellis-upstream-") as temp:
            root = Path(temp)
            git(root, "init", "-b", "main")
            env = {**os.environ, "HOME": str(root / "home"), "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run(
                [str(cli), *arguments], cwd=root, env=env, check=False,
                capture_output=True, text=True,
            )
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip()
                raise AuthorityError(f"Official Trellis {version} profile generation failed: {detail}")
            tree: dict[str, tuple[bytes, int]] = {}
            for path in sorted(root.rglob("*")):
                if ".git" in path.parts or "__pycache__" in path.parts:
                    continue
                if path.is_symlink():
                    raise AuthorityError(f"Generated profile contains a symlink: {path.relative_to(root)}")
                if path.is_file():
                    data = path.read_bytes()
                    if str(root).encode() in data or str(root / "home").encode() in data:
                        raise AuthorityError("Generated profile contains a machine-local path")
                    relative = path.relative_to(root).as_posix()
                    tree[relative] = (
                        _canonical_generated_file(relative, data),
                        path.stat().st_mode & 0o777,
                    )
            profiles[name] = tree
    return profiles


def _canonical_generated_file(path: str, data: bytes) -> bytes:
    if path != ".trellis/.developer":
        return data
    try:
        values = dict(line.split("=", 1) for line in data.decode("utf-8").splitlines())
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthorityError("Generated .trellis/.developer is invalid") from exc
    if set(values) != {"name", "initialized_at"} or values["name"] != "uil-base":
        raise AuthorityError("Generated .trellis/.developer contract changed")
    return b"name=uil-base\ninitialized_at=<excluded-observed-at>\n"


def _inventory(tree: Mapping[str, tuple[bytes, int]]) -> list[dict[str, object]]:
    return [
        {"digest": sha256(data).hexdigest(), "mode": mode, "path": path}
        for path, (data, mode) in sorted(tree.items())
    ]


def _full_inventory(
    cli_tree: Mapping[str, tuple[bytes, int]],
    core_tree: Mapping[str, tuple[bytes, int]],
    profiles: Mapping[str, Mapping[str, tuple[bytes, int]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for prefix, tree in (("packages/cli", cli_tree), ("packages/core", core_tree)):
        rows.extend({**row, "path": f"{prefix}/{row['path']}"} for row in _inventory(tree))
    for name, tree in sorted(profiles.items()):
        rows.extend({**row, "path": f"generated/{name}/{row['path']}"} for row in _inventory(tree))
    return sorted(rows, key=lambda row: str(row["path"]))


def _semver_key(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in _stable_version(version).split("."))  # type: ignore[return-value]


def _read_candidates(candidate_root: Path) -> list[dict[str, object]]:
    manifests: list[dict[str, object]] = []
    if not candidate_root.is_dir():
        return manifests
    for directory in candidate_root.iterdir():
        manifest_path = directory / "manifest.json"
        if directory.is_symlink() or not directory.is_dir() or manifest_path.is_symlink():
            raise AuthorityError(f"Unsafe upstream candidate artifact: {directory}")
        if not manifest_path.is_file():
            raise AuthorityError(f"Incomplete upstream candidate artifact: {directory}")
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = value.get("identity") if isinstance(value, Mapping) else None
        if (
            not isinstance(identity, Mapping)
            or value.get("candidate_id") != digest(identity)
            or directory.name != value.get("candidate_id")
        ):
            raise AuthorityError(f"Invalid upstream candidate identity: {directory}")
        manifests.append(dict(value))
    return manifests


def load_candidate(repo_root: Path, candidate_id: str) -> dict[str, object]:
    root = Path(repo_root).resolve()
    candidate_root = git_common_dir(root) / "trellis/upstream/candidates"
    manifest = next(
        (item for item in _read_candidates(candidate_root) if item["candidate_id"] == candidate_id),
        None,
    )
    if not manifest:
        raise AuthorityError(f"Upstream candidate is unavailable: {candidate_id}")
    identity = manifest["identity"]
    if identity.get("schema_version") != CANDIDATE_SCHEMA_VERSION:  # type: ignore[union-attr]
        raise AuthorityError(f"Upstream candidate schema is unsupported: {candidate_id}")
    inventory = manifest.get("full_inventory")
    delta = manifest.get("delta")
    if (
        not isinstance(inventory, list)
        or identity.get("full_inventory_digest") != digest(inventory)  # type: ignore[union-attr]
        or not isinstance(delta, list)
        or identity.get("delta_digest") != digest(delta)  # type: ignore[union-attr]
    ):
        raise AuthorityError(f"Upstream candidate manifest drifted: {candidate_id}")
    directory = candidate_root / candidate_id
    seen: set[str] = set()
    for row in inventory:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("path"), str)
            or row["path"] in seen
        ):
            raise AuthorityError(f"Upstream candidate inventory is invalid: {candidate_id}")
        seen.add(row["path"])
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise AuthorityError(f"Unsafe upstream candidate path: {row['path']}")
        path = directory.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise AuthorityError(f"Upstream candidate file is unavailable: {row['path']}")
        if sha256(path.read_bytes()).hexdigest() != row.get("digest"):
            raise AuthorityError(f"Upstream candidate file drifted: {row['path']}")
    for group in ("package", "core"):
        metadata = identity.get(group)  # type: ignore[union-attr]
        tarball = directory / f"tarballs/{'cli' if group == 'package' else 'core'}.tgz"
        if (
            not isinstance(metadata, Mapping)
            or tarball.is_symlink()
            or not tarball.is_file()
            or sha256(tarball.read_bytes()).hexdigest() != metadata.get("tarball_digest")
        ):
            raise AuthorityError(f"Upstream candidate tarball drifted: {group}")
    return manifest


def _delta(
    previous: list[Mapping[str, object]], current: list[Mapping[str, object]]
) -> list[dict[str, object]]:
    before = {str(row["path"]): row for row in previous}
    after = {str(row["path"]): row for row in current}
    rows: list[dict[str, object]] = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        rows.append(
            {
                "after_digest": new.get("digest") if new else None,
                "before_digest": old.get("digest") if old else None,
                "path": path,
                "status": "add" if old is None else "delete" if new is None else "modify",
            }
        )
    return rows


def _write_tree(root: Path, tree: Mapping[str, tuple[bytes, int]]) -> None:
    for raw, (data, mode) in tree.items():
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise AuthorityError(f"Unsafe candidate output path: {raw}")
        path = root.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)


def refresh_candidate(
    repo_root: Path,
    *,
    version: str | None = None,
    dist_tag: str = "latest",
    cli_executable: str = "trellis",
    fetch: Fetch = _fetch,
    profile_generator: ProfileGenerator = _generate_profiles,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    cli_metadata = _package_metadata(
        _registry_document(CLI_PACKAGE, fetch), version, dist_tag
    )
    exact_version = str(cli_metadata["version"])
    core_version = _stable_version(
        cli_metadata["dependencies"].get(CORE_PACKAGE)  # type: ignore[union-attr]
    )
    if core_version != exact_version:
        raise AuthorityError("Trellis CLI/Core versions are not exactly aligned")
    core_metadata = _package_metadata(
        _registry_document(CORE_PACKAGE, fetch), core_version, dist_tag
    )
    core_metadata["dist_tag"] = None

    cli_tarball = fetch(str(cli_metadata["tarball_url"]))
    core_tarball = fetch(str(core_metadata["tarball_url"]))
    _verify_integrity(cli_tarball, str(cli_metadata["integrity"]))
    _verify_integrity(core_tarball, str(core_metadata["integrity"]))
    cli_tree = _package_tree(cli_tarball)
    core_tree = _package_tree(core_tarball)
    cli = _resolve_cli(cli_executable, exact_version)
    packaged_cli = cli_tree.get("bin/trellis.js")
    if not packaged_cli or sha256(cli.read_bytes()).digest() != sha256(packaged_cli[0]).digest():
        raise AuthorityError("TOOL_ARTIFACT_MISMATCH: Trellis CLI differs from the npm package")
    profiles = {
        name: {
            path: (_canonical_generated_file(path, data), mode)
            for path, (data, mode) in tree.items()
        }
        for name, tree in profile_generator(cli, exact_version).items()
    }
    inventory = _full_inventory(cli_tree, core_tree, profiles)

    candidate_root = git_common_dir(root) / "trellis/upstream/candidates"
    existing = _read_candidates(candidate_root)
    for manifest in existing:
        package = manifest["identity"]["package"]  # type: ignore[index]
        if package["version"] == exact_version and package["integrity"] != cli_metadata["integrity"]:  # type: ignore[index]
            raise AuthorityError("Upstream registry integrity drifted for an existing version")
    previous = max(
        (
            item for item in existing
            if item["identity"].get("schema_version") == CANDIDATE_SCHEMA_VERSION  # type: ignore[union-attr]
            and _semver_key(str(item["identity"]["package"]["version"])) < _semver_key(exact_version)  # type: ignore[index]
        ),
        key=lambda item: _semver_key(str(item["identity"]["package"]["version"])),  # type: ignore[index]
        default=None,
    )
    previous_inventory = previous.get("full_inventory", []) if previous else []
    delta = _delta(previous_inventory, inventory)  # type: ignore[arg-type]
    identity = {
        "cli_executable_digest": sha256(cli.read_bytes()).hexdigest(),
        "core": {
            **{key: core_metadata[key] for key in ("name", "version", "integrity", "tarball_url")},
            "tarball_digest": sha256(core_tarball).hexdigest(),
            "tree_digest": digest(_inventory(core_tree)),
        },
        "delta_digest": digest(delta),
        "full_inventory_digest": digest(inventory),
        "package": {
            **{key: cli_metadata[key] for key in ("name", "version", "dist_tag", "integrity", "tarball_url")},
            "tarball_digest": sha256(cli_tarball).hexdigest(),
            "tree_digest": digest(_inventory(cli_tree)),
        },
        "previous_candidate_id": previous.get("candidate_id") if previous else None,
        "profiles": [
            {
                "arguments": list(PROFILE_ARGS.get(name, ())),
                "name": name,
                "tree_digest": digest(_inventory(tree)),
            }
            for name, tree in sorted(profiles.items())
        ],
        "normalizations": [
            {
                "path": "generated/*/.trellis/.developer",
                "rule": "initialized_at-excluded-from-candidate-identity",
            }
        ],
        "schema_version": CANDIDATE_SCHEMA_VERSION,
    }
    candidate_id = digest(identity)
    destination = candidate_root / candidate_id
    if destination.is_dir():
        return load_candidate(root, candidate_id)
    if destination.exists() or destination.is_symlink():
        raise AuthorityError(f"Unsafe upstream candidate destination: {destination}")

    manifest: dict[str, object] = {
        "artifact_locator": {
            "candidate": candidate_id,
            "root": "trellis/upstream/candidates",
            "scheme": "git-common-dir-v1",
        },
        "candidate_id": candidate_id,
        "delta": delta,
        "full_inventory": inventory,
        "identity": identity,
        "observed_at": utc_now(),
        "state": "AVAILABLE",
    }
    candidate_root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(dir=candidate_root, prefix=f".{candidate_id}."))
    try:
        _write_tree(temp / "packages/cli", cli_tree)
        _write_tree(temp / "packages/core", core_tree)
        for name, tree in profiles.items():
            _write_tree(temp / f"generated/{name}", tree)
        (temp / "tarballs").mkdir()
        (temp / "tarballs/cli.tgz").write_bytes(cli_tarball)
        (temp / "tarballs/core.tgz").write_bytes(core_tarball)
        (temp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temp, destination)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return manifest


def candidate_status(repo_root: Path) -> dict[str, object]:
    root = Path(repo_root).resolve()
    candidates = _read_candidates(
        git_common_dir(root) / "trellis/upstream/candidates"
    )
    supported = [
        load_candidate(root, str(item["candidate_id"])) for item in candidates
        if item["identity"].get("schema_version") == CANDIDATE_SCHEMA_VERSION  # type: ignore[union-attr]
    ]
    ordered = sorted(
        supported,
        key=lambda item: _semver_key(str(item["identity"]["package"]["version"])),  # type: ignore[index]
    )
    return {
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "observed_at": item["observed_at"],
                "state": item["state"],
                "version": item["identity"]["package"]["version"],  # type: ignore[index]
            }
            for item in ordered
        ],
        "latest_available": ordered[-1]["candidate_id"] if ordered else None,
        "superseded_schema_candidates": sorted(
            item["candidate_id"] for item in candidates if item not in supported
        ),
        "state": "AVAILABLE" if ordered else "EMPTY",
    }
