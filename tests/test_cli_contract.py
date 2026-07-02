from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.cli import EXIT_CONTRACT_MISMATCH, EXIT_OK, main


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def test_stub_json_contracts() -> None:
    commands = [
        ("validate-config", "--profile", "missing.yaml", "--json"),
        ("healthcheck", "--json"),
        ("run-daily", "--json"),
        ("apply-limited-live", "--json"),
    ]
    for args in commands:
        code, stdout, stderr = run_cli(*args)
        assert code == EXIT_OK
        assert stderr == ""
        payload = json.loads(stdout)
        assert payload["ok"] is True
        assert payload["mode"] == "stub"
        assert payload["error"] is None


def test_invalid_args_json_contract() -> None:
    code, stdout, stderr = run_cli("missing-command", "--json")
    assert code == EXIT_CONTRACT_MISMATCH
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "contract_mismatch"
    assert payload["exit_code"] == EXIT_CONTRACT_MISMATCH


if __name__ == "__main__":
    test_stub_json_contracts()
    test_invalid_args_json_contract()
