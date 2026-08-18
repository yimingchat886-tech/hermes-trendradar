---
name: trellis-check
description: "Read-only review of a complete Unified Intent Loop candidate."
---

# Unified Candidate Check

1. Read the accepted PRD and authoritative `task.py status --json` snapshot.
2. Confirm every action is passed and all bound deterministic checks are green.
3. Review the complete candidate without editing files, Git state, specs, or SQLite.
4. Report findings only. Each finding needs severity, category, REQ IDs, and path scope.
5. Only correctness, security, data loss, accepted REQ, public compatibility, or
   deterministic-proof mismatch may block `VERIFIED`.
6. Recheck historical blocking findings and report closure evidence separately.

The Check Agent is structurally read-only. It never self-fixes, commits, spawns
work, creates Tasks, or performs external effects.
