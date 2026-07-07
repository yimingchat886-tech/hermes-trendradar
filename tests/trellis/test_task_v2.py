from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".trellis" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common.task_context import cmd_validate
from common.paths import generate_task_date_prefix
from common.task_store import cmd_archive, cmd_claim, cmd_create, cmd_release, cmd_soft_archive


def args(**values):
    return argparse.Namespace(**values)


def seed_repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".trellis" / "tasks").mkdir(parents=True)
    (tmp_path / ".trellis" / "archive").mkdir()
    (tmp_path / ".trellis" / ".developer").write_text("name=jym\n", encoding="utf-8")
    shutil.copytree(
        ROOT / ".trellis" / "templates" / "v2",
        tmp_path / ".trellis" / "templates" / "v2",
        dirs_exist_ok=True,
    )
    return tmp_path


def create_args(title: str, slug: str, **overrides):
    values = {
        "title": title,
        "slug": slug,
        "assignee": "jym",
        "priority": "P1",
        "description": "test task",
        "parent": None,
        "package": None,
        "tier": "light",
        "owner": "codex",
        "touches": [],
    }
    values.update(overrides)
    return args(**values)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v2_create_writes_tier_metadata_templates_and_state(tmp_path: Path, monkeypatch) -> None:
    seed_repo(tmp_path, monkeypatch)
    prefix = generate_task_date_prefix()

    assert cmd_create(create_args("Parent", "parent", tier="parent")) == 0
    parent = tmp_path / ".trellis" / "tasks" / f"{prefix}-parent"
    pdata = read_json(parent / "task.json")

    assert pdata["tier"] == "parent"
    assert pdata["owner"] == "codex"
    assert pdata["meta"]["workflow_mode"] == "harness_state_machine"
    assert pdata["meta"]["state_machine"]["current_state"] == "parent_prd_draft"
    assert (parent / "governance.md").is_file()

    assert cmd_create(create_args("Child", "child", parent=str(parent), touches=["src/**,tests/**"])) == 0
    child = tmp_path / ".trellis" / "tasks" / f"{prefix}-child"
    cdata = read_json(child / "task.json")
    pdata = read_json(parent / "task.json")

    assert cdata["tier"] == "child"
    assert cdata["parent"] == f"{prefix}-parent"
    assert cdata["touches"] == ["src/**", "tests/**"]
    assert cdata["meta"]["state_machine"]["current_state"] == "child_plan_draft"
    assert f"{prefix}-child" in pdata["children"]
    assert (child / "stage-report.md").is_file()


def test_v2_validate_and_soft_archive_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    seed_repo(tmp_path, monkeypatch)
    prefix = generate_task_date_prefix()
    assert cmd_create(create_args("Parent", "parent", tier="parent")) == 0
    parent = tmp_path / ".trellis" / "tasks" / f"{prefix}-parent"
    assert cmd_create(create_args("M2 child", "child", parent=str(parent), touches=["src/**"])) == 0
    child = tmp_path / ".trellis" / "tasks" / f"{prefix}-child"

    (parent / "governance.md").write_text(
        """# Governance: Parent

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| M2 child | x | none | codex | codex/x | planned | |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| WV2-M2-REQ-001 | M2 child | planned | |

## External Review

### PRD Review

pass

### Closeout Review

TBD

## Boundary Pass

pass
""",
        encoding="utf-8",
    )
    (child / "stage-report.md").write_text(
        "# Stage Report: M2 child\n\n## Acceptance\n\n- [x] WV2-M2-REQ-001 done\n",
        encoding="utf-8",
    )

    assert cmd_validate(args(dir=str(child))) == 0
    assert cmd_soft_archive(args(name=str(child), commit="abc123", force_archive=False, reason="")) == 0
    assert cmd_validate(args(dir=str(parent))) == 0

    cdata = read_json(child / "task.json")
    assert cdata["status"] == "completed"
    assert cdata["commit"] == "abc123"
    assert cdata["meta"]["state_machine"]["current_state"] == "child_archived"
    governance = (parent / "governance.md").read_text(encoding="utf-8")
    assert "| M2 child | x | none | codex | codex/x | completed | abc123 |" in governance
    assert f"| WV2-M2-REQ-001 | M2 child | completed | {prefix}-child/stage-report.md |" in governance

    before_log = (child / "state-events.jsonl").read_text(encoding="utf-8")
    assert cmd_soft_archive(args(name=str(child), commit="abc123", force_archive=False, reason="")) == 0
    assert (child / "state-events.jsonl").read_text(encoding="utf-8") == before_log


def test_archive_done_gate_blocks_template_acceptance_without_mutation(tmp_path: Path, monkeypatch) -> None:
    seed_repo(tmp_path, monkeypatch)
    prefix = generate_task_date_prefix()
    assert cmd_create(create_args("Light", "light", tier="light")) == 0
    task = tmp_path / ".trellis" / "tasks" / f"{prefix}-light"
    before = (task / "task.json").read_text(encoding="utf-8")

    code = cmd_archive(args(name=str(task), no_commit=True, force_archive=False, reason=""))

    assert code == 1
    assert (task / "task.json").read_text(encoding="utf-8") == before
    assert task.is_dir()


def test_archive_parent_advances_state_machine_to_archived(tmp_path: Path, monkeypatch) -> None:
    seed_repo(tmp_path, monkeypatch)
    prefix = generate_task_date_prefix()
    assert cmd_create(create_args("Parent", "parent", tier="parent")) == 0
    parent = tmp_path / ".trellis" / "tasks" / f"{prefix}-parent"

    assert cmd_archive(args(name=str(parent), no_commit=True, force_archive=False, reason="")) == 0

    archived = next((tmp_path / ".trellis" / "tasks" / "archive").glob(f"*/{prefix}-parent"))
    data = read_json(archived / "task.json")
    assert data["status"] == "completed"
    assert data["meta"]["state_machine"]["current_state"] == "parent_archived"


def test_claim_and_release_update_owner_and_audit_events(tmp_path: Path, monkeypatch) -> None:
    seed_repo(tmp_path, monkeypatch)
    prefix = generate_task_date_prefix()
    assert cmd_create(create_args("Child", "child", tier="child", owner="cc")) == 0
    child = tmp_path / ".trellis" / "tasks" / f"{prefix}-child"

    assert cmd_claim(args(name=str(child), owner="codex", override_claim=False, reason="")) == 0
    data = read_json(child / "task.json")
    assert data["owner"] == "codex"
    events = (child / "state-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "claim"' in events
    assert "owner cc -> codex" in events

    assert cmd_claim(args(name=str(child), owner="cc", override_claim=True, reason="handoff")) == 0
    data = read_json(child / "task.json")
    assert data["owner"] == "cc"
    events = (child / "state-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "override_claim"' in events
    assert "reason: handoff" in events

    assert cmd_release(args(name=str(child), owner="jym", reason="done")) == 0
    data = read_json(child / "task.json")
    assert data["owner"] == "jym"
    events = (child / "state-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "release"' in events
    assert "owner cc -> jym" in events
