from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / ".codex" / "hooks" / "inject-subagent-context.py"


class CodexHookEncodingTests(unittest.TestCase):
    def test_subagent_hook_reads_utf8_under_ascii_process_locale(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "LC_ALL": "C",
                "PYTHONCOERCECLOCALE": "0",
                "PYTHONUTF8": "0",
            }
        )
        payload = json.dumps(
            {"hook_event_name": "PreToolUse", "message": "\u4e2d\u6587"},
            ensure_ascii=False,
        ).encode("utf-8")

        result = subprocess.run(
            [sys.executable, str(HOOK)],
            cwd=REPO,
            input=payload,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
