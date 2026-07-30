from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from downstream_deployer import cli
from test_downstream_deployer_plan import git, prepare_repositories, qualified
from test_downstream_deployer_transaction import checks, materializer


def write_json(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_cli(input_path: Path) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(["single-target", "--input", str(input_path)])
    return code, json.loads(output.getvalue())


class SingleTargetTests(unittest.TestCase):
    def test_single_target_plan_is_source_and_target_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target, scratch = prepare_repositories(base)
            artifacts = base / "artifacts"
            artifacts.mkdir()
            candidate = artifacts / "candidate"
            plan_path = artifacts / "plan.json"
            state_path = artifacts / "state.sqlite3"
            request = {
                "candidate_output": str(candidate),
                "manifest_path": str(source / "manifest.json"),
                "plan_output": str(plan_path),
                "scratch_root": str(scratch),
                "source_root": str(source),
                "target_id": target.name,
                "target_root": str(target),
                "verification_commands": [list(command) for command in checks()],
            }
            input_path = write_json(
                artifacts / "single-target.json",
                {"operation": "plan", "request": request},
            )
            source_before = {
                "head": git(source, "rev-parse", "HEAD"),
                "status": git(source, "status", "--porcelain=v1"),
            }
            target_before = {
                "head": git(target, "rev-parse", "HEAD"),
                "status": git(target, "status", "--porcelain=v1"),
            }

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
                code, result = run_cli(input_path)

            self.assertEqual(code, 0, result)
            self.assertEqual(result["command"], "single-target")
            self.assertEqual(result["result"]["operation"], "plan")
            self.assertEqual(result["result"]["result"]["status"], "planned")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertTrue(plan["read_only"]["verified"])
            self.assertTrue(candidate.is_dir())
            self.assertFalse(state_path.exists())
            self.assertEqual(git(source, "rev-parse", "HEAD"), source_before["head"])
            self.assertEqual(git(source, "status", "--porcelain=v1"), source_before["status"])
            self.assertEqual(git(target, "rev-parse", "HEAD"), target_before["head"])
            self.assertEqual(git(target, "status", "--porcelain=v1"), target_before["status"])

    def test_single_target_rejects_fleet_and_malformed_envelopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, payload in {
                "fleet": {"operation": "cycle", "request": {}},
                "malformed": {"operation": "plan", "request": [], "extra": True},
                "non-string": {"operation": [], "request": {}},
            }.items():
                with self.subTest(name=name):
                    input_path = write_json(base / f"{name}.json", payload)
                    code, result = run_cli(input_path)
                    self.assertEqual(code, 2)
                    self.assertEqual(result["error"]["code"], "CLI_INPUT_INVALID")
            self.assertEqual(list(base.glob("*.sqlite3")), [])

    def test_single_target_delegates_each_exact_core_operation(self) -> None:
        handlers = {
            "plan": "_plan",
            "apply": "_apply",
            "verify": "_verify",
            "recover": "_recover",
        }
        for operation, handler_name in handlers.items():
            with self.subTest(operation=operation), mock.patch.object(
                cli,
                handler_name,
                return_value=({"status": f"{operation}-result"}, 0),
            ) as handler:
                request = {"exact": operation}
                result, exit_code = cli._dispatch(
                    "single-target",
                    {"operation": operation, "request": request},
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(result["operation"], operation)
            self.assertEqual(result["result"], {"status": f"{operation}-result"})
            handler.assert_called_once_with(request)
