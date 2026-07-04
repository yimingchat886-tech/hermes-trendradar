# Trellis Backlog Triage — 2026-07-04

Status: Approved by jym（2026-07-04 会话确认）
Executor: Codex (GPT-5.5, xhigh)
Companion doc: `docs/reports/trellis-loop-workflow-v2.md`（M1）

## Bottom Line

27 张存量任务卡 triage 完毕：**26 张关闭/归档，1 张保活**（07-02-mediacrawler-long-running-stability）。清扫后看板只剩 1 张活卡，为 v2 工作流的 BOARD/催办上线扫清僵尸。

## Triage 决定表

| 组 | 任务 | 判定 | 依据 |
|---|---|---|---|
| A | 06-29-child-1..7、06-30-child-8（共 8 张） | 补录归档 | 全部 completed + soft_archived + 有 commit；其 parent（06-29-parent-1）已归档，这批是清扫遗漏 |
| B1 | 07-01-child-1/2/3（completed）、child-4/5/6/7、07-02-child-6a（in_progress 但 phase=soft_archived） | 修状态 → 随 parent closeout 归档 | 全部有 commit + stage-report；F6 双状态机分裂修正 |
| B2 | 07-01-child-8-v1-4-feishu-limited-live-table4、07-01-child-9-v1-4-production-hardening | 取消 | 用户决定：v1.4 closeout 收尾，8/9 取消；PRD 需求留在 docs/PRD，未来按 v2 重建 |
| B3 | 07-01-parent-v1-4-benchmark-productionization | 补 closeout 证据 → 归档 | 7/9 child 完成 + 6a；RTM 补齐后 closeout |
| C1 | 06-30-local-ai-state-versioning | 关闭归档 | 已 commit 3ac9891 + 66L stage-report，只差归档信号 |
| C2 | 07-01-production-deployment-handoff | 取消归档 | 零产物；职能并入 M5 CI/staging parent |
| C3 | 07-03-prd-pre-design-input-skill | 取消归档（superseded） | 被 workflow-v2 `/prd` skill 取代 |
| C4 | 07-03-staged-acceptance-commit-archive-rule | 取消归档（superseded） | 规则已并入 workflow-v2 §4.3 |
| C5 | 07-03-gitignore-development-hygiene | 取消归档（absorbed） | 已并入 v2 边界规则第 4 条 |
| C6 | 07-03-v2-1-0-state-engine-mvp | **先核实再关** | phase=soft_archived 但 task.json 无 commit hash；需从 git log 核实实际 commit 后补录归档；核实不到 → 报告，不归档 |
| D | 07-02-mediacrawler-long-running-stability | **保活，不动** | 唯一活跃实现任务（phase=implementation, 115L 报告），迁入 v2 流程继续 |

---

## Codex 执行工单

### 约束（先读）

1. **只动 `.trellis/tasks/` 下的文件**。不碰源码、不碰 `docs/`、不碰 `.trellis/spec|scripts|templates`。
2. **不 push**。全部改动最终合成 **一个 commit**：
   `chore(trellis): triage backlog, closeout v1.4 parent (workflow v2 M1)`
3. 所有 `task.py archive` 一律加 `--no-commit`（避免逐卡自动 commit 刷屏），最后统一提交。
4. `task.py` 的 status 枚举只有 planning/in_progress/review/completed，**没有 cancelled**。取消卡的写法：直接编辑 task.json 设 `"status": "cancelled"`，并在 `"notes"` 写明原因。若 archive 因未知 status 拒绝执行（先拿 C2 试一张），fallback：`"status": "completed"` + notes 前缀 `CANCELLED:`。
5. 07-02-mediacrawler-long-running-stability **绝对不碰**。
6. 每批做完跑一次 `python3 ./.trellis/scripts/task.py list` 核对，再进下一批。

### Batch A — 06-29 批次补录（8 张，低风险，先做）

对 06-29-child-1..7、06-30-child-8 逐张：

1. task.json 补 `"parent": "06-29-parent-1-benchmark-account-tracking"`（修 F3，归档记录里链接完整）。
2. `meta.staged_delivery.trellis_archive_completed` → `true`。
3. `python3 ./.trellis/scripts/task.py archive <dir> --no-commit`。

### Batch B — v1.4 closeout（10 张）

顺序执行：

1. **修 F6 双状态机**：child-4/5/6/7、07-02-child-6a 的 task.json：`"status": "completed"`，`"completedAt"` 取各自 `soft_archive_completed_at` 的日期部分。
2. **取消 child-8/9**：按约束 4 写 cancelled；notes：`v1.4 closeout 决定取消（2026-07-04），需求保留于 docs/PRD/releases/PRD_v1.4_*.md，未来按 workflow v2 重建`。
3. **补 parent closeout 证据**（07-01-parent-v1-4-benchmark-productionization）：
   - `rtm-delta.md`：P14-REQ-010/020/030/040/050/055/060 → Completed 表，evidence 引用各 child stage-report.md，commit 用各 child task.json 的 commit 字段；P14-REQ-070/080 → Deferred 表，原因 `child 8/9 cancelled at closeout`；6a 若有独立 REQ 一并补。**逐条与各 child stage-report 核对，不确定的标 `partial` 并注明，禁止编造 evidence。**
   - `child-task-index.md`：全部 child 行补 status/commit。
   - `subphase-report.md`：写 closeout 摘要（完成 8 切片、取消 2、6a 为中途追加切片及其原因一句话）。
   - **closeout review**：以 rescue review 身份逐条报「漏做哪条 / 超做哪条」（对照 rtm-delta），结论写进 subphase-report.md 的 `## Closeout Review (Codex)` section。发现真实缺口 → 停下报告 jym，不要自行归档 parent。
4. **归档 parent**：task.json `"status": "completed"`、staged_delivery 补 `trellis_archive_completed: true` → `python3 ./.trellis/scripts/task.py archive 07-01-parent-v1-4-benchmark-productionization --no-commit`。
5. **归档全部 v1.4 child**（含 cancelled 的 8/9）：逐张 `archive --no-commit`。若 archive 顺序有依赖（parent-children 关系更新），先 child 后 parent 也可，以 task.py 实际行为为准，保证最终 tasks/ 下 v1.4 相关目录清零。

### Batch C — 孤卡处置（5 张）

| 卡 | 动作 |
|---|---|
| 06-30-local-ai-state-versioning | status→completed，staged_delivery.soft_archive_completed→true（commit 3ac9891 已在），archive --no-commit |
| 07-01-production-deployment-handoff | cancelled；notes：`职能并入 M5 CI/staging parent（workflow v2 §7）`；archive |
| 07-03-prd-pre-design-input-skill | cancelled；notes：`superseded by workflow v2 /prd skill（docs/reports/trellis-loop-workflow-v2.md §8）`；archive |
| 07-03-staged-acceptance-commit-archive-rule | cancelled；notes：`superseded by workflow v2 §4.3`；archive |
| 07-03-gitignore-development-hygiene | cancelled；notes：`absorbed into workflow v2 boundary rule #4`；archive |

### Batch C6 — state-engine-mvp（核实型，单独做）

1. `git log --oneline --all -- src/ | head -50` 结合该卡 stage-report.md（89L）里提到的文件/命令，定位实际 commit。
2. 也检索 commit message 含 `state engine` / `v2.1` / `state-engine` 的提交。
3. 找到 → task.json 补 commit hash + status→completed → archive --no-commit。
4. 找不到唯一对应 → **停，报告 jym**，该卡保留不动，在最终报告里列出候选 commit。

### 最终验收（全部满足才算完成）

```bash
python3 ./.trellis/scripts/task.py list
# 期望：仅 07-02-mediacrawler-long-running-stability（可能加 C6 待定卡）

ls .trellis/tasks/ | grep -v archive
# 期望：仅上述保活卡目录

python3 ./.trellis/scripts/task.py list-archive 2026-07
# 期望：新增 ~25 条归档记录

git status --short
# 期望：仅 .trellis/tasks/ 范围内改动，一次性 commit 后 working tree clean
git log -1 --stat
# 期望：单 commit，message 如约束 2，无源码文件混入
```

### 交付报告格式

完成后向 jym 报告：归档数 / 取消数 / C6 核实结果 / closeout review 发现的缺口（若有）/ 与本工单的任何偏离及原因。
