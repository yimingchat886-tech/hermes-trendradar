# Child PRD: v1.4 Profile And Local Config

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-020, P14-REQ-040

## Goal

Load and validate manually edited local v1.4 profile files for runtime, accounts, transcription, analysis, and Feishu allowlist refs.

## Requirements

- Support a main runtime profile that references child profiles.
- Support local account profile input with exactly 10 enabled Douyin accounts for production validation.
- Reject enabled non-Douyin accounts in production profile.
- Detect plaintext cookies, tokens, CDP endpoints, proxy URLs, passwords, and login state.
- Allow only refs such as `env:`, `file:`, `secret:`, `vault:`, or redacted runtime refs for sensitive values.
- Produce stable profile hash and validation summary.
- Keep real secrets and local production profiles out of committed fixtures unless redacted/sample-only.

## Out of Scope

- Reading accounts from Feishu table 3.
- Connecting to CDP or Feishu.
- Running collection.

## Acceptance Criteria

- [ ] Valid local v1.4 Douyin profile reports 10 enabled accounts.
- [ ] Plaintext sensitive values fail validation.
- [ ] Profile hash is deterministic for equivalent input.
- [ ] `--config` remains a compatibility alias for `--profile` if implemented here.

## Risk Level

- T2 high-risk: profile/security boundary.
- High-risk trial PLAN: conditional.
- Oracle required: conditional if profile schema or secret policy changes.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
