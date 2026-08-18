# Child 3: Internal Digest Message Path

## Parent

- Parent task: `07-05-parent-v2-0-real-analysis-loop`
- Parent requirements: P20-REQ-040

## Goal

Build and prove the message-first internal digest path using analyzed items, degraded/error state, and trace IDs.

## Requirements

- C3-REQ-001: Build a digest payload from real-analysis result refs and existing run/content state.
- C3-REQ-002: Include trace IDs back to run/content/package/result refs.
- C3-REQ-003: Include degraded/error status instead of silently omitting failed parts.
- C3-REQ-004: Prove one internal-group message delivery through Hermes/Feishu message channel or record an explicit blocker.

## Out of Scope

- Bitable writes.
- Hotspot/Twitter digest.
- Human feedback persistence.
- External group card flow.

## Acceptance Criteria

- [ ] Digest payload contains analyzed items and trace refs.
- [ ] Error/degraded state appears in the payload when present.
- [ ] One internal message delivery is evidenced or a blocker/fallback is recorded.
- [ ] No table write happens in this child.

## Verification Commands

- `python3 ./.trellis/scripts/task.py validate 07-05-child-3-v2-0-internal-digest-message`
- Focused digest/message tests or smoke evidence, then `git diff --check`

## In

- Internal digest payload and message delivery proof.

## Out

- Feedback, Bitable writes, v2.1 hotspot sources, and external cards.
