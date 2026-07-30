---
name: trellis-loop-v1
description: Operate an admitted Loop v1 parent from the exact digest-bound next action returned by loop_v1.orchestrator. Use when the user asks to start, continue, inspect, pause, resume, or cancel a real Loop v1 parent; present its start or final gate; dispatch its listed workers or reviewers through an approved agent surface; or ingest their transport results.
---

# Trellis Loop v1

Drive one existing Loop v1 run through the A1 JSON CLI. Treat the CLI, ledger,
and exact Git identities as authority; this skill owns presentation and
transport only.

## Hard Boundary

- Read only the current `next_action`. Never invent, merge, reorder, or reuse an
  action from chat or an older output file.
- Never open or edit the ledger, `operator-state.json`, refs, commits,
  integration state, qualification state, or Trellis task authority directly.
- Never let a worker or reviewer commit, update refs, merge, approve a gate, or
  call the orchestrator's mutating commands.
- Never treat a channel/agent message as approval or a state transition.
- Never dispatch through a surface absent from
  `next_action.payload.approved_agent_surfaces`. If no approved listed surface
  is available, stop and report that exact blocker.
- Keep raw transport only under the ignored run directory. Do not copy it into
  task evidence, source, logs, diffs, or the final response.
- Push, release, deploy, and network writes remain prohibited unless the exact
  parent envelope and a separate direct user authorization both allow them.

## Command Prefix

Run from the canonical repository root:

```bash
PYTHONPATH=.trellis/scripts python3 -m loop_v1.orchestrator \
  --repo-root . --run-id "$RUN_ID" <command>
```

Use `--output` only below:

```text
.trellis/.runtime/loop-v1/parents/<run-id>/outputs/*.json
```

Use repository-relative input files under the same ignored run directory and
set every file containing a user or transport response to mode `0600`.

## Action Loop

1. Run `status`. If state may have advanced since the last observation, run
   `advance` and use only the returned `next_action`.
2. Verify the action's `action_id`, `action_digest`, `authority_digest`, and
   `action_type`. Read its ledger-derived `payload.recovery` projection and keep
   the complete action object unchanged.
3. Route exactly one action using the table below.
4. After a successful response or ingest, run `advance` again. Stop when it
   returns another external action, `paused`, `cancelled`, or `archived`.

| Action type | Route |
|---|---|
| `start_response` | Present the exact start request and wait for the direct user. |
| `dispatch_workers` | Dispatch every listed child through one approved available surface; ingest each result separately. |
| `precommit_review` | Request one independent review of the exact validated child tree. |
| `final_review` | Request a fresh non-implementer review of the exact integration identity. |
| `final_response` | Present the exact final request/pack and wait for the direct user. |
| `human_intervention` | Present the exact classified reason and durable details, then stop; do not auto-resume or cancel. |

Routine worker validation, required pre-commit findings, stale resume work,
failed integration candidates, and required final-review findings remain inside
the same run. A1 records their stable 0+3 problem lineage, replaces only the
current failed graph slice, and returns the next worker or review action. Never
create `retry-N` Trellis tasks or ask the user to authorize that replacement.

For a quiescent `worker_required_test_failed` dispatch, the final failed ingest
may include `replacement_guidance`. Before running `advance`, an operator may
submit that object unchanged plus a stable `operation_id` and an explicit
nonempty `prerequisite_child_ids` list to `guide-replacement`. Select only
current integrated transitive ancestors and name every intermediate dependency
on every path. Never infer prerequisites from worker prose, add an unlisted
child, omit a path node, or select a prerequisite with a current descendant
outside the slice. Exact replay is allowed; changed or late new guidance stops.
Without this explicit operation, run `advance` for the compatible same-child
replacement.

## Direct User Gates

For `start_response` and `final_response`, show the current action ID/digest,
request ID/digest, exact base/integration identity, receipt/readiness identity,
requirements, effects, prohibited actions, and pack paths when present. Do not
reduce the gate to a generic yes/no summary.

Wait for a new direct response in the current conversation. A planning,
implementation, worker, reviewer, channel, prior-run, or inferred signal is not
valid. Build the response from the unchanged action fields with:

- `direct_user_action: true`
- the exact `action_id`, `action_digest`, `request_id`, and `request_digest`
- current RFC 3339 `response_at`
- a stable non-secret `response_identity`

Write it to an ignored `0600` input file, then call `respond-start` or
`respond-final`. Never send direct-user gates through the capture helper.

## Worker Dispatch

For every entry in `dispatch_workers.payload.children`:

1. Select one currently available surface whose exact logical name appears in
   `approved_agent_surfaces`. Use the platform's native agent surface or the
   existing Trellis channel; do not substitute another provider.
2. Give the worker only its exact packet, issued worktree, and any
   ledger-derived `recovery_context` on that dispatch entry. The recovery
   context carries accepted required findings and failed check evidence; never
   substitute raw transport content. Require the worker to
   work inside that worktree, obey allowed/forbidden touches, run the packet
   tests, leave changes uncommitted, and return one JSON object with exactly
   `packet.expected_result_fields`.
3. Require packet-bound `child_id`, `packet_id`, freshness identities, base
   HEAD/tree, observed result tree/diff, actual touches, commands, coverage,
   findings, risks, and artifacts. The A1 CLI independently validates all of
   them; transport text is not evidence by itself.
4. Preserve the complete raw worker response and capture its structured result
   using the procedure below. Call A1 `ingest` once per child. Keep the same
   pending dispatch action until A1 reports no remaining child IDs.

Replacement packets use a new child/packet identity and a higher attempt/round.
Dispatch only the entries in the current action; prior failed/stale identities
remain evidence and must not be reused.

Do not dispatch a child omitted from the action, combine child results, or let
arrival order select integration order.

## Reviews

For `precommit_review`, give an independent reviewer the exact action payload:
validation identity/tree, child ID, review ID, and approved surface list. For
`final_review`, use a fresh non-implementer and include the exact integration
identity, requirements, review ID, and `fresh_context_receipt`.

Reviewers return only the A1 review schema. Their `reviewer_identity` must equal
the stable transport identity recorded by the dispatcher. A failed pre-commit
review must contain at least one `required_findings` entry; a passing one must
contain none. A final review must echo the exact `fresh_context_receipt` and
select `affected_requirement_ids` only from the action requirements. A failed
final review must name at least one affected requirement; a passing one must use
an empty list. Preserve the full raw response and use the capture procedure;
never convert prose into `passed` without the reviewer returning that verdict
and complete structured fields.

## Capture And Ingest

Create one JSON file directly under:

```text
.trellis/.runtime/loop-v1/parents/<run-id>/transport/inbox/
```

Set it to `0600`. It must contain exactly:

```json
{
  "action": {},
  "payload": {},
  "raw_message": "exact transport response text",
  "role": "worker | precommit_reviewer | final_reviewer",
  "surface": "exact approved surface name",
  "transport_identity": "stable non-secret agent identity"
}
```

Run:

```bash
python3 .agents/skills/trellis-loop-v1/scripts/capture_transport.py \
  --repo-root . --run-id "$RUN_ID" --input "$CAPTURE_INPUT"
```

The helper validates the action binding before persistence, writes an immutable
raw record, and returns only paths/digests. Pass its `ingest_path` unchanged to:

```bash
PYTHONPATH=.trellis/scripts python3 -m loop_v1.orchestrator \
  --repo-root . --run-id "$RUN_ID" ingest --input "$INGEST_PATH"
```

Do not retype or normalize the payload between capture and ingest. On helper or
A1 rejection, preserve the raw record, report the structured error, and keep
the current action pending. Never patch authority to make a message pass.

## Control

- `pause`: require an exact current operator request; preserve unfinished work.
- `resume`: require the exact direct-user authority and supplied tool/resource
  evidence. After resume, run `advance`; stale work is replaced autonomously
  inside the approved envelope while late results remain rejected.
- `cancel`: first run read-only `cancel-safe-point`; use
  `reconcile-for-cancel` only when it reports a locally reconcilable intent,
  then require exact direct-user cancellation authority. Never cancel an
  ambiguous external effect.
- Terminal closeout: run `projection-status`, submit its exact authority/task
  digests to `project-terminal`, recheck `current`, then call
  `retire-task-evidence` with those current digests. For a formal uninitialized
  cancellation, use `archive-pre-admission` instead. Never use the generic task
  archive surface for Loop evidence.
- `human_intervention`: inspect and report durable evidence only. Wait for a
  separate direct user instruction before any control command.

## Completion Report

Report the run ID, parent status, authority digest, child-state summary,
`recovery.status`, active/exhausted problem IDs, replacement child IDs, current
or terminal action, verification result, and any classified intervention. Report
only raw-message paths/digests, never raw content or fence/secret material.
