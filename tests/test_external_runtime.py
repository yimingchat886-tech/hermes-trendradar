from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.external_runtime import build_manifest, cleanup_run_temp, public_process_result, run_process, runtime_layout, smoke_account


def test_public_process_projection_keeps_internal_log_paths_private() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_root = root / "run"
        helper = root / "helper.py"
        helper.write_text("import sys\nprint(sys.argv[1])\nprint('stderr path', file=sys.stderr)\n", encoding="utf-8")
        input_path = root / "input.mp4"
        input_path.write_bytes(b"fixture")

        result = run_process("fixture", [sys.executable, str(helper), str(input_path)], mode="real", log_dir=run_root / "logs")
        visible = public_process_result(result, run_root=run_root)
        serialized = json.dumps(visible)
        stdout_path = result.get("stdout_path")
        stderr_path = result.get("stderr_path")

        assert isinstance(stdout_path, str)
        assert isinstance(stderr_path, str)
        assert Path(stdout_path).is_file()
        assert Path(stderr_path).is_file()
        assert visible["stdout_ref"] == "file:logs/fixture.stdout.log"
        assert visible["stderr_ref"] == "file:logs/fixture.stderr.log"
        assert str(root) not in serialized
        assert "/tmp/" not in serialized
        assert "/var/tmp/" not in serialized


def test_manifest_public_projection_uses_run_scoped_refs_without_temp_roots() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = runtime_layout(external_root=root / "_external", run_id="run-public-projection")
        run_temp = Path(layout["run_temp_root"])
        cleanup_target = run_temp / "raw-video.mp4"
        cleanup_target.parent.mkdir(parents=True, exist_ok=True)
        cleanup_target.write_bytes(b"raw video")
        removed = cleanup_run_temp(run_temp, [cleanup_target])
        account = smoke_account("douyin", "local_smoke_account", "https://fixture.invalid/user/local-smoke")
        process = run_process(
            "mediacrawler",
            ["python", "main.py", "--output", layout["run_temp_root"]],
            mode="dry_run",
        )

        manifest = build_manifest(
            mode="dry_run",
            account=account,
            layout={key: str(value) for key, value in layout.items()},
            mediacrawler=process,
            import_proof={"status": "importable"},
            funasr={"status": "blocked", "work_dir": layout["transcript_dir"]},
            cleanup={"removed": removed, "retained": [layout["transcript_dir"]]},
        )
        serialized = json.dumps(manifest)

        assert manifest["layout"]["run_root"] == "file:."
        assert manifest["layout"]["run_temp_root"] == "file:tmp"
        assert manifest["layout"]["log_dir"] == "file:logs"
        assert manifest["cleanup"]["removed"] == ["file:tmp/raw-video.mp4"]
        assert manifest["cleanup"]["retained"] == ["file:transcripts"]
        assert str(root) not in serialized
        assert "/tmp/" not in serialized
        assert "/var/tmp/" not in serialized
