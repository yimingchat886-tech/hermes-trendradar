# Workflow v2 操作教材（jym 视角）

Date: 2026-07-04
配套正典：`docs/reports/trellis-loop-workflow-v2.md`
定位：回答五个实操问题 —— 何时 commit / 何时 PR / 何时开 worktree / 何时 release / 何时 push，以及你本人在流程里的职责。

## 0. 升级到哪了（如实）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | v2 正典 + triage 清扫 + `/prd` `/split` skill | ✅ 完成（M1 commit `354a618`；main 当前 `576d43b`） |
| M2 | task.py 改造：2 件套模板、自动回写、validate 闸、G1/G3、owner/touches | ⬜ 未做 |
| M3 | `trellis_pr.sh` + G2/G4 | ⬜ 未做 |
| M4 | BOARD.md + SessionStart 催办 | ⬜ 未做 |
| M5 | CI + staging + G6 | ⬜ 未做 |
| M6 | claim-guard 升 BLOCK + 并行开发 | ⬜ 未做 |

**结论：工作流「规则层」已升级完成并即日生效；「机制层」（跳不掉的闸）还没建。** M2–M4 落地前，G1–G4 靠 AI 纪律 + 你在关键点把关顶替。Loop 的形已经在跑（三问→实现→验收→沉淀→回边），闸的硬度还没到位。

两处当前环境事实（教材内容以此为准）：

- **仓库没有配 remote**。所以现在谈 push/PR/release 都是「本地语义」；真正的 GitHub PR / CI / release 要等 M5 配好 remote + Actions 才生效。
- 当前已回到 `main`，先行合 main 工单完成；`main` HEAD 为 `576d43b`。工作区仍有未提交文档/本地指导文件改动，继续遵守“不搭车”规则。

## 1. 你的职责清单（只有你能做的事）

流程里 AI 不许替你做的决定，共 7 个：

| # | 时点 | 你做什么 | 口令/形式 |
|---|---|---|---|
| J1 | `/prd` 三问后定级存疑 | 裁决 T0/T1 还是 T2+ | 一句话 |
| J2 | T2+ PRD 共创 | 回答高价值问题；**用户可见行为必须和你对齐 mockup/时序图后才许写码** | plan mode 里确认 |
| J3 | `/split` Boundary Pass 命中 blocking finding | 四选一：最小路径 / 缩范围 / 继续 / 重设计 | 明确选择 |
| J4 | PLAN confirmation | 允许**开始实现**（注意：≠ 允许 commit/push/archive） | "开始吧" |
| J5 | **完成信号**（最重要的一个） | 验收报告逐条对照后，你发信号才许 commit+归档 | `任务完成` / `验证通过` / `通过` / `可以提交` / `可以提交并归档` / `这个任务 OK`；限制词优先：`先别提交` / `不要归档` / `还要改` |
| J6 | merge 撞车 / trade-off 不可兼得 | 拍板保哪边，理由记 governance.md | 明确选择 |
| J7 | **push / release** | 只有你能下命令，AI 永不自行 push | `push` / `发版` |

记忆法：**你是三道闸的人肉钥匙 —— 开工闸（J4）、收货闸（J5）、出厂闸（J7）**。其余时间 AI 自转，你只在被 AskUserQuestion 打断时出现。

## 2. 何时 commit

**单位：可运行检查点。** 一个 commit = 一个能跑通验证命令的状态，不是"今天下班了"。

| 场景 | 谁触发 | 规则 |
|---|---|---|
| child/轻卡实现中 | AI | 每到一个可运行检查点（测试绿）即可 commit 到**任务分支**，不需要你的信号 |
| child 完成 → soft archive | **你（J5）** | 完成信号后才许最终 commit + 写 hash 进 stage-report/task.json |
| parent closeout | **你** | closeout review 无缺口 → 你确认 → archive commit |
| 工作流/Trellis 状态变更 | AI | 独立 `chore(trellis):` commit，不与源码混 |

三条铁律：

1. 完成信号前，任务分支上的 commit 是**工作存档**，可以有；`main` 上绝不出现未验证代码。
2. commit message 解释 why 不是 what，带 `[task-slug]`。
3. 别的窗口的脏改绝不混入（这次 M1 commit 排除 gitnexus skills 遗留脏改就是范例）。

## 3. 何时 PR

**单位：一个 child（或一张轻卡）= 一个 PR。** 永远不要把多个 child 打包成一个 PR。

时点：child 实现完 + `trellis-check` 全绿 + 验收报告写好 → 发 PR → 你 J5 验收。

| 阶段 | PR 的形态 |
|---|---|
| 现在（无 remote） | 「本地 PR」：任务分支 → 逐条自查 7 坑（BASE 钉死 / 临时 index / 删除行审查 / migrations 硬闸 / diff-tree 终验 / `[task-slug]` 标题 / lint 预检）→ 高风险面交 Codex rescue review → 你验收后 merge 进 main |
| M3 后 | `trellis_pr.sh` 一条命令固化上述 7 坑 |
| M5 后 | 真 GitHub PR + required checks，CI 非绿不可 merge（G6） |

高风险交付面（migrations / workflows / common / 公共 API）→ **必须** Codex rescue review，报告「漏做哪条 / 超做哪条」对照卡上 success:/范围，才到你验收。

## 4. 何时开 worktree

**默认：不开。** 单窗口串行开发（现状）永远直接在任务分支上干，worktree 是并行工具不是仪式。

开 worktree 的三个且仅有的时机：

| 时机 | 做法 |
|---|---|
| CC 和 Codex **同时**各做一个 child（M6 并行开发） | 每个 agent 一个 worktree，各在自己的 `cc/*` / `codex/*` 分支，物理隔离防撞车；claim-guard（owner/touches）此时升 BLOCK |
| 一个长任务进行中，你要**紧急插队**修 hotfix | 主工作区不动，开 worktree 从 main 切 hotfix 分支，修完 merge 回，删 worktree |
| review 别人分支同时不想丢自己现场 | 临时 worktree checkout 对方分支，看完即删 |

M6 并行最小命令序列：

```bash
git worktree add ../Hermes-stock-m6-2 -b codex/workflow-v2-m6-2-merge-collision-protocol HEAD
git worktree add ../Hermes-stock-m6-3 -b cc/workflow-v2-m6-3-worktree-parallel-trial HEAD

cd ../Hermes-stock-m6-2
python3 ./.trellis/scripts/task.py claim .trellis/tasks/07-04-workflow-v2-m6-2-merge-collision-protocol --owner codex

cd ../Hermes-stock-m6-3
python3 ./.trellis/scripts/task.py claim .trellis/tasks/07-04-workflow-v2-m6-3-worktree-parallel-trial --owner cc

printf '{"tool_input":{"file_path":".trellis/scripts/conflict_checklist.py"}}' | TRELLIS_OWNER=cc python3 ./.claude/hooks/claim_guard.py
# 期望：exit 2，证明 cc 不能碰 codex-owned M6-2 路径。

git worktree list
git status --short --branch
python3 ./.trellis/scripts/board.py --summary --max-lines 10
git diff --check

# 两边验收、合并、soft archive 后再清理：
python3 ./.trellis/scripts/task.py release .trellis/tasks/07-04-workflow-v2-m6-3-worktree-parallel-trial --owner jym --reason "M6 closeout"
cd ../Hermes-stock-m6-2
python3 ./.trellis/scripts/task.py release .trellis/tasks/07-04-workflow-v2-m6-2-merge-collision-protocol --owner jym --reason "M6 closeout"
cd ../Hermes\ stock
git worktree remove ../Hermes-stock-m6-2
git worktree remove ../Hermes-stock-m6-3
git worktree prune
git branch -d codex/workflow-v2-m6-2-merge-collision-protocol cc/workflow-v2-m6-3-worktree-parallel-trial
```

判据一句话：**同一时刻有两件事需要两个不同的 checked-out 状态才开；否则分支切换够用。**

## 5. 何时 push / release

### push

- **触发人：只有你（J7）。** AI 完成任何工作后的默认状态都是 `Pushed: no`。
- 前置：child 已验收（J5）+ commit 干净 + 不含私有运行态（`.trellis/.gitignore` 已挡 `.runtime/.developer` 等，但 push 前 AI 仍须过一遍 diff）。
- M5 后追加：G4 pre-push（freshness+lint）自动拦，CI 非绿拦 staging deploy。
- 节奏建议：**每个 child 验收后 push 一次**，不要攒。攒 push = 攒风险。
- 现状：remote 未配，push 暂不可用 —— 配 remote 是 M5 第一个 child。

### release

**单位：parent closeout。** child 级不 release。

```text
parent 全部 child 完成/取消 → RTM 补齐 → closeout review（rescue review）无缺口
→ 你确认 closeout（J5 的 parent 版）
→ merge main → push → CI 绿 → staging 部署（M5 后）
→ 用户可见行为：staging 真机验收（Playwright/CDP + 你人肉 dogfood）
→ 纯内部：CI 收口
→ ACCEPTANCE 报告逐条对照 → 你说「发」→ tag + release
```

M5 前的简化版：parent closeout + 你确认 = "release"（本地 tag 可打可不打）。

## 6. 全流程一页图（你的视角）

```text
【你】"开工" ──→ BOARD 亮板 + 催办（M4 后自动）
        │
        ▼
     /prd 三问 ──定级存疑──→【你 J1 裁决】
        ├─ T0/T1 ──→ Fast Loop：AI 实现+自验 ──→ 验收报告 ──→【你 J5 完成信号】──→ commit+归档+沉淀
        └─ T2+  ──→ plan mode 共创 ──→【你 J2 对齐 mockup/答问】
                      │
                      ▼
                 Codex 审 PRD（你只需把审查提示词粘去 Codex）
                      │
                      ▼
                 /split ──blocking finding──→【你 J3 四选一】
                      │
                      ▼
                 child 依赖序逐个：
                   【你 J4 PLAN confirmation】→ AI 红命令实现（检查点自由 commit）
                   → check 全绿 → 本地 PR 7 坑自查（高风险→Codex rescue review）
                   → 验收报告逐条对照 ──→【你 J5 完成信号】──→ commit + soft archive（自动回写 parent）
                   →（撞车/trade-off →【你 J6 拍板】）
                      │
                      ▼
                 parent closeout：RTM+review 无缺口 ──→【你确认】──→ archive
                      │
                      ▼
                 【你 J7】"push" / "发版" ──→ (M5 后) CI 绿 → staging 真机 → tag
        │
        ▼
     沉淀 ≤1 行 → 回边 → "还有下一件活?" → 循环
```

## 7. 立即待办（按序）

1. **重审 M2–M6 PLAN**：确认 M2-0 自举修正已覆盖 `/split --tier`、真实 git G3、G1 写盘前拦截、M2–M4/M5 parent 边界。
2. PLAN 审查通过后，用 `/split` 建 M2–M4 parent `workflow-v2-mechanization`，再按 child 顺序动工 M2。
3. 遗留脏改（如 `CLAUDE.md`）单独看一眼，要么独立 commit 要么丢弃，不许搭车。
4. M5 另建独立 parent 配 remote + GitHub Actions，push/PR/release 从"本地语义"转正。
