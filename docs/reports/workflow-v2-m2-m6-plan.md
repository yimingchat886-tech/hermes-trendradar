# Workflow v2 机制化执行方案（M2–M6 PLAN）

Date: 2026-07-04
Status: PLAN（待 Codex 跨 AI 审后动工——按 v2 流程自举）
正典: `docs/reports/trellis-loop-workflow-v2.md`
操作教材: `docs/runbooks/workflow-v2-operator-guide.md`
本文件角色: M2–M6 的 `docs/PRD/` 级单一事实源。M2–M4 建 parent `workflow-v2-mechanization`，其 prd.md 薄指针指向本文件；M5 另建独立 parent；M6 等 M2–M4 parent 与 M5 parent 均 closeout 后再开。

## 0. 总则

- **自举原则**：本方案本身按 v2 流程执行——本文件即 PRD，动工前交 Codex 审（范围漏洞/隐藏依赖/拆分合理性），审后 `/split` 建 parent+child。
- **分工**：Codex (GPT-5.5 xhigh) 实现全部 child（脏活）；CC 负责 PLAN、每个 child 的 rescue review、以及 jym 验收前的逐条对照。注意这与 v2 默认分工（高风险归 CC）相反，属于本 parent 的显式例外，记入 governance.md External Review。
- **jym 的闸**：每个 child 完成信号（J5）、每个里程碑 closeout、M5 的 remote/CI 决策、M6 的并行开启（J7 级）。
- **环境事实**（实现必须依此）：
  - 项目 `.claude/` 目前只有 `settings.local.json`（仅 permissions）+ `skills/`，**无 hooks 配置、无 hooks 目录**——所有 hook 从零建。
  - `.trellis/.runtime/` 已被 `.trellis/.gitignore` 忽略——session 级 marker 文件放这里，天然不入库。
  - `.trellis/scripts/state_machine.py`（07-03-v2-1-0-state-engine-mvp 的产物，opt-in harness 模式）已存在——M2 自动回写**优先复用其事件机制**，不重复造状态机。
  - `.trellis/templates/staged/` 仍是旧 6 件套模板——M2 需新增 2 件套模板并退役旧件。
  - **仓库无 remote**——M5 第一个 child 就是配 remote。
- **通用 DoD（每个 child 都要满足，下文不再重复）**：
  1. 至少一条可复现验证命令，写进 child stage-report。
  2. 负路径测试（闸类 child 必须演示"被拦"场景）。
  3. 改动只属于本 child（detect_changes 或 diff 核对）。
  4. 不推私有运行态；hook 脚本与模板入库，marker 不入库。

## Codex 工单 A（先行，独立于 M2–M6）：合回 main

完成记录：2026-07-04 已 fast-forward 合回 `main`，实际合入 6 个 commit，`main` 当前 HEAD 为 `576d43b chore(gitnexus): refresh project guidance and skills`。原任务分支已删除；未提交脏改保持在工作区。

验收：`git branch` 仅剩 main；`git log main -1` = `576d43b`；`git status` 脏改原样保留。

---

## M2 — task.py 改造 + 首批闸（G1/G3/G5-WARN）

目标：2 件套/轻卡成为机器模板；状态自动回写消灭 F1/F6；归档、commit、跨 owner 编辑三处有闸。

### M2-0 新流程自举修正

- 交付物：
  - 更新 `/split` skill：当前 CLI 尚无 `--tier` 时允许用旧 `create` 自举建 M2–M4 parent，但 M2-1 落地后 parent/child/light 必须显式传 `--tier`。
  - v2 新卡以 `tier` + `owner/touches` 为事实源，并写 `meta.workflow_mode = "harness_state_machine"`；旧 `staged_overlay` 卡只作 legacy evidence，不阻塞 M2–M4 新流程。
  - G1/G3 都必须落在工具无关层：G1 在 `task.py` 写盘前检查；G3 按真实 git `pre-commit` 实现，Claude hook 只作提示增强。
  - G3 判定用 staged diff/tree 指纹，不用文件 mtime；marker 记录 detect_changes 覆盖的 staged 指纹，pre-commit 比对当前 staged 指纹。
  - 记录 parent 边界：M2–M4 属于 `workflow-v2-mechanization` parent；M5 属于独立 remote/CI/staging parent；M6 等两者 closeout 后再开。
- 验收：
  ```bash
  rg -n -- '--tier parent|pre-commit|写盘前|staged.*指纹|workflow_mode|M2.*M4|M5 parent|M6' docs/reports/workflow-v2-m2-m6-plan.md .claude/skills/split/SKILL.md
  ```

### M2-1 模板与 create 改造

- 交付物：
  - `.trellis/templates/v2/parent/`：`prd.md`（薄指针骨架，≤20 行）+ `governance.md`（四 section 骨架：Child Index 表 / RTM 表 / External Review / Boundary Pass）。
  - `.trellis/templates/v2/child/`：`prd.md`（目标一句话 + REQ-ID + 验证命令 + In/Out）+ `stage-report.md`（含 `## Acceptance` 逐条对照骨架）。
  - `.trellis/templates/v2/light/`：轻卡 `prd.md`（SDD 三问骨架）+ `stage-report.md`。
  - `task.py create` 新参数：`--tier light|child|parent`（默认 light；`--parent` 存在时强制 child）。按 tier 选模板，不再生成旧 6 件套。
  - `task.json` schema 新增：`owner`（"cc"|"codex"|"jym"）、`touches`（路径 glob 列表）、`tier`。create 时 `--owner`/`--touches` 写入；新 tier 卡统一写 `meta.workflow_mode = "harness_state_machine"`。
- 旧模板处置：`templates/staged/` 保留但 create 不再引用（存量归档卡还引用它，不删）。
- 验收：
  ```bash
  python3 ./.trellis/scripts/task.py create "t" --slug tmp-parent --tier parent
  ls .trellis/tasks/*tmp-parent/   # 仅 prd.md governance.md task.json (+jsonl)
  python3 ./.trellis/scripts/task.py create "t" --slug tmp-light --tier light --owner cc
  # task.json 含 owner/tier；目录内无 governance/oracle-budget/design
  ```

### M2-2 validate 闸扩展

- 交付物：`task.py validate` 按 tier 校验：
  - parent：governance.md 存在且四 section 齐全；Boundary Pass 非空；External Review 的 PRD Review 非空。
  - child：prd.md 含 ≥1 个 REQ-ID、≥1 条验证命令；task.json 有 owner；parent/children 双向指针一致（修 F3）。
  - light：prd.md 三问非空。
  - 任一不满足 → exit 1 + 逐条缺失清单。
- 验收：对 M2-1 造的空 parent 跑 validate → 非零退出并列出「Boundary Pass 为空、External Review 为空」；补齐后 → 0。

### M2-3 `task.py soft-archive` + 状态自动回写（消灭 F1/F6）

- 交付物：新增 `task.py soft-archive <task-dir> --commit <hash>`，作为 child soft-archive 的唯一命令入口；该命令触发：
  1. 回写 parent `governance.md` Child Index 对应行的 Status/Commit 列（按 child slug 定位行，只改这两列，保持其余手写内容不动）。
  2. RTM 中该 child 的 REQ-ID 行 Status → completed，Evidence 列补 `<child>/stage-report.md`。
  3. child `task.json.status` 与 `meta.state_machine.current_state` 单向同步（state machine 为准：`child_archived` → `completed`）。
- 实现要点：新 tier 卡必须是 `meta.workflow_mode = "harness_state_machine"`；复用 `state_machine.py` 的事件写入（`state-events.jsonl`）记录每次回写，幂等（重跑不重复追加）。
- 验收：造一个 tmp parent+child，跑 `task.py soft-archive <child> --commit <hash>` → governance.md 两列被机器改写、RTM 行 completed、status 同步；重跑一次 → 文件无 diff（幂等）。

### M2-4 G1 done-gate（BLOCK）

- 交付物：`task.py archive` 与 `task.py soft-archive` 共用内置写盘前检查；任一失败 → 拒绝执行 + 说明，且不得修改 `task.json`、移动目录、写 governance、写事件：
  - stage-report.md 的 `## Acceptance` section 非空且不含模板占位符。
  - child (tier=child)：所属 parent 的 External Review 非空。
  - parent：全部 children 状态为 completed/cancelled；RTM 无 planned 残留（partial/deferred 需带原因）。
  - 逃生门：`--force-archive --reason "<why>"`，reason 写入 task.json.notes 和 state-events.jsonl（可审计，不可静默）。
- 验收（负路径必测）：
  ```bash
  python3 ./.trellis/scripts/task.py archive <acceptance为空的卡>   # exit≠0，指出缺 Acceptance
  python3 ./.trellis/scripts/task.py soft-archive <acceptance为空的child> --commit deadbeef  # exit≠0，指出缺 Acceptance
  git diff -- .trellis/tasks/<acceptance为空的卡>/task.json          # 无 diff
  python3 ./.trellis/scripts/task.py archive <同卡> --force-archive --reason "test"  # 成功且留痕
  ```

### M2-5 G3 scope-gate（BLOCK）

- 交付物：
  - 真实 git gate：`.trellis/scripts/hooks/pre-commit` + `.trellis/scripts/install_hooks.sh`，安装到 `.git/hooks/pre-commit`（`.git/hooks/*` 本身不入库）。
  - 可选提示增强：`.claude/hooks/scope_gate.py` + 项目 `.claude/settings.json` 注册 PreToolUse(Bash)，但 Claude hook 不作为唯一拦截点。
  - 逻辑：`git commit` 触发 pre-commit（排除 `--amend --no-edit` 的纯 message 修补）→ 计算当前 staged diff/tree 指纹；检查 marker `.trellis/.runtime/scope-check.ok` 中记录的指纹与当前 staged 指纹一致 → 放行；否则 exit 2（BLOCK）+ 提示先跑 detect_changes。
  - marker 写入方：PostToolUse(mcp__gitnexus__detect_changes) hook 可写 marker；另提供 `.trellis/scripts/mark_scope_ok.sh` 供 CLI 跑 `npx gitnexus detect-changes` 后手动盖章（Codex 侧无 MCP hook 时用）。
- 边界：只拦 commit，不拦 add/status/diff；仅 `.trellis/tasks/` 归档/证据类提交与 `.trellis/workspace/` 提交可豁免。`.trellis/scripts/`、`.trellis/templates/`、`.trellis/spec/` 不豁免。
- 验收：安装 hooks；改一个 src 文件 → 直接 `git commit` 被真实 git pre-commit BLOCK；跑 detect-changes（或盖章脚本）→ commit 放行；改变 staged 内容后旧 marker 失效；纯 `.trellis/tasks/` 证据 commit 与 `.trellis/workspace/` commit 不受影响。

### M2-6 G5 claim-guard（WARN）

- 交付物：`.claude/hooks/claim_guard.py` 注册 PreToolUse(Edit|Write)。逻辑：目标路径匹配某张**活跃**卡的 `touches`、且该卡 `owner` ≠ 当前身份（读 `.trellis/.developer` / env）→ stderr 输出 WARN（卡名+owner），**放行**。强度常量置顶 `MODE = "WARN"`，M6 改一行升 BLOCK。
- 验收：造 owner=codex、touches 含 `src/foo.py` 的活跃卡 → 以 cc 身份 Edit 该文件出现 WARN 且编辑成功；无冲突路径无输出。

**M2 closeout 标准**：M2-0~6 全过 + 用新流程真实跑完一张轻卡（自举演练：三问→实现→check→J5→归档，全程 G1/G3 生效）+ tmp 卡清理干净。

---

## M3 — PR 一条命令 + G2/G4

### M3-1 `trellis_pr.sh`（7 坑机制化）

- 交付物：`.trellis/scripts/trellis_pr.sh <task-dir>`，串行执行，任一步失败即停：
  1. BASE 钉死：`git merge-base HEAD main` 记录并 diff 只对 base（防漂移）。
  2. 临时 index：`GIT_INDEX_FILE` 隔离，不污染工作区 staging。
  3. 删除行审查：列出全部被删行 >0 时要求 `--ack-deletions`。
  4. migrations 硬闸：diff 触碰 `migrations/|schema` → 要求 `--ack-migrations` + 强制 rescue review。
  5. lint/typecheck 预检（用项目已有工具，探测 package.json/pyproject）。
  6. diff-tree 终验：输出逐文件清单与卡 touches 对照，超范围文件高亮。
  7. 标题规范：commit/PR 标题含 `[<task-slug>]`。
  - 高风险交付面（migrations/workflows/common/公共 API）→ 打印 rescue review 提示词模板（给 Codex），`--reviewed "<结论>"` 后才允许最终放行标记 `.trellis/.runtime/pr-ok-<slug>`。
- 验收：对一张真实 child 跑通全 7 步；构造含删除行+migration 的假 diff → 无 ack 参数被拦。

### M3-2 G2 impact-gate（BLOCK）

- 交付物：`.claude/hooks/impact_gate.py` 注册 PreToolUse(Edit|Write)。源码路径（`src/`、`scripts/` 等，配置在 hook 顶部常量）且本 session 无 `.trellis/.runtime/impact-<sid>.ok` marker → exit 2，提示先跑 impact。marker 写入方：PostToolUse(mcp__gitnexus__impact|context)。docs/.trellis/.claude 路径豁免。
- 验收：新 session 直接 Edit src 文件 → BLOCK；跑一次 impact → 放行；编辑 docs → 不拦。

### M3-3 G4 pre-push + 修 bug 路线入 skill

- 交付物：
  - `.git/hooks/pre-push`（由 M2 已有 `.trellis/scripts/install_hooks.sh` 扩展安装，脚本本体入库在 `.trellis/scripts/hooks/`）：freshness（本地 base 落后 main → 拦）+ lint 预检 + 私有状态扫描（diff 中出现 `.trellis/.runtime|.developer` 路径 → 拦）。
  - 修 bug 红命令路线写入 `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` 追加 section（动手前已跑过/能变红/可复现；二次失败→break-loop 列 3–5 可证伪假设）。
- 验收：base 过期时 push 被拦（本地 remote 可用 `git init --bare /tmp/hermes-remote.git` 模拟，M5 前不依赖 GitHub）；skill 文件含新 section。

**M3 closeout 标准**：一张真实 child 从实现到 PR 全程只用 `trellis_pr.sh` 走完，G2 触发记录 ≥1 次。

---

## M4 — BOARD + SessionStart 催办

### M4-1 BOARD 生成器

- 交付物：`.trellis/scripts/board.py` → 生成 `BOARD.md`（项目根，入库）：活跃卡表（slug/tier/owner/status/最后动静时间/blocked 标记）+ 待验收区（等 J5 的卡）+ 最近 7 天归档。归档/soft-archive/create 动作后自动重生成（挂进 task.py 对应命令尾部）。
- 验收：`python3 board.py` 幂等；create/archive 后 BOARD.md 自动更新。

### M4-2 SessionStart 亮板 + 催办

- 交付物：BOARD 摘要由共享脚本生成（例如 `python3 ./.trellis/scripts/board.py --summary --max-lines 10`），`.claude/hooks/session_start.py` 与 `.codex/hooks/session-start.py` 都调用该共享入口；输出 BOARD 摘要（≤10 行）+ 催办（>48h 无 mtime 变化的活跃卡逐张点名）+ owner 身份行（`owner=<who>`）。
- 验收：Claude 与 Codex 新开 session 首屏都出现同一份看板摘要；把一张卡 mtime 改到 3 天前 → 两边都点名催办。

**M4 closeout 标准**：连续 3 个真实 session 开工时看板/催办正常，无误报僵尸卡。

---

## M5 — Remote + CI + Staging + G6（独立 T2 parent，含原 production-deployment-handoff 职能）

前置：M5 不挂在 M2–M4 parent 下；另建独立 parent。动工前先问 jym：GitHub 仓库（新建/已有）、可见性、staging 宿主（本机 docker / 云）。**此 parent 动工前先问，不自行假设。**

### M5-1 remote + 分支保护

- 交付物：`git remote add origin`；push main + 全量历史；main 分支保护（禁 force-push、PR required——CI 建好后再加 required checks）。
- 验收：`git push` 成功；直接 push main 被 GitHub 拒。

### M5-2 CI（GitHub Actions）

- 交付物：`.github/workflows/ci.yml`：lint + typecheck + test（按项目实际技术栈探测后写死，不做 matrix 豪华配置）；PR 触发 + main push 触发。设为 required checks。
- 验收：故意提交一个 lint 错误的 PR → CI 红 → merge 按钮被禁；修复 → 绿 → 可 merge。

### M5-3 staging 部署 + G6

- 交付物：`docker-deploy-staging` workflow（main 绿后手动/自动触发，方案随 M5 前置决策定）；G6 = deploy job `needs: ci` + `if: success()`（CI 非绿物理上无法 deploy）。
- 验收：CI 红时手动触发 deploy → 被拒；绿时 deploy 成功且 staging 可访问。

### M5-4 验收自动化

- 交付物：Playwright 冒烟脚本（用户可见行为 top 路径，≤5 条用例起步）+ `verify workflow:head_sha` 回查脚本（验收报告里贴 CI run 链接 + head_sha 与 merge SHA 一致性检查）。acceptance 模板增加「CI run / staging 验证」栏。
- 验收：跑一轮真实 child 的 staging 验收，ACCEPTANCE 报告含绿色 run 的 head_sha。

**M5 closeout 标准**：一个真实 child 走完 merge→CI→staging→真机验收→tag 全链路；操作教材 §5 的「本地语义」段落更新为转正版。

---

## M6 — 并行开发开启（G5 升 BLOCK + 撞车协议机制化）

前置：M2–M4 parent 与 M5 parent 均 closeout；jym 明确说「开启并行」。

### M6-1 claim-guard 升 BLOCK

- 交付物：claim_guard.py `MODE = "BLOCK"`（exit 2）；配套 `task.py claim <dir>` / `release <dir>` 命令（改 owner + 记事件）；豁免：`--override-claim` 参数留痕放行。
- 验收：跨 owner 编辑被 BLOCK；claim 后放行；override 留 state-events 记录。

### M6-2 merge 撞车协议机制化

- 交付物：`.trellis/scripts/merge_protocol.md`（还原双方意图→双保留→不可兼得记 trade-off 进 governance→绝不 --abort/发明新行为）+ conflict 发生时的检查清单脚本（列出冲突文件对应的两张卡与 owner，强制先读双方 stage-report 再动手）。
- 验收：人为制造一次两分支冲突演练，按协议走完，trade-off 记录落 governance.md。

### M6-3 worktree 并行试运行

- 交付物：worktree 使用规程写入操作教材 §4（已有判据，补命令序列）；CC+Codex 各领一张互不相交 touches 的 child 同时实施一轮。
- 验收：并行两 child 各自 PR/验收/归档无互相污染；claim-guard 至少正确拦截一次演练性越界。

**M6 closeout 标准**：并行试运行一轮零事故；教材更新；v2 全部七个真实问题（F1–F7）对照复查各有机制覆盖。

---

## 依赖与顺序

```text
工单A(合main) ──→ parent A: M2 ──→ M3 ──→ M4 ──┐
                                               ├──→ M6
jym决策(repo/staging) ──→ parent B: M5 ────────┘
```

- M2 是一切闸的地基（模板/回写/marker 约定），必须最先。
- M5 与 M3/M4 无硬依赖，jym 决策到位后可用独立 parent 并行推进。
- M6 必须最后，且只在 parent A 与 parent B 都 closeout 后开启。

## 风险与回退

| 风险 | 缓解 |
|---|---|
| 闸误报打断心流（尤其 G3 marker 时序） | 每个 BLOCK 闸都有留痕逃生门（--force/--override + reason），静默绕过一律禁止；连续误报 ≥3 次 → 降 WARN 并开 bug 卡 |
| hook 在 Codex CLI 侧不生效（hooks 是 Claude Code 机制） | 双轨：G1/G3 核心逻辑放 task.py/git hook（工具无关），Claude hooks 只是加强层；Codex 侧靠 pre-push/task.py 兜底 |
| task.py 改造破坏存量归档卡兼容 | 全部新逻辑按 tier 字段分派，无 tier 的旧卡走旧路径只读不改 |
| state_machine.py 复用发现接口不合 | 允许只用其事件日志部分，回写逻辑独立实现，不强行耦合 |

## 全局验收（M2–M6 总闭环）

用一个真实 T2 feature 走完全流程并录入 journal：

```text
/prd 三问 → docs/PRD 正文 → Codex 审 → /split(validate 绿)
→ child 实现(G2 拦过一次) → trellis_pr.sh(G4) → CI 绿 → staging 真机
→ J5 完成信号 → soft archive(G1 过、自动回写可见) → parent closeout → tag
→ BOARD 更新、催办无僵尸、沉淀 ≤1 行落 spec
```

七个原始问题 F1–F7 每条指认对应机制截图/命令输出，全部命中 → v2 机制化完成。
