# Child PRD: v1.4 Douyin Collection Runner

## Parent

- Parent task: `.trellis/tasks/07-01-parent-v1-4-benchmark-productionization`
- Parent requirements: P14-REQ-040

## Goal

Run collection for 10 enabled Douyin accounts from local profile through the existing external MediaCrawler boundary, normalize output, and isolate per-account failures.

## Requirements

- Read enabled Douyin accounts from validated profile output.
- Use the confirmed account set: Ai小白Lab, 阿川同学, 柱子哥TzFilm, 懂点大模型, 晓辉博士, 马克的技术工作坊, Josh的AI笔记, 山海有灵AI, KK学姐, 木子不写代码.
- Resolve CDP/runtime refs without logging secrets.
- Call external MediaCrawler through process adapter boundaries, not vendored source.
- Normalize output into accepted content rows.
- Upsert through the content ledger.
- Continue on single-account failure and record deterministic account/content errors.

## Out of Scope

- Scheduler.
- Xiaohongshu production path.
- Transcription.
- Live Feishu writes.

## Acceptance Criteria

- [ ] 10 configured Douyin accounts are attempted or clearly skipped with errors.
- [ ] Each account has one terminal status: `attempted_success`, `attempted_failed`, or `skipped_with_error`.
- [ ] Single-account failure does not fail the entire batch by default.
- [ ] Normalized rows can enter the dedup ledger.
- [ ] Secrets/endpoints are redacted from logs/artifacts.
- [ ] Real MediaCrawler execution remains gated unless local inputs and explicit approval exist.

## Risk Level

- T3: external runtime and platform collection.
- High-risk trial PLAN: yes.
- Oracle required: yes before real external execution.

## Completion Signal

After implementation and report, stop and wait for user completion signal before commit or soft archive.
