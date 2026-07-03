# External Runtime ASR

## Scenario: Local FunASR Transcription Runtime

### 1. Scope / Trigger

- Trigger: change local ASR transcription runtime, provider names, CLI arguments, model/cache locations, GPU smoke commands, or transcript artifact parsing.
- Scope: Hermes repo owns adapters, profiles, checks, and docs only; `_external` owns third-party checkouts, venvs, model cache, temp media, and smoke run outputs.

### 2. Signatures

- Provider in transcription profile: `local-funasr`.
- Transcript record provider in contract fixtures: `local_funasr`.
- Command shape:
  ```bash
  funasr <audio-or-video> --model <model> --device <device> --output-dir <dir> --output-format json
  ```
- Local management commands:
  ```bash
  bash /home/jym/workspace/_external/scripts/manage.sh check
  bash /home/jym/workspace/_external/scripts/manage.sh gpu-status
  bash /home/jym/workspace/_external/scripts/manage.sh gpu-smoke
  ```

### 3. Contracts

- External checkout: `/home/jym/workspace/_external/FunASR`.
- External venv: `/home/jym/workspace/_external/venvs/funasr`.
- Model/cache root: `/home/jym/workspace/_external/model-cache/funasr`.
- Required cache env keys:
  - `MODELSCOPE_CACHE=/home/jym/workspace/_external/model-cache/funasr/modelscope`
  - `HF_HOME=/home/jym/workspace/_external/model-cache/funasr/huggingface`
- Transcript batch artifacts stay under the configured artifact root as `transcripts/<run_id>/...`.
- Run evidence stays under `/home/jym/workspace/_external/hermes-stock-runs/`.

### 4. Validation & Error Matrix

- Missing `provider`, `command`, `model`, `device`, or `storage` -> `transcription_config_missing`.
- Provider other than `local-funasr` -> config error naming `provider=local-funasr`.
- Missing/unreadable temp video -> `transcription_video_missing` or `transcription_video_unreadable`.
- Non-zero FunASR process -> `transcription_command_failed`.
- Missing transcript JSON and stdout text -> `transcription_command_failed`.

### 5. Good/Base/Bad Cases

- Good: `device=cuda` with `torch.cuda.is_available() == True`; output JSON exists under transcript output dir.
- Base: CPU fallback may be used manually for diagnosis, but production-test profile should keep `device=cuda` while this machine has CUDA.
- Bad: storing FunASR checkout, venv, model cache, temp media, raw videos, or smoke outputs inside the Hermes repo.

### 6. Tests Required

- `python3 tests/test_transcript_batch.py` or `python3 -m pytest -s tests/test_transcript_batch.py`.
- `python3 -m hermes_benchmark.transcript_batch`.
- `python3 -m hermes_benchmark.external_runtime`.
- `_external` checks:
  ```bash
  bash /home/jym/workspace/_external/scripts/manage.sh check
  bash /home/jym/workspace/_external/scripts/manage.sh gpu-status
  ```
- Run `gpu-smoke` when validating a new GPU/runtime install, model cache, or first migration from another ASR runtime.

### 7. Wrong vs Correct

#### Wrong

```json
{
  "provider": "local-whisper",
  "command": ["/home/jym/workspace/_external/venvs/openai-whisper/bin/whisper"]
}
```

#### Correct

```json
{
  "provider": "local-funasr",
  "command": [
    "/usr/bin/env",
    "MODELSCOPE_CACHE=/home/jym/workspace/_external/model-cache/funasr/modelscope",
    "HF_HOME=/home/jym/workspace/_external/model-cache/funasr/huggingface",
    "/home/jym/workspace/_external/venvs/funasr/bin/funasr"
  ],
  "model": "sensevoice",
  "device": "cuda"
}
```
