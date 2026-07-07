"""
JSON file I/O utilities.

Provides read_json and write_json as the single source of truth
for JSON file operations across all Trellis scripts.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def read_json(path: Path) -> dict | None:
    """Read and parse a JSON file.

    Returns None if the file doesn't exist, is invalid JSON, or can't be read.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_json(path: Path, data: dict) -> bool:
    """Write dict to JSON file with pretty formatting.

    Returns True on success, False on error.
    """
    try:
        write_bytes_atomic(path, json_bytes(data))
        return True
    except (OSError, IOError):
        return False


def json_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = write_temp_bytes(path, data)
    try:
        os.replace(tmp, path)
    finally:
        _unlink_if_exists(tmp)


def write_temp_bytes(path: Path, data: bytes) -> Path:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    return tmp


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
