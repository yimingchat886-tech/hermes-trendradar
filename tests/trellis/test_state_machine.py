from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".trellis" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import state_cli
import state_machine
from state_machine import StateMachineError, apply_event, init_task, status


def write_task(task_dir: Path, *, mode: str | None = "harness_state_machine", status_value: str = "planning") -> Path:
    task_dir.mkdir()
    meta = {} if mode is None else {"workflow_mode": mode}
    data = {
        "id": "task",
        "status": status_value,
        "meta": meta,
        "kept": {"unknown": True},
    }
    path = task_dir / "task.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def log_lines(task_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (task_dir / "state-events.jsonl").read_text(encoding="utf-8").splitlines()]


def unchanged_after_failure(task_dir: Path, func) -> None:
    task_path = task_dir / "task.json"
    log_path = task_dir / "state-events.jsonl"
    before_task = task_path.read_bytes()
    before_log = log_path.read_bytes() if log_path.exists() else None
    try:
        func()
    except StateMachineError:
        pass
    else:
        raise AssertionError("expected StateMachineError")
    assert task_path.read_bytes() == before_task
    if before_log is None:
        assert not log_path.exists()
    else:
        assert log_path.read_bytes() == before_log


def test_init_parent_and_child_write_state_and_init_event(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    write_task(parent, status_value="in_progress")
    write_task(child, status_value="planning")

    init_task(parent, "parent", by="user")
    init_task(child, "child", by="agent")

    parent_task = read_json(parent / "task.json")
    child_task = read_json(child / "task.json")
    assert parent_task["status"] == "in_progress"
    assert child_task["status"] == "planning"
    assert parent_task["meta"]["state_machine"]["current_state"] == "parent_prd_draft"
    assert child_task["meta"]["state_machine"]["current_state"] == "child_plan_draft"
    assert parent_task["kept"] == {"unknown": True}
    assert log_lines(parent)[0]["event"] == "init"
    assert log_lines(child)[0]["event"] == "init"


def test_reinit_same_kind_is_noop_and_different_kind_is_rejected(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")

    before_task = (task_dir / "task.json").read_bytes()
    before_log = (task_dir / "state-events.jsonl").read_bytes()
    result = init_task(task_dir, "child")
    assert result["changed"] is False
    assert (task_dir / "task.json").read_bytes() == before_task
    assert (task_dir / "state-events.jsonl").read_bytes() == before_log

    unchanged_after_failure(task_dir, lambda: init_task(task_dir, "parent"))


def test_valid_child_transition_appends_event_and_preserves_status(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir, status_value="in_progress")
    init_task(task_dir, "child")
    apply_event(task_dir, "plan_drafted", by="user", note="ready")

    task = read_json(task_dir / "task.json")
    machine = task["meta"]["state_machine"]
    events = log_lines(task_dir)
    assert task["status"] == "in_progress"
    assert machine["previous_state"] == "child_plan_draft"
    assert machine["current_state"] == "child_waiting_completion_signal"
    assert machine["last_event"] == "plan_drafted"
    assert events[-1]["event"] == "plan_drafted"
    assert events[-1]["note"] == "ready"
    assert len(events) == 2


def test_invalid_transition_does_not_write(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")

    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "commit_created"))


def test_blocker_edges_and_resolution(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")

    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "blocker_resolved"))
    apply_event(task_dir, "blocker_opened", note="blocked")
    machine = read_json(task_dir / "task.json")["meta"]["state_machine"]
    assert machine["current_state"] == "child_blocked"
    assert machine["blocked_from_state"] == "child_plan_draft"

    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "blocker_opened"))
    apply_event(task_dir, "blocker_resolved")
    assert read_json(task_dir / "task.json")["meta"]["state_machine"]["current_state"] == "child_plan_draft"

    apply_event(task_dir, "plan_drafted")
    apply_event(task_dir, "completion_signal_received")
    apply_event(task_dir, "commit_created")
    apply_event(task_dir, "child_archive_completed")
    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "blocker_opened"))


def test_missing_blocked_from_state_is_rejected_without_write(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")
    task = read_json(task_dir / "task.json")
    task["meta"]["state_machine"]["current_state"] = "child_blocked"
    task["meta"]["state_machine"]["blocked_from_state"] = "not-a-state"
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")

    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "blocker_resolved"))


def test_non_harness_init_and_event_do_not_mutate_files(tmp_path: Path) -> None:
    for mode, with_log in [("staged_overlay", False), (None, True)]:
        task_dir = tmp_path / f"task-{mode}-{with_log}"
        write_task(task_dir, mode=mode)
        if with_log:
            (task_dir / "state-events.jsonl").write_text('{"existing": true}\n', encoding="utf-8")

        unchanged_after_failure(task_dir, lambda task_dir=task_dir: init_task(task_dir, "child"))
        unchanged_after_failure(task_dir, lambda task_dir=task_dir: apply_event(task_dir, "plan_drafted"))


def test_event_log_replace_failure_leaves_files_unchanged(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")

    real_replace = os.replace

    def fail_log_replace(src, dst):
        if Path(dst).name == "state-events.jsonl":
            raise OSError("log write failed")
        return real_replace(src, dst)

    monkeypatch.setattr(state_machine.os, "replace", fail_log_replace)
    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "plan_drafted"))


def test_task_replace_failure_restores_previous_log(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    init_task(task_dir, "child")

    real_replace = os.replace

    def fail_task_replace(src, dst):
        if Path(dst).name == "task.json":
            raise OSError("task write failed")
        return real_replace(src, dst)

    monkeypatch.setattr(state_machine.os, "replace", fail_task_replace)
    unchanged_after_failure(task_dir, lambda: apply_event(task_dir, "plan_drafted"))


def test_status_and_cli_surface(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    write_task(task_dir)
    assert state_cli.main(["init", str(task_dir), "--kind", "child"]) == 0

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = state_cli.main(["status", str(task_dir)])

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["state_machine"]["current_state"] == "child_plan_draft"
    assert status(task_dir)["event_count"] == 1
