from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upstream_release import wrapper


REPO_ROOT = Path(__file__).resolve().parents[3]


class WrapperTests(unittest.TestCase):
    def _run(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = wrapper.main(["--repo-root", str(REPO_ROOT), *arguments])
        output = stream.getvalue().strip()
        return code, json.loads(output), output

    def test_version_status_and_doctor_are_stable_and_redacted(self) -> None:
        version_code, version, version_raw = self._run("version")
        status_code, status, status_raw = self._run("status")
        doctor_code, doctor, doctor_raw = self._run("doctor")

        self.assertEqual(version_code, 0)
        self.assertEqual(status_code, 0)
        self.assertEqual(doctor_code, 0)
        self.assertEqual(
            version,
            {"schema_version": 1, "wrapper_version": wrapper.WRAPPER_VERSION},
        )
        self.assertEqual(status["status"], "source_only")
        self.assertEqual(doctor["checks"], {"shell_source": "ok", "templates": "ok"})
        self.assertEqual(status["wrapper_id"], doctor["wrapper_id"])
        for output in (version_raw, status_raw, doctor_raw):
            self.assertNotIn(str(REPO_ROOT), output)

    def test_render_stays_in_temp_and_never_activates_machine_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            result = wrapper.render_isolated_bundle(bundle, REPO_ROOT)

            self.assertTrue(bundle.is_dir())
            self.assertEqual(result["status"], "rendered_candidate")
            self.assertNotIn(str(REPO_ROOT), json.dumps(result, sort_keys=True))
            service = (bundle / "trellis-upstream-wrapper.service").read_text(
                encoding="utf-8"
            )
            timer = (bundle / "trellis-upstream-wrapper.timer").read_text(
                encoding="utf-8"
            )
            self.assertIn(" doctor", service)
            self.assertNotIn("systemctl", service + timer)

        with self.assertRaises(wrapper.WrapperError):
            wrapper.render_isolated_bundle(REPO_ROOT / "machine-wrapper", REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
