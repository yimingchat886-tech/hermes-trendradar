# Trellis Full Development Workflow Report

Date: 2026-07-03

## Bottom Line

这份报告只描述当前 Trellis 开发过程，不覆盖具体业务依赖或运行期工具接入。完整工作流是：

```text
自然语言目标
  -> PRD 前设计输入
  -> PRD 共创
  -> 主 PRD / PRD Map
  -> Trellis parent task
  -> Oracle PRD 审查
  -> 架构边界检查
  -> child task 拆解
  -> child PLAN / Oracle 条件审查
  -> child 实现
  -> Trellis check + Ponytail + GitNexus
  -> completion signal
  -> child commit + soft archive
  -> parent RTM 汇总
  -> parent closeout review
  -> parent commit + built-in archive
  -> journal / finish-work
```

核心分工：

- PRD 管产品目标、边界、验收和 PRD Map。
- Parent task 管阶段治理、child 索引、冲突审查、Oracle 预算、RTM 和汇总报告。
- Child task 管单个可验证实现切片。
- Oracle 管高风险外部审查。
- Ponytail 管复杂度和过度架构。
- GitNexus 管代码影响面和变更检测。
- Trellis 管状态、证据、归档和会话记录。

## 1. Workflow Layers

| 层级 | 目的 | 典型文件 / 工具 |
|---|---|---|
| Product intake | 把自然语言目标变成 PRD-ready 设计输入 | `trellis-prd-design-input` |
| PRD co-creation | 和用户迭代目标、范围、验收 | `trellis-brainstorm` |
| Main PRD | 记录主目标、边界、架构、PRD Map | `docs/PRD/*` 或 parent `prd.md` |
| Parent task | 管阶段范围、child 计划、RTM、Oracle 预算 | `.trellis/tasks/<parent>/` |
| Architecture boundary | child 拆解前防止硬编码、厚 adapter、过度抽象 | `trellis-architecture-boundary` |
| Child task | 管一个最小可验证实现单元 | `.trellis/tasks/<child>/` |
| Implementation context | 读取 spec、任务、代码影响面 | `trellis-before-dev`, GitNexus |
| Verification | 测试、lint/typecheck、Ponytail、GitNexus detect | `trellis-check` |
| Knowledge capture | 把反复规则沉淀回 spec | `trellis-update-spec`, `trellis-break-loop` |
| Finish | commit、archive、journal | Git, `trellis-finish-work` |

## 2. Phase 0: PRD 前设计输入

触发条件：用户明确要做 task、PRD、feature plan、parent-child planning，或给出自然语言目标并准备讨论落地。

使用：

- `trellis-prd-design-input`

目标：

- 复述原始目标。
- 推导真实目标。
- 优先问实现边界。
- 判断是否需要主 PRD + 辅 PRD。
- 判断是否进入 Trellis parent-child。

边界：

- 不固定问题清单。
- 不为了填模板强问用户。
- 能从 repo、已有 PRD、task、代码、配置推导的信息先自己查。
- 如果目标还不稳定，不进入 child 拆解。

产物：

```md
## Pre-PRD Design Input

### Original Goal

### Real Goal

### Implementation Boundary
- In:
- Out:
- Later:
- Readiness level:

### Workable Scope

### Risks And Constraints

### Open Questions
- Blocking:
- Preference:
```

## 3. Phase 1: PRD 共创

使用：

- `trellis-brainstorm`

目标：

- 先建或确认 task，避免想法只留在聊天。
- 一次只问一个高价值问题。
- 先查 repo/PRD/spec，再问用户。
- 发散考虑未来演进、相关场景、失败边界，再收敛 MVP。
- 输出可验收 PRD。

主 PRD 要包含：

- Goal
- Requirements
- Acceptance Criteria
- Definition of Done
- Technical Approach
- Decision / ADR-lite
- Out of Scope
- Technical Notes
- PRD Map

PRD Map 用来管理主 PRD 和辅 PRD：

```md
## PRD Map

| Block | PRD | Type | Purpose | Status | Depends On | Trellis Mapping |
|---|---|---|---|---|---|---|
| Overall framework | <main-prd>.md | Main | 架构、范围、共享数据流 | Draft/Accepted | - | Parent task |
| <feature> | <feature-prd>.md | Auxiliary | <功能细化范围> | Draft/Accepted | <block> | Child candidate |
```

## 4. Phase 2: Trellis Parent Task

触发条件：主 PRD 边界已稳定，需要进入阶段治理。

创建命令：

```bash
python3 ./.trellis/scripts/task.py create "<parent title>" --slug <parent-slug>
```

Parent artifact set：

```text
prd.md
child-task-index.md
conflict-review.md
oracle-review-budget.md
rtm-delta.md
subphase-report.md
implement.jsonl
check.jsonl
task.json
```

Parent 负责：

- 记录主 PRD 和阶段范围。
- 建立 child 候选列表和依赖。
- 做冲突审查。
- 建立 Oracle review budget。
- 建立 RTM requirement 映射。
- 汇总 child evidence。
- Parent 不直接承载大规模代码实现。

## 5. Phase 3: Oracle PRD 审查

使用：

- Oracle / GPT-5.5 Pro 外部审查路径。
- `.trellis/spec/project/oracle-review-policy.md`

必跑场景：

- T3/T4 parent 的 PRD first complete draft。
- 高风险 T2/T3/T4 child PLAN。
- unresolved blocker。
- T3/T4 parent closeout code/report review。

通常跳过：

- T0/T1。
- ordinary child PLAN。
- low-risk implementation。
- 已有强验证的普通 stage-report。

Parent 必须维护：

```md
# Oracle Review Budget

| Checkpoint | Required | Trigger | Reason | Expected Cost | Decision |
|---|---:|---|---|---:|---|
| PRD first draft | yes | parent PRD ready | intent/risk review | high | run |
| child PLAN | conditional | high-risk child | architecture/data/security risk | medium | skip/run |
| blocker | yes | unresolved blocker | avoid guessing | high | run if occurs |
| closeout code review | yes | parent closeout | final evidence review | high | run |
```

如果 Oracle 不可用：

- T0/T1 可跳过并记录原因。
- ordinary T2 可用本地 checklist fallback。
- high-risk T2 需要阻塞或让用户明确同意降级。
- T3/T4 默认阻塞，除非用户明确接受降级。

## 6. Phase 4: 架构边界检查

触发时间：主 PRD / parent PRD 已接受，但 child task 还没拆，或 child index 仍是草稿。

使用：

- `trellis-architecture-boundary`
- `.trellis/spec/project/ponytail-boundary.md`

产物：

```md
## Architecture Boundary Pass

### External Boundaries
- External I/O:
- Required adapter/client:
- Config/env needs:

### Child Split Rules
- Code children:
- Non-code/docs children:
- Dependencies:

### Delivery Rules
- Branch:
- Commit checkpoint:
- Push policy:
- Verification:

### Risks To Block Before Split
- ...
```

8 条边界规则：

1. `main` 只收已验证结果。
2. 每个代码 child 一个分支，分支名带 Trellis task slug。
3. Commit 以可运行检查点为单位。
4. Push 只推代码成果，不推私有 AI/Trellis 状态。
5. PR 合并前检查 diff、secrets、测试、任务范围。
6. 外部依赖必须走薄 adapter/client。
7. 环境差异和敏感信息进 config/env；普通业务常量不要滥进 env。
8. 每个代码任务必须有最小测试或验证命令。

Ponytail blocking finding：

- 新生产依赖。
- 新 framework。
- 架构边界变化。
- 新抽象层。
- broad directory reorganization。
- module rewrite。
- 删除或替换 public API。
- child task 偷扩成更大 feature。

遇到 blocking finding 时，先停下让用户选最小路径、缩小范围、继续当前 PLAN 或重设计。

## 7. Phase 5: Child Task 拆解

创建命令：

```bash
python3 ./.trellis/scripts/task.py create "<child title>" --slug <child-slug> --parent <parent-dir>
```

Child artifact set：

```text
prd.md
implement.md
implement.jsonl
check.jsonl
stage-report.md
task.json
```

Child 拆分标准：

- 一个 child 只有一个主要交付物。
- 可以独立验证。
- 不实现 sibling child 的未来范围。
- 有明确依赖。
- 有明确 branch plan。
- 有至少一个测试或 smoke command。
- 高风险 child 有 Oracle PLAN review 决策。

Child task 进入实现前，应先确认：

- child PRD 是否完整。
- implement.md 是否写明实现边界。
- `implement.jsonl` / `check.jsonl` 是否有必要上下文。
- 是否需要 Oracle。
- 是否需要用户 PLAN confirmation。

## 8. Phase 6: Child 实现

启动 child：

```bash
python3 ./.trellis/scripts/task.py start <child-task-dir>
```

使用：

- `trellis-before-dev`
- GitNexus impact/context/detect-changes
- Ponytail

实现顺序：

1. 读 active task 的 `prd.md`、`implement.md`、context JSONL。
2. 读 `.trellis/spec/guides/index.md` 和相关 project specs。
3. 修改源码符号前运行 GitNexus impact analysis。
4. 实现最小可运行切片。
5. 不加 speculative abstraction。
6. 不加无必要依赖。
7. 不扩大 child 范围。
8. 留下最小测试或 smoke。

实现期间的硬规则：

- PLAN confirmation 只允许开始实现，不允许 commit、push、archive 或跳过验证。
- 如果发现 PRD 错误，回到 PRD/PLAN 修正，不在代码里偷偷改范围。
- 遇到重复 bug，使用 `trellis-break-loop` 分析 root cause，并把可复用规则沉淀到 spec。

## 9. Phase 7: Trellis Check

使用：

- `trellis-check`
- Ponytail review
- GitNexus detect-changes

检查顺序：

```bash
git diff --name-only HEAD
git status
python3 ./.trellis/scripts/get_context.py --mode packages
<focused tests>
git diff --check
npx gitnexus detect-changes --repo "<repo-name>"
```

必须确认：

- 改动只属于当前 child。
- 相关测试 / smoke 已运行。
- lint/typecheck 使用项目已有工具。
- Ponytail review 没发现可删除复杂度。
- 没有 debug code、临时日志、无关格式化。
- 高风险路径有更宽检查。

Ponytail review 只砍复杂度，不砍：

- validation
- security
- accessibility
- data-loss protection
- correctness checks
- 用户明确要求

## 10. Phase 8: Completion Signal And Soft Archive

Staged child 完成后，先报告：

- 实际改动。
- 验证命令。
- 未验证项。
- missed/extra scope。
- commit plan。
- soft archive plan。
- `Pushed: no`。

等待用户完成信号：

- `任务完成`
- `验证通过`
- `通过`
- `可以提交`
- `可以提交并归档`
- `这个任务 OK`

如果用户说 `先别提交`、`不要归档`、`还要改`，限制优先。

Child commit approval 默认也允许 soft archive，除非用户显式排除。

Soft archive 步骤：

1. commit 当前 child 范围。
2. 写 commit hash 到 `stage-report.md`。
3. 写 commit hash 和 `soft_archive_completed = true` 到 child `task.json`。
4. 保留 child task 目录，供 parent 汇总。
5. 不调用 built-in `task.py archive` 归档 child。

## 11. Phase 9: Spec Update / Break Loop

使用：

- `trellis-update-spec`
- `trellis-break-loop`

触发：

- 新命令/API/DB/schema/env contract。
- cross-layer contract。
- 新外部集成边界。
- 反复 bug 的 root cause。
- 新 project convention。
- 新 forbidden pattern。

原则：

- 具体实现规则写 `.trellis/spec/<layer>/`。
- 思考检查清单写 `.trellis/spec/guides/`。
- 不把重要经验只留在聊天里。

对于 bug：

1. 分析 root cause category。
2. 解释为什么之前修复失败。
3. 设计 prevention mechanism。
4. 找相似风险面。
5. 更新 spec 或 guide。

## 12. Phase 10: Parent Closeout

当 child 完成、deferred 或明确 blocked 后，parent 汇总：

- `child-task-index.md`
- `rtm-delta.md`
- `subphase-report.md`
- child `stage-report.md`
- Oracle closeout review

Parent acceptance 表示整个 parent scope 可以关闭。

Parent acceptance 后：

1. commit approved parent evidence。
2. 使用 built-in `task.py archive <parent>`。
3. archive parent，不是 child soft archive。
4. push 仍需用户明确命令。

## 13. Phase 11: Finish Work

使用：

- `trellis-finish-work`

作用：

- 检查当前 git 状态。
- 确认没有未提交的当前任务代码。
- archive active task。
- 记录 session journal。

关键规则：

- `finish-work` 不负责代码 commit。
- 代码 commit 必须先在 Phase 3.4 完成。
- 如果工作区还有当前任务源码脏改，先回到 commit 阶段。
- 其他窗口的无关脏改要明确排除，不混入当前任务。

## 14. Skill / Tool Routing Table

| 时间点 | 使用对象 | 用途 |
|---|---|---|
| 用户自然语言目标 -> PRD 前 | `trellis-prd-design-input` | 产品经理视角拆目标和实现边界 |
| PRD 共创 | `trellis-brainstorm` | 逐问逐答、PRD 迭代、MVP 收敛 |
| 主 PRD 完成 -> child 未拆 | `trellis-architecture-boundary` | 架构边界、child 拆分规则、delivery gate |
| 高风险 PRD/PLAN/closeout | Oracle | 外部审查 intent、scope、risk、evidence |
| 实现前 | `trellis-before-dev` | 读取 specs 和 checklist |
| 源码影响面 | GitNexus | impact/context/detect-changes |
| 实现复杂度 | Ponytail | 防过度抽象、防无用依赖、最小实现 |
| 代码完成后 | `trellis-check` | 测试、lint/typecheck、Ponytail、scope check |
| 重复 bug 后 | `trellis-break-loop` | root cause、prevention、spec 更新 |
| 学到可复用规则 | `trellis-update-spec` | 写回 `.trellis/spec/` |
| 会话收尾 | `trellis-finish-work` | archive + journal |

## 15. Minimal Command Sequence

```bash
# create parent
python3 ./.trellis/scripts/task.py create "<parent>" --slug <parent-slug>

# create child under parent
python3 ./.trellis/scripts/task.py create "<child>" --slug <child-slug> --parent <parent-dir>

# start child implementation
python3 ./.trellis/scripts/task.py start <child-task-dir>

# validate task context
python3 ./.trellis/scripts/task.py validate <child-task-dir>

# inspect context/specs
python3 ./.trellis/scripts/get_context.py
python3 ./.trellis/scripts/get_context.py --mode packages

# verify code task
git diff --check
npx gitnexus detect-changes --repo "<repo-name>"

# archive parent only after acceptance
python3 ./.trellis/scripts/task.py archive <parent>
```

## 16. What This Workflow Prevents

- PRD 还没清楚就拆 child。
- Parent 直接塞大规模实现。
- Child 偷做 sibling scope。
- PLAN approval 被误当 commit/push/archive approval。
- Low-risk child 过度 Oracle 审查。
- High-risk child 没有 Oracle 审查。
- Ponytail 被误用来砍安全和验证。
- 未验证代码进入 `main`。
- 私有 AI/Trellis 状态被误推。
- 经验只留在聊天里，下一次重复踩坑。

## 17. One-Screen Summary

```text
PRD input: trellis-prd-design-input
PRD co-create: trellis-brainstorm
Parent task: prd + child-task-index + conflict-review + oracle-budget + rtm + subphase
PRD review: Oracle if T3/T4
Boundary: trellis-architecture-boundary + Ponytail
Child split: one verifiable outcome per child
Implementation: trellis-before-dev + GitNexus + minimal code
Verification: trellis-check + tests + Ponytail + detect-changes
Completion: user signal required
Child closeout: commit + soft archive, no built-in archive
Parent closeout: aggregate evidence + Oracle closeout + commit + built-in archive
Finish: trellis-finish-work journal
```
