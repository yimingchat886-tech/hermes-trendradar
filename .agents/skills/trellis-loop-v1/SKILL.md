---
name: trellis-loop-v1
description: "Read sealed pre-cutover Loop v1 evidence after Unified Intent Loop hard cutover."
---

# Legacy Loop Reader

Loop v1 is sealed evidence. It has no active admission, advance, recovery,
integration, cancellation, qualification, or archive writer.

Use only:

    python3 ./.trellis/scripts/task.py legacy-status

Report compact path, status, disposition, and digest facts. Never open or edit
legacy SQLite/ledgers, synthesize current authority, call removed orchestrators,
or restore one writer. All old writer commands must return
LEGACY_WRITE_DISABLED before mutation.

Rollback after cutover is whole-commit revert plus the matching shared DB
backup. Partial mixed-mode rollback is prohibited.
