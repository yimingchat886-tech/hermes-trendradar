# Stage Report: sync trellis harness

## Acceptance

- [x] Synced reusable Trellis harness files from `/home/jym/workspace/trellis harness`.
- [x] Preserved `.trellis/tasks/`, `.trellis/workspace/`, `.trellis/.runtime/`, `.trellis/.developer`, and ignored local Claude settings.
- [x] Kept Hermes-specific project specs by avoiding `--delete` for `.trellis/spec/guides/` and `.trellis/spec/project/`.
- [x] Updated local tests for the synced done-gate and impact-gate behavior.
- [x] Updated `~/.local/bin/update-ai-tooling` to include tracked `.claude` hooks/settings in future harness syncs.

## Verification

- `python3 -m compileall -q .trellis/scripts` passed.
- `TMPDIR=/tmp python3 -m pytest tests/trellis .trellis/scripts/tests` passed: 52 tests.
- `python3 ./.trellis/scripts/get_context.py --mode packages` passed: single-repo project context.
- `python3 ./.trellis/scripts/task.py current --source` passed: current task remains `.trellis/tasks/07-06-sync-trellis-harness`.
- `bash -n ~/.local/bin/update-ai-tooling` passed.
- `git diff --check` passed.
- `node .gitnexus/run.cjs detect-changes --repo hermes-trendradar --scope unstaged` passed with low risk and 0 affected processes.
- `node .gitnexus/run.cjs detect-changes --repo hermes-trendradar --scope compare --base-ref main` ran, but includes earlier branch history and reported critical risk across 229 files, so it is not representative of only this task.
- Ponytail complexity review completed: canonical sync plus small test/updater compatibility edits; no local abstraction, dependency, or dead wrapper to cut.
