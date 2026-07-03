# brainstorm: production deployment handoff

## Goal

Create a concise handoff document that summarizes the current Hermes stock repo state and gives a production-deployment handoff path for Hermes.

## What I already know

- Repo is clean on `main` at the start of this task.
- Current repo is a small Python-only benchmark-account tracking slice under `hermes_benchmark/`.
- Source PRD is `docs/PRD/releases/PRD_v1.3.md`, indexed by `docs/PRD/PRD_MASTER.md`.
- v1 scope is benchmark account tracking only: account registry, MediaCrawler import, local Whisper transcript boundary, Hermes decomposition outputs, Feishu dry-run mapping, daily digest/ops alerts, and an external runtime smoke boundary.
- Hotspot/RSS/TrendRadar, live Feishu writes, scheduler/daemon/queue, custom Feishu adapter, RAG/vector storage, and long-term raw video storage are out of current v1 implementation.
- External runtime layout is documented in `docs/runbooks/external-runtime-smoke.md`; MediaCrawler and openai-whisper stay outside this repo under `/home/jym/workspace/_external`.
- Child 8 real smoke evidence exists for one Douyin detail run and one Whisper transcript proof, kept outside the repo.

## Requirements

- Summarize current repo status, implemented modules, completed child work, and known non-goals.
- Produce a deployment-oriented handoff document rather than new runtime code.
- Produce a gap report against the user's intended production target: independently deployable Hermes-callable tool that tracks benchmark accounts, transcribes, decomposes, creates cards/topic-pool supplements, and updates Feishu on schedule.
- Keep any production-secrets, cookies, login state, raw videos, external source trees, venvs, and model caches out of the repo.
- Use the "current handoff boundary" approach: clearly separate what is ready today from fixture/dry-run/smoke-only pieces and deployment blockers.
- Answer the current MCP/CLI question explicitly: no production MCP server or production CLI is implemented in this repo today.

## Open Questions

- None for the first handoff draft.

## Acceptance Criteria

- [x] Document states what is production-ready today versus what is only a fixture/dry-run/smoke boundary.
- [x] Document lists required external runtime layout, secrets/local inputs, checks, and evidence retention rules.
- [x] Document lists current verification commands and unresolved deployment blockers.
- [x] Document avoids inventing unimplemented services or broad architecture.
- [x] Report estimates current completion and names the concrete gaps to the user's production target.

## Output

- `docs/runbooks/production-deployment-handoff.md`
- `docs/reports/repo-completion-gap-report.md`

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
