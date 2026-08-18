---
name: trellis-meta
description: "Understand or customize this repository's Unified Intent Loop harness."
---

# Trellis Meta

Read `.trellis/workflow.md`, `.trellis/spec/project/index.md`, `README.md`, and
`HANDOFF.md` before changing the harness. The active lifecycle is implemented by
`.trellis/scripts/task.py` and `.trellis/scripts/taskrun/`; Git-common-dir SQLite
is authoritative and file views are projections. Legacy lifecycle code is
read-only migration evidence and must not be restored as an active writer.
