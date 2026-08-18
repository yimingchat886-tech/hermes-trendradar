# Legacy Loop v1 Admission

Status: sealed read-only evidence.

Unified Intent Loop v1 disabled Loop admission and every orchestration,
advance, recovery, cancellation, and archive writer. Calls return
LEGACY_WRITE_DISABLED before mutation. Historical ledgers may be summarized
but never enter active authority.
