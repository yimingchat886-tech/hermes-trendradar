# Stage Report

## Summary

- Replaced the current production-test transcription runtime from `local-whisper` to `local-funasr`.
- Updated `_external` with a FunASR checkout, uv lock, venv, model cache root, manifest, README, AGENTS, and manage script.
- Removed old Whisper runtime assets from `_external`: `uv/openai-whisper`, `venvs/openai-whisper`, `model-cache/openai-whisper`, and `model_cache/whisper`.
- Preserved `/home/jym/workspace/_external/hermes-stock-runs/` with 17 retained run directories after adding one FunASR smoke run.
- Updated current repo code, tests, profiles, runbooks, v1.4 docs, and Feishu dry-run field text.

## External Runtime Evidence

- FunASR checkout: `/home/jym/workspace/_external/FunASR`
- FunASR commit: `f9937385517cccaa8cd780b61c8b404c701c1d44`
- FunASR venv: `/home/jym/workspace/_external/venvs/funasr`
- FunASR package: `1.3.14`
- Torch runtime: `2.12.1+cu130`
- CUDA device: `NVIDIA GeForce RTX 4070 SUPER`
- FunASR GPU smoke run: `/home/jym/workspace/_external/hermes-stock-runs/run-funasr-gpu-smoke-20260703T071044`
- FunASR GPU smoke output: `transcripts/gpu-smoke.json`

## Verification

- `python3 tests/test_transcript_batch.py` passed.
- `python3 tests/test_state.py` passed.
- `python3 tests/test_runtime_cdp.py` passed.
- `python3 -m pytest -s tests/test_transcript_batch.py tests/test_state.py tests/test_runtime_cdp.py` passed: 16 tests.
- `python3 -m hermes_benchmark.transcript_pipeline && python3 -m hermes_benchmark.transcript_batch && python3 -m hermes_benchmark.external_runtime` passed.
- `python3 -m hermes_benchmark.feishu_dry_run && python3 -m hermes_benchmark.daily_digest` passed.
- `python3 -m compileall -q hermes_benchmark` passed.
- `bash /home/jym/workspace/_external/scripts/manage.sh check` passed.
- `bash /home/jym/workspace/_external/scripts/manage.sh gpu-status` passed.
- `bash /home/jym/workspace/_external/scripts/manage.sh gpu-smoke` passed.
- `git diff --check` passed.
- `gitnexus detect-changes -r "Hermes stock"` completed; risk reported `critical` because the diff spans current docs plus runtime symbols.

## User Completion Signal

- Raw signal: `提交git并归档`
- Received: 2026-07-03
- Commit allowed: yes
- Archive allowed: yes
- Push allowed: no

## Notes

- Default `pytest` capture failed in this environment with `FileNotFoundError` during collection; the same test set passed with `-s`.
- Oracle API failed because `OPENAI_API_KEY` is missing. Oracle browser mode failed because attachments did not finish uploading before timeout.
- Remaining `whisper` mentions are historical evidence text or digest backward-compatible classification.
- No push was performed.
