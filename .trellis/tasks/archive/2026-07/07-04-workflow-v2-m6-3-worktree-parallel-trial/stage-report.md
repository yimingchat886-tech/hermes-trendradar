# Stage Report: M6-3 worktree parallel trial

## Acceptance

- [x] Operator guide section 4 has the minimal M6 worktree/branch, claim/release, negative smoke, verification, and cleanup command sequence.
- [x] CC and Codex worktrees/branches were observed as separate checkouts.
- [x] Claim-guard negative smoke blocked a cc edit attempt against codex-owned M6-2 path with exit 2.
- [x] Parent integration merged M6-2 and M6-3 with no cross-child file pollution outside declared task/runbook/protocol surfaces.

## Parallel Trial Evidence

### Worktrees

```text
/home/jym/workspace/Hermes stock       73b0a42 [codex/workflow-v2-m4-board]
/home/jym/workspace/Hermes-stock-m6-2  73b0a42 [codex/workflow-v2-m6-2-merge-collision-protocol]
/home/jym/workspace/Hermes-stock-m6-3  73b0a42 [cc/workflow-v2-m6-3-worktree-parallel-trial]
```

### CC worktree status before edits

```text
## cc/workflow-v2-m6-3-worktree-parallel-trial
 M .trellis/tasks/07-04-workflow-v2-m6-3-worktree-parallel-trial/state-events.jsonl
 M .trellis/tasks/07-04-workflow-v2-m6-3-worktree-parallel-trial/task.json
 M BOARD.md
```

The pre-existing dirty files above are task claim / board metadata in the M6-3 allowed scope, not M6-2 files.

### Board summary

```text
owner=unknown
BOARD active=4 waiting=0 stale=0 recent_archives=30
Active: 07-02-mediacrawler-long-running-stability, 07-04-workflow-v2-m6-2-merge-collision-protocol, 07-04-workflow-v2-m6-3-worktree-parallel-trial ...
Waiting: none
Stale >48h: none
Recent archives: 07-03-prd-pre-design-input-skill, 07-03-staged-acceptance-commit-archive-rule, 07-04-workflow-v2-mechanization
```

### Claim-guard negative smoke

Command:

```bash
printf '{"tool_input":{"file_path":".trellis/scripts/conflict_checklist.py"}}' | env TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py
```

Result: expected exit 2.

```text
G5 claim guard: .trellis/scripts/conflict_checklist.py belongs to 07-04-workflow-v2-m6-2-merge-collision-protocol owner=codex; current=cc
```

## Verification

- `sed -n '72,120p' docs/runbooks/workflow-v2-operator-guide.md`: pass
- `python3 ./.trellis/scripts/board.py --summary --max-lines 10`: pass
- `git worktree list`: pass
- `git status --short --branch`: pass
- `python3 ./.trellis/scripts/conflict_checklist.py --repo .`: pass during the real merge conflict; both conflicted files mapped to M6-2 + M6-3.
- `git diff --check`: pass

## User Completion Signal

- Raw signal: `真实两分支冲突演练、合并后无污染确认、验收/提交/soft archive`
- Allows commit: yes
- Allows soft archive: yes
- Explicit limits: none
- Push allowed: no

## CI Run / Staging Verification

- CI run: not applicable for this docs/evidence slice.
- Workflow head_sha: not applicable.
- Expected merge SHA: 8398979
- Staging URL: not applicable.
- Playwright smoke: not applicable.
