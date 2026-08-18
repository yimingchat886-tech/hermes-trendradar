# Stage Report: stabilize gitnexus generated guidance

## Acceptance

- [x] Repo guidance tells agents to use index-only GitNexus refresh by default.
- [x] GitNexus skill docs no longer recommend plain `analyze` for routine stale-index recovery.
- [x] Guidance identifies plain/full `analyze` as an explicit metadata refresh, not the normal path.
- [x] `git diff --check` passes.

## Verification

- `git diff --check -- AGENTS.md CLAUDE.md .claude/skills/gitnexus .trellis/spec/guides/project-development.md`: pass
- `node .gitnexus/run.cjs analyze --help | rg -- '--index-only|--skip-agents-md|--skip-skills|--no-stats|--name'`: pass
- `rg -n 'run\.cjs analyze(?! --index-only)|npx gitnexus analyze(?! --index-only)' AGENTS.md CLAUDE.md .claude/skills/gitnexus .trellis/spec/guides/project-development.md -S --pcre2`: pass, no matches
- `git diff --no-index --check /dev/null <task-file>` for this task's PRD/report/jsonl files: pass
- `git diff --cached --check`: pass
- `node .gitnexus/run.cjs detect-changes --scope staged --repo hermes-trendradar`: pass, low risk, affected processes 0
- `python3 ./.trellis/scripts/task.py validate .trellis/tasks/07-05-stabilize-gitnexus-generated-guidance`: pass after restoring the light-task PRD headings
- Ponytail review: pass; skipped wrapper/hook/config because GitNexus already provides `--index-only`.

## Changed Files

- `AGENTS.md`
- `CLAUDE.md`
- `.claude/skills/gitnexus/gitnexus-cli/SKILL.md`
- `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md`
- `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md`
- `.claude/skills/gitnexus/gitnexus-guide/SKILL.md`
- `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md`
- `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md`
- `.trellis/spec/guides/project-development.md`

## Notes

- Default routine refresh command is now `node .gitnexus/run.cjs analyze --index-only --name hermes-trendradar`.
- Plain/full `gitnexus analyze` remains allowed only for explicit AGENTS/CLAUDE/skill guidance refreshes.
- No source code, tests, dependencies, or generated `.gitnexus/` files were intentionally changed.
