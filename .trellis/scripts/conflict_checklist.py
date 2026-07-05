#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Card:
    name: str
    owner: str
    status: str
    task_dir: Path
    touches: tuple[str, ...]

    @property
    def stage_report(self) -> Path:
        return self.task_dir / "stage-report.md"


@dataclass(frozen=True)
class CardMatch:
    card: Card
    report_ok: bool
    report_note: str


@dataclass(frozen=True)
class ConflictResult:
    path: str
    matches: tuple[CardMatch, ...]


def normalize_path(repo: Path, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def path_matches(path: str, pattern: str) -> bool:
    normalized_pattern = pattern
    while normalized_pattern.startswith("./"):
        normalized_pattern = normalized_pattern[2:]
    return fnmatch.fnmatchcase(path, normalized_pattern)


def conflicted_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "git diff failed")
    return [normalize_path(repo, line) for line in result.stdout.splitlines() if line.strip()]


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def active_cards(repo: Path) -> list[Card]:
    cards: list[Card] = []
    tasks_dir = repo / ".trellis" / "tasks"
    for task_json in sorted(tasks_dir.glob("*/task.json")):
        data = read_json(task_json)
        if not data:
            continue
        if data.get("status") in {"completed", "cancelled"}:
            continue
        if data.get("tier") == "parent":
            continue
        touches = tuple(item for item in data.get("touches", []) if isinstance(item, str))
        if not touches:
            continue
        cards.append(
            Card(
                name=str(data.get("name") or task_json.parent.name),
                owner=str(data.get("owner") or "unknown"),
                status=str(data.get("status") or "unknown"),
                task_dir=task_json.parent,
                touches=touches,
            )
        )
    return cards


def stage_report_status(path: Path) -> tuple[bool, str]:
    try:
        path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, "missing"
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"unreadable: {exc}"
    return True, "ok"


def build_report(repo: Path, conflicts: list[str]) -> tuple[list[ConflictResult], list[str]]:
    cards = active_cards(repo)
    results: list[ConflictResult] = []
    errors: list[str] = []

    for raw_path in conflicts:
        path = normalize_path(repo, raw_path)
        matches: list[CardMatch] = []
        for card in cards:
            if any(path_matches(path, pattern) for pattern in card.touches):
                ok, note = stage_report_status(card.stage_report)
                matches.append(CardMatch(card, ok, note))
                if not ok:
                    errors.append(f"{path}: {card.name} stage-report.md {note}")
        if len(matches) < 2:
            errors.append(f"{path}: fewer than two matching active task cards ({len(matches)})")
        results.append(ConflictResult(path, tuple(matches)))

    return results, errors


def print_report(results: list[ConflictResult], errors: list[str], repo: Path) -> None:
    if not results:
        print("No conflicted files.")
        return
    for result in results:
        print(f"- {result.path}")
        if not result.matches:
            print("  matches: none")
            continue
        print(f"  matches: {len(result.matches)}")
        for match in result.matches:
            card = match.card
            report_path = card.stage_report.relative_to(repo).as_posix()
            print(
                f"  - {card.name} owner={card.owner} status={card.status} "
                f"stage_report={report_path} report={match.report_note}"
            )
    if errors:
        print("errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List conflicted files and their active Trellis task cards.")
    parser.add_argument("--repo", default=".", help="repository path")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        conflicts = conflicted_files(repo)
        results, errors = build_report(repo, conflicts)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print_report(results, errors, repo)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
