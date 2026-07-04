---
name: split
description: "Workflow v2 拆分：PRD 通过 Codex 审查后，创建 2 件套 parent（薄 prd.md + governance.md）、过 Boundary Pass、拆 child、跑 validate 闸。Examples: \"拆任务\"、\"建 parent\"、\"PRD 审完了，拆 child\""
---

# /split — Parent 创建 · 边界检查 · Child 拆解

规范来源：`docs/reports/trellis-loop-workflow-v2.md` §3.3。前置条件：PRD 已在 `docs/PRD/` 且已过 Codex 跨 AI 审查（`/prd` Step 5）。前置不满足 → 停，回 `/prd`。

## Step 1: 建 parent（2 件套）

```bash
python3 ./.trellis/scripts/task.py create "<parent title>" --slug <parent-slug>
```

**`prd.md` = 薄指针，≤20 行，禁止复制 PRD 正文**：

```md
# Parent: <title>
## Source PRD
- docs/PRD/<file>.md        （唯一事实源）
## Stage Scope
- In: <本阶段切片>
- Out: <本阶段不做，PRD 里属于 Later 的部分>
## Stage Constraints
- <阶段性约束，如依赖序、live 边界>
```

**`governance.md` = 四 section**（状态列由机器回写，人只写判断性内容）：

```md
# Governance: <title>

## Child Index
| Child | 交付物 | 依赖 | Owner | Branch | Status | Commit |
|---|---|---|---|---|---|---|

## RTM
| REQ-ID | Child | Status | Evidence |
|---|---|---|---|

## External Review
### PRD Review (Codex, <date>)
<逐条结论 / 豁免理由 —— 来自 /prd Step 5>
### Closeout Review
<parent closeout 时由 rescue review 填写>

## Boundary Pass
<Step 2 逐条结果>
```

## Step 2: Boundary Pass（拆 child 前必过）

8 条边界规则逐条写结论（pass / 例外+理由）：

1. `main` 只收已验证结果。
2. 每个代码 child 一个分支：CC 实现 → `cc/<child-slug>`，Codex 实现 → `codex/<child-slug>`。
3. Commit 以可运行检查点为单位。
4. Push 只推代码成果，不推私有 AI/Trellis 运行态。
5. PR 合并前查 diff / secrets / 测试 / 任务范围。
6. 外部依赖走薄 adapter/client。
7. 环境差异和敏感信息进 config/env；普通业务常量不滥进 env。
8. 每个代码 child 至少一条测试或 smoke 命令。

再过 **Ponytail blocking finding**（新依赖 / 新框架 / 边界变化 / 新抽象层 / 目录重组 / rewrite / 改删公共 API / child 偷扩）：命中 → **停**，让用户在「最小路径 / 缩范围 / 继续 / 重设计」中选择，记入 Boundary Pass。

## Step 3: 拆 child

标准（每条不满足即重拆）：

- 一个 child 只有一个主要交付物，独立可验证。
- 不实现 sibling 的未来范围。
- 明确依赖序、明确 branch、至少一条测试/smoke 命令。
- 每个 child 挂 REQ-ID（写入 RTM 行）。

```bash
python3 ./.trellis/scripts/task.py create "<child title>" --slug <child-slug> --parent <parent-dir>
```

child `task.json` 补 `owner`（cc / codex）+ `touches`（预计触碰的路径列表，claim-guard 用）。
child `prd.md` 只写：目标一句话 + 对应 REQ-ID + 验证命令 + In/Out。

分工默认：高风险 child（Boundary Pass 中标记过风险面的）→ CC；普通 child → Codex。

## Step 4: validate 闸

```bash
python3 ./.trellis/scripts/task.py validate <parent-dir>
python3 ./.trellis/scripts/task.py validate <child-dir>   # 每个 child
```

必须全绿。检查项（M2 落地前手工核对同样清单）：

- governance.md 存在且含四 section，Boundary Pass 非空。
- External Review 的 PRD Review 非空。
- 每个 child 有 REQ-ID、验证命令、owner、双向 parent/children 指针一致。

## Step 5: 汇报并交接

向用户报告：child 清单（交付物/依赖序/owner 分派）+ Boundary Pass 结论 + 第一个可动工 child。用户确认后按依赖序进入实现（Loop ③，规范见 workflow v2 §4）。

**PLAN confirmation 只允许开始实现，不允许 commit / push / archive。**
