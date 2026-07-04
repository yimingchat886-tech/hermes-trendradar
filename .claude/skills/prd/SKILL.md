---
name: prd
description: "Workflow v2 入口：任何新任务从这里开始。SDD 三问定级分流 —— T0/T1 直接建轻卡进 Fast Loop；T2+ 进 plan mode 共创 PRD（正文只写 docs/PRD/），完成后交 Codex 跨 AI 审查。Examples: \"开工\"、\"我要做 X 功能\"、\"新任务\""
---

# /prd — SDD 三问 · 定级分流 · PRD 共创

规范来源：`docs/reports/trellis-loop-workflow-v2.md` §3.1。定级细则：`.trellis/spec/project/task-sizing.md`。

## Step 1: SDD 三问（所有任务必答，答案 ≤5 行）

1. **成功**：做完后用户能看到/得到什么？一句话。
2. **底线**：绝不能破坏什么？（数据、已有行为、安全边界）
3. **边界**：明确不做什么？（Out / Later）

先查 repo、docs/PRD、`.trellis/spec/`、代码，能自己推导的不问用户。

Recall（回边入口）：读 `.trellis/spec/guides/index.md` + 最近沉淀 delta，≤5 行。

## Step 2: 定级

命中任何一条 **Ponytail 升级触发器** → T2+，走 Step 4：

> 新生产依赖 / 新框架 / 架构边界变化 / 新抽象层 / broad directory reorganization / module rewrite / 改删公共 API / DB migration

未命中 → 按 task-sizing.md 判 T0/T1。存疑 → 问用户一次，不自行拔高。

## Step 3: T0/T1 → Fast Loop（本 skill 到此结束）

```bash
python3 ./.trellis/scripts/task.py create "<title>" --slug <slug>
```

- 轻卡件套：仅 `prd.md`（写入三问答案）+ `stage-report.md` + `task.json`（补 `owner` 字段）。
- 不生成 design / governance / oracle-budget。
- 直接进实现：修 bug 先建**已跑过、能变红、可复现**的命令；改源码符号前跑 GitNexus impact。
- 完成 → 用户完成信号 → commit + 归档 → 沉淀 ≤1 行 delta。

## Step 4: T2+ → PRD 共创（plan mode）

纪律：

- 一次只问一个高价值问题；先查再问。
- 发散（未来演进 / 相关场景 / 失败边界）→ 收敛 MVP。
- **碰用户可见行为或平台能力 → mockup / 时序图与用户对齐，写码前硬 gate，未对齐不得进 /split。**

PRD 正文**只写 `docs/PRD/`**（唯一事实源，parent 侧后续只放薄指针），必含：

```md
# PRD: <title>
## Goal
## Requirements          （带 REQ-ID，供 RTM 引用）
## Acceptance Criteria    （逐条可验证）
## Definition of Done
## Technical Approach
## Decision (ADR-lite)    （关键取舍一行一条）
## Out of Scope
## PRD Map                （仅多 PRD 时：Block / PRD / Type / Status / Depends On / Trellis Mapping）
```

## Step 5: 交 Codex 跨 AI 审查

PRD 草案完成后，提示用户到 Codex 侧执行：

> 以审查者身份挑战 `docs/PRD/<file>.md`：范围漏洞（漏拆切片）、隐藏依赖/外部 I/O、拆分合理性、与既有 spec 及 ARCHITECTURE 术语的冲突。结论写成逐条清单。

- 审查反馈回来后：有效意见改 PRD；结论（谁审、日期、逐条结论/豁免理由）暂存，待 `/split` 建 parent 后写入 `governance.md` 的 `## External Review`。
- **未经审查不进 /split**（G1 done-gate 会检查 External Review 非空）。
- 审查通过 → 运行 `/split`。
