# staged acceptance commit archive rule

## Goal

Make staged overlay acceptance semantics less ambiguous: when the user approves
git submission for a child task, the child is also soft archived; when the user
accepts the parent task, the parent is committed and archived.

## Requirements

- Do not create a new skill for this single rule; update the existing Trellis
  workflow/spec/template surfaces instead.
- For staged child tasks, user approval to commit git also authorizes child
  soft archive by default.
- For parent staged tasks, user acceptance means the parent evidence is committed
  and the parent task is archived with the built-in Trellis archive flow.
- Explicit user limits still win, such as `先别提交`, `不要归档`, or `还要改`.
- Push remains separate and requires explicit user approval.
- Do not change `task.py`, archive scripts, hooks, or runtime state.

## Acceptance Criteria

- [ ] `.trellis/workflow.md` states child commit approval includes soft archive
      and parent acceptance includes archive.
- [ ] `.trellis/spec/project/staged-delivery-overlay.md` records the child and
      parent acceptance rules.
- [ ] `.trellis/spec/project/git-commit-push-policy.md` no longer implies
      staged child commit approval is commit-only.
- [ ] `.trellis/templates/staged/stage-report.md` reflects that child commit
      approval normally allows soft archive too.
- [ ] Verification confirms the diff is scoped to workflow/spec/template/task
      evidence only.

## Notes

- Current repo has unrelated untracked work under
  `.agents/skills/trellis-prd-design-input/` and
  `.trellis/tasks/07-03-prd-pre-design-input-skill/`; keep it out of this task.
- This task is docs/spec/template-only.
