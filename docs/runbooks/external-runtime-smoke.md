# External Runtime Smoke Runbook

## Scope

Child 8 keeps MediaCrawler and openai-whisper outside this repo.
The repo only stores adapters, manifests, logs, transcripts, runbooks, and checks.

## External Layout

Use `/home/jym/workspace/_external`:

- `MediaCrawler/` for the external MediaCrawler checkout.
- `venvs/mediacrawler/` for MediaCrawler dependencies.
- `venvs/openai-whisper/` for openai-whisper dependencies.
- `model-cache/openai-whisper/` for reusable Whisper model cache.
- `hermes-stock-runs/<run_id>/` for run-specific logs, transcripts, and temp files.

## Real Smoke Inputs

Provide these locally only:

- one verified target account/handle;
- one public video/audio sample;
- temporary platform cookies via ignored file or environment variable.

Never commit cookies, login state, proxy settings, raw videos, external source trees, venvs, or model caches.

## Safety Gates

Before real execution:

- run `python3 -m hermes_benchmark.external_runtime`;
- reject `shell=True`;
- reject external runtime paths inside this repo;
- reject cleanup targets outside the run temp directory;
- reject tracked or unignored cookie files inside this repo;
- copy user-provided media into run temp before any cleanup-capable code touches it.

## Evidence

Retain:

- redacted manifest;
- logs;
- transcript or explicit Whisper blocker/fallback;
- MediaCrawler import proof.

Delete:

- raw videos;
- temporary cookies;
- temporary raw collection artifacts;
- other run-specific test inputs.

## Real Execution Boundary

One account, one public video, one MediaCrawler run, one import proof,
and one Whisper run or fallback/blocker.
No queue, concurrency, daemon, scheduler, or long-term video storage.
