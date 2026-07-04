# Implementation Plan: External Runtimes Adapter and Local Deployment Smoke Test

## Parent Task

- Parent: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Requirement IDs: P1-REQ-045, P1-REQ-060, P1-REQ-065, P1-REQ-100, P1-REQ-110

## Scope

- Merge the MediaCrawler runner and Whisper runtime work into the final child.
- Keep MediaCrawler and openai-whisper as external runtimes under `/home/jym/workspace/_external`.
- Add CLI/process adapter boundaries, manifests, logs, cleanup proof, and smoke checks in the main repo.
- Preserve dry-run/fake-command behavior before real external calls.
- Run the final local smoke test with temporary user-provided cookies.
- Gate real external execution on local-only smoke target inputs, strict path preflight, and cookie redaction.

## Expected Files

- CLI/process adapter modules or commands.
- Runbook/check for external runtime layout and venvs.
- Manifest/log/transcript evidence pattern.
- Cleanup check for temporary cookies, raw videos, and raw collection artifacts, while preserving external runtime installs, venvs, and model caches.
- Stage report after implementation.

## Ponytail Pass

- Blocking findings: queue, concurrency, daemon/service mode, committed cookies, vendored external code, or long-term video storage exceed this child.
- Advisory findings: use `subprocess.run([...], shell=False)` for local CLI/process calls; avoid a service wrapper until repeated runs prove it is needed.
- Decision: process adapters first; queue/concurrency/daemon is future work.

## Oracle

- Required: completed before implementation.
- Reason: this child touches external installs, temporary cookies, real collection, and GPU/runtime behavior.
- Decision: PLAN may proceed after adding strict path/cookie/preflight, import-proof, and cleanup constraints.

## Steps

1. Reuse child 2 enabled account tracking output.
2. Preserve dry-run/fake-command command planning.
3. Add `/home/jym/workspace/_external` runtime layout/runbook and venv checks.
4. Add path preflight that rejects repo-contained external runtimes, unsafe temp roots, unsafe cleanup targets, tracked cookies, unignored repo cookies, and `shell=True`.
5. Add MediaCrawler CLI/process adapter with redacted command logging and run-specific raw output.
6. Normalize MediaCrawler raw artifacts into child 3 row shape and prove importability through `import_mediacrawler_rows(rows, accounts=[smoke_account])`.
7. Add openai-whisper CLI/process adapter with GPU detection, device selection, CPU fallback, and explicit `done` / `fallback_done` / `blocked` / `failed` status.
8. Copy user-provided media into run temp before cleanup-capable transcript code touches it.
9. Retain transcripts/logs plus external runtime installs, venvs, and model caches; delete only run-specific test artifacts.
10. Add one focused self-check for manifest shape, redaction, import proof, fallback semantics, and cleanup evidence.

## Verification

- Command: adapter self-check, external runtime smoke commands when local inputs exist, cleanup check, and `git diff --check`.
- Expected result: dry-run/fake mode works, unsafe real execution is rejected, real MediaCrawler artifact is importable when credentials are supplied, openai-whisper transcript or fallback is recorded, transcripts/logs remain as evidence, external runtime/cache remains reusable outside the repo, and run-specific test artifacts are cleaned.

## Parent Acceptance Note

This final child provides the local deployment evidence for MediaCrawler plus openai-whisper. Parent closeout should reference this child evidence rather than running a separate parent-only real-call test.

## Rollback

- Remove adapter code, fixtures, manifests, and checks.
- Do not uninstall external runtimes or delete external venvs/model caches unless explicitly requested.

## Confirmation Gate

- [x] User confirmed this PLAN
- [x] Oracle high-risk PLAN review completed
- [x] `task.json.meta.staged_delivery.plan_confirmed = true`
