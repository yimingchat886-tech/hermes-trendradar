# Trellis-Loop Workflow v2

Date: 2026-07-04
Status: Active（本文档即日起为新任务的唯一工作流依据，取代 `trellis-full-development-workflow-report.md` 描述的旧 11 阶段流程）
Supersedes: `docs/reports/trellis-full-development-workflow-report.md`（保留作历史参考）

## Bottom Line

三层架构：

```text
规划治理层 = Trellis（瘦身版）：/prd → Codex 跨 AI 审 → /split
运转执行层 = Loop：实现(红命令) → PR(一条命令) → 验收 → 沉淀 ≤1 行 → 回边
保障层     = 机制闸 G1–G6：把 prose 里的 MUST/NEVER 变成跳不掉的 hook/脚本
```

设计来源：

- 本地 Trellis 全流程报告（2026-07-03）。
- 开工 Loop · 运转端逻辑图（agent-foreman v2.3, 2026-07-04）。
- `.trellis/` 27 张真实任务卡的执行数据（v1.4 parent 实况）。

修复的真实问题：

| # | 问题 | 证据 | v2 对策 |
|---|---|---|---|
| F1 | Parent 治理件执行期无人维护 | v1.4 parent 7 child 完成仍 `planning`，RTM 全停在 `planned` | 件套 6→2，状态列由 task.py 自动回写 |
| F2 | 孤儿卡堆积 | 27 张卡仅 3 张归档，8 张 completed 未清扫 | closeout 连带清扫 + BOARD 催办 |
| F3 | 双向链接断裂 | 06-29 批次 child `parent: null` | create --parent 强制双向，validate 校验 |
| F4 | 拆分遮蔽 | child-6a 中途追加 | Codex 跨 AI 审 PRD/拆分 |
| F5 | Oracle 纸面预算 | budget 表 `Available: not checked`，从未执行 | Oracle 独立阶段废除，并入两个真实执行点 |
| F6 | 双状态机分裂 | 5 张卡 `status=in_progress` 但 `phase=soft_archived` | status 与 staged_delivery 单向同步 |
| F7 | 小任务过重 | 12 张独立卡背完整件套（如 143 行 prd + oracle-budget） | T0/T1 轻卡分流 |

## 1. 入口与分流

用户「开工」→ SessionStart 亮 BOARD.md + 催办（>48h 未动卡）→ 定级分流。

**定级判据 = Ponytail blocking finding 反向充当升级触发器**（命中任何一条 → T2+ 全流程）：

> 新生产依赖 / 新框架 / 架构边界变化 / 新抽象层 / broad directory reorganization / module rewrite / 改删公共 API / DB migration

未命中 → T0/T1 走 Fast Loop。存疑时按 `.trellis/spec/project/task-sizing.md` 判级，仍存疑 → 问用户。

## 2. Fast Loop（T0/T1）

```text
① SDD 三问（成功 / 底线 / 边界）写进轻卡 prd.md
   recall ≤5 行：读最近沉淀 delta + spec/guides/index.md
② task.py create（无 parent），task.json 写 owner
③ 实现：修 bug 红命令路线 + GitNexus impact（G2）
④ trellis-check 一条命令（G3 覆盖 commit）
⑥ 用户完成信号 → commit + 归档（过 G1 done-gate）
⑦ 沉淀 ≤1 行 delta → 回边
还有下一件活? → ①
```

轻卡件套：仅 `prd.md`（三问）+ `stage-report.md` + `task.json`。不生成 design / oracle-budget / governance。

## 3. 规划治理层（T2+，全部在 Claude Code 侧）

原 Phase 0–5 六阶段 → 三个动作。

### 3.1 `/prd`（单 skill，分层展开）

- 入口先跑 SDD 三问 + 定级；T0/T1 在此直接转 Fast Loop。
- T2+ 进入 plan mode 共创：先查 repo/已有 PRD/代码再问人；一次只问一个高价值问题；发散（未来演进/相关场景/失败边界）→ 收敛 MVP。
- **碰用户可见行为 / 平台能力 → mockup / 时序图对齐，写码前硬 gate。**
- PRD 正文**只写 `docs/PRD/`（唯一事实源）**，含 Goal / Requirements / Acceptance Criteria / DoD / Technical Approach / Decision (ADR-lite) / Out of Scope / PRD Map。
- parent 侧不再复制 PRD 正文（消灭 F3 双份漂移）。

### 3.2 Codex 跨 AI 审查（替代旧 Oracle 前置审）

- CC 完成 PRD 草案 + child 拆分草案后，交 Codex 以审查者身份挑战：
  - 范围漏洞（漏拆的切片，child-6a 类问题）。
  - 隐藏依赖 / 隐藏外部 I/O。
  - 拆分合理性（child 是否独立可验证、依赖序是否成立）。
  - 与既有 spec / ARCHITECTURE 易混术语冲突。
- 反馈写回 parent `governance.md` 的 `## External Review` section（谁审、结论、豁免理由）。
- **G1 done-gate 检查该 section 非空**，未审不得进入实现。
- T3/T4 closeout 追加外部审查：并入 PR 层 rescue review（见 4.2），不再有独立 Oracle 阶段。
- `oracle-review-policy.md` / `oracle-review-budget.md` 自本文档起退役。

### 3.3 `/split`（parent 创建 + 拆分 + 边界检查合体）

Parent 件套瘦身为 **2 件**：

```text
prd.md         ≤20 行薄指针：引用 docs/PRD 路径 + 本阶段范围切片 + 阶段约束
governance.md  四 section：
  ## Child Index       （表：child / 交付物 / 依赖 / owner / branch / status / commit）
  ## RTM               （requirement_ids ↔ child ↔ status ↔ evidence）
  ## External Review   （Codex 审查记录 + closeout 审查记录）
  ## Boundary Pass     （8 条边界规则逐条过 + Ponytail blocking finding 检查）
```

- 8 条边界规则不变（main 只收已验证 / 一 child 一分支 / 可运行检查点 commit / 不推私有状态 / PR 前查 diff-secrets-测试-范围 / 薄 adapter / config-env 纪律 / 最小测试命令）。
- child 拆分标准不变：一 child 一个主要交付物、独立可验证、不偷做 sibling 范围、明确依赖、明确 branch、至少一个测试或 smoke 命令。
- **机制闸：`task.py validate` 校验 governance.md 缺 Boundary Pass / child 缺 requirement_ids / 双向指针缺失 → 不通过。**
- `trellis-architecture-boundary` 不再是独立阶段。

### 3.4 状态自动回写（消灭 F1/F6）

`task.py` 在 child soft-archive 时自动：

1. 回写 parent `governance.md` Child Index 行：status + commit hash。
2. RTM 中该 child 的 requirement_ids → completed。
3. child `task.json` 的 `status` 与 `staged_delivery.phase` 单向同步（phase 为准）。

人只写判断性内容（风险、trade-off、review 结论），状态列一律机器写。

## 4. 运转执行层（Loop）

### 4.1 实现（child 或轻卡）

- `trellis-before-dev`：读 active task prd/implement + spec/guides + **最近沉淀 delta（≤5 行 recall，回边入口）**。
- 源码符号修改前 GitNexus impact（G2）；HIGH/CRITICAL 风险必须警告用户。
- **修 bug 路线（硬规则）**：动手前必须有一条**已跑过、能变红、可复现**的命令；建不出来 → 明说并用替代观测手段。第二次修复失败 → 触发 `trellis-break-loop`：列 3–5 个可证伪假设排序，防锚定。
- 测试四类枚举：happy / 边界 / 错误 / 状态。
- 最小实现纪律不变：无 speculative abstraction、无无必要依赖、不扩大范围；PRD 有错回 PRD 改，不在代码里偷改范围。
- **merge 撞车协议**（M6 并行开发生效，先立为纪律）：还原双方意图 → 双保留 → 不可兼得记 trade-off 进 governance.md → 绝不 `--abort` / 发明双方都没写过的新行为。

### 4.2 PR（一条命令）

`trellis_pr.sh`（M3 落地）固化 7 坑：BASE 钉死 / 临时 index / 删除行审查 / migrations 硬闸 / diff-tree 终验 / `[task-slug]` 标题 / freshness+lint 预检（G4）。

高风险交付面（migrations / workflows / common / 公共 API）→ Codex rescue review，`--reviewed '证据'`；Spec 轴逐条报「漏做哪条 / 超做哪条」对照卡上 success:/范围。T3/T4 closeout 的外部审查在此执行。

### 4.3 验收（LOOP-CLOSE）

- 用户可见行为 → 真机验收（M5 后：staging + Playwright/CDP；M5 前：本地跑通 + 用户完成信号）。
- 纯内部 → CI 收口，acceptance 记「纯内部 · CI 收口」。
- ACCEPTANCE 报告**逐条对照** success: / Acceptance Criteria。
- 完成信号词表不变（`任务完成` / `验证通过` / `通过` / `可以提交` / `可以提交并归档` / `这个任务 OK`）；限制词优先（`先别提交` / `不要归档` / `还要改`）。
- PLAN confirmation 只允许开始实现，不允许 commit/push/archive——不变。

### 4.4 沉淀与回边

- 每次归档蒸馏 **≤1 行 delta**，路由表：

| 类型 | 去向 |
|---|---|
| 项目实现规则（命令/API/schema/env contract） | `.trellis/spec/<layer>/` |
| 思考检查清单 | `.trellis/spec/guides/` |
| 跨项目通用教训 | `~/.claude/CLAUDE.md` / memory |
| 工具用法 | 对应 skill |

- 回边：`trellis-before-dev` recall 固定读回最近 N 条 delta（≤5 行）→ 下一件活自动受益。
- BOARD.md 在每次归档时从 `.trellis/tasks/` 重生成（M4）。

## 5. 机制闸 G1–G6

| 闸 | 拦截点 | 强度 | 里程碑 |
|---|---|---|---|
| G1 done-gate | 归档前：stage-report 无 acceptance 逐条对照，或 T2+ governance.md External Review 为空 | BLOCK | M2 |
| G2 impact-gate | Edit/Write 源码前本 session 未跑 impact | BLOCK | M3 |
| G3 scope-gate | `git commit` 前未跑 detect_changes | BLOCK | M2 |
| G4 pre-push | push 前 freshness + lint | BLOCK | M3 |
| G5 claim-guard | 编辑其他 owner 卡的 touches 文件 | WARN → M6 升 BLOCK | M2(WARN) |
| G6 CI-gate | CI 非绿 → 拦 staging deploy | BLOCK | M5 |

## 6. 分工与分支（并行开发基础）

| 角色 | 职责 | 分支前缀 |
|---|---|---|
| Claude Code | PRD / 拆分 / 高风险 child 实现 / 工作流治理 | `cc/<task-slug>` |
| Codex (GPT-5.5) | 普通 child 实现 / PRD 跨 AI 审 / PR rescue review / BUGS.md | `codex/<task-slug>` |
| Gemini | DOCS.md（README / API docs / changelog） | — |

`task.json` 新增 `owner` + `touches` 字段（M2），claim-guard 基于此。BUGS.md 条目 → 直接映射为 Fast Loop 轻卡。

## 7. 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 本文档 + 存量 triage 表 + `/prd`、`/split` skill 骨架；**新任务即日起强制走 v2** | 本次交付 |
| M2 | triage 执行（Codex 工单）+ task.py 改造：2 件套模板、自动回写、validate 边界闸、G1/G3、owner/touches 字段 | 工单已开 |
| M3 | `trellis_pr.sh` + 修 bug 路线写进 skill + G2/G4 | 待办 |
| M4 | BOARD.md 生成 + SessionStart + >48h 催办 | 待办 |
| M5 | CI + staging（独立 T2 parent 自举，含原 production-deployment-handoff 职能） | 待办 |
| M6 | claim-guard 升 BLOCK + merge 撞车协议机制化，开启多 agent 并行 | 待办 |

## 8. Supersede 映射

| 旧 | 新去向 |
|---|---|
| Phase 0 `trellis-prd-design-input` + Phase 1 `trellis-brainstorm` | `/prd` 单 skill 分层展开 |
| Phase 2 parent 6 件套 | `prd.md`(薄) + `governance.md` 2 件套 |
| Phase 3 Oracle 审查 + oracle-review-policy/budget | Codex 跨 AI 审（前置）+ PR rescue review（closeout），记录进 governance.md External Review |
| Phase 4 `trellis-architecture-boundary` 独立阶段 | governance.md Boundary Pass section + validate 闸 |
| Phase 5 child 拆解 | `/split` 内完成 |
| Phase 6–9 实现/检查/归档/沉淀 | Loop ③④⑥⑦ + G1–G4 |
| 任务 07-03-prd-pre-design-input-skill | 被本文档 supersede，triage 关闭 |
| 任务 07-03-staged-acceptance-commit-archive-rule | 完成信号/限制词规则已并入 4.3，triage 关闭 |
| 任务 07-03-gitignore-development-hygiene | 已并入边界规则第 4 条，triage 关闭 |
| 任务 07-01-production-deployment-handoff | 职能并入 M5 parent，triage 关闭 |

## 9. One-Screen Summary

```text
开工 → BOARD + 催办 → 定级（Ponytail 触发器）
T0/T1: 三问轻卡 → 实现 → check → 完成信号 → 归档(G1) → 沉淀 ≤1 行 → 回边
T2+:  /prd(docs/PRD 唯一正文, mockup 硬 gate)
      → Codex 跨 AI 审(写 External Review, G1 查非空)
      → /split(2 件套 + Boundary Pass + validate 闸)
      → child: 红命令实现(G2) → trellis_pr.sh(G3/G4, 高风险→rescue review)
      → 验收逐条对照 → soft archive(自动回写 parent)
      → parent closeout(rescue review) → archive 连带清扫 children
      → 沉淀 ≤1 行 → 回边 → 还有下一件活?
分工: CC 规划+高风险 / Codex 实现+审 / 分支 cc|codex/<slug> / owner+touches 防撞车
```
