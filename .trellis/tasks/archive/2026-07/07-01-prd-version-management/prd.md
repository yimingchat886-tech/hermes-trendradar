# brainstorm: PRD version management

## Goal

为 Hermes stock 建立一套不频繁污染总 PRD 的版本化 PRD 管理方式：总 PRD 承载长期产品边界和路线图，版本 PRD 承载每个阶段可落地范围、验收标准和废弃决策。

## What I already know

- 原正式 PRD 已迁移为 `docs/PRD/releases/PRD_v1.3.md`。
- 新增总 PRD：`docs/PRD/PRD_MASTER.md`。
- 该 PRD 同时包含长期产品定位、总体架构、v1/v2 边界、飞书表格、工具层和 v1.3 具体收敛记录。
- 当前已执行的 v1 工作围绕“对标账号追踪 + 转录 + 拆解 + 卡片 + 选题池补充”，热点系统整体后置到 v2。
- 用户倾向使用一个稳定总 PRD 加多个版本 PRD；版本号放文件名，日期放 frontmatter。
- 用户已确认沿用现有 `docs/PRD/` 目录。
- 用户已确认 child task 仍按原 Trellis 机制放在 `.trellis/tasks/` 管理；`docs/PRD/` 只管理总 PRD 和版本 PRD。
- 用户已确认版本 PRD 对应 parent task，不对应 child task。
- 用户已确认保留当前事实版本号 `v1.3`，将旧根目录版本 PRD 落位为 `docs/PRD/releases/PRD_v1.3.md`。
- 用户已确认 `PRD_MASTER.md` 采用抽取式总 PRD，并在其中维护版本索引。
- 用户已确认迁移后删除旧根目录版本 PRD 路径，不保留跳转文件。

## Assumptions

- 本任务只讨论 Hermes stock 的落地方案，不抽象成跨项目通用规范。
- 正式 docs 暂不修改，直到讨论收敛。

## Requirements

- 总 PRD 不应随版本频繁改名，内容聚焦长期蓝图、产品边界、总体架构、长期路线图、版本索引和废弃决策。
- 总 PRD 采用抽取式：从当前版本 PRD 中提炼长期稳定内容，而不是只做空索引，也不是复制版本 PRD 全量细节。
- 总 PRD 必须包含版本索引，记录各版本 PRD 路径、状态、对应 parent task、版本目标和当前/废弃关系。
- 版本 PRD 独立成文档，内容聚焦单版本目标、范围、非目标、验收标准和版本内决策。
- 文件命名应避免过长；版本号放文件名，日期和状态放 frontmatter。
- 迁移现有 `PRDv1.3` 时应尽量减少路径 churn，并保留清晰索引或兼容说明。
- 目录沿用 `docs/PRD/`，不迁移到 `docs/prd/`。
- `docs/PRD/` 的职责边界：只存放 `PRD_MASTER.md` 和 `releases/PRD_v*.md` 这类版本 PRD。
- child task PRD、执行计划、验收记录、上下文 JSONL 继续保留在 `.trellis/tasks/<child-task>/`。
- 版本 PRD 与 parent task 对齐；一个版本 PRD 可以索引其下的 Trellis child tasks，但不替代 child task 的详细执行文档。
- 旧根目录版本 PRD 迁移为 `docs/PRD/releases/PRD_v1.3.md`；不倒改成 `v1.0`。
- 迁移后删除旧根目录版本 PRD 路径，避免 `docs/PRD/` 根目录同时存在旧版本 PRD 和总 PRD。

## Technical Approach

- 新建 `docs/PRD/PRD_MASTER.md`。
- 新建目录 `docs/PRD/releases/`。
- 将旧根目录版本 PRD 移动为 `docs/PRD/releases/PRD_v1.3.md`。
- 从 v1.3 中抽取长期稳定内容进入总 PRD：产品定位、目标用户、核心问题、产品边界、总体架构、长期路线图、版本规划、当前版本索引、废弃决策记录。
- 在 `PRD_MASTER.md` 的版本索引中记录 `v1.3`、状态、路径、对应 parent task、版本目标和 child task 管理边界。
- 保留版本 PRD 的具体范围、字段、阈值、表格设计、验收和版本内决策，不复制到总 PRD。

## Decision (ADR-lite)

**Context**: 原 `PRDv1.3.md` 同时承担长期蓝图和版本落地职责，后续版本迭代容易污染母文档。

**Decision**: 沿用 `docs/PRD/`；建立抽取式 `PRD_MASTER.md`；版本 PRD 放入 `docs/PRD/releases/`；`PRD_v1.3.md` 对应现有 parent task 1；child task 继续由 Trellis 管理。

**Consequences**: 总 PRD 更稳定，版本 PRD 能按 parent task 独立演进；旧路径会被删除，后续必须通过总 PRD 的版本索引查找版本文档。

## Acceptance Criteria

- [x] 明确总 PRD 与版本 PRD 的职责边界。
- [x] 明确 Hermes stock 沿用 `docs/PRD/`。
- [x] 明确 Hermes stock 的文件命名方案。
- [x] 明确 child task 不进入 `docs/PRD/`。
- [x] 明确版本 PRD 对应 parent task。
- [x] 明确现有 v1.3 PRD 的处理方式。
- [x] 明确迁移后删除旧路径，不保留跳转文件。
- [x] 正式实现时只修改 PRD/docs 文件，不改源码、测试、配置或生成文件。

## Open Questions

- 无。

## Implementation Plan

- [x] 移动旧根目录版本 PRD 到 `docs/PRD/releases/PRD_v1.3.md`。
- [x] 创建抽取式 `docs/PRD/PRD_MASTER.md`。
- [x] 检查 `git diff -- docs/PRD .trellis/tasks/07-01-prd-version-management/prd.md`。
- [x] 运行 `git diff --check -- docs/PRD .trellis/tasks/07-01-prd-version-management/prd.md` 和 `git status --short`。

## Out of Scope

- 本轮不重写产品范围，不改变 v1/v2 功能边界。
- 本轮不编辑源码、测试、配置、锁文件或运行时逻辑。

## Technical Notes

- Repo inspection before migration: `find docs -maxdepth 4 ...` only found the old root v1.3 PRD as a PRD file.
- Repo inspection before migration: non-hidden `rg` did not find old v1.3 PRD references outside the file itself.
- Implementation: `docs/PRD/PRD_MASTER.md` and `docs/PRD/releases/PRD_v1.3.md` now define the PRD structure.
- Verification: old root version PRD path is removed; master/release paths and internal link targets exist; no trailing whitespace found in changed Markdown/JSON files.
- Current git status before formal docs implementation already has unrelated untracked docs under `docs/reports/` and `docs/runbooks/production-deployment-handoff.md`; avoid touching them.
