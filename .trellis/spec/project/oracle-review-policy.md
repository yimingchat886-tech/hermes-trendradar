# Oracle Review Stop Policy

## Purpose

Oracle review is stopped repository-wide by explicit user instruction on
2026-07-12. This compatibility-named file prevents older workflow guidance from
reintroducing Oracle as a gate.

## Active Rule

- Do not invoke Oracle in API, browser, bridge, render-copy, or manual mode.
- Do not probe Oracle availability, retry Oracle, or wait on Oracle.
- Do not create new `oracle-review-budget.md` artifacts.
- Oracle unavailability must never block PRD, PLAN, ADR, implementation,
  blocker handling, integration, closeout, or archive.
- Apply this rule to the Unified Intent Loop and sealed legacy evidence.

## Replacement Review

| Task | Review behavior |
|---|---|
| T0/T1 | normal task verification; no independent reviewer required |
| ordinary T2 | local checklist or one independent local review when risk warrants it |
| high-risk T2 | two independent local reviews bound to the same artifact digest |
| T3/T4 | two independent local reviews for high-risk PRD, PLAN/ADR, and closeout checkpoints |

For two-review checkpoints:

- use separate read-only review passes;
- bind both verdicts to the same artifact SHA-256 digest;
- record every blocker and its disposition;
- invalidate both verdicts after a semantic revision;
- keep final acceptance dependent on relevant tests and evidence, not reviewer
  opinion alone.

## Historical Evidence

- Preserve existing Oracle transcripts, review reports, budgets, and verdicts as
  historical evidence.
- An existing Oracle-named artifact does not authorize a new Oracle call.
- Active task documents must label remaining Oracle checkpoints as superseded
  and use the replacement read-only Check Agent contract above.

## Resume Rule

Oracle remains stopped until the user explicitly requests resumption. A future
resume requires an intentional update to this policy and the affected workflow
artifacts; tool availability alone must not reactivate it.
