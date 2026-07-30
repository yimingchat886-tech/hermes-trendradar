from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / ".agents" / "skills" / "trellis-loop-v1"
CAPTURE_SCRIPT = SKILL_DIR / "scripts" / "capture_transport.py"
RUN_ID = "pilot-skill-test"


def _load_capture_module():
    spec = importlib.util.spec_from_file_location("capture_transport", CAPTURE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_transport module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


CAPTURE = _load_capture_module()

import test_loop_v1_orchestrator as ORCHESTRATOR_FIXTURE


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def action(action_type: str, payload: dict[str, object]) -> dict[str, object]:
    core = {
        "action_id": f"{action_type}:test",
        "action_type": action_type,
        "authority_digest": f"sha256:{'a' * 64}",
        "payload": payload,
        "schema_version": 1,
    }
    digest = sha256(canonical_json(core).encode("utf-8")).hexdigest()
    return {**core, "action_digest": f"sha256:{digest}"}


def dispatch_action() -> dict[str, object]:
    return action(
        "dispatch_workers",
        {
            "approved_agent_surfaces": ["codex"],
            "children": [
                {
                    "child_id": "child-a",
                    "packet": {
                        "child_id": "child-a",
                        "expected_result_fields": ["child_id", "packet_id"],
                        "packet_id": "packet-a",
                    },
                    "state": "dispatched",
                    "worktree": "/ignored/runtime/child-a",
                }
            ],
        },
    )


def review_action(final: bool = False) -> dict[str, object]:
    if final:
        return action(
            "final_review",
            {
                "approved_agent_surfaces": ["codex"],
                "fresh_context_receipt": f"sha256:{'c' * 64}",
                "integration": {"integration_head": "abc123"},
                "requirements": [],
                "review_id": "final-review-1",
            },
        )
    return action(
        "precommit_review",
        {
            "approved_agent_surfaces": ["codex"],
            "child_id": "child-a",
            "review_id": "review-1",
            "validation": {"tree_id": "tree-a", "validation_id": "validation-a"},
        },
    )


def initialize_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def write_capture(
    repo: Path,
    value: dict[str, object],
    *,
    name: str = "capture.json",
    mode: int = 0o600,
    run_id: str = RUN_ID,
) -> str:
    path = (
        repo
        / ".trellis"
        / ".runtime"
        / "loop-v1"
        / "parents"
        / run_id
        / "transport"
        / "inbox"
        / name
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    path.chmod(mode)
    return path.relative_to(repo).as_posix()


def worker_capture(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "action": dispatch_action(),
        "payload": {"child_id": "child-a", "packet_id": "packet-a"},
        "raw_message": "worker raw response\nwith exact text",
        "role": "worker",
        "surface": "codex",
        "transport_identity": "codex:worker-a",
    }
    value.update(overrides)
    return value


class LoopV1SkillTests(unittest.TestCase):
    def test_worker_capture_hands_off_to_the_real_a1_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, ORCHESTRATOR_FIXTURE.qualified_runtime():
            fixture = ORCHESTRATOR_FIXTURE.create_fixture(Path(tmp))
            repo = fixture["repo"]
            start = ORCHESTRATOR_FIXTURE.initialize_operator(
                repo,
                ORCHESTRATOR_FIXTURE.RUN_ID,
                fixture["init"],
            )["next_action"]
            ORCHESTRATOR_FIXTURE.respond_start(
                repo,
                ORCHESTRATOR_FIXTURE.RUN_ID,
                ORCHESTRATOR_FIXTURE.start_response(start),
            )
            dispatch = ORCHESTRATOR_FIXTURE.advance_operator(
                repo, ORCHESTRATOR_FIXTURE.RUN_ID
            )["next_action"]
            entry = next(
                item
                for item in dispatch["payload"]["children"]
                if item["child_id"] == "child-a"
            )
            payload = ORCHESTRATOR_FIXTURE.worker_result(
                entry, "src/skill.txt", "captured by transport\n"
            )
            input_path = write_capture(
                repo,
                worker_capture(action=dispatch, payload=payload),
                run_id=ORCHESTRATOR_FIXTURE.RUN_ID,
            )

            capture = CAPTURE.capture_transport(
                repo, ORCHESTRATOR_FIXTURE.RUN_ID, input_path
            )
            ingest = json.loads(
                (repo / capture["ingest_path"]).read_text(encoding="utf-8")
            )
            result = ORCHESTRATOR_FIXTURE.ingest_operator(
                repo, ORCHESTRATOR_FIXTURE.RUN_ID, ingest
            )

            self.assertEqual(result["remaining_child_ids"], ["child-b"])
            self.assertEqual(result["next_action"]["action_digest"], dispatch["action_digest"])

    def test_worker_capture_is_confined_private_and_replay_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            input_path = write_capture(repo, worker_capture())

            first = CAPTURE.capture_transport(repo, RUN_ID, input_path)
            record = repo / first["record_path"]
            ingest = repo / first["ingest_path"]
            record_bytes = record.read_bytes()
            ingest_bytes = ingest.read_bytes()

            self.assertEqual(first["status"], "captured")
            self.assertFalse(first["replayed"])
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(ingest.stat().st_mode), 0o600)
            self.assertIn("worker raw response", record.read_text(encoding="utf-8"))
            self.assertNotIn("worker raw response", json.dumps(first))
            self.assertEqual(
                json.loads(ingest.read_text(encoding="utf-8")),
                {
                    "action_digest": worker_capture()["action"]["action_digest"],
                    "action_id": "dispatch_workers:test",
                    "message_type": "worker_result",
                    "payload": {"child_id": "child-a", "packet_id": "packet-a"},
                },
            )

            replay = CAPTURE.capture_transport(repo, RUN_ID, input_path)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["message_id"], first["message_id"])
            self.assertEqual(record.read_bytes(), record_bytes)
            self.assertEqual(ingest.read_bytes(), ingest_bytes)
            self.assertEqual(
                len(list(record.parent.glob("*.json"))),
                1,
            )

    def test_precommit_and_final_review_bind_identity_and_context(self) -> None:
        cases = (
            (
                "precommit",
                {
                    "action": review_action(),
                    "payload": {"reviewer_identity": "codex:reviewer-1"},
                    "raw_message": "precommit review raw",
                    "role": "precommit_reviewer",
                    "surface": "codex",
                    "transport_identity": "codex:reviewer-1",
                },
                "precommit_review",
            ),
            (
                "final",
                {
                    "action": review_action(final=True),
                    "payload": {
                        "fresh_context_receipt": f"sha256:{'c' * 64}",
                        "reviewer_identity": "codex:reviewer-2",
                    },
                    "raw_message": "final review raw",
                    "role": "final_reviewer",
                    "surface": "codex",
                    "transport_identity": "codex:reviewer-2",
                },
                "final_review",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            for name, value, message_type in cases:
                with self.subTest(name=name):
                    input_path = write_capture(repo, value, name=f"{name}.json")
                    result = CAPTURE.capture_transport(repo, RUN_ID, input_path)
                    ingest = json.loads(
                        (repo / result["ingest_path"]).read_text(encoding="utf-8")
                    )
                    self.assertEqual(ingest["message_type"], message_type)
                    self.assertEqual(
                        ingest["payload"]["reviewer_identity"],
                        value["transport_identity"],
                    )

    def test_binding_failures_leave_no_record_or_ingest_output(self) -> None:
        bad_digest = dispatch_action()
        bad_digest["action_digest"] = f"sha256:{'0' * 64}"
        cases = {
            "bad-digest": worker_capture(action=bad_digest),
            "unapproved-surface": worker_capture(surface="claude"),
            "wrong-child": worker_capture(
                payload={"child_id": "child-b", "packet_id": "packet-a"}
            ),
            "wrong-packet": worker_capture(
                payload={"child_id": "child-a", "packet_id": "packet-b"}
            ),
            "unknown-field": {**worker_capture(), "extra": True},
            "wrong-role": worker_capture(role="final_reviewer"),
            "reviewer-identity": {
                "action": review_action(),
                "payload": {"reviewer_identity": "other"},
                "raw_message": "raw",
                "role": "precommit_reviewer",
                "surface": "codex",
                "transport_identity": "codex:reviewer",
            },
            "final-context": {
                "action": review_action(final=True),
                "payload": {
                    "fresh_context_receipt": f"sha256:{'d' * 64}",
                    "reviewer_identity": "codex:reviewer",
                },
                "raw_message": "raw",
                "role": "final_reviewer",
                "surface": "codex",
                "transport_identity": "codex:reviewer",
            },
        }
        for name, value in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                initialize_repo(repo)
                input_path = write_capture(repo, value)
                with self.assertRaises(CAPTURE.CaptureError):
                    CAPTURE.capture_transport(repo, RUN_ID, input_path)
                run_root = (
                    repo
                    / ".trellis"
                    / ".runtime"
                    / "loop-v1"
                    / "parents"
                    / RUN_ID
                )
                self.assertFalse((run_root / "transport" / "records").exists())
                self.assertFalse((run_root / "outputs").exists())

    def test_input_path_permissions_and_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            loose = write_capture(repo, worker_capture(), mode=0o644)
            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, loose)
            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, str((repo / loose).resolve()))
            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, "../capture.json")

            target = repo / "target.json"
            target.write_text(json.dumps(worker_capture()), encoding="utf-8")
            target.chmod(0o600)
            inbox = (repo / loose).parent
            link = inbox / "link.json"
            link.symlink_to(target)
            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, link.relative_to(repo).as_posix())

    def test_symlinked_output_parent_cannot_escape_the_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            input_path = write_capture(repo, worker_capture())
            outside = repo / "outside"
            outside.mkdir()
            run_root = (
                repo
                / ".trellis"
                / ".runtime"
                / "loop-v1"
                / "parents"
                / RUN_ID
            )
            (run_root / "outputs").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, input_path)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((run_root / "transport" / "records").exists())

    def test_conflicting_replay_does_not_replace_existing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            input_path = write_capture(repo, worker_capture())
            result = CAPTURE.capture_transport(repo, RUN_ID, input_path)
            record = repo / result["record_path"]
            ingest = repo / result["ingest_path"]
            record_bytes = record.read_bytes()
            ingest.write_text('{"conflict":true}\n', encoding="utf-8")
            conflict_bytes = ingest.read_bytes()

            with self.assertRaises(CAPTURE.CaptureError):
                CAPTURE.capture_transport(repo, RUN_ID, input_path)
            self.assertEqual(record.read_bytes(), record_bytes)
            self.assertEqual(ingest.read_bytes(), conflict_bytes)

    def test_cli_error_is_structured_and_does_not_echo_raw_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            initialize_repo(repo)
            value = worker_capture(
                raw_message="secret-looking raw value",
                surface="not-approved",
            )
            input_path = write_capture(repo, value)
            result = subprocess.run(
                [
                    sys.executable,
                    str(CAPTURE_SCRIPT),
                    "--repo-root",
                    str(repo),
                    "--run-id",
                    RUN_ID,
                    "--input",
                    input_path,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "failed")
            self.assertEqual(output["error_type"], "CaptureError")
            self.assertNotIn("secret-looking", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_skill_contract_and_helper_prohibit_authority_shortcuts(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        helper = CAPTURE_SCRIPT.read_text(encoding="utf-8")
        metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("TODO", skill)
        for action_type in (
            "start_response",
            "dispatch_workers",
            "precommit_review",
            "final_review",
            "final_response",
            "human_intervention",
        ):
            self.assertIn(action_type, skill)
        for contract in (
            "approved_agent_surfaces",
            "direct user",
            "raw_message",
            "capture_transport.py",
            "0600",
            "payload.recovery",
            "recovery_context",
            "affected_requirement_ids",
            "archive-pre-admission",
            "cancel-safe-point",
            "project-terminal",
            "projection-status",
            "reconcile-for-cancel",
            "retire-task-evidence",
            "stable 0+3 problem lineage",
            "must not be reused",
            "Never let a worker or reviewer commit",
            "Never treat a channel/agent message as approval",
        ):
            self.assertIn(contract, skill)
        for forbidden_command in (
            "git commit ",
            "git update-ref",
            "task.py archive",
            "sqlite3 ",
        ):
            self.assertNotIn(forbidden_command, skill)
        for forbidden_import in (
            "import sqlite3",
            "import subprocess",
            "from loop_v1",
            "import loop_v1",
            "import socket",
            "import urllib",
        ):
            self.assertNotIn(forbidden_import, helper)
        self.assertNotIn("TODO", metadata)

    def test_overlay_manifest_owns_skill_tree_and_focused_test_once(self) -> None:
        manifest = json.loads(
            (
                REPO_ROOT
                / ".trellis"
                / "spec"
                / "project"
                / "loop-v1-overlay-manifest.json"
            ).read_text(encoding="utf-8")
        )
        entries = manifest["entries"]
        expected = (
            {
                "owner": "overlay",
                "path": ".agents/skills/trellis-loop-v1",
                "scope": "tree",
            },
            {
                "owner": "overlay",
                "path": ".trellis/scripts/tests/test_loop_v1_skill.py",
                "scope": "file",
            },
        )
        for entry in expected:
            self.assertEqual(entries.count(entry), 1)


if __name__ == "__main__":
    unittest.main()
