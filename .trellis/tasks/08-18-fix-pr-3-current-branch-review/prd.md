# Fix PR 3 current branch review

## Intent Snapshot

Address all unresolved Codex review findings on PR #3: pin direct-download connections to validated public addresses while preserving Host and TLS hostname verification; remove only obsolete legacy Trellis tests that import deleted modules; preserve v2.0 Extra behavior; verify full CI and publish pre.2 only after green.

## Requirements

- `FIX-PR-3-CURRENT-BRANCH-REVIEW-REQ-001` [owner: codex]: Address all unresolved Codex review findings on PR #3: pin direct-download connections to validated public addresses while preserving Host and TLS hostname verification; remove only obsolete legacy Trellis tests that import deleted modules; preserve v2.0 Extra behavior; verify full CI and publish pre.2 only after green.

## Logical Checks

- `trellis.unittest.focused`
- `trellis.diff.check`
