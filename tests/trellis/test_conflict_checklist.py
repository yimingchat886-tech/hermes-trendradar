from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".trellis" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import conflict_checklist


def write_card(
    repo: Path,
    name: str,
    touches: list[str],
    *,
    tier: str = "child",
    status: str = "in_progress",
    report: bool = True,
) -> Path:
    task_dir = repo / ".trellis" / "tasks" / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "name": name,
                "owner": "codex",
                "status": status,
                "tier": tier,
                "touches": touches,
            }
        ),
        encoding="utf-8",
    )
    if report:
        (task_dir / "stage-report.md").write_text(f"# Stage Report: {name}\n", encoding="utf-8")
    return task_dir


def test_conflict_matches_two_child_cards_and_skips_parent(tmp_path: Path) -> None:
    write_card(tmp_path, "parent", ["shared/**"], tier="parent", report=False)
    write_card(tmp_path, "child-a", ["shared/**"])
    write_card(tmp_path, "child-b", ["shared/file.md"])

    results, errors = conflict_checklist.build_report(tmp_path, ["./shared/file.md"])

    assert errors == []
    assert [match.card.name for match in results[0].matches] == ["child-a", "child-b"]


def test_conflict_fails_for_single_card_or_missing_report(tmp_path: Path) -> None:
    write_card(tmp_path, "child-a", ["src/**"])
    write_card(tmp_path, "child-b", ["docs/**"], report=False)
    write_card(tmp_path, "child-c", ["docs/**"])

    src_results, src_errors = conflict_checklist.build_report(tmp_path, ["src/app.py"])
    docs_results, docs_errors = conflict_checklist.build_report(tmp_path, ["docs/guide.md"])

    assert len(src_results[0].matches) == 1
    assert src_errors == ["src/app.py: fewer than two matching active task cards (1)"]
    assert [match.card.name for match in docs_results[0].matches] == ["child-b", "child-c"]
    assert docs_errors == ["docs/guide.md: child-b stage-report.md missing"]
