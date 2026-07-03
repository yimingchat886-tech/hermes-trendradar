# Implementation Plan

1. Update `_external` to add FunASR checkout/config and remove current Whisper runtime assets while keeping `hermes-stock-runs/`.
2. Change repo transcript runtime from `local-whisper` to `local-funasr` with the smallest parser change for FunASR JSON/stdout output.
3. Update current profiles, runbooks, handoff wording, and tests.
4. Verify focused tests, self-checks, external workspace check, GitNexus detect changes, and `git diff --check`.
