# Unified Intent Loop Workflow

## Source Of Truth

One user intent owns one stable Task, one persistent TaskRun, and one current
accepted PRD binding generation. The only writable lifecycle authority is:

    <git-common-dir>/trellis/harness.sqlite3

Task PRDs and summaries are tracked assets. task.json, BOARD.md, session
pointers, and archive views are rebuildable projections. They never override
SQLite.

## Core Rules

1. New implementation work defaults to Loop. Use --single only when the user
   explicitly asks for one attempt.
2. Research, implementation, verification, integration, recovery, release, and
   sync are actions in the same run. They are not Tasks.
3. Do not create child, successor, review-fix, archive-fix, cleanup, or repair
   Tasks for failures inside an accepted intent.
4. Harness stores action scope, claims, results, checks, reviews, and findings.
   Codex owns any actual sub-agent dispatch.
5. Deterministic checks run before model review. A complete high-risk candidate
   gets one read-only review, with at most one semantic re-review.
6. Push, remote deletion, publication, deployment, activation, timers, and real
   downstream sync require separate explicit authority.

## Agent CLI

    python3 ./.trellis/scripts/task.py plan --title "..." --request "..."
    python3 ./.trellis/scripts/task.py run --task <task-id>
    python3 ./.trellis/scripts/task.py run --task <task-id> --single
    python3 ./.trellis/scripts/task.py status --task <task-id> --json
    python3 ./.trellis/scripts/task.py resume --task <task-id>
    python3 ./.trellis/scripts/task.py close --task <task-id> --authorization-ref <ref>
    python3 ./.trellis/scripts/task.py cancel --task <task-id> --authorization-ref <ref>

The user normally speaks naturally; the Agent maps the request to these
commands. Legacy lifecycle commands return LEGACY_WRITE_DISABLED before any
write and point to the cutover report.

## Phase 1: Plan And Bind

### Small Bug Or Local Maintenance

When the request states the behavior and expected result:

1. Inspect the failing path and applicable specs.
2. Run task run with title and request.
3. The CLI creates one minimal PRD snapshot, one Task, and one default Loop.
4. Do not ask for another PRD or start confirmation.

### Complex Product Or Architecture Work

1. Create one draft with task plan and refine its single PRD.
2. Resolve only material product choices. Ask one question at a time.
3. “确认方案并开始执行” accepts the current revision, authorizes one scoped
   local PRD commit, appends its binding generation, and starts the same Loop.
4. “采纳”, “记录”, or “确认方向” updates Draft only.
5. Material revisions after start append a binding generation to the same run.

Bindings contain Git commit, PRD path/digest, REQ IDs, and base commit.
Generated views do not grant authority.

## Phase 2: Execute And Verify

1. Load trellis-before-dev before code changes.
2. Read authoritative status and the persistent action graph.
3. Claim only ready actions. Read-only actions may overlap; write actions with
   overlapping touches are serialized.
4. Record every attempt and deterministic check. Limits are soft 4, hard 8;
   two unchanged root-cause fingerprints cause human_blocked.
5. Compact may become Delegated in the same run. No new Task or branch appears.
6. Only complete deterministic candidates or high-risk boundaries receive a
   read-only Check Agent review.
7. Findings keep stable IDs. Only correctness, security, data loss, accepted
   REQ, compatibility, and proof failures block VERIFIED.
8. Evidence-only repairs use targeted checks, full regression, and delta guard.

Required verification:

    TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.trellis/scripts       python3 -m unittest discover -s .trellis/scripts/tests -p 'test_*.py' -q
    python3 -m compileall -q .trellis/scripts
    uvx --from ruff==0.15.20 ruff check --select E9,F63,F7,F82 .trellis/scripts
    git diff --check

Run trellis-check after deterministic checks. The reviewer is structurally
read-only and returns findings; Coordinator or Implement Agent fixes them.
Run Ponytail review before reporting VERIFIED.

## Phase 3: Verified And Closeout

At VERIFIED, report candidate, checks, migration/release/sync facts, advisories,
scoped commit plan, local merge/cleanup plan, and “Pushed: no”. Then wait for a
closeout signal from protocol-phrases.md.

For an unchanged candidate, “完成任务”, “提交git”, or equivalent authorizes the
local closeout saga: reverify, scoped commit, local merge, completion, logical
archive, pointer/runtime cleanup, worktree removal, and removal of a fully
merged local task branch. It never authorizes push or an external effect.

Failures replay the same persisted step. Product work stays completed when
post-commit cleanup enters cleanup_pending.

## Release And Sync

A Harness release is an immutable content-addressed manifest. Qualification
binds managed payload, Trellis base, capability range, logical check catalog,
and semantic suite evidence. Task, BOARD, host, path, stdout, and runtime drift
do not invalidate it.

One named multi-target sync request creates one source Sync TaskRun and one slot
per target. Qualification completes before target writes. Each target uses one
branch/worktree; unrelated dirt is preserved, managed overlap blocks before
mutation, and targets never receive a Task or TaskRun. Target commit and merge
wait for closeout. Push remains separate.

Use `task.py sync --task <task-id> --target <path> --current-source` only when
the request explicitly names current source; otherwise sync selects the latest
qualified immutable release.

## Legacy

Pre-cutover TaskRun, Parent/Child, Loop v1, and physical archive records are
sealed evidence. task legacy-status reads their compact inventory. They cannot
be started, advanced, linked, completed, archived, recovered, or used as
fallback authority.
