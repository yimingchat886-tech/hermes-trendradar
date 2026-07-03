# brainstorm: PRD pre-design input skill

## Goal

Create a global Codex skill that captures the agreed workflow: use a lightweight product-manager design-input pass before writing a formal PRD, manage the resulting PRD Map, then keep formal development and execution inside the existing Trellis parent-child task model.

## Requirements

- The skill must trigger when planning a new PRD, parent task, child task, feature plan, or staged Trellis work where upfront design inputs would reduce ambiguity.
- The skill must be global under `$CODEX_HOME/skills/trellis-prd-design-input/`, not repo-local.
- The skill must preserve Trellis parent-child as the official development execution model.
- The skill must define design-input dimensions before PRD without forcing every request through a fixed question list.
- The skill must prioritize implementation-boundary questions when the user's natural-language goal could expand.
- The skill must support chaining into `trellis-brainstorm` when local Trellis workflow expects iterative requirement discovery.
- The skill must require a PRD Map after PRD writing: one main PRD as architecture/framework and auxiliary or feature PRDs for detailed functions when needed.
- The skill must tell Codex to convert design inputs into a parent PRD and child-task breakdown instead of replacing Trellis with loose docs.
- The repo-local skill copy must be removed.

## Acceptance Criteria

- [x] A global skill exists under `$CODEX_HOME/skills/trellis-prd-design-input/`.
- [x] Repo-local `.agents/skills/trellis-prd-design-input/` files are removed.
- [x] The skill has valid `SKILL.md` frontmatter and concise procedural instructions.
- [x] The skill has valid `agents/openai.yaml` metadata.
- [x] Skill validation passes.
- [ ] No source code is modified.

## Notes

- This is a small docs/process skill. No child tasks are needed.
- Ponytail boundary: no scripts or references unless the skill proves too large for one `SKILL.md`.
