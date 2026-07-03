# Oracle PLAN Review: MediaCrawler Long-Running Stability

## Session

- Session: `mediacrawl-stability-plan-review-browser-3`
- Engine: Browser Oracle, GPT-5.5 Pro
- Transcript: `/home/jym/.oracle/sessions/mediacrawl-stability-plan-review-browser-3/artifacts/transcript.md`
- API attempt: failed because `OPENAI_API_KEY` was not set.
- Browser attachment attempt: timed out while uploading attachments.
- Final mode: browser text bundle with the PRD, task metadata, v1.4 CLI PRD, Hermes runtime PRD, and user stability plan.

## Result

Oracle said the plan was directionally compatible with v1.4 but not implementation-ready until the following blockers were fixed in `implement.md`:

- Define `healthcheck` as check-only and separate current CDP state from run eligibility.
- Add an explicit non-production `smoke-mediacrawler` surface instead of weakening production `run-daily`.
- Acquire a runtime CDP port/profile lock before preflight, launch, or MediaCrawler.
- Treat `/json/version` as necessary but insufficient; require runner-owned Chrome identity.
- Add a runtime lock conflict code mapped to exit code 9.
- Keep MediaCrawler external; do not patch or vendor source in this task.
- Expand redaction beyond WebSocket URL to endpoints, cookies, tokens, proxy, login-state paths, Chrome profile paths, external roots, raw crawler dumps, raw videos, and model cache paths.
- Split initial CDP status from final preflight status so "no Chrome before launch" is not counted as a failed final precheck.
- Derive Markdown smoke report from the JSON report.

## Accepted Decisions

- Add `smoke-mediacrawler --profile <profile> --json`.
- Keep `healthcheck --profile --json` check-only.
- Keep `validate-config` schema-only.
- Keep `run-daily` production semantics: 10 Douyin accounts, serial, failure isolated, no scheduler.
- Use stdlib/runtime helpers only; no new dependency.
- Do not edit the external MediaCrawler checkout.

## Remaining Risk

- Real smoke can still require valid local Douyin login state.
- If MediaCrawler's first-hop WebSocket behavior is not externally configurable, this task must record that limitation instead of patching MediaCrawler source.
