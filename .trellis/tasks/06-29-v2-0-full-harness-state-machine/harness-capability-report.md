# Harness Capability Report

## Scope

Reality check for V2.0 Full Harness State Machine against the current standalone Trellis copy in this repo.

External plan inspected:

- `/mnt/c/Users/Jym/Downloads/tele/v2_0_full_harness_state_machine_execution_plan.md`

Local surfaces inspected:

- `.trellis/scripts/`
- `.trellis/workflow.md`
- `.trellis/config.yaml`
- `.trellis/spec/project/`
- `.agents/skills/`
- `.codex/`

## Repo State

- Git root: `/home/jym/workspace/Hermes stock`
- Branch: `main`
- Recent commits: none
- Pre-existing untracked bootstrap files: `.gitignore`, `.trellis/`, `AGENTS.md`
- Active task after creation: `.trellis/tasks/06-29-v2-0-full-harness-state-machine`
- Package mode: single-repo, no packages configured

## `task.py` Command Surface

Observed command list:

- `create`
- `add-context`
- `validate`
- `list-context`
- `start`
- `current`
- `finish`
- `set-branch`
- `set-base-branch`
- `set-scope`
- `archive`
- `list`
- `add-subtask`
- `remove-subtask`
- `list-archive`

Capabilities already present:

- Task creation with `MM-DD-slug` directory naming.
- `task.json` status starts at `planning`.
- `implement.jsonl` and `check.jsonl` are seeded when a sub-agent-capable platform directory exists.
- Session-scoped active task pointer is written when Codex session identity is available.
- `start` sets the active task and flips `planning` to `in_progress`.
- `finish` clears the active task pointer without changing task status.
- `archive` marks status `completed`, writes `completedAt`, moves the task under `archive/YYYY-MM/`, clears active-session pointers, and may auto-commit archive changes.
- Parent/child helpers exist through `create --parent`, `add-subtask`, and `remove-subtask`.

Missing from `task.py`:

- No `event` command.
- No `state_machine` command group.
- No mode-aware `continue`, `finish`, or `archive` routing.
- No commit gate.
- No parent evidence aggregation.
- No RTM sync/check command.
- No Oracle adapter.
- No Ponytail gate script.

## Lifecycle Semantics

Current compatible status layer:

- `planning`
- `in_progress`
- `completed`

Observed behavior:

- `task.py create` creates the task directory and records `status=planning`.
- `task.py start` writes the active task pointer and changes `planning -> in_progress`.
- `task.py finish` only clears the active task pointer.
- `task.py archive` is the only built-in terminal lifecycle operation: it writes `status=completed` and moves the task directory.

V2 implication:

- The V2 plan's dual-layer model is compatible with the current shape: keep `task.json.status` as the compatibility layer and put fine-grained V2 state under `task.json.meta.state_machine`.
- Do not add new top-level `task.json.status` values for the first executable state-machine slice.

## Archive Behavior

Current archive behavior:

- Built-in archive physically moves the task directory to `.trellis/tasks/archive/YYYY-MM/<task>`.
- It sets `status=completed` before the move.
- It clears active session files that point at the archived task.
- If archiving a parent, active children have their `parent` field cleared.
- Parent `children` entries are retained so missing active children count as done in progress display.

V2 implication:

- True child archive cannot reuse built-in archive directly without a wrapper.
- Child evidence must be copied to the parent before any physical move.
- A V2 `archive-child` flow needs an archive manifest and parent evidence write-before-move behavior.

## Continue / Finish-Work Routing

Current routing shape:

- `trellis-continue` is a skill that reads `get_context.py`, `workflow.md`, and task artifact presence.
- `trellis-finish-work` is a skill that surveys state, checks dirty paths, calls `task.py archive`, and records a session journal.
- `.trellis/workflow.md` contains per-status breadcrumb blocks.
- There is no single Python `continue` or `finish-work` router today.

V2 implication:

- `route_continue.py` and `route_finish.py` from the V2 plan should start as explicit wrapper commands, not edits to a nonexistent central router.
- The skill docs and workflow breadcrumbs will still need follow-up alignment after wrappers exist.

## Config And Hooks

Current config facts:

- `.trellis/config.yaml` is single-repo mode.
- Codex dispatch is documented as default inline unless explicitly overridden.
- Task lifecycle hooks are supported in config comments: `after_create`, `after_start`, `after_finish`, `after_archive`.
- No task lifecycle hooks are configured.

Codex hook facts:

- `.codex/hooks.json` registers `UserPromptSubmit` to run `.codex/hooks/inject-workflow-state.py`.
- `.codex/config.toml` notes hooks require user-level enablement and hook review.

V2 implication:

- Codex prompt hooks are useful hints, not a safe enforcement boundary.
- Hard V2 gates should live in explicit scripts/commands that can be tested directly.

## Parent / Child Capability

Already present:

- `task.json.children`
- `task.json.parent`
- `task.py create --parent`
- `task.py add-subtask`
- `task.py remove-subtask`
- Progress display counts archived/missing children as done.

Missing:

- No `child-evidence.jsonl`.
- No `child-task-index.md` automation.
- No archive manifest.
- No parent rollup command.
- No parent closeout gate.

## Tests

Observed test state:

- No `tests/` directory.
- No `pyproject.toml`, `pytest.ini`, `tox.ini`, `package.json`, or requirements file.
- System `pytest` is installed.
- `env TMPDIR=/tmp python3 -m pytest --collect-only` works and collects 0 tests.
- Plain `python3 -m pytest --version` hit a temp-file issue because Python resolved temp storage to the Windows temp path; use `TMPDIR=/tmp` for Trellis test commands in WSL.

V2 implication:

- v2.1.0 should add the first tests under `tests/trellis/`.
- Test commands should use `TMPDIR=/tmp` until project-local pytest config or environment handling is added.

## RTM / Oracle / Ponytail

Present:

- `.trellis/spec/project/rtm-guidelines.md`
- `.trellis/spec/project/oracle-review-policy.md`
- `.trellis/spec/project/ponytail-boundary.md`
- Staged templates for Oracle budget and RTM delta.

Missing:

- No `docs/requirements-traceability-matrix.md`.
- No `docs/requirements-traceability-matrix.json`.
- No `.trellis/scripts/rtm_sync.py`.
- No `.trellis/scripts/rtm_check.py`.
- No `.trellis/scripts/oracle_adapter.py`.
- No `.trellis/scripts/ponytail_gate.py`.

V2 implication:

- These should stay out of v2.1.0 unless the user explicitly broadens the next slice.

## Capability Verdict

This repo can be optimized as a harness testbed now.

The safe path is not to wire the full V2 state machine immediately. The current harness has a clean task lifecycle and enough metadata extensibility to support a new `meta.state_machine` layer, but it lacks tests, executable routing, and adapter scripts. Start with a small state engine and tests, then wire routers and gates one slice at a time.
