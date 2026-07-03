# gitignore development hygiene

## Goal

Make repository ignore rules match the private development workflow: Trellis task records and agent/tooling instructions should be trackable, while local runtime state and generated Python artifacts should stay out of Git.

## Requirements

- Do not ignore `.trellis/tasks/` as a whole.
- Do not ignore `.agents/`, `.codex/`, `.claude/`, or `CLAUDE.md` as a whole.
- Keep local-only Trellis state ignored, including `.trellis/workspace/`, runtime files, logs, artifacts, and temp files.
- Keep local profile and production profile overrides ignored while allowing sample/example profiles.
- Add Python project ignore rules for generated build, packaging, cache, coverage, and virtualenv artifacts.
- Keep the change scoped to `.gitignore` unless verification exposes a necessary related adjustment.

## Acceptance Criteria

- [x] New files under `.trellis/tasks/` are not ignored by root `.gitignore`.
- [x] New files under `.agents/`, `.codex/`, `.claude/`, and `CLAUDE.md` are not ignored by root `.gitignore`.
- [x] `.trellis/workspace/`, `.trellis/.runtime/`, logs, artifacts, temp files, and local profile overrides remain ignored.
- [x] Common Python generated outputs are ignored.
- [x] `git diff --check` passes.

## Notes

- Task size: T1/default Trellis. No staged overlay or Oracle review needed.
- Target file: `.gitignore`.
- No source code behavior changes.
