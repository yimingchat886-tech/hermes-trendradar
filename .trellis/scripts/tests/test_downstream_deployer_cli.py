from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downstream_deployer import cli
from downstream_deployer.authority import AuthorityStore, TARGET_ORDER
from test_downstream_deployer_authority import (
    activate_grants,
    at,
    common_binding,
    digest,
    source_identity,
)
from test_downstream_deployer_plan import git, prepare_repositories, qualified
from test_downstream_deployer_transaction import checks, materializer


NOW = "2026-07-18T00:00:00Z"
EXPIRES = "2026-07-19T00:00:00Z"


def write_json(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_cli(command: str, input_path: Path) -> tuple[int, dict[str, object], str]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main([command, "--input", str(input_path)])
    raw = output.getvalue()
    return code, json.loads(raw), raw


def action_binding(
    target_id: str,
    plan: dict[str, object],
    *,
    recover: dict[str, object] | None = None,
) -> dict[str, str]:
    value = {
        **common_binding(target_id),
        "base_head": str(plan["target"]["head"]),
        "lineage_anchor": str(plan["target"]["head"]),
        "plan_digest": str(plan["plan_digest"]),
        "source_receipt_id": str(plan["source"]["qualification_receipt_id"]),
    }
    if recover is not None:
        value.update(
            {
                "failed_transaction_id": str(recover["transaction_id"]),
                "preimage_digest": str(recover["recovery"]["preimage_digest"]),
            }
        )
    return value


def approve_one_off(
    store: AuthorityStore,
    *,
    authority_id: str,
    effect: str,
    binding: dict[str, str],
    prefix: str,
) -> None:
    request = store.request_one_off(
        event_id=f"{prefix}:request",
        authority_id=authority_id,
        effect=effect,
        binding=binding,
        not_before=NOW,
        expires_at=EXPIRES,
        now=NOW,
    )
    store.approve_one_off(
        event_id=f"{prefix}:approve",
        authority_id=authority_id,
        request_digest=request["request_digest"],
        response_identity=f"{prefix}:response",
        direct_user_action=True,
        now=NOW,
    )


class DownstreamDeployerCliTests(unittest.TestCase):
    def test_public_docs_match_cli_and_preserve_managed_agents_block(self) -> None:
        root = Path(__file__).resolve().parents[3]
        readme = (root / "README.md").read_text(encoding="utf-8")
        handoff = (root / "HANDOFF.md").read_text(encoding="utf-8")
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        launcher = "PYTHONPATH=.trellis/scripts python3 -m downstream_deployer.cli"
        self.assertIn(launcher, readme)
        self.assertIn(launcher, handoff)
        self.assertNotIn("apply unsupported; stop after plan.", handoff)
        self.assertNotIn("apply unsupported; stop after plan.", agents)
        for command in cli.COMMANDS:
            self.assertIn(f"`{command}`", handoff)

        committed = subprocess.run(
            ["git", "show", "HEAD:AGENTS.md"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout

        def managed(value: str) -> str:
            start = value.index("<!-- TRELLIS:START -->")
            end = value.index("<!-- TRELLIS:END -->") + len("<!-- TRELLIS:END -->")
            return value[start:end]

        self.assertEqual(managed(agents), managed(committed))

    def test_state_commands_emit_one_redacted_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state = base / "state.sqlite3"
            target_id = TARGET_ORDER[0]
            binding = {
                **common_binding(target_id),
                "base_head": "b" * 40,
                "plan_digest": digest("plan"),
                "source_receipt_id": digest("source", prefixed=True),
            }
            authority_id = digest("cli-one-off", prefixed=True)
            request_input = write_json(
                base / "request.json",
                {
                    "authority_id": authority_id,
                    "binding": binding,
                    "effect": "apply",
                    "event_id": "cli:request",
                    "expires_at": EXPIRES,
                    "not_before": NOW,
                    "now": NOW,
                    "state_path": str(state),
                },
            )
            code, request, raw = run_cli("one-off-request", request_input)
            self.assertEqual(code, 0)
            self.assertEqual(request["status"], "ok")
            self.assertNotIn(str(base), raw)
            request_digest = request["result"]["request_digest"]

            approve_input = write_json(
                base / "approve.json",
                {
                    "authority_id": authority_id,
                    "event_id": "cli:approve",
                    "now": NOW,
                    "request_digest": request_digest,
                    "response_identity": "cli:response",
                    "state_path": str(state),
                },
            )
            code, approved, _ = run_cli("one-off-approve", approve_input)
            self.assertEqual(code, 0)
            self.assertEqual(approved["result"]["state"], "approved")

            status_input = write_json(
                base / "status.json",
                {"state_path": str(state)},
            )
            code, status, raw = run_cli("status", status_input)
            self.assertEqual(code, 0)
            self.assertIn(authority_id, status["result"]["authorities"])
            self.assertNotIn(str(base), raw)
            code, inbox, _ = run_cli("inbox", status_input)
            self.assertEqual(code, 0)
            self.assertIn("cli:request", inbox["result"]["events"])

    def test_failed_plan_check_persists_no_candidate_plan_or_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            artifacts = base / "artifacts"
            artifacts.mkdir()
            candidate = artifacts / "candidate"
            plan_path = artifacts / "plan.json"
            state_path = artifacts / "state.sqlite3"
            plan_input = write_json(
                artifacts / "plan-input.json",
                {
                    "candidate_output": str(candidate),
                    "manifest_path": str(source / "manifest.json"),
                    "plan_output": str(plan_path),
                    "scratch_root": str(scratch),
                    "source_root": str(source),
                    "target_id": target.name,
                    "target_root": str(target),
                    "verification_commands": [
                        [sys.executable, "-c", "raise SystemExit(9)"]
                    ],
                },
            )
            source_before = git(source, "status", "--porcelain=v1")
            target_before = git(target, "status", "--porcelain=v1")

            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materializer(source),
                ),
            ):
                code, result, _ = run_cli("plan", plan_input)

            self.assertEqual(code, 1)
            self.assertEqual(result["error"]["code"], "CHECK_FAILED")
            self.assertFalse(candidate.exists())
            self.assertFalse(plan_path.exists())
            self.assertFalse(state_path.exists())
            self.assertEqual(git(source, "status", "--porcelain=v1"), source_before)
            self.assertEqual(git(target, "status", "--porcelain=v1"), target_before)
            self.assertEqual(list(scratch.iterdir()), [])

    def test_plan_apply_verify_and_separate_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            artifacts = base / "artifacts"
            artifacts.mkdir()
            recovery = base / "recovery"
            recovery.mkdir()
            state_path = base / "state.sqlite3"
            candidate = artifacts / "candidate"
            plan_path = artifacts / "plan.json"
            plan_input = write_json(
                artifacts / "plan-input.json",
                {
                    "candidate_output": str(candidate),
                    "manifest_path": str(source / "manifest.json"),
                    "plan_output": str(plan_path),
                    "scratch_root": str(scratch),
                    "source_root": str(source),
                    "target_id": target.name,
                    "target_root": str(target),
                    "verification_commands": [list(command) for command in checks()],
                },
            )
            with (
                mock.patch(
                    "downstream_deployer.planning.configured_qualification",
                    return_value=qualified(),
                ),
                mock.patch(
                    "downstream_deployer.planning._materialize_official",
                    side_effect=materializer(source),
                ),
            ):
                code, planned, raw = run_cli("plan", plan_input)
            self.assertEqual(code, 0)
            self.assertEqual(planned["result"]["status"], "planned")
            self.assertNotIn(str(base), raw)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            base_head = git(target, "rev-parse", "HEAD")

            store = AuthorityStore.initialize(state_path)
            binding = action_binding(target.name, plan)
            apply_id = digest("cli-apply", prefixed=True)
            commit_id = digest("cli-commit", prefixed=True)
            approve_one_off(
                store,
                authority_id=apply_id,
                effect="apply",
                binding=binding,
                prefix="cli:apply",
            )
            approve_one_off(
                store,
                authority_id=commit_id,
                effect="commit",
                binding=binding,
                prefix="cli:commit",
            )
            apply_input = write_json(
                artifacts / "apply-input.json",
                {
                    "authority": {
                        "authority_id": apply_id,
                        "binding": binding,
                        "event_id": "cli:apply:consume",
                        "kind": "one_off",
                    },
                    "candidate_root": str(candidate),
                    "commit_authority": {
                        "authority_id": commit_id,
                        "binding": binding,
                        "event_id": "cli:commit:consume",
                        "kind": "one_off",
                    },
                    "now": NOW,
                    "plan_path": str(plan_path),
                    "receipt_event_id": "cli:final:receipt",
                    "recovery_root": str(recovery),
                    "source_root": str(source),
                    "state_path": str(state_path),
                    "target_root": str(target),
                    "verification_commands": [list(command) for command in checks()],
                },
            )
            with mock.patch(
                "downstream_deployer.planning.configured_qualification",
                return_value=qualified(),
            ):
                code, applied, raw = run_cli("apply", apply_input)
            self.assertEqual(code, 0, applied)
            self.assertEqual(
                applied["result"]["transaction"]["status"],
                "succeeded",
            )
            self.assertNotIn(str(base), raw)
            final_receipt = applied["result"]["transaction"]["final_receipt"]
            receipt_path = write_json(
                artifacts / "final-receipt.json",
                final_receipt,
            )
            verify_input = write_json(
                artifacts / "verify-input.json",
                {
                    "plan_path": str(plan_path),
                    "receipt_path": str(receipt_path),
                    "source_root": str(source),
                    "target_root": str(target),
                    "verification_commands": [list(command) for command in checks()],
                },
            )
            with mock.patch(
                "downstream_deployer.planning.configured_qualification",
                return_value=qualified(),
            ):
                code, verified, raw = run_cli("verify", verify_input)
            self.assertEqual(code, 0)
            self.assertEqual(verified["result"]["status"], "verified")
            self.assertNotIn(str(base), raw)

            transaction_result = applied["result"]["transaction"]
            recovery_binding = action_binding(
                target.name,
                plan,
                recover=transaction_result,
            )
            recovery_id = digest("cli-recover", prefixed=True)
            approve_one_off(
                store,
                authority_id=recovery_id,
                effect="recover",
                binding=recovery_binding,
                prefix="cli:recover",
            )
            recover_input = write_json(
                artifacts / "recover-input.json",
                {
                    "authority": {
                        "authority_id": recovery_id,
                        "binding": recovery_binding,
                        "event_id": "cli:recover:consume",
                        "kind": "one_off",
                    },
                    "now": NOW,
                    "receipt_event_id": "cli:recovery:receipt",
                    "recovery_root": str(recovery),
                    "state_path": str(state_path),
                    "target_root": str(target),
                    "transaction_id": transaction_result["transaction_id"],
                },
            )
            code, recovered, raw = run_cli("recover", recover_input)
            self.assertEqual(code, 0)
            self.assertEqual(
                recovered["result"]["recovery"]["status"],
                "recovered",
            )
            self.assertEqual(git(target, "rev-parse", "HEAD"), base_head)
            self.assertEqual(git(target, "status", "--porcelain=v1"), "")
            self.assertNotIn(str(base), raw)

    def test_apply_rejects_state_inside_a_protected_root_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            candidate = base / "candidate"
            recovery = base / "recovery"
            for root in (source, target, candidate, recovery):
                root.mkdir()
            state_path = target / "state.sqlite3"
            plan_path = write_json(base / "plan.json", {})
            input_path = write_json(
                base / "apply.json",
                {
                    "authority": {
                        "authority_id": digest("apply", prefixed=True),
                        "binding": {},
                        "event_id": "apply:consume",
                        "kind": "one_off",
                    },
                    "candidate_root": str(candidate),
                    "now": NOW,
                    "plan_path": str(plan_path),
                    "receipt_event_id": "apply:receipt",
                    "recovery_root": str(recovery),
                    "source_root": str(source),
                    "state_path": str(state_path),
                    "target_root": str(target),
                    "verification_commands": [["true"]],
                },
            )

            code, result, raw = run_cli("apply", input_path)

            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "CLI_INPUT_INVALID")
            self.assertFalse(state_path.exists())
            self.assertNotIn(str(base), raw)

    def test_cycle_rejects_any_order_other_than_the_frozen_five(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            targets = [
                {
                    "binding": {},
                    "grant_id": f"grant:{target_id}",
                    "recovery_root": str(base / "recovery"),
                    "target_id": target_id,
                    "target_root": str(base / target_id),
                    "verification_commands": [["true"]],
                }
                for target_id in reversed(TARGET_ORDER)
            ]
            input_path = write_json(
                base / "cycle.json",
                {
                    "cycle_id": "cycle:test",
                    "manifest_path": str(base / "manifest.json"),
                    "now": NOW,
                    "scratch_root": str(base),
                    "source_root": str(base / "source"),
                    "state_path": str(base / "state.sqlite3"),
                    "targets": targets,
                },
            )
            code, result, raw = run_cli("cycle", input_path)
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "CLI_INPUT_INVALID")
            self.assertNotIn(str(base), raw)

    def test_cycle_source_identity_ignores_task_evidence_but_binds_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _target, _scratch = prepare_repositories(Path(tmp))
            with mock.patch(
                "downstream_deployer.planning.configured_qualification",
                return_value=qualified(),
            ):
                before = cli._source_identity(source, source / "manifest.json")
                active_task = source / ".trellis/tasks/active/task.json"
                active_task.parent.mkdir(parents=True)
                active_task.write_text(
                    '{"status":"in_progress"}\n',
                    encoding="utf-8",
                )
                (source / "BOARD.md").write_text(
                    "active task evidence\n",
                    encoding="utf-8",
                )
                with_task = cli._source_identity(source, source / "manifest.json")
                (source / "overlay/managed.txt").write_text(
                    "overlay:drift\n",
                    encoding="utf-8",
                )
                with_payload_drift = cli._source_identity(
                    source,
                    source / "manifest.json",
                )

            self.assertEqual(with_task, before)
            self.assertEqual(with_payload_drift["commit"], before["commit"])
            self.assertEqual(with_payload_drift["tree"], before["tree"])
            self.assertNotEqual(
                with_payload_drift["status_digest"],
                before["status_digest"],
            )

    def test_cycle_persists_invalid_source_before_target_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _target, scratch = prepare_repositories(base)
            state_path = base / "state.sqlite3"
            recovery = base / "recovery"
            recovery.mkdir()
            targets = [
                {
                    "binding": common_binding(target_id),
                    "grant_id": f"grant:{target_id}",
                    "recovery_root": str(recovery),
                    "target_id": target_id,
                    "target_root": str(base / target_id),
                    "verification_commands": [["true"]],
                }
                for target_id in TARGET_ORDER
            ]
            input_path = write_json(
                base / "cycle.json",
                {
                    "cycle_id": "cycle:source-blocked",
                    "manifest_path": str(source / "manifest.json"),
                    "now": NOW,
                    "scratch_root": str(scratch),
                    "source_root": str(source),
                    "state_path": str(state_path),
                    "targets": targets,
                },
            )

            code, result, raw = run_cli("cycle", input_path)

            self.assertEqual(code, 1)
            self.assertEqual(result["error"]["code"], "CYCLE_SOURCE_BLOCKED")
            self.assertNotIn(str(base), raw)
            store = AuthorityStore(state_path)
            cycle = store.snapshot()["cycles"]["cycle:source-blocked"]
            self.assertEqual(cycle["status"], "source_blocked")
            self.assertTrue(
                all(
                    slot["stable"] and slot["result"] == "source_blocked"
                    for slot in cycle["slots"]
                )
            )
            self.assertEqual(
                sum(
                    event["event_type"] == "cycle_source_blocked"
                    for event in store.events()
                ),
                1,
            )

    def test_cycle_composes_the_frozen_five_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state_path = base / "state.sqlite3"
            store = AuthorityStore.initialize(state_path)
            grant_ids = activate_grants(store, not_before=at(19))
            scratch = base / "scratch"
            scratch.mkdir()
            recovery = base / "recovery"
            recovery.mkdir()
            targets = [
                {
                    "binding": common_binding(target_id),
                    "grant_id": grant_id,
                    "recovery_root": str(recovery),
                    "target_id": target_id,
                    "target_root": str(base / target_id),
                    "verification_commands": [["true"]],
                }
                for target_id, grant_id in zip(
                    TARGET_ORDER,
                    grant_ids,
                    strict=True,
                )
            ]
            input_path = write_json(
                base / "cycle.json",
                {
                    "cycle_id": "cycle:ordered",
                    "manifest_path": str(base / "manifest.json"),
                    "now": "2026-07-19T00:00:00Z",
                    "scratch_root": str(scratch),
                    "source_root": str(base / "source"),
                    "state_path": str(state_path),
                    "targets": targets,
                },
            )
            planned: list[str] = []
            applied: list[str] = []

            def fake_plan(
                _source: Path,
                _target: Path,
                target_id: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                planned.append(target_id)
                return {
                    "candidate": {
                        "predicted_mutations": [
                            {
                                "change": "update",
                                "owner": "overlay",
                                "path": "managed.txt",
                            }
                        ]
                    },
                    "plan_digest": digest(f"plan:{target_id}"),
                    "source": {
                        "qualification_receipt_id": source_identity()[
                            "qualification_receipt_id"
                        ]
                    },
                    "target": {
                        "head": "b" * 40,
                        "id": target_id,
                    },
                }

            def fake_apply(
                _source: Path,
                target: Path,
                _candidate: Path,
                plan: dict[str, object],
                **_kwargs: object,
            ) -> dict[str, object]:
                target_id = str(plan["target"]["id"])
                applied.append(target_id)
                return {
                    "commit": None,
                    "final_receipt": {
                        "receipt_id": digest(
                            f"receipt:{target_id}",
                            prefixed=True,
                        )
                    },
                    "status": "succeeded",
                    "target_id": target.name,
                    "transaction_id": digest(
                        f"transaction:{target_id}",
                        prefixed=True,
                    ),
                }

            with (
                mock.patch(
                    "downstream_deployer.cli._source_identity",
                    return_value=source_identity(),
                ),
                mock.patch(
                    "downstream_deployer.cli.planning._repo_identity",
                    return_value={"head": "b" * 40},
                ),
                mock.patch(
                    "downstream_deployer.cli.plan_target",
                    side_effect=fake_plan,
                ),
                mock.patch(
                    "downstream_deployer.cli.apply_transaction",
                    side_effect=fake_apply,
                ),
            ):
                code, result, raw = run_cli("cycle", input_path)

            self.assertEqual(code, 0, result)
            self.assertEqual(result["result"]["status"], "completed")
            self.assertEqual(planned, list(TARGET_ORDER))
            self.assertEqual(applied, list(TARGET_ORDER))
            self.assertNotIn(str(base), raw)

    def test_cycle_never_reports_post_transaction_failure_as_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state_path = base / "state.sqlite3"
            store = AuthorityStore.initialize(state_path)
            grant_ids = activate_grants(store, not_before=at(19))
            scratch = base / "scratch"
            scratch.mkdir()
            recovery = base / "recovery"
            recovery.mkdir()
            targets = [
                {
                    "binding": common_binding(target_id),
                    "grant_id": grant_id,
                    "recovery_root": str(recovery),
                    "target_id": target_id,
                    "target_root": str(base / target_id),
                    "verification_commands": [["true"]],
                }
                for target_id, grant_id in zip(
                    TARGET_ORDER,
                    grant_ids,
                    strict=True,
                )
            ]
            input_path = write_json(
                base / "cycle.json",
                {
                    "cycle_id": "cycle:receipt-failure",
                    "manifest_path": str(base / "manifest.json"),
                    "now": "2026-07-19T00:00:00Z",
                    "scratch_root": str(scratch),
                    "source_root": str(base / "source"),
                    "state_path": str(state_path),
                    "targets": targets,
                },
            )

            def fake_plan(
                _source: Path,
                _target: Path,
                target_id: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                return {
                    "candidate": {
                        "predicted_mutations": [
                            {
                                "change": "update",
                                "owner": "overlay",
                                "path": "managed.txt",
                            }
                        ]
                    },
                    "plan_digest": digest(f"plan:{target_id}"),
                    "source": {
                        "qualification_receipt_id": source_identity()[
                            "qualification_receipt_id"
                        ]
                    },
                    "target": {"head": "b" * 40, "id": target_id},
                }

            with (
                mock.patch(
                    "downstream_deployer.cli._source_identity",
                    return_value=source_identity(),
                ),
                mock.patch(
                    "downstream_deployer.cli.planning._repo_identity",
                    return_value={"head": "b" * 40},
                ),
                mock.patch(
                    "downstream_deployer.cli.plan_target",
                    side_effect=fake_plan,
                ),
                mock.patch(
                    "downstream_deployer.cli.apply_transaction",
                    return_value={
                        "commit": None,
                        "final_receipt": {
                            "receipt_id": digest("receipt", prefixed=True)
                        },
                        "status": "succeeded",
                        "target_id": TARGET_ORDER[0],
                        "transaction_id": digest(
                            "transaction",
                            prefixed=True,
                        ),
                    },
                ),
                mock.patch(
                    "downstream_deployer.cli._record_result_receipt",
                    side_effect=RuntimeError("RECEIPT_WRITE_FAILED"),
                ),
            ):
                code, result, raw = run_cli("cycle", input_path)

            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "error")
            status_input = write_json(
                base / "status.json",
                {"state_path": str(state_path)},
            )
            status_code, status, _ = run_cli("status", status_input)
            self.assertEqual(status_code, 0)
            slots = status["result"]["cycles"]["cycle:receipt-failure"]["slots"]
            self.assertEqual(slots[0]["result"], "unknown_outcome")
            self.assertNotEqual(slots[0]["result"], "ineligible")
            self.assertTrue(
                all(slot["result"] == "blocked_by_previous" for slot in slots[1:])
            )
            self.assertNotIn(str(base), raw)


if __name__ == "__main__":
    unittest.main()
