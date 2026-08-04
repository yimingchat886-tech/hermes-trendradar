# {title}

## Goal

{description}

## Accepted PRD Binding

- Git commit: `TODO`
- PRD paths: `TODO`
- REQ IDs: `TODO-REQ-001`

## Evidence Model

- `task.json.meta.workflow_mode` selects the lifecycle authority.
- `state-events.jsonl` records transitions only for `harness_state_machine`;
  TaskRun tasks do not create that stream.
- `stage-report.md` records acceptance, verification, commit, and child-completion evidence.
- Do not add legacy staged metadata.

## Verification Commands

- `TODO`

## In

- TODO

## Out

- TODO

## Protocol Gates

Use `.trellis/spec/project/protocol-phrases.md` for completion, commit,
archive, limit, and push wording. Do not copy the phrase table here.
