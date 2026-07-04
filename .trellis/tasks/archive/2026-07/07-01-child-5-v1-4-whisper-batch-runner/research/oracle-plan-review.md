# Oracle PLAN Review

## Session

- Oracle session: `child5-whisper-batch-plan-review-3`
- Result: BLOCK

## Required Changes Before Coding

- Make stubbed tests control-flow-only, not completion evidence.
- Require one attempted invocation of the resolved configured local Whisper command/model/device.
- Define report evidence: resolved command, resolved model, resolved device, content_id attempted, artifact ref/hash or stable error code, temp video cleanup proof, and SQLite state rows matching summary.
- Define runtime config source, artifact/hash/state contract, deterministic error taxonomy, and queue/counting semantics.

## Applied PLAN Changes

- Added runtime config contract to `implement.md`.
- Added queue/counting semantics to `implement.md`.
- Added artifact/state contract to `implement.md`.
- Added deterministic error taxonomy to `implement.md`.
- Added stubbed-test caveat and deterministic blocker rule to `prd.md` / `implement.md`.

## Second Review

- Oracle session: `child5-whisper-batch-plan-review-4`
- Result: BLOCK

## Second Review Required Changes

- Resolve missing-video contradiction: count missing/unusable video as `failed`, not `skipped`.
- Bind every deterministic error code to count/state/error-row behavior.
- Clarify run-level config blocker behavior.
- Strengthen stage-report evidence with attempted `content_id`, invocation argv, and SQLite rows.
- Define artifact failure atomicity.

## Second Review Applied Changes

- Missing/unusable video now counts as `failed`.
- `skipped` is reserved only for future intentional pre-filter exclusions.
- Added error-code outcome table.
- Added run-level blocker rule.
- Added required stage-report evidence.
- Added artifact failure atomicity rule.

## Third Review

- Oracle session: `child5-whisper-batch-plan-review-5`
- Result: BLOCK

## Third Review Required Change

- Remove the remaining contradiction where `failed` implied only queued attempted items even though missing video is a pre-queue failure.

## Third Review Applied Change

- Replaced the `failed` definition with explicit pre-queue and queued-attempt failure paths.
- Added the queued/failed summary invariant.

## Final Review

- Oracle session: `child5-whisper-batch-plan-review-6`
- Result: APPROVE
