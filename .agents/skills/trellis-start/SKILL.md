---
name: trellis-start
description: "Load Unified Intent Loop status, Git state, workflow, and project specs at session start."
---

# Start Session

1. Run:

       python3 ./.trellis/scripts/task.py status --json
       git status --short --branch

2. Read .trellis/workflow.md, .trellis/spec/guides/project-development.md,
   and .trellis/spec/project/index.md.
3. If the conversation names a Task, use that exact task ID. If one Task is
   running or human_blocked, continue it. If several could match, ask rather
   than guessing.
4. A clear small bug may use task run with title/request immediately. A complex
   product or architecture request uses trellis-brainstorm and one PRD.
5. Load trellis-before-dev before any code edit and trellis-check after
   deterministic verification.

Task, run, and action status comes from shared SQLite. BOARD, task.json, and
session pointers are projections.
