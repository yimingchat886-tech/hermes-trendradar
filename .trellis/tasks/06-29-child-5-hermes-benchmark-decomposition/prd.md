# Child PRD: Hermes Benchmark Decomposition Outputs

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-020, P1-REQ-070, P1-REQ-090, P1-REQ-100

## Goal

Define and validate the Hermes output boundary for benchmark content decomposition, cards, and topic-pool supplements without letting Hermes autonomously submit official topic titles.

## Requirements

- Accept benchmark content and transcript data.
- Produce summary, topic one-liner, content type, hook, title formula, structure, audience pain, reusable angle, non-reusable notes, evidence state, and sedimentation suggestion.
- Produce card fields for external display.
- Produce topic-pool supplement fields only after a manual topic title exists or as candidate-shaped suggestions.
- Keep output schema stable and mockable.

## Out of Scope

- Production LLM orchestration.
- Auto-enabling selection Skill.
- Official autonomous topic submission.
- Risk-review system.

## Acceptance Criteria

- [ ] Mock Hermes output validates against schema.
- [ ] Manual fields are not overwritten.
- [ ] Evidence-insufficient content remains marked.
- [ ] Card/topic outputs include source IDs and trace IDs.

## Risk Level

- T3: LLM boundary and product behavior.
- High-risk trial PLAN: yes.
- Oracle required: decide before implementation.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
