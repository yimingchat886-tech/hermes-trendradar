# Child PRD: MediaCrawler Collection Runner Dry-Run

## Parent

- Parent task: `.trellis/tasks/06-29-parent-1-benchmark-account-tracking`
- Parent requirements: P1-REQ-045, P1-REQ-100

## Goal

Create the collection execution boundary for benchmark accounts without doing real platform collection in this child. The child proves that the system can build a MediaCrawler command plan, run a local fake command or fixture output, and produce raw artifacts plus a run manifest for later import.

## User Decision

- Child 8 scope: dry-run plus local fake-command/fixture output.
- Parent acceptance scope: later run a real MediaCrawler complete-chain test with 1-2 user-provided accounts.

## Requirements

- Read enabled benchmark accounts from the local registry/tracking plan.
- Build a MediaCrawler command plan without invoking the real external tool by default.
- Provide dry-run mode that prints or records planned commands.
- Provide a local fake-command or fixture-output mode that simulates an external command result.
- Write raw collection artifacts under a local artifacts path.
- Write a run manifest with `run_id`, account/source IDs, platform, command, mode, artifact paths, timestamps, exit code, and stdout/stderr summaries.
- Emit source-health style warnings or failures for failed fake commands.
- Use standard library process execution when a command is actually run; do not add a scheduler, queue, worker, or new dependency.

## Out of Scope

- Real MediaCrawler calls in this child.
- Cookie, login-state, proxy, or bypass handling.
- Embedding or vendoring MediaCrawler source.
- Mapping raw results into `BenchmarkContent`; that remains child 3.
- Deduplication; that remains child 3.
- Whisper, Hermes, Feishu, scheduling, or alert delivery.

## Acceptance Criteria

- [ ] Dry-run produces a deterministic command plan for enabled accounts.
- [ ] Fake-command/fixture mode writes raw artifact files and a run manifest.
- [ ] Manifest records traceable `run_id`, account/source IDs, timestamps, mode, command, artifact path, and exit code.
- [ ] Failed fake command produces a health/failure record without crashing unrelated accounts.
- [ ] No credentials, cookies, proxy settings, or platform login material are stored.
- [ ] No real MediaCrawler command is required for child completion.

## Risk Level

- T3: external-tool execution boundary, kept safe by dry-run/fake-command scope.
- High-risk trial PLAN: yes.
- Oracle required: no for this child; parent may decide before real-call acceptance.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
