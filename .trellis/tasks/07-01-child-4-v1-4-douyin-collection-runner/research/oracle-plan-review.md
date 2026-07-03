# Oracle PLAN Review: Child 4 v1.4 Douyin Collection Runner

## Session

- Session: `child4-douyin-collection-plan-review-4`
- Engine: Browser Oracle, GPT-5.5 Pro
- Completed: 2026-07-02
- Attachment mode: prompt-only fallback after attachment upload timeout

## Result

Go for implementation of the runner boundary, dry-run checks, fixture checks,
normalization, ledger upsert, failure isolation, and redaction proof.

Do not perform real external Douyin / MediaCrawler execution in this child until
all external execution inputs and gates are present.

## Blocking Findings

- Real external execution is not approved by the current PLAN. It remains gated
  on validated local profile, external runtime outside the repo, passing
  dry-run/fixture checks, and explicit execution approval.
- Runtime account source must be validated profile output, not a hard-coded
  account list.
- Raw MediaCrawler output, cookies, tokens, CDP endpoints, profile paths,
  unredacted subprocess args, and raw crawler dumps must stay out of committed
  repo artifacts.
- Per-account and per-content failures need deterministic error records.

## Advisory Findings

- Serial account loop is the right minimal shape.
- Reuse existing profile validation, process/redaction helpers,
  MediaCrawler-row normalization, and SQLite ledger helpers.
- Keep scheduler, daemon, queue, concurrency, Xiaohongshu, transcription, and
  Feishu writes out of this child.
- Use `shell=False`, array commands, and redacted command summaries only.

## Acceptance Tweaks Accepted

- Each enabled account must end as `attempted_success`, `attempted_failed`, or
  `skipped_with_error`.
- Fixture/dry-run account set must match the confirmed list:
  Ai小白Lab, 阿川同学, 柱子哥TzFilm, 懂点大模型, 晓辉博士, 马克的技术工作坊,
  Josh的AI笔记, 山海有灵AI, KK学姐, 木子不写代码.
- Batch failure defaults to partial success for single-account failures; only
  systemic errors should fail the batch.
- Normalized rows must have ledger dedup keys before upsert.
- Redaction checks must prove sensitive refs are absent from logs/artifacts.
