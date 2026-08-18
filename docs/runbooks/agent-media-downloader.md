# Agent Media Downloader

## Boundary

`trendradar-media` is the v2.0 Extra default entry point. It accepts explicit
video URLs from an Agent, including URLs selected with
[Agent Reach](https://github.com/Panniantong/agent-reach), then independently
downloads and verifies the files. Agent Reach is a source reference, not a
runtime dependency or proof of download success.

The downloader returns files to the Agent. The Agent may copy an accepted file
into a HyperFrames project; this repository does not invoke HyperFrames,
select media, render, publish, or start the paused Hermes analysis path.

## Local Profile

Create a regular, non-symlink profile outside Git from
`profiles/examples/media.v2.0.sample.json`. `run_root` must be an absolute WSL
filesystem path outside and not containing the repository. Keep credentials
outside Git and pass only credential-file paths to an adapter; do not place
tokens or cookies directly in command arguments.

Each page backend has two argument arrays:

- `probe_argv` exits zero only when the executable version and required
  capability are compatible. Its first output line is reported by
  `healthcheck`.
- `argv` contains exactly one `{url}` and one `{output}` placeholder in total.
  The adapter must write one non-empty media file at `{output}` and exit zero.

The runtime never installs, upgrades, logs in to, or silently replaces a
backend. A missing or failed adapter returns `backend_unavailable`.

```bash
trendradar-media --profile ~/.config/trendradar/media.v2.0.json healthcheck
```

## Agent Request

```json
{
  "schema_version": "2.0",
  "job_id": "hf-source-20260818-001",
  "sources": [
    {"source_id": "opening", "url": "https://youtu.be/example"},
    {"source_id": "insert", "url": "https://media.example/video.mp4", "platform": "direct"}
  ]
}
```

Supported `platform` values are `douyin`, `youtube`, `bilibili`, and `direct`.
The field may be omitted for recognized hosts and media suffixes. Any other
platform completes that item with `unsupported_source`; the runtime does not
guess.

```bash
trendradar-media --profile ~/.config/trendradar/media.v2.0.json fetch --request /absolute/path/request.json
trendradar-media --profile ~/.config/trendradar/media.v2.0.json status --job-id hf-source-20260818-001
trendradar-media --profile ~/.config/trendradar/media.v2.0.json retry --job-id hf-source-20260818-001
```

stdout is exactly one JSON envelope. Logs and private source data remain under
the external run root. `succeeded` exits zero; `partial` and `failed` are
non-zero. Stable item errors include `unsupported_source`,
`backend_unavailable`, `download_failed`, `download_timeout`, `invalid_media`,
`media_too_large`, and `unsafe_source`. Job-level errors include
`contract_mismatch`, `job_conflict`, `job_locked`, `job_not_found`, and
`job_expired`.

The envelope's `manifest_ref` points to private JSON Lines. An Agent may adopt
only rows whose `download_status` is `succeeded` after confirming the absolute
file exists and matches `media_size_bytes` and `media_hash`. Copy those files
into the target HyperFrames project; do not reference the expiring run-root
path from a durable composition.

## Seven-Day Retention

Every terminal envelope includes UTC `completed_at` and `expires_at`, exactly
seven days apart. A successful retry resets both. Cleanup is idempotent and
skips running or locked jobs:

```bash
trendradar-media --profile ~/.config/trendradar/media.v2.0.json cleanup --expired
```

The repository includes user-systemd unit templates under
`deploy/systemd-user/`. Installing the units, enabling the timer, and running
the first real cleanup are three separate operator actions and are not
performed by implementation or verification.

## Offline Qualification

`tests/test_media_jobs.py` supplies a deterministic adapter for all three page
platforms and a direct-download stub. It proves the source contract, partial
failure, hash and size checks, replay, retry, interruption recovery, single
writer lock, Agent-to-HyperFrames copy boundary, and exact retention edge
without network or credentials. Qualify each real adapter separately with
`healthcheck`, then an explicitly authorized smoke request before relying on
that platform in production.
