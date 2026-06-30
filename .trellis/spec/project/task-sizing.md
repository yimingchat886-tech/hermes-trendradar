# Task Sizing

## Levels

| Level | Type | Default mode |
|---|---|---|
| T0 | Q&A, explanation, read-only analysis | No task or `default_trellis` |
| T1 | Single-file typo or low-risk supplement | `default_trellis` |
| T2 | Ordinary feature, 2-5 files, independently verifiable | `default_trellis` or `staged_overlay` by risk |
| T3 | Subphase, PRD/SPEC/RTM, parent/child delivery | `staged_overlay` |
| T4 | Harness, state machine, archive/finish, tool orchestration | `staged_overlay` |

## Rule

Do not force staged overlay onto T0/T1 work. Use staged overlay for T3/T4 and for T2 only when the risk justifies the extra artifacts.

High-risk T2 examples:

- architecture boundary changes
- new production dependency
- data model or migration changes
- security/auth/token/user-data handling
- state machine, concurrent runner, or harness/tooling behavior
- irreversible operations
- expected diff over 5 core files
