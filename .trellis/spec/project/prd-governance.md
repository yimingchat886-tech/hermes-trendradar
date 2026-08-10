# PRD Governance

## One Contract

A Task has one current PRD and stable identity. Requirement declarations use:

    - REQ-ID [owner: owner]: text

In the actual Markdown, REQ-ID is backticked. README, manifests, RTMs, task JSON,
and BOARD are projections, not product truth.

## Intake

- A clear small bug or local maintenance request becomes a minimal intent
  snapshot and starts its default Loop without repeated confirmation.
- New behavior, architecture, public API, major dependency, migration,
  irreversible effect, or unresolved multi-option design uses a complex PRD.
- Ask only material questions, one at a time.

## Acceptance

For a complex PRD, “确认方案并开始执行” authorizes exactly the shown revision,
one scoped local PRD commit, one immutable binding generation, and start of the
same default Loop. “采纳”, “记录”, and “确认方向” update Draft only.

Acceptance never authorizes implementation commit, closeout, push, real
downstream sync, publication, deployment, or activation.

## Revision

Execution binds Git commit, PRD path/digest, REQ IDs, and base commit. Material
changes pause the run, require acceptance, and append a generation to the same
TaskRun. Never create a successor. Historical bindings/actions remain evidence.

T3/T4 PRDs receive one compact independent red-team before acceptance. Oracle
remains disabled. Semantic conflicts go to the user; deterministic owner,
duplicate ID, path, and digest errors are fixed first.
