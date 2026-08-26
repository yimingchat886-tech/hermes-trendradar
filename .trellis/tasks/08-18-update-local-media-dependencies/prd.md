# Update local media dependencies

## Intent Snapshot

Inventory the local v2.0 Extra runtime dependency chain before mutation, update only outdated user-scoped dependencies without changing system package channels, and verify the installed downloader healthcheck. Update yt-dlp from 2026.03.17 to the latest published 2026.07.04 release via the existing uv tool manager; leave current apt-managed tools and reference-only/downstream tools unchanged.

## Requirements

- `UPDATE-LOCAL-MEDIA-DEPENDENCIES-REQ-001` [owner: codex]: Inventory the local v2.0 Extra runtime dependency chain before mutation, update only outdated user-scoped dependencies without changing system package channels, and verify the installed downloader healthcheck. Update yt-dlp from 2026.03.17 to the latest published 2026.07.04 release via the existing uv tool manager; leave current apt-managed tools and reference-only/downstream tools unchanged.

## Logical Checks

- `trellis.unittest.focused`
- `trellis.diff.check`
