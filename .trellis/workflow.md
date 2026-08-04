# Development Workflow

---

## Core Principles

1. **Plan before code** — figure out what to do before you start
2. **Specs injected, not remembered** — guidelines are injected via hook/skill, not recalled from memory
3. **Persist everything** — research, decisions, and lessons all go to files; conversations get compacted, files don't
4. **Incremental development** — one task at a time
5. **Capture learnings** — after each task, review and write new knowledge back to spec

---

## Trellis System

### Developer Identity

On first use, initialize your identity:

```bash
python3 ./.trellis/scripts/init_developer.py <your-name>
```

Creates `.trellis/.developer` (gitignored) + `.trellis/workspace/<your-name>/`.

### Spec System

`.trellis/spec/` holds coding guidelines organized by package and layer.

- `.trellis/spec/<package>/<layer>/index.md` — entry point with **Pre-Development Checklist** + **Quality Check**. Actual guidelines live in the `.md` files it points to.
- `.trellis/spec/guides/index.md` — cross-package thinking guides.

```bash
python3 ./.trellis/scripts/get_context.py --mode packages   # list packages / layers
```

**When to update spec**: new pattern/convention found · bug-fix prevention to codify · new technical decision.

### Task System

Every task has its own directory under `.trellis/tasks/{MM-DD-name}/` holding `prd.md`, `implement.jsonl`, `check.jsonl`, `task.json`, optional `research/`, `info.md`.

```bash
# Task lifecycle when taskrun_v2.new_code_tasks is true
python3 ./.trellis/scripts/task.py create "<title>" [--slug <name>] [--parent <dir>] [--strategy single|loop]
python3 ./.trellis/scripts/task.py start <name> --taskrun-input <json>

# A task whose stored workflow_mode is neither taskrun_v1 nor taskrun_v2 keeps its legacy start
python3 ./.trellis/scripts/task.py start <name>
python3 ./.trellis/scripts/task.py current --source      # show active task and source
python3 ./.trellis/scripts/task.py finish                # clear active task (legacy modes trigger after_finish hooks)
python3 ./.trellis/scripts/task.py archive <name>        # eligible legacy tasks only
python3 ./.trellis/scripts/task.py list [--mine] [--status <s>]
python3 ./.trellis/scripts/task.py list-archive

# Code-spec context (injected into implement/check agents via JSONL).
# `implement.jsonl` / `check.jsonl` are seeded on `task create` for sub-agent-capable
# platforms; the AI curates real spec + research entries during Phase 1.3.
python3 ./.trellis/scripts/task.py add-context <name> <action> <file> <reason>
python3 ./.trellis/scripts/task.py list-context <name> [action]
python3 ./.trellis/scripts/task.py validate <name>

# Task metadata
python3 ./.trellis/scripts/task.py set-branch <name> <branch>
python3 ./.trellis/scripts/task.py set-base-branch <name> <branch>    # PR target
python3 ./.trellis/scripts/task.py set-scope <name> <scope>

# Hierarchy (parent/child)
python3 ./.trellis/scripts/task.py add-subtask <parent> <child>
python3 ./.trellis/scripts/task.py remove-subtask <parent> <child>

# PR creation
python3 ./.trellis/scripts/task.py create-pr [name] [--dry-run]
```

> Run `python3 ./.trellis/scripts/task.py --help` to see the authoritative, up-to-date list.

**Current-task mechanism**: with `taskrun_v2.new_code_tasks: true`, `task.py create` marks the task as `taskrun_v2`, defaults to `single`, and (when session identity is available) auto-sets the per-session active-task pointer so the planning breadcrumb fires immediately. When the v2 key is absent, a retained downstream `taskrun_v1.new_code_tasks: true` key enables the same v2 creation path without rewriting target configuration; existing v1 tasks keep v1. `task.py start --taskrun-input <json>` admits or exactly reopens that task's one TaskRun and projects `status=running`; the input contract is defined in [`.trellis/spec/project/taskrun-runtime.md`](spec/project/taskrun-runtime.md). State is stored under `.trellis/.runtime/sessions/`. A missing session identity prevents only the non-authoritative active pointer, not TaskRun admission. `task.py finish` deletes the current session file (status unchanged). Repositories with cutover disabled and tasks whose stored mode is not TaskRun keep the historical create/start/lifecycle behavior.

### Legacy Staged Delivery Overlay

Do not create new `staged_overlay` / `meta.staged_delivery` tasks or templates.
Those fields are legacy read-compatibility inputs only.

Existing v3 parent/child work keeps `task.json.meta.workflow_mode =
"harness_state_machine"`, `meta.state_machine.schema_version = 2`,
`state-events.jsonl`, and evidence files. Do not extend that authority with new
children after TaskRun cutover.

Existing Harness child tasks use `task.py complete-child` after the user completion or
commit-approval signal: commit the approved child scope, record the commit in
`stage-report.md`, keep the child task directory in place, and do not call
built-in `task.py archive` for that child. Child commit approval includes child
completion unless the user explicitly limits it. Parent task acceptance includes
committing parent evidence and archiving the parent with built-in
`task.py archive`. After child completion, the child is evidence only and is no
longer the active implementation target. Parent archive moves its exact
terminal child family first and the parent last into one archive month; linked
children cannot be hard-archived directly. An incomplete family move blocks
new archive work until `task.py archive-recover <transaction-id>`. Push still
requires an explicit user command.

### Workspace System

Records every AI session for cross-session tracking under `.trellis/workspace/<developer>/`.

- `journal-N.md` — session log. **Max 2000 lines per file**; a new `journal-(N+1).md` is auto-created when exceeded.
- `index.md` — personal index (total sessions, last active).

```bash
python3 ./.trellis/scripts/add_session.py --title "Title" --commit "hash" --summary "Summary"
```

### Context Script

```bash
python3 ./.trellis/scripts/get_context.py                            # full session runtime
python3 ./.trellis/scripts/get_context.py --mode packages            # available packages + spec layers
python3 ./.trellis/scripts/get_context.py --mode phase --step <X.Y>  # detailed guide for a workflow step
```

---

<!--
  WORKFLOW-STATE BREADCRUMB CONTRACT (read this before editing the tag blocks below)

  The 4 [workflow-state:STATUS] blocks embedded in the ## Phase Index section
  below are the SINGLE source of truth for the per-turn `<workflow-state>`
  breadcrumb that the v3-supported Codex and Claude Code surfaces read.
  inject-workflow-state.py parses them — there is no fallback dict baked
  into the script after v0.5.0-rc.0.

  STATUS charset: [A-Za-z0-9_-]+. When the hook can't find a tag, it
  degrades to a generic "Refer to workflow.md for current step." line —
  intentionally visible so users notice and fix a broken workflow.md.

  INVARIANT (test/regression.test.ts):
    Every workflow-walkthrough step marked `[required · once]` must have a
    matching enforcement line in its phase's [workflow-state:*] block. The
    breadcrumb is the only per-turn channel; if a mandatory step isn't
    mentioned there, the AI silently skips it (Phase 1.3 jsonl curation
    skip and Phase 3.4 commit skip both manifested via this gap).

  TAG ↔ PHASE scoping:
    [workflow-state:no_task]      → no active task; before Phase 1
    [workflow-state:planning]     → all of Phase 1 (status='planning')
    [workflow-state:in_progress]  → Phase 2 + Phase 3.1-3.4
                                    (TaskRun status='running'; legacy status
                                    stays 'in_progress' until legacy close)
    [workflow-state:completed]    → TaskRun completed/cancelled terminal
                                    projection; status-only close leaves the
                                    task in place until task.py finish clears
                                    the session pointer

  Editing checklist:
    - When you change a [workflow-state:STATUS] block, also check the
      matching phase's `[required · once]` walkthrough steps for sync
    - Run `trellis update` after editing to push the new bodies to
      downstream user projects (block-level managed replacement)
    - Full runtime contract:
      .trellis/spec/cli/backend/workflow-state-contract.md
-->

## Phase Index

```
Phase 1: Plan    → figure out what to do (brainstorm + research → prd.md)
Phase 2: Execute → write code and pass quality checks
Phase 3: Finish  → distill lessons + wrap-up
```

<!-- Per-turn breadcrumb: shown when there is no active task (before Phase 1) -->

[workflow-state:no_task]
No active task. **A Direct answer** — pure Q&A / explanation / lookup / chat; no file writes + one-line answer + repo reads ≤ 2 files → AI judges, no override needed.
**B Create a task** — any implementation / code change / build / refactor work. First read `.trellis/config.yaml`. With `taskrun_v2.new_code_tasks: true` (or the retained downstream v1 activation key): (1) `task.py create "<title>"` creates a `taskrun_v2` `single` (`--strategy loop` is explicit concurrent/unattended work) → (2) load `trellis-brainstorm` and, for PRD work, `.trellis/spec/project/prd-governance.md` → (3) after an accepted commit/path/REQ binding exists and the user explicitly authorizes start, run `task.py start <task-dir> --taskrun-input <json>` using the exact accepted execution binding from [`.trellis/spec/project/taskrun-runtime.md`](spec/project/taskrun-runtime.md). With cutover disabled or absent, follow the retained selector/start contract in [`.trellis/spec/project/loop-v1-admission.md`](spec/project/loop-v1-admission.md). **"It looks small" is NOT grounds for downgrading B to A or C**.
For T3/T4 or high-risk T2 work, create the parent directly with `--tier parent`; do not create a throwaway light task first. Under TaskRun cutover, do not supply `--workflow-mode`: new HSM and independent Loop lifecycle admission are disabled. Otherwise omit `--workflow-mode` so the configured default remains authoritative, use `current_trellis` only as an explicit override, and stop rather than silently downgrade if implicit Loop admission fails. Recorded tasks remain under their original authority. Apply `.trellis/spec/project/index.md`; do not invent custom statuses or new `meta.staged_delivery` writes.
**C Inline change** (per-turn only, escape hatch for B) — the user's CURRENT message MUST contain one of: "skip trellis" / "no task" / "just do it" / "don't create a task" / "跳过 trellis" / "别走流程" / "小修一下" / "直接改" / "先别建任务" → briefly acknowledge ("ok, skipping trellis flow this turn"), then inline. **Without seeing one of these phrases you must NOT inline on your own**; do not invent an override the user never said.
[/workflow-state:no_task]

### Phase 1: Plan
- 1.0 Create task `[required · once]` (choose light or parent up front; when
  cutover is enabled TaskRun `single` is default and `--strategy loop` is
  explicit; status enters planning)
- 1.1 Requirement exploration `[required · repeatable]`
- 1.2 Research `[optional · repeatable]`
- 1.3 Configure context `[required · once]` — Claude Code, Codex
- 1.4 Activate task `[required · once]` (dispatch start from the stored mode;
  TaskRun uses `--taskrun-input` and projects running)
- 1.5 Completion criteria

<!-- Per-turn breadcrumb: shown throughout Phase 1 (status='planning') -->

[workflow-state:planning]
Load `trellis-brainstorm`; for PRD work also read `.trellis/spec/project/prd-governance.md`. Iterate on prd.md with the user and do not start without an accepted commit/path/REQ binding plus explicit start authority.
If this is an existing `harness_state_machine` task, also read `.trellis/spec/project/index.md`, follow its recorded legacy contract and existing parent/child planning artifacts, and keep fine-grained workflow facts in `meta.state_machine`, `state-events.jsonl`, and evidence files rather than creating another HSM child or changing `task.json.status`.
Phase 1.3 (required, once): before `task.py start`, you MUST curate `implement.jsonl` and `check.jsonl` — list the spec / research files sub-agents need so they get the right context injected. You may skip only if the jsonl already has agent-curated entries (the seed `_example` row alone doesn't count).
Then inspect `task.json.meta.workflow_mode`: TaskRun requires `task.py start <task-dir> --taskrun-input <json>` with the exact accepted execution binding; a stored legacy mode retains `task.py start <task-dir>`.
[/workflow-state:planning]

<!-- Per-turn breadcrumb: shown throughout Phase 1 when codex.dispatch_mode=inline.
     Codex-only opt-in alternate to [workflow-state:planning]. The main agent
     edits code directly in Phase 2, so Phase 1.3 jsonl curation is skipped —
     the inline workflow loads `trellis-before-dev` instead of injecting JSONL
     into a sub-agent. -->

[workflow-state:planning-inline]
Load `trellis-brainstorm`; for PRD work also read `.trellis/spec/project/prd-governance.md`. Iterate on prd.md with the user and do not start without an accepted commit/path/REQ binding plus explicit start authority.
If this is an existing `harness_state_machine` task, also read `.trellis/spec/project/index.md`, follow its recorded legacy contract and existing parent/child planning artifacts, and keep fine-grained workflow facts in `meta.state_machine`, `state-events.jsonl`, and evidence files rather than creating another HSM child or changing `task.json.status`.
Phase 1.3 jsonl curation is **skipped** in inline dispatch mode — the main session loads `trellis-before-dev` directly in Phase 2 and reads spec context itself, so there is no sub-agent to inject jsonl into.
Then inspect `task.json.meta.workflow_mode`: TaskRun requires `task.py start <task-dir> --taskrun-input <json>` with the exact accepted execution binding; a stored legacy mode retains `task.py start <task-dir>`.
[/workflow-state:planning-inline]

### Phase 2: Execute
- 2.1 Implement `[required · repeatable]`
- 2.2 Quality check `[required · repeatable]`
- 2.3 Rollback `[on demand]`

<!-- Per-turn breadcrumb: shown while TaskRun status='running' or legacy
     status='in_progress'. Scope: all of Phase 2 + Phase 3.1-3.4. The body
     therefore must cover every required step from implementation through
     commit, including Phase 3.3 spec update and Phase 3.4 commit. -->

[workflow-state:in_progress]
**Tools**: `trellis-implement` / `trellis-research` are sub-agent types only (Task/Agent tool, NOT Skill — there is no skill by these names). `trellis-update-spec` is a skill. `trellis-check` exists as both; prefer the Agent form when verifying after code changes.
**Flow**: trellis-implement → trellis-check → trellis-update-spec → separately authorized commit (Phase 3.4) → `/trellis:finish-work`.
For TaskRun tasks, SQLite is the admitted lifecycle authority. Generic `complete-child` is unavailable; cancel is limited to explicit pre-admission cancellation, close is status-only, and archive is a later explicit gate limited to verified cancelled planning tasks or terminal closed runs. Commit, push, release, deploy, migration, network, cancellation, and destructive effects remain separate explicit gates. Existing `harness_state_machine` tasks keep their recorded legacy rules; if terminal, treat them as evidence-only.
**Main-session default (no override)**: dispatch the `trellis-implement` / `trellis-check` sub-agents — the main agent does NOT edit code by default. Phase 3.4 commit (required once before closeout, but separately gated): implementation completion and TaskRun state never authorize Git. After a direct user commit signal, state the scoped commit plan and run `git commit` before suggesting `/trellis:finish-work`; otherwise stop at verified commit-ready state.
**Sub-agent self-exemption**: if you are already running as `trellis-implement`, implement directly from the loaded task context and do NOT spawn another `trellis-implement`; if you are already running as `trellis-check`, review/fix directly and do NOT spawn another `trellis-check`. The default dispatch rule applies to the main session only.
**Sub-agent dispatch protocol (Codex / Claude Code)**: When you spawn `trellis-implement` / `trellis-check` / `trellis-research`, your dispatch prompt **MUST** start with one line: `Active task: <task path from \`task.py current\`>`. No exceptions. Codex sub-agents depend on this line because they pull task context after startup. For Claude Code it is a required fallback when hook context is absent or stale. For `trellis-research`, the line tells the sub-agent which `{task_dir}/research/` to write into.
**Inline override** (per-turn only, escape hatch for sub-agent dispatch): the user's CURRENT message MUST explicitly contain one of: "do it inline" / "no sub-agent" / "你直接改" / "别派 sub-agent" / "main session 写就行" / "不用 sub-agent". **Without seeing one of these phrases you must NOT inline on your own**; do not invent an override the user never said.
[/workflow-state:in_progress]

<!-- Per-turn breadcrumb: shown while status='in_progress' when
     codex.dispatch_mode=inline. Codex-only opt-in alternate to
     [workflow-state:in_progress]. The main session edits code directly
     instead of dispatching sub-agents. -->

[workflow-state:in_progress-inline]
**Flow** (inline mode): main session loads `trellis-before-dev` → main session edits code → main session loads `trellis-check` → run lint / type-check / tests → fix → `trellis-update-spec` → separately authorized commit (Phase 3.4) → `/trellis:finish-work`.
For TaskRun tasks, SQLite is the admitted lifecycle authority. Generic `complete-child` is unavailable; cancel is limited to explicit pre-admission cancellation, close is status-only, and archive is a later explicit gate limited to verified cancelled planning tasks or terminal closed runs. Commit, push, release, deploy, migration, network, cancellation, and destructive effects remain separate explicit gates. Existing `harness_state_machine` tasks keep their recorded legacy rules; if terminal, treat them as evidence-only.
**Main-session default (inline dispatch_mode)**: the main agent edits code directly. Do NOT dispatch `trellis-implement` / `trellis-check` sub-agents. Load the `trellis-before-dev` skill before writing code; load the `trellis-check` skill before reporting completion.
Phase 3.4 commit is required once before closeout but separately gated: implementation completion and TaskRun state never authorize Git. After a direct user commit signal, state the scoped commit plan and run `git commit` before suggesting `/trellis:finish-work`; otherwise stop at verified commit-ready state.
[/workflow-state:in_progress-inline]

### Phase 3: Finish
- 3.1 Quality verification `[required · repeatable]`
- 3.2 Debug retrospective `[on demand]`
- 3.3 Spec update `[required · once]`
- 3.4 Commit changes `[required · once]`
- 3.5 Wrap-up reminder

<!-- Per-turn breadcrumb: shown for a TaskRun completed/cancelled terminal
     projection. Legacy archive usually moves the task before this can fire. -->

[workflow-state:completed]
TaskRun terminal disposition does not imply close, commit, or archive. Confirm the status-only close projected `meta.task_run.state=closed`; if authorized code remains uncommitted, return to Phase 3.4 first. Then run `/trellis:finish-work`; for TaskRun it clears only the active pointer and records the session. Run `task.py archive <task>` only after separate archive authority; it requires a verified pre-admission cancellation or terminal closed authority, and an admitted run rejects `--no-commit` before mutation.
[/workflow-state:completed]

### Rules

1. Identify which Phase you're in, then continue from the next step there
2. Run steps in order inside each Phase; `[required]` steps can't be skipped
3. Phases can roll back (e.g., Execute reveals a prd defect → return to Plan to fix, then re-enter Execute)
4. Steps tagged `[once]` are skipped if the output already exists; don't re-run

### Skill Routing

When a user request matches one of these intents, load the corresponding skill (or dispatch the corresponding sub-agent) first — do not skip skills.

[Claude Code, codex-sub-agent]

| User intent | Route |
|---|---|
| Wants a new feature, PRD, or requirement clarification | `trellis-brainstorm`; PRD work also reads `.trellis/spec/project/prd-governance.md` |
| About to write code / start implementing | Dispatch the `trellis-implement` sub-agent per Phase 2.1 |
| Finished writing / want to verify | Dispatch the `trellis-check` sub-agent per Phase 2.2 |
| Stuck / fixed same bug several times | `trellis-break-loop` |
| Spec needs update | `trellis-update-spec` |

**Why `trellis-before-dev` is NOT in this table:** you are not the one writing code — the `trellis-implement` sub-agent is. Sub-agent platforms get spec context via `implement.jsonl` injection / prelude, not via the main thread loading `trellis-before-dev`.

[/Claude Code, codex-sub-agent]

[codex-inline]

| User intent | Skill |
|---|---|
| Wants a new feature, PRD, or requirement clarification | `trellis-brainstorm`; PRD work also reads `.trellis/spec/project/prd-governance.md` |
| About to write code / start implementing | `trellis-before-dev` (then implement directly in the main session) |
| Finished writing / want to verify | `trellis-check` |
| Stuck / fixed same bug several times | `trellis-break-loop` |
| Spec needs update | `trellis-update-spec` |

[/codex-inline]

### DO NOT skip skills

[Claude Code, codex-sub-agent]

| What you're thinking | Why it's wrong |
|---|---|
| "This is simple, I'll just code it in the main thread" | Dispatching `trellis-implement` is the cheap path; skipping it tempts you to write code in the main thread and lose spec context — sub-agents get `implement.jsonl` injected, you don't |
| "I already thought it through in plan mode" | Plan-mode output lives in memory — sub-agents can't see it; must be persisted to prd.md |
| "I already know the spec" | The spec may have been updated since you last read it; the sub-agent gets the fresh copy, you may not |
| "Code first, check later" | `trellis-check` surfaces issues you won't notice yourself; earlier is cheaper |

[/Claude Code, codex-sub-agent]

[codex-inline]

| What you're thinking | Why it's wrong |
|---|---|
| "This is simple, just code it" | Simple tasks often grow complex; `trellis-before-dev` takes under a minute and loads the spec context you'll need |
| "I already thought it through in plan mode" | Plan-mode output lives in memory — must be persisted to prd.md before code |
| "I already know the spec" | The spec may have been updated since you last read it; read again |
| "Code first, check later" | `trellis-check` surfaces issues you won't notice yourself; earlier is cheaper |

[/codex-inline]

### Loading Step Detail

At each step, run this to fetch detailed guidance:

```bash
python3 ./.trellis/scripts/get_context.py --mode phase --step <step>
# e.g. python3 ./.trellis/scripts/get_context.py --mode phase --step 1.1
```

---

## Phase 1: Plan

Goal: figure out what to build, produce a clear requirements doc and the context needed to implement it.

#### 1.0 Create task `[required · once]`

Create the task directory (status enters `planning`, the session active-task pointer auto-targets the new task when session identity is available):

```bash
python3 ./.trellis/scripts/task.py create "<task title>" --slug <name>
```

`--slug` is the human-readable name only. Do **not** include the `MM-DD-` date prefix; `task.py create` adds that prefix automatically.

After this command succeeds, the per-turn breadcrumb auto-switches to `[workflow-state:planning]`, telling the AI to enter the brainstorm + jsonl curation phase.

⚠️ **Run only `create` here — do not also run `start`**. Starting switches the breadcrumb to the implementation phase before brainstorm + context are done. Save the mode-appropriate start for step 1.4.

Skip when `python3 ./.trellis/scripts/task.py current --source` already points to a task.

#### 1.1 Requirement exploration `[required · repeatable]`

Load the `trellis-brainstorm` skill. For product or task PRD work, also read
`.trellis/spec/project/prd-governance.md` before changing the PRD.

The brainstorm skill will guide you to:
- Ask one question at a time
- Prefer researching over asking the user
- Prefer offering options over open-ended questions
- Update `prd.md` immediately after each user answer

Return to this step whenever requirements change and revise `prd.md`.

#### 1.2 Research `[optional · repeatable]`

Research can happen at any time during requirement exploration. It isn't limited to local code — you can use any available tool (MCP servers, skills, web search, etc.) to look up external information, including third-party library docs, industry practices, API references, etc.

[Claude Code, codex-sub-agent]

Spawn the research sub-agent:

- **Agent type**: `trellis-research`
- **Task description**: Research <specific question>
- **Key requirement**: Research output MUST be persisted to `{TASK_DIR}/research/`

[/Claude Code, codex-sub-agent]

[codex-inline]

Do the research in the main session directly and write findings into `{TASK_DIR}/research/`. (For `codex-inline` this avoids the `fork_turns="none"` isolation that prevents `trellis-research` sub-agents from resolving the active task path.)

[/codex-inline]

**Research artifact conventions**:
- One file per research topic (e.g. `research/auth-library-comparison.md`)
- Record third-party library usage examples, API references, version constraints in files
- Note relevant spec file paths you discovered for later reference

Brainstorm and research can interleave freely — pause to research a technical question, then return to talk with the user.

**Key principle**: Research output must be written to files, not left only in the chat. Conversations get compacted; files don't.

#### 1.3 Configure context `[required · once]`

[Claude Code, codex-sub-agent]

Curate `implement.jsonl` and `check.jsonl` so the Phase 2 sub-agents get the right spec context. These files were seeded on `task create` with a single self-describing `_example` line; your job here is to fill in real entries.

**Location**: `{TASK_DIR}/implement.jsonl` and `{TASK_DIR}/check.jsonl` (already exist).

**Format**: one JSON object per line — `{"file": "<path>", "reason": "<why>"}`. Paths are repo-root relative.

**What to put in**:
- **Spec files** — `.trellis/spec/<package>/<layer>/index.md` and any specific guideline files (`error-handling.md`, `conventions.md`, etc.) relevant to this task
- **Research files** — `{TASK_DIR}/research/*.md` that the sub-agent will need to consult

**What NOT to put in**:
- Code files (`src/**`, `packages/**/*.ts`, etc.) — those are read by the sub-agent during implementation, not pre-registered here
- Files you're about to modify — same reason

**Split between the two files**:
- `implement.jsonl` → specs + research the implement sub-agent needs to write code correctly
- `check.jsonl` → specs for the check sub-agent (quality guidelines, check conventions, same research if needed)

**How to discover relevant specs**:

```bash
python3 ./.trellis/scripts/get_context.py --mode packages
```

Lists every package + its spec layers with paths. Choose the entries that match this task's domain.

**How to append entries**:

Either edit the jsonl file directly in your editor, or use:

```bash
python3 ./.trellis/scripts/task.py add-context "$TASK_DIR" implement "<path>" "<reason>"
python3 ./.trellis/scripts/task.py add-context "$TASK_DIR" check "<path>" "<reason>"
```

Delete the seed `_example` line once real entries exist (optional — it's skipped automatically by consumers).

Skip when: `implement.jsonl` has agent-curated entries (the seed row alone doesn't count).

[/Claude Code, codex-sub-agent]

[codex-inline]

Skip this step. Context is loaded directly by the `trellis-before-dev` skill in Phase 2.

[/codex-inline]

#### 1.4 Activate task `[required · once]`

Once the PRD has an accepted commit/path/REQ binding, the user explicitly
authorizes start, and 1.3 jsonl curation is done, inspect
`task.json.meta.workflow_mode`. For TaskRun, admit or exactly reopen one run:

```bash
python3 ./.trellis/scripts/task.py start <task-dir> --taskrun-input <json>
```

The input is the exact accepted execution binding described in [`.trellis/spec/project/taskrun-runtime.md`](spec/project/taskrun-runtime.md). After admission projects `status=running`, the breadcrumb auto-switches to `[workflow-state:in_progress]`. For a stored non-TaskRun mode, run `task.py start <task-dir>` and retain its historical status transition.

If session identity is unavailable, TaskRun admission still succeeds but the
non-authoritative active-task pointer is not persisted. Stored legacy tasks
retain their session-identity requirement.

#### 1.5 Completion criteria

| Condition | Required |
|------|:---:|
| `prd.md` exists | ✅ |
| User confirms requirements | ✅ |
| Accepted Git commit + PRD path(s) + REQ IDs are recorded | ✅ |
| The stored mode's start command has run (TaskRun status = running) | ✅ |
| `research/` has artifacts (complex tasks) | recommended |
| `info.md` technical design (complex tasks) | optional |

[Claude Code, codex-sub-agent]

| `implement.jsonl` has agent-curated entries (not just the seed row) | ✅ |

[/Claude Code, codex-sub-agent]

---

## Phase 2: Execute

Goal: turn the prd into code that passes quality checks.

#### 2.1 Implement `[required · repeatable]`

[Claude Code]

Spawn the implement sub-agent:

- **Agent type**: `trellis-implement`
- **Task description**: Implement the requirements per prd.md, consulting materials under `{TASK_DIR}/research/`; finish by running project lint and type-check
- **Dispatch prompt guard**: Tell the spawned agent it is already the `trellis-implement` sub-agent and must implement directly, not spawn another `trellis-implement` / `trellis-check`.

The platform hook/plugin auto-handles:
- Reads `implement.jsonl` and injects the referenced spec files into the agent prompt
- Injects prd.md content

[/Claude Code]

[codex-sub-agent]

Spawn the implement sub-agent:

- **Agent type**: `trellis-implement`
- **Task description**: Implement the requirements per prd.md, consulting materials under `{TASK_DIR}/research/`; finish by running project lint and type-check
- **Dispatch prompt guard**: The prompt MUST start with `Active task: <task path>`, then explicitly say the spawned agent is already `trellis-implement` and must implement directly without spawning another `trellis-implement` / `trellis-check`.

The Codex sub-agent definition auto-handles the context load requirement:
- Resolves the active task with `task.py current --source`, then reads `prd.md` and `info.md` if present
- Reads `implement.jsonl` and requires the agent to load each referenced spec file before coding

[/codex-sub-agent]

[codex-inline]

1. Load the `trellis-before-dev` skill to read project guidelines
2. Read `{TASK_DIR}/prd.md` for requirements
3. Consult materials under `{TASK_DIR}/research/`
4. Implement the code per requirements
5. Run project lint and type-check

[/codex-inline]

#### 2.2 Quality check `[required · repeatable]`

[Claude Code, codex-sub-agent]

Spawn the check sub-agent:

- **Agent type**: `trellis-check`
- **Task description**: Review all code changes against spec and prd; fix any findings directly; ensure lint and type-check pass
- **Dispatch prompt guard**: Tell the spawned agent it is already the `trellis-check` sub-agent and must review/fix directly, not spawn another `trellis-check` / `trellis-implement`.

The check agent's job:
- Review code changes against specs
- Auto-fix issues it finds
- Run lint and typecheck to verify

[/Claude Code, codex-sub-agent]

[codex-inline]

Load the `trellis-check` skill and verify the code per its guidance:
- Spec compliance
- lint / type-check / tests
- Cross-layer consistency (when changes span layers)

If issues are found → fix → re-check, until green.

[/codex-inline]

#### 2.3 Rollback `[on demand]`

- `check` reveals a prd defect → return to Phase 1, fix `prd.md`, then redo 2.1
- Implementation went wrong → revert code, redo 2.1
- Need more research → research (same as Phase 1.2), write findings into `research/`

---

## Phase 3: Finish

Goal: ensure code quality, capture lessons, record the work.

#### 3.1 Quality verification `[required · repeatable]`

Load the `trellis-check` skill and do a final verification:
- Spec compliance
- lint / type-check / tests
- Cross-layer consistency (when changes span layers)

If issues are found → fix → re-check, until green.

#### 3.2 Debug retrospective `[on demand]`

If this task involved repeated debugging (the same issue was fixed multiple times), load the `trellis-break-loop` skill to:
- Classify the root cause
- Explain why earlier fixes failed
- Propose prevention

The goal is to capture debugging lessons so the same class of issue doesn't recur.

#### 3.3 Spec update `[required · once]`

Load the `trellis-update-spec` skill and review whether this task produced new knowledge worth recording:
- Newly discovered patterns or conventions
- Issues you hit
- New technical decisions

Update the docs under `.trellis/spec/` accordingly. Even if the conclusion is "nothing to update", walk through the judgment.

#### 3.4 Commit changes `[required · once]`

The AI drives a batched commit of this task's code changes so `/finish-work` can run cleanly afterwards. Goal: produce work commits FIRST, then bookkeeping (archive + journal) commits land after — never interleaved.

**Step-by-step**:

1. **Inspect dirty state**:
   ```bash
   git status --porcelain
   ```
   Snapshot every dirty path. If the working tree is clean, skip to 3.5.

2. **Learn commit style** from recent history (so drafted messages blend in):
   ```bash
   git log --oneline -5
   ```
   Note the prefix convention (`feat:` / `fix:` / `chore:` / `docs:` ...), language (中文/English), and length style.

3. **Classify dirty files into two groups**:
   - **AI-edited this session** — files you wrote/edited via Edit/Write/Bash tool calls in this session. You know what changed and why.
   - **Unrecognized** — dirty files you did NOT touch this session (could be the user's manual edits, leftover WIP from a previous session, or unrelated work). Do NOT silently include these.

4. **Draft a commit plan**. Group AI-edited files into logical commits (1 commit per coherent change unit, not 1 commit per file). Each entry: `<commit message>` + file list. List unrecognized files separately at the bottom.

5. **Present the plan once, ask for one-shot confirmation**. Format:
   ```
   Proposed commits (in order):
     1. <message>
        - <file>
        - <file>
     2. <message>
        - <file>

   Unrecognized dirty files (NOT in any commit — confirm include/exclude):
     - <file>
     - <file>

   Reply 'ok' / '行' to execute. Reply with edits, or '我自己来' / 'manual' to abort.
   ```

6. **On confirmation**: run `git add <files>` + `git commit -m "<msg>"` for each batch in order. Do not amend. Do not push.

7. **On rejection** (user replies "不行" / "我自己来" / "manual" / any pushback on the plan): stop. Do not attempt a second plan. The user will commit by hand; you skip ahead to 3.5 once they confirm.

**Rules**:
- No `git commit --amend` anywhere — three-stage three-commit flow (work commits → archive commit → journal commit).
- Never push to remote in this step.
- If the user wants different message wording but accepts the file grouping, edit the message and re-confirm once — but if they reject the grouping, exit to manual mode.
- The batched plan is one prompt; do not prompt per commit.

#### 3.5 Wrap-up reminder

After the above, remind the user they can run `/finish-work` to wrap up (archive the task, record the session).

---

## Customizing Trellis (for forks)

This section is for developers who want to modify the Trellis workflow itself. All customization is done by editing this file; the scripts are parsers only.

### Changing what a step means

Edit the corresponding step's walkthrough body in the Phase 1 / 2 / 3 sections above. **Critical constraint**: if you change a step's `[required · once]` marker or add a new `[required · once]` step, you MUST also add a matching enforcement line to that phase's `[workflow-state:STATUS]` tag block — otherwise the per-turn breadcrumb omits the reinforcement, and the AI silently skips the step. The regression tests assert this.

All 4 tag blocks live in the `## Phase Index` section above, immediately after each phase summary:

| Scope | Corresponding tag |
|---|---|
| No active task (before Phase 1) | `[workflow-state:no_task]` (after the Phase Index ASCII art) |
| All of Phase 1 (task created → ready for implementation) | `[workflow-state:planning]` (after Phase 1 summary) |
| Phase 2 + Phase 3.1–3.4 (implementation + check + wrap-up) | `[workflow-state:in_progress]` (after Phase 2 summary) |
| After TaskRun close or legacy Phase 3.5 archive | `[workflow-state:completed]` (after Phase 3 summary) |

### Changing the per-turn prompt text

Directly edit the body of the corresponding `[workflow-state:STATUS]` block. After editing, run `trellis update` (if you're a template maintainer) or restart your AI session (if you're customizing your own project) — no script changes required.

### Adding a custom status

Add a new block:

```
[workflow-state:my-status]
your per-turn prompt text
[/workflow-state:my-status]
```

Constraints:
- STATUS charset: `[A-Za-z0-9_-]+` (underscores and hyphens allowed, e.g. `in-review`, `blocked-by-team`)
- For a legacy workflow, a lifecycle hook must write `task.json.status` to your custom value, otherwise the tag is never read
- Legacy lifecycle hooks live in `task.json.hooks.after_*` and bind to one of `after_create / after_start / after_finish / after_archive`; TaskRun create, start, and finish do not invoke them

### Adding a legacy lifecycle hook

Add a `hooks` field to your `task.json`:

```json
{
  "hooks": {
    "after_finish": [
      "your-script-or-command-here"
    ]
  }
}
```

Legacy events are `after_create / after_start / after_finish / after_archive`.
For those workflows, `after_finish` ≠ a status change (it only clears the
active-task pointer); use `after_archive` for "task is done" notifications.
TaskRun does not invoke these configurable hooks.

### Full contract

For the workflow state machine's runtime contract, the locations of all status writers, pseudo-statuses (`no_task` / `stale_<source_type>`), the hook reachability matrix, and other deep details, see:

- `.trellis/workflow.md` — workflow-state prompt blocks and phase rules
- `.codex/hooks/inject-workflow-state.py` — actual Codex parser (reads workflow.md only, no embedded text)
