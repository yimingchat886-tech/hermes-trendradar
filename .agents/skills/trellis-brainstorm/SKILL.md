---
name: trellis-brainstorm
description: "Collaboratively clarify a feature, task, or PRD before implementation. Use one high-value question at a time, persist accepted decisions in the active task, and keep PRD acceptance separate from implementation start."
---

# Trellis Brainstorm

Use this as a thin planning loop. Do not turn it into a second lifecycle,
questionnaire, PRD hierarchy, or PM-method pipeline.

## Start

1. Resolve the active task with `task.py current --source`. Create a task only
   when none exists and the request requires implementation or persistent PRD
   work.
2. Read `.trellis/workflow.md`, the active `prd.md`, applicable project specs,
   and relevant repository files before asking questions.
3. For any product or task PRD, read
   `.trellis/spec/project/prd-governance.md`. That spec owns product truth,
   acceptance, execution binding, successor, and PM-method rules.
4. If an accepted binding already exists and the user asks to implement it, do
   not reopen resolved product decisions. Continue to the explicit start gate.

## Planning Loop

1. Preserve the user's original goal, then write the smallest executable goal,
   scope, acceptance criteria, and explicit out-of-scope boundary into `prd.md`.
2. Inspect the repository or research facts that can be derived. Do not ask the
   user for information the Agent can obtain directly.
3. Ask one unresolved, high-value blocking or preference question at a time.
   Include a recommended answer and concrete trade-offs.
4. After each answer, update Draft immediately without upgrading its authority;
   use the acceptance and start gates from `prd-governance.md`.
5. Use research only when it changes a real decision, and persist the result in
   the task's `research/` directory without copying it into the PRD.
6. For complex work, create child plans only after the parent scope is stable.
   Child PRDs cite the accepted parent commit, PRD path, and assigned REQ IDs;
   they do not copy product requirement text.

## Conditional Product Methods

Run no extra PM method by default. When one unresolved decision needs help,
select at most one method allowed by `prd-governance.md`. WWA and test scenarios
are internal writing transforms, not user-visible workflow phases. T3/T4 PRDs
get one compact model red-team before acceptance.

## Converge

The PRD is ready to propose when it contains:

- goal and user-visible outcome;
- in-scope and out-of-scope boundaries;
- stable REQ IDs with one owner for product requirements;
- testable acceptance criteria;
- research references and material risks when present;
- the minimum implementation split and verification plan; and
- no unresolved semantic conflict in the proposed delta.

Show the proposed delta, then follow the single acceptance transaction in
`prd-governance.md`. Record its resulting execution binding in task evidence
and return to `.trellis/workflow.md` Phase 1.4 for the separate start gate.

## Avoid

- mandatory expansion sweeps, PRD Maps, or fixed questionnaires;
- multiple PM methods for one decision;
- independent feature PRDs that duplicate one logical product contract;
- copied phrase tables, global RTMs, generated-view authority, or routine
  compliance narration;
- implementation from Draft or from a narrower authority than the governing
  acceptance/start contracts.
