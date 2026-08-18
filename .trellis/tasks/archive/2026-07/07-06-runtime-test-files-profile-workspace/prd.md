# Codify runtime test files in Hermes profile workspace

## What will change?

建立一条明确约定：运行时测试产生的临时文件、私有证据、本地 profile 派生配置、raw dump、媒体缓存、数据库副本和 smoke 输出，默认放在 Hermes profile 工作区或现有外部运行区，不直接递交到 Hermes 源码仓库。

本任务只负责把这个约定沉淀到项目规则/文档；不迁移既有源码逻辑，不提交任何运行时测试产物。

## Why now?

近期 Hermes 运行时测试依赖本机 profile、CDP/MediaCrawler、FunASR、SQLite 和外部 run 证据。若把测试文件直接放进源码仓库，容易把本地状态、私有证据、缓存或大文件带入 git diff，污染后续任务和 review。

## How will it be verified?

- 更新后的规则明确区分源码仓库与 Hermes profile/外部运行工作区。
- `git status --short` 中不出现运行时测试产物、缓存、raw media、SQLite run DB 或私有 profile 派生文件。
- `git diff --check` 通过。

## Requirements

- 运行时测试文件默认写入 Hermes profile 工作区；已有 `_external/hermes-stock-runs/` 约定的 run evidence 继续放在 `_external`，不移动进源码仓库。
- 源码仓库只保留可复用的代码、样例 profile、脱敏文档、测试代码和必要的规则说明。
- 禁止把以下内容直接递交到源码仓库：登录态、cookies、proxy/CDP endpoint 明文、raw videos、runtime dumps、临时 SQLite DB、model cache、venv、第三方 checkout、未脱敏 smoke 输出。
- 如果某个运行时测试需要可复现实例，提交最小脱敏 fixture 或生成脚本；真实运行证据仍留在 profile/外部工作区。

## Acceptance Criteria

- [x] 项目级规则或 runbook 记录“运行时测试文件放在 Hermes profile 工作区/外部运行区，不直接提交源码仓库”的约定。
- [x] 规则说明覆盖允许提交与禁止提交的文件类型。
- [x] 当前任务 diff 只包含规则/文档和 Trellis 任务文件，不包含运行时测试产物。
- [x] `git diff --check` passes.

## Out of Scope

- 迁移历史 run evidence。
- 改造 CLI、runner 或 profile loader 行为。
- 提交任何 Hermes profile 工作区文件。
- 推送、归档或提交 git；这些仍需要用户单独指令。

## Evidence Model

- `task.json.meta.workflow_mode` is `harness_state_machine`.
- `prd.md` records scope and verification.
- `stage-report.md` records acceptance and verification evidence.
- Do not add legacy staged metadata.

## Technical Notes

- 现有项目规则已约定 `_external` 承担第三方 checkout、venv、model cache、temp media 和 smoke run outputs；本任务补上 Hermes profile 工作区侧的运行时测试文件边界。
- Ponytail boundary: 先写规则，不加新目录同步脚本、watcher、cleanup job 或 git hook；只有实际反复污染 git diff 时再加自动化。

## Protocol Gates

Use `.trellis/spec/project/protocol-phrases.md` for completion, commit,
archive, limit, and push wording. Do not copy the phrase table here.
