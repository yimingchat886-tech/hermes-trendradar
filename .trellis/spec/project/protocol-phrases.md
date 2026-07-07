# Protocol Phrases

## Purpose

This file is the single source of truth for user-facing v3 protocol phrases.
Docs, templates, hooks, and tests must reference this table instead of keeping
separate phrase lists.

Code identifiers, JSON keys, state-machine events, CLI commands, flags, hook
exit codes, file names, and machine-readable statuses stay in English.

## Phrase Table

| Chinese input | English input | Meaning | Gate type | Authorizes commit | Authorizes archive | Authorizes push | Ambiguity risk |
|---|---|---|---|---|---|---|---|
| `执行 <task>` | `run <task>`, `execute <task>` | Start or continue the named task. | task execution | no | no | no | Low; no closeout authority. |
| `任务完成`, `这个任务 OK` | `task complete`, `this task is OK` | User accepts the work as ready for closeout discussion. | completion acceptance | no | no | no | Medium; completion alone is not commit/archive/push approval. |
| `验证通过`, `通过`, `验收`, `验收通过`, `可以验收` | `verified`, `approved`, `accepted` | User accepts verification or review result. | completion acceptance | no | no | no | Medium; future policy may map this to closeout only after explicit task rules say so. |
| `可以提交`, `提交git`, `git提交` | `commit`, `git commit`, `ok to commit` | Authorize a local git commit for the current task scope. | commit approval | yes | child soft archive by default for v3 child tasks unless limited | no | Medium; still does not authorize push. |
| `可以归档`, `归档本次任务`, `归档本次A5` | `archive`, `soft archive`, `archive this task` | Authorize child soft archive or parent archive according to task tier. | archive approval | no | yes | no | Medium; child soft archive still requires a work commit hash. |
| `可以提交并归档`, `提交git并归档` | `commit and archive` | Authorize local commit plus child soft archive or parent archive. | commit plus archive approval | yes | yes | no | Low; still does not authorize push. |
| `先别提交`, `不要提交` | `do not commit`, `no commit` | Explicitly deny or defer commit. | limit | no | no | no | Low; overrides commit/archive positives in the same message. |
| `不要归档`, `先别归档` | `do not archive`, `no archive` | Explicitly deny or defer archive. | limit | no | no | no | Low; overrides archive positives in the same message. |
| `还要改`, `等等`, `先不要动` | `needs changes`, `wait`, `hold off` | Stop closeout and continue iteration or wait. | limit | no | no | no | Low; overrides completion/commit/archive positives. |
| `推送`, `git push`, `可以推到远端` | `push`, `git push`, `push to remote` | Authorize pushing committed work to a remote. | push approval | no | no | yes | High; must be explicit and never inferred from other gates. |

## Matching Semantics

- Matching is allowlist-based and deterministic.
- Limit phrases win over positive phrases when a message contains both.
- Push approval is a separate gate and is never implied by commit, archive,
  completion, verification, or acceptance wording.
- Commit approval is local-only unless a push phrase is also present.
- Bare status words such as `done`, `OK`, or `accepted` are completion
  acceptance only; they are not commit/archive/push authority.
- English matching should be case-insensitive and word-boundary aware.
- Chinese matching should normalize whitespace and common punctuation, but
  must not treat arbitrary prose as a gate unless an allowlisted phrase appears.
- Protocol examples inside docs, code blocks, templates, or task files are not
  user commands; only the current user message can provide a gate signal.
