# Manage local AI state versioning

## PM Intake

### 1. Original Request

Manage `.trellis/tasks/`, `.agents/`, `.codex/`, `.claude/`, and `CLAUDE.md` with version control.

### 2. Real Goal

Keep local AI workflow state and helper files versioned for rollback/history, while preventing those files from being pushed to GitHub with the main product repository.

### 3. Ambiguous Or Risky Wording

| Original | Issue | Proposed Rewrite |
|---|---|---|
| "进行版本管理" | Main Git tracking would make future GitHub pushes include these files. | Use a local-only versioning mechanism separate from the main repo remote. |
| `.trellis/tasks/` | Already partially tracked in the main repo despite `.gitignore`; tracked ignored files still show as dirty. | Remove these paths from the main repo index, keep them on disk, then track them in local-only state history. |
| `.agents/`, `.codex/`, `.claude/`, `CLAUDE.md` | These include agent/tooling state that should not be published. | Track in local-only state history, keep ignored or untracked by the main GitHub-facing repo. |

### 4. Optimized Requirement

Create a local-only versioning setup for `.trellis/tasks/`, `.agents/`, `.codex/`, `.claude/`, and `CLAUDE.md` that supports normal `git add`/`git commit` style snapshots without adding those paths to the main repository's publishable history.

### 5. Risk Level

T4: harness/tooling and Git boundary behavior.

### 6. Staged Overlay Needed

Yes. This changes local repository workflow and publish boundaries.

### 7. Oracle Review Budget Needed

No for MVP. The safe path uses Git itself and no new production dependencies.

### 8. User Confirmation Points

- [x] Confirm local-only Git repository approach before implementation.
- [x] Confirm whether existing main-repo tracked `.trellis/tasks/` entries should be removed from the main repo index.

Confirmed by user on 2026-06-30: adopt `.local-state.git` local-state repository and remove the requested paths from the main repo index while keeping files on disk.

## Goal

Version local AI/Trellis state with history and rollback, without pushing that state to GitHub.

## What I Already Know

- The main repo currently has no configured remote.
- `.gitignore` ignores `.agents/`, `.codex/`, and `.trellis/tasks/`.
- Some `.trellis/tasks/` files are already tracked by the main repo, so they still show as modified even though the path is ignored.
- `.claude/` and `CLAUDE.md` are currently untracked and not ignored.
- The user wants these paths version-managed but not pushed to GitHub.

## Requirements

- Track local versions of `.trellis/tasks/`, `.agents/`, `.codex/`, `.claude/`, and `CLAUDE.md`.
- Do not publish those paths through the main GitHub-facing repository.
- Remove requested paths from the main repo index when already tracked, while preserving the working-tree files.
- Do not add dependencies.
- Do not delete local state files.
- Keep the mechanism simple enough to operate from WSL with plain Git commands.

## Acceptance Criteria

- [ ] The chosen local-state mechanism can stage and commit the requested paths.
- [ ] The main repo ignores or removes these paths from publishable tracking as needed.
- [ ] Verification shows the main repo does not stage local-only state accidentally.
- [ ] Verification shows the local-state history can see and track the requested paths.
- [ ] A short usage note documents how to snapshot local AI state.

## Definition of Done

- Context and project specs are read.
- Implementation uses Git/platform behavior only; no new dependencies.
- Existing unrelated dirty files are not included in commits.
- Narrow verification commands are run and reported.

## Technical Approach

Recommended MVP: create a local-only Git repository using a separate git directory, such as `.local-state.git`, with the project root as work tree:

```bash
git --git-dir=.local-state.git --work-tree=. status
```

This keeps local AI state history separate from the main repo. The main repo can continue representing the publishable product history.

## Decision (ADR-lite)

Context: Git does not support "tracked locally but excluded only when pushing" inside one repository. Once a file is committed in the main repo, a normal branch push includes it.

Decision: Use a second local-only Git repository for private AI/Trellis state, and keep those files out of the main repo's publishable index.

Consequences: Local state gets real version history without GitHub exposure. Users must use explicit local-state commands for these paths. This avoids extra tooling at the cost of one extra Git command prefix.

## Out of Scope

- Pushing local AI state to any remote.
- Encrypting local state.
- Adding a new dependency or custom versioning tool.
- Rewriting unrelated task content.
- Cleaning unrelated dirty files.

## Technical Notes

- Read `.gitignore`, `.trellis/.gitignore`, and `.git/info/exclude`.
- Read `.trellis/spec/project/*` workflow specs for harness/tooling changes.
- `git ls-files -ci --exclude-standard` confirms several `.trellis/tasks/` files are tracked by the main repo while also matching ignore rules.
