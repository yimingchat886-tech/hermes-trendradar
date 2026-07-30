"""Crash-recoverable task archive moves with exact BOARD and Git preimages."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from .io import json_bytes, write_bytes_atomic


TERMINAL_PHASES = {"committed", "rolled_back"}
PHASES = {
    "planned",
    "moving",
    "files_moved",
    "board_projected",
    "git_staged",
    "committed",
    "rolled_back",
    "recovery_required",
}


class ArchiveTransactionError(RuntimeError):
    """Raised when archive state cannot advance or recover exactly."""


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise ArchiveTransactionError(
            result.stderr.strip() or result.stdout.strip() or "git command failed"
        )
    return result


def _runtime(root: Path) -> Path:
    return root / ".trellis" / ".runtime" / "archive-transactions"


def _journal_path(root: Path, transaction_id: str) -> Path:
    if not transaction_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in transaction_id
    ):
        raise ArchiveTransactionError("archive transaction ID is invalid")
    return _runtime(root) / f"{transaction_id}.json"


def _write_journal(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(path, json_bytes(value))


def _read_journal(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveTransactionError("archive transaction journal is invalid") from exc
    if not isinstance(value, dict) or value.get("phase") not in PHASES:
        raise ArchiveTransactionError("archive transaction journal is malformed")
    return value


def unresolved_archive_transactions(
    repo_root: Path,
    *,
    exclude: str | None = None,
) -> list[str]:
    """Return incomplete transaction IDs, failing closed on malformed journals."""
    root = Path(repo_root).resolve()
    directory = _runtime(root)
    if not directory.is_dir():
        return []
    blockers = []
    for path in directory.glob("*.json"):
        value = _read_journal(path)
        if path.stem != exclude and value["phase"] not in TERMINAL_PHASES:
            blockers.append(path.stem)
    return sorted(blockers)


def assert_archive_transactions_resolved(
    repo_root: Path,
    *,
    exclude: str | None = None,
) -> None:
    blockers = unresolved_archive_transactions(repo_root, exclude=exclude)
    if blockers:
        raise ArchiveTransactionError(
            "archive recovery is required before a new transaction: "
            + ", ".join(blockers)
        )


def _relative(root: Path, path: Path) -> str:
    target = path.resolve()
    try:
        return target.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ArchiveTransactionError("archive path escapes the repository") from exc


def _tree_digest(path: Path) -> str:
    values = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ArchiveTransactionError("archive source contains a symlink")
        if item.is_file():
            values.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "sha256": sha256(item.read_bytes()).hexdigest(),
                }
            )
    return f"sha256:{sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()}"


def _bytes_preimage(path: Path) -> dict[str, object]:
    present = path.is_file()
    payload = path.read_bytes() if present else b""
    return {
        "bytes": base64.b64encode(payload).decode("ascii"),
        "present": present,
        "sha256": f"sha256:{sha256(payload).hexdigest()}",
    }


def _restore_bytes(path: Path, preimage: dict[str, object]) -> None:
    payload = base64.b64decode(str(preimage["bytes"]))
    if preimage["present"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(path, payload)
    elif path.exists():
        path.unlink()


def _index_path(root: Path) -> Path:
    value = _git(root, "rev-parse", "--git-path", "index").stdout.strip()
    path = Path(value)
    return path if path.is_absolute() else root / path


def _rollback(
    root: Path,
    path: Path,
    journal: dict[str, object],
) -> dict[str, object]:
    errors = []
    for item in reversed(list(journal["moves"])):
        source = root / str(item["source"])
        destination = root / str(item["destination"])
        try:
            if destination.is_dir() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
            elif not (source.is_dir() and not destination.exists()):
                raise ArchiveTransactionError(
                    f"archive rollback path is ambiguous: {item['source']}"
                )
        except (OSError, shutil.Error, ArchiveTransactionError) as exc:
            errors.append(str(exc))
    try:
        _restore_bytes(root / "BOARD.md", dict(journal["board_preimage"]))
        _restore_bytes(
            Path(str(journal["index_path"])),
            dict(journal["index_preimage"]),
        )
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    journal["phase"] = "recovery_required" if errors else "rolled_back"
    journal["recovery_errors"] = errors
    _write_journal(path, journal)
    if errors:
        raise ArchiveTransactionError(
            "archive rollback is incomplete: " + "; ".join(errors)
        )
    return journal


def archive_paths_transaction(
    repo_root: Path,
    *,
    transaction_id: str,
    moves: Sequence[tuple[Path, Path]],
    commit_message: str,
    commit_enabled: bool = True,
    family_root: str,
) -> dict[str, object]:
    """Move ordered task paths, project BOARD, and commit or roll back exactly."""
    root = Path(repo_root).resolve()
    journal_path = _journal_path(root, transaction_id)
    assert_archive_transactions_resolved(root, exclude=transaction_id)
    if journal_path.is_file():
        existing = _read_journal(journal_path)
        if existing["phase"] == "committed":
            return existing
        if existing["phase"] != "rolled_back":
            raise ArchiveTransactionError(
                f"archive transaction requires recovery: {transaction_id}"
            )
    if commit_enabled and _git(root, "diff", "--cached", "--quiet", check=False).returncode:
        raise ArchiveTransactionError("archive transaction requires a clean Git index")

    move_values = []
    for source, destination in moves:
        source_relative = _relative(root, source)
        destination_relative = _relative(root, destination)
        if not source.is_dir() or destination.exists():
            raise ArchiveTransactionError(
                f"archive source/destination conflict: {source_relative}"
            )
        move_values.append(
            {
                "destination": destination_relative,
                "source": source_relative,
                "source_digest": _tree_digest(source),
            }
        )
    index = _index_path(root)
    identity = {
        "family_root": family_root,
        "git_head": _git(root, "rev-parse", "HEAD").stdout.strip(),
        "moves": move_values,
    }
    journal: dict[str, object] = {
        **identity,
        "board_preimage": _bytes_preimage(root / "BOARD.md"),
        "commit_enabled": commit_enabled,
        "commit_message": commit_message,
        "git_commit": None,
        "index_path": str(index),
        "index_preimage": _bytes_preimage(index),
        "moved": [],
        "phase": "planned",
        "recovery_errors": [],
        "transaction_id": transaction_id,
    }
    _write_journal(journal_path, journal)
    commit_succeeded = False
    try:
        journal["phase"] = "moving"
        _write_journal(journal_path, journal)
        for item in move_values:
            source = root / str(item["source"])
            destination = root / str(item["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            journal["moved"].append(item["source"])
            _write_journal(journal_path, journal)
        journal["phase"] = "files_moved"
        _write_journal(journal_path, journal)

        board = root / ".trellis" / "scripts" / "board.py"
        if board.is_file():
            result = subprocess.run(
                [sys.executable, str(board)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ArchiveTransactionError(
                    result.stderr.strip() or "BOARD generator failed"
                )
        journal["phase"] = "board_projected"
        _write_journal(journal_path, journal)

        if commit_enabled:
            paths = [
                value
                for item in move_values
                for value in (str(item["source"]), str(item["destination"]))
            ]
            if (root / "BOARD.md").exists() or journal["board_preimage"]["present"]:
                paths.append("BOARD.md")
            _git(root, "add", "-A", "--", *paths)
            journal["phase"] = "git_staged"
            _write_journal(journal_path, journal)
            _git(
                root,
                "commit",
                "-m",
                commit_message,
                "-m",
                f"Archive-Transaction: {transaction_id}",
            )
            commit_succeeded = True
            journal["git_commit"] = _git(root, "rev-parse", "HEAD").stdout.strip()
        journal["phase"] = "committed"
        _write_journal(journal_path, journal)
        return journal
    except Exception:
        if commit_succeeded:
            journal["phase"] = "recovery_required"
            journal["recovery_errors"] = [
                "Git commit succeeded before journal projection completed"
            ]
            _write_journal(journal_path, journal)
        else:
            _rollback(root, journal_path, journal)
        raise


def recover_archive_transaction(
    repo_root: Path,
    transaction_id: str,
) -> dict[str, object]:
    """Finish journal projection after a proven commit or roll files back."""
    root = Path(repo_root).resolve()
    path = _journal_path(root, transaction_id)
    journal = _read_journal(path)
    if journal["phase"] in TERMINAL_PHASES:
        return journal
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    message = _git(root, "show", "-s", "--format=%B", "HEAD").stdout
    if (
        head != journal["git_head"]
        and f"Archive-Transaction: {transaction_id}" in message
    ):
        journal["git_commit"] = head
        journal["phase"] = "committed"
        _write_journal(path, journal)
        return journal
    return _rollback(root, path, journal)
