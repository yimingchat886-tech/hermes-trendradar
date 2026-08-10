---
name: trellis-continue
description: "Resume the same Unified Intent Loop TaskRun from authoritative status."
---

# Continue Task

1. Resolve the exact task ID from the current conversation or authoritative
   status. Do not infer it from BOARD or stale task JSON.
2. Run:

       python3 ./.trellis/scripts/task.py status --task <task-id> --json

3. Route by work_state:
   - ready: run the task; Loop is default.
   - running: claim and continue ready actions.
   - human_blocked: present the persisted semantic/resource blocker and wait
     for the missing authority or evidence.
   - verified: report checks and wait for closeout authority.
   - completed with cleanup_pending: resume the same closeout saga only.
   - completed/cancelled and clean: no lifecycle work remains.
4. Resume uses:

       python3 ./.trellis/scripts/task.py resume --task <task-id>

Never create a successor, repair, review-fix, archive-fix, or cleanup Task.
