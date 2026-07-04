# Governance: Workflow v2 Mechanization

## Child Index

| Child | Delivery | Dependencies | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|
| M2 task.py tiers and gates | v2 templates, tier metadata, validate, soft-archive, G1/G3/G5-WARN | Codex review accepted | codex | codex/workflow-v2-m2-taskpy-gates | completed | c34665a |
| M3 PR helper and impact push gates | trellis_pr.sh, G2, G4, debugging skill update | M2 | codex | codex/workflow-v2-m3-pr-gates | completed | 2d4ed10 |
| M4 BOARD and session start | BOARD generator, shared summary, Claude/Codex SessionStart | M2 | codex | codex/workflow-v2-m4-board | completed | 211600f |

## RTM

| REQ-ID | Child | Status | Evidence |
|---|---|---|---|
| WV2-M2-REQ-001 | M2 task.py tiers and gates | completed | 07-04-workflow-v2-m2-taskpy-gates/stage-report.md |
| WV2-M2-REQ-002 | M2 task.py tiers and gates | completed | 07-04-workflow-v2-m2-taskpy-gates/stage-report.md |
| WV2-M2-REQ-003 | M2 task.py tiers and gates | completed | 07-04-workflow-v2-m2-taskpy-gates/stage-report.md |
| WV2-M3-REQ-001 | M3 PR helper and impact push gates | completed | 07-04-workflow-v2-m3-pr-gates/stage-report.md |
| WV2-M4-REQ-001 | M4 BOARD and session start | completed | 07-04-workflow-v2-m4-board/stage-report.md |

## External Review

### PRD Review (Codex, 2026-07-04)

Result: pass after accepted corrections. Review challenged scope leaks and hidden dependencies; jym accepted fixes for `task.py soft-archive`, staged diff/tree fingerprint for G3, narrowed `.trellis/` commit exemptions, default `--tier light`, `meta.workflow_mode = "harness_state_machine"`, and shared BOARD summary for Claude/Codex SessionStart.

### Closeout Review

TBD at parent closeout.

## Boundary Pass

1. `main` only receives verified results: pass; M2-M4 children use task branches and checks before merge.
2. One code child per branch: pass; initial planned branches are listed in Child Index.
3. Commit by runnable checkpoint: pass; child stage reports must list verification commands before commit.
4. Do not push private AI/Trellis runtime: pass; `.trellis/.runtime`, `.trellis/.developer`, workspace runtime, and hook markers stay local.
5. PR/merge checks cover diff, secrets, tests, and scope: pass; M3 mechanizes this with `trellis_pr.sh`.
6. External dependencies use thin adapters/clients: pass; no new production dependency is authorized by this parent.
7. Environment differences and secrets stay in config/env: pass; ordinary workflow constants stay in scripts/templates.
8. Each code child has at least one test or smoke command: pass; child PRDs must record the command.

Ponytail blocking finding: accepted as intentional workflow surface. No new dependency, no framework, no broad product rewrite. New scripts/hooks are justified only where prose rules need a machine gate.
