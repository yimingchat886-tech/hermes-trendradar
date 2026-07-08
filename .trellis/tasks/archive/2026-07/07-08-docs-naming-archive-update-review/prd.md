# Review docs naming archive and update status

## PM Intake

### 1. Original Request

Create a task to review docs folder file naming, file archiving/placement, and
file update status. First list the current docs files.

### 2. Real Goal

Establish a current inventory of `docs/`, then use it to decide which documents
are canonical, stale, duplicated, misnamed, or better moved to an archive area.

### 3. Ambiguous Or Risky Wording

| Original | Issue | Proposed Rewrite |
|---|---|---|
| 文件归档 | Could mean analysis only, or actual file moves/deletes. | First audit and recommend archive candidates; do not move/delete files until explicitly approved. |
| 文件更新情况 | Could mean filesystem modified time, Git last-update commit, or content freshness. | Record both current filesystem modified time and latest Git commit date/hash, then flag documents whose content may need review. |

### 4. Optimized Requirement

Audit and clean up the current `docs/` tree: add a docs entry point, keep the
current PRD surface visible, physically archive historical PRDs, rename files
that violate the accepted convention, update stale Hermes docs, and transfer
Trellis workflow material to the canonical `trellis harness` repo instead of
keeping it in Hermes stock.

### 5. Risk Level

T2. The implementation is docs-only, but it may touch multiple paths and one
adjacent repo (`/home/jym/workspace/trellis harness`) for Trellis workflow
material.

### 6. Staged Overlay Needed

No. This is ordinary docs governance work, not parent/child delivery.

### 7. Oracle Review Budget Needed

No for the inventory pass. Consider a review only if the later proposal moves
canonical PRD material or changes active release docs.

### 8. User Confirmation Points

- [x] Add `docs/README.md`.
- [x] Keep the current PRD surface.
- [x] Physically archive historical PRDs.
- [x] Apply the new naming convention and list before/after path changes.
- [x] Do not lowercase `docs/PRD/`.
- [x] Move Trellis workflow material to the `trellis harness` repo.
- [x] Update stale Hermes status docs.
- [x] Fix heading noise in workflow/triage docs.

## What will change?

- Add `docs/README.md` as the Hermes docs entry point.
- Keep the current PRD surface visible:
  `docs/PRD/PRD_MASTER.md`, `docs/PRD/releases/PRD_v2.0.md`,
  `docs/PRD/trending-system.md`, and
  `docs/PRD/_ledger/trending-twitter-source.md`.
- Physically archive historical v1.3/v1.4 PRDs and update links in the same
  pass.
- Rename docs that violate the accepted convention and include a before/after
  comparison.
- Transfer Trellis workflow docs to `/home/jym/workspace/trellis harness`
  instead of treating them as Hermes stock product docs.
- Update stale Hermes status docs:
  `docs/runbooks/production-deployment-handoff.md` and
  `docs/reports/repo-completion-gap-report.md`.

## Why now?

The repository has multiple PRD releases, runbooks, and workflow reports under
`docs/`. A small inventory first keeps later cleanup from mixing canonical
documents with historical reports.

## How will it be verified?

- `docs/README.md` links every remaining Hermes-owned docs category.
- `PRD_MASTER.md` links resolve after archival path changes.
- The before/after rename table accounts for every moved or renamed file.
- Trellis workflow docs are present in the `trellis harness` repo before they
  are removed from Hermes stock.
- `git diff --check` passes in every repo touched.

## Current Docs Inventory

Snapshot captured on 2026-07-08.

- Directories: 6 total (`docs`, `docs/PRD`, `docs/PRD/_ledger`,
  `docs/PRD/releases`, `docs/reports`, `docs/runbooks`).
- Files: 17 total; all 17 are tracked by Git.
- Current `git status --short docs`: clean.
- No `docs/archive/` directory currently exists.

| Path | Size bytes | FS modified | Latest Git update |
|---|---:|---|---|
| `docs/PRD/PRD_MASTER.md` | 12457 | 2026-07-05 22:38 | 2026-07-05 `8fcdce0` |
| `docs/PRD/_ledger/trending-twitter-source.md` | 10102 | 2026-07-04 22:03 | 2026-07-04 `a99d90f` |
| `docs/PRD/releases/PRD_v1.3.md` | 77521 | 2026-07-01 10:37 | 2026-07-01 `acef576` |
| `docs/PRD/releases/PRD_v1.4_Codex_CLI_Implementation.md` | 21901 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/PRD/releases/PRD_v1.4_Hermes_Runtime_and_Profiles.md` | 19881 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/PRD/releases/PRD_v1.4_split_index.md` | 4728 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/PRD/releases/PRD_v2.0.md` | 4775 | 2026-07-05 22:38 | 2026-07-05 `8fcdce0` |
| `docs/PRD/trending-system.md` | 6244 | 2026-07-04 22:04 | 2026-07-04 `a99d90f` |
| `docs/reports/repo-completion-gap-report.md` | 10206 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/reports/trellis-full-development-workflow-report.md` | 14760 | 2026-07-04 13:58 | 2026-07-04 `354a618` |
| `docs/reports/trellis-loop-workflow-v2.md` | 12130 | 2026-07-04 13:58 | 2026-07-04 `354a618` |
| `docs/reports/trellis-triage-2026-07-04.md` | 7299 | 2026-07-04 13:58 | 2026-07-04 `354a618` |
| `docs/reports/workflow-v2-m2-m6-plan.md` | 18735 | 2026-07-04 14:54 | 2026-07-04 `981249f` |
| `docs/runbooks/external-runtime-smoke.md` | 1727 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/runbooks/hermes-skill-invocation.md` | 4367 | 2026-07-05 22:00 | 2026-07-05 `3209700` |
| `docs/runbooks/production-deployment-handoff.md` | 7645 | 2026-07-03 07:00 | 2026-07-03 `a3ef21e` |
| `docs/runbooks/workflow-v2-operator-guide.md` | 10177 | 2026-07-04 23:17 | 2026-07-04 `adbf0a7` |

## Recommendation List

### Accepted decisions from 2026-07-08

1. Add `docs/README.md`.
2. Keep the current PRD surface:
   `PRD_MASTER.md`, `PRD_v2.0.md`, `trending-system.md`, and
   `_ledger/trending-twitter-source.md`.
3. Physically archive historical PRDs instead of only marking them.
4. Apply the new naming convention and include a before/after path table.
5. Do not lowercase `docs/PRD/`.
6. Treat workflow/Trellis docs as belonging to the canonical
   `/home/jym/workspace/trellis harness` repo; they were temporary here and
   should move out of Hermes stock.
7. Update the stale production handoff and gap report.
8. Fix the heading-noise issue in transferred or retained workflow docs.

### Implemented before/after path table

| Current path | Proposed path | Action |
|---|---|---|
| `(new)` | `docs/README.md` | Create Hermes docs index. |
| `docs/PRD/PRD_MASTER.md` | `docs/PRD/PRD_MASTER.md` | Keep; update links to archived historical PRDs. |
| `docs/PRD/releases/PRD_v2.0.md` | `docs/PRD/releases/PRD_v2.0.md` | Keep current release PRD. |
| `docs/PRD/trending-system.md` | `docs/PRD/trending-system.md` | Keep current v2.1 module PRD. |
| `docs/PRD/_ledger/trending-twitter-source.md` | `docs/PRD/_ledger/trending-twitter-source.md` | Keep current ledger. |
| `docs/PRD/releases/PRD_v1.3.md` | `docs/PRD/releases/archive/PRD_v1.3.md` | Archive historical release PRD. |
| `docs/PRD/releases/PRD_v1.4_split_index.md` | `docs/PRD/releases/archive/PRD_v1.4_split-index.md` | Archive and normalize topic suffix. |
| `docs/PRD/releases/PRD_v1.4_Codex_CLI_Implementation.md` | `docs/PRD/releases/archive/PRD_v1.4_codex-cli-implementation.md` | Archive and normalize topic suffix. |
| `docs/PRD/releases/PRD_v1.4_Hermes_Runtime_and_Profiles.md` | `docs/PRD/releases/archive/PRD_v1.4_hermes-runtime-and-profiles.md` | Archive and normalize topic suffix. |
| `docs/reports/repo-completion-gap-report.md` | `docs/reports/repo-completion-gap-report.md` | Keep path; update content to current v2.0 state. |
| `docs/runbooks/production-deployment-handoff.md` | `docs/runbooks/production-deployment-handoff.md` | Keep path; update content to current v2.0 state. |
| `docs/runbooks/external-runtime-smoke.md` | `docs/runbooks/external-runtime-smoke.md` | Keep Hermes-owned runbook. |
| `docs/runbooks/hermes-skill-invocation.md` | `docs/runbooks/hermes-skill-invocation.md` | Keep Hermes-owned runbook. |
| `docs/reports/trellis-full-development-workflow-report.md` | `/home/jym/workspace/trellis harness/docs/reports/trellis-full-development-workflow-report.md` | Transfer to canonical Trellis harness repo, then remove from Hermes stock. |
| `docs/reports/trellis-loop-workflow-v2.md` | `/home/jym/workspace/trellis harness/docs/reports/trellis-loop-workflow-v2.md` | Transfer to canonical Trellis harness repo, then remove from Hermes stock. |
| `docs/reports/trellis-triage-2026-07-04.md` | `/home/jym/workspace/trellis harness/docs/reports/trellis-triage-2026-07-04.md` | Transfer to canonical Trellis harness repo, then remove from Hermes stock. |
| `docs/reports/workflow-v2-m2-m6-plan.md` | `/home/jym/workspace/trellis harness/docs/reports/workflow-v2-m2-m6-plan.md` | Transfer to canonical Trellis harness repo, then remove from Hermes stock. |
| `docs/runbooks/workflow-v2-operator-guide.md` | `/home/jym/workspace/trellis harness/docs/runbooks/workflow-v2-operator-guide.md` | Transfer to canonical Trellis harness repo, then remove from Hermes stock. |

## Requirements

- Add a docs index at `docs/README.md`.
- Keep the accepted current PRD surface unchanged except for link updates.
- Archive historical v1.3/v1.4 release PRDs under `docs/PRD/releases/archive/`.
- Normalize renamed filenames according to the accepted convention.
- Transfer Trellis workflow docs to `/home/jym/workspace/trellis harness`.
- Update Hermes-owned stale docs in place.
- Fix heading noise where expected command output is currently top-level
  Markdown headings.

## Acceptance Criteria

- [ ] Every current `docs/` file appears in the inventory.
- [ ] Findings separate naming issues, placement/archive candidates, and update
      recency issues.
- [ ] `docs/README.md` exists and points to the remaining Hermes docs.
- [ ] Historical PRD archive moves are reflected in `PRD_MASTER.md`.
- [ ] Before/after path table is updated if implementation chooses a different
      exact destination after inspecting `trellis harness`.
- [ ] Trellis workflow docs are transferred to the canonical repo before they
      are removed from Hermes stock.
- [ ] `production-deployment-handoff.md` and `repo-completion-gap-report.md`
      are updated for current v2.0 state.

## Out of Scope

- Source code, tests, config, lockfiles, schemas, or build files.
- Git commit, archive, or push without explicit user approval.
- Lowercasing `docs/PRD/`.

## Evidence Model

- `task.json.meta.workflow_mode` is `harness_state_machine`.
- `prd.md` records scope and verification.
- `stage-report.md` records acceptance and verification evidence.
- Do not add legacy staged metadata.

## Protocol Gates

Use `.trellis/spec/project/protocol-phrases.md` for completion, commit,
archive, limit, and push wording. Do not copy the phrase table here.
