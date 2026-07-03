# FunASR Research

Source: https://github.com/modelscope/FunASR

## Findings

- FunASR is the upstream project requested for replacement. The official README describes it as an industrial speech recognition toolkit with ASR, timestamps, speaker diarization, emotion detection, streaming, and OpenAI-compatible API options.
- The documented quick start installs PyTorch/torchaudio first, then `funasr`. Source install is `git clone https://github.com/modelscope/FunASR.git && cd FunASR && pip install -e ./`.
- The agent-friendly CLI shape is `funasr audio.wav --output-format json`, with optional `--output-dir`, `--spk`, `--timestamps`, `--model`, and `--language`.
- Model names shown in the README include `sensevoice` as the default CLI model, plus `paraformer`, `paraformer-en`, and `fun-asr-nano`.
- For this repo, the smallest safe migration is to keep Hermes' transcript artifact/state flow and swap the external command/provider to FunASR rather than building a new ASR abstraction layer.

## Local Mapping

- External checkout path: `/home/jym/workspace/_external/FunASR`.
- External venv path: `/home/jym/workspace/_external/venvs/funasr`.
- External model/cache path: `/home/jym/workspace/_external/model-cache/funasr`.
- Hermes profile provider: `local-funasr`.
- Transcript record provider: `local_funasr`.
