---
project: Hermes Agent
doc_type: release_prd
version: v2.0 Extra
status: accepted
created: 2026-08-18
updated: 2026-08-18
owner: Jym
parent: ../PRD_MASTER.md
---

# PRD v2.0 Extra：面向 Agent 的视频获取器

## 0. 文档边界

本文件是 v2.0 Extra 的已接受产品契约。它定义新增视频来源、下载途径、任务编排和 Agent 文件交付契约。

原 `PRD_v2.0.md` 及 Hermes 分析链路保留但暂停；v2.0 Extra 不删除、不重写，也不自动调用该链路。执行状态只进入 Trellis SQLite authority，不写入本文件。

## 1. Goal

让 HyperFrames + AI 工作流中的 Agent 能提交一个视频获取任务，并在任务结束后稳定取得可直接消费的本地媒体文件和机器可读 manifest，而不需要理解各视频平台的采集、下载、重试和临时文件细节。

```text
Agent Reach / 其他来源工具
  -> Agent 提交明确来源
  -> 来源适配与元数据归一化
  -> 下载、校验、重试与原子落盘
  -> 任务编排器
  -> Agent completion envelope + media manifest + 本地文件
  -> HyperFrames / 其他 AI 工作流
```

## 2. Architecture Boundary

本项目负责：

- 接收 Agent 的结构化任务。
- 解析受支持的视频来源并归一化元数据。
- 下载媒体、校验文件、生成哈希并原子落盘。
- 编排任务状态、逐项错误和安全重试。
- 向 Agent 返回稳定的 JSON completion envelope 和 manifest 引用。

本项目不负责：

- 调用或编排 HyperFrames 的选材、剪辑、生成、渲染或发布。
- 替 Agent 决定视频如何使用。
- 将媒体写入 HyperFrames 项目目录；接收 Agent 自行 adopt/copy。
- 激活原 Hermes 分析、digest、反馈或飞书路径。

Agent Reach 作为上游来源能力参考：它负责帮助 Agent 发现、读取并判断当前可用的平台工具；Agent 将选中的明确 URL 或结构化来源条目交给本项目。本项目不把 Agent Reach 的搜索结果直接当作下载成功，也不接管其安装、登录态或后端路由。

## 3. Agent Delivery Contract

任务完成后的直接接收者是 Agent，而不是人工 UI，因此交付必须满足：

- stdout 只输出一个版本化 JSON envelope；运行日志写 stderr。
- envelope 至少包含 `schema_version`、`job_id`、`status`、`manifest_ref`、`completed_at`、`expires_at`、成功/失败计数和稳定错误码。
- `status` 只使用 `succeeded`、`partial`、`failed`；部分失败不得伪装成成功。
- manifest 每项至少包含稳定资产 ID、来源平台、原始来源 URL、本地绝对路径、媒体类型、字节数、SHA-256、下载状态、错误码和 `retryable`。
- completion 只在 manifest 原子写入且成功文件可读后产生；临时文件不得出现在交付中。
- 同一 `job_id` 可安全恢复或重试失败项，不重复覆盖已校验成功的文件。
- Agent 不需要解析自然语言才能判断下一步；敏感下载 URL、Cookie、token 和本机配置不得进入可分享日志。

## 4. Requirements

- `P20X-REQ-000` [owner: codex]: 建立 v2.0 Extra PRD，并将其登记为当前交付主线。
- `P20X-REQ-010` [owner: codex]: 首批支持 Douyin、YouTube、Bilibili 和通用 HTTP(S) 媒体直链；四类来源统一输出媒体 manifest。其他平台返回稳定的 `unsupported_source`，不得猜测下载。
- `P20X-REQ-015` [owner: codex]: 接受 Agent Reach 或其他上游来源工具产出的明确 URL/结构化来源条目，但下载资格与成功状态由本项目独立验证。
- `P20X-REQ-020` [owner: codex]: 为受支持来源提供可验证的下载途径，采用临时文件、内容校验和原子落盘。
- `P20X-REQ-025` [owner: codex]: 仓库保持零 Python 运行时依赖；平台能力通过 local profile 指向的外部 CLI 适配器提供，并由 healthcheck 验证可执行文件、版本和能力。缺失能力 fail closed。
- `P20X-REQ-030` [owner: codex]: 提供一个最小任务编排器，维护 job 与 item 状态，支持 `succeeded`、`partial`、`failed` 和失败项重试。
- `P20X-REQ-035` [owner: codex]: 编排器首版提供同步 `fetch`、`status`、`retry` CLI；每个 job 在外部 run root 内原子保存独立 JSON 状态，不建设 daemon、队列或中心数据库。
- `P20X-REQ-040` [owner: codex]: 提供 §3 定义的 Agent completion envelope；CLI 退出码与 JSON 状态一致。
- `P20X-REQ-045` [owner: codex]: 终态 job 的媒体、manifest 和 job 状态从最近一次 `completed_at` 起保留 7×24 小时；到达 `expires_at` 后自动清理。运行中或加锁 job 不得删除。
- `P20X-REQ-047` [owner: codex]: 提供幂等 `cleanup --expired`；由平台原生每日 timer 调用，项目不常驻 daemon。timer 的安装、启用和首次真实清理分别保持独立授权。
- `P20X-REQ-050` [owner: codex]: 通过 manifest 和本地文件完成 HyperFrames 交接，本项目不反向依赖或调用 HyperFrames。
- `P20X-REQ-060` [owner: codex]: 原 Hermes CLI、数据和文档保留，但默认任务路径和调度不再触发 Hermes 分析链路。
- `P20X-REQ-070` [owner: codex]: 媒体与任务运行物只写到 Git 仓库外的受控 run root；路径越界、符号链接覆盖和凭据泄漏必须 fail closed。

## 5. Acceptance Criteria

- AC-1：Agent 提交一个有效任务后，只读取 JSON envelope 和 manifest 即可定位所有成功文件并判断失败项是否可重试。
- AC-2：每个成功文件均存在、非空、可读，其字节数和 SHA-256 与 manifest 一致；任务目录不残留 `.tmp` 文件。
- AC-3：一个包含成功项与失败项的任务返回 `partial`、非零失败计数和逐项稳定错误码；重试只处理失败项。
- AC-4：相同 `job_id` 重放不会重复下载或破坏已经通过哈希校验的文件。
- AC-5：至少完成一次 Agent -> downloader -> manifest -> HyperFrames adopt 边界验证；不要求本项目执行 HyperFrames 渲染。
- AC-6：原 Hermes 命令仍可显式调用，但 v2.0 Extra 默认入口、任务编排和任何新调度均不触发 Hermes 路径。
- AC-7：仓库内无下载媒体、临时文件、Cookie、token 或包含敏感查询参数的运行日志。
- AC-8：Agent Reach 返回的候选来源只有经过本项目下载、文件校验和 manifest 落盘后才计为成功；Agent Reach 未安装或不可用不影响直接 URL 任务。
- AC-9：Douyin、YouTube、Bilibili 和 HTTP(S) 直链均通过离线契约检查及各自的显式 smoke gate；未支持平台以 `unsupported_source` 结束对应 item，不阻塞其他 item。
- AC-10：缺失、版本不兼容或 healthcheck 不通过的外部 CLI 返回稳定的 `backend_unavailable`，不触发自动安装，也不回退到未经验证的实现。
- AC-11：`fetch` 被中断后，`status` 能读取最后一个完整状态；`retry` 能从该状态恢复。并发执行同一 `job_id` 时只有一个写者，其他调用返回稳定的 `job_locked`。
- AC-12：终态 envelope 明确给出 UTC `completed_at` 和精确晚 7×24 小时的 `expires_at`；未到期、运行中或加锁 job 不被清理，到期 job 的媒体、manifest、临时文件和 job 状态作为一个边界单元删除。
- AC-13：`cleanup --expired` 重复执行结果一致，只删除受控 run root 内已到期且未加锁的完整 job 目录；timer 未经单独授权不得安装、启用或执行真实清理。

## 6. Logical Checks

| Check ID | Proves |
|---|---|
| P20X-CHK-CONTRACT | completion envelope、manifest schema、退出码和状态一致。 |
| P20X-CHK-DOWNLOAD | 下载原子性、哈希、非媒体响应拒绝和失败清理。 |
| P20X-CHK-RESUME | job 重放、失败项重试和成功文件复用。 |
| P20X-CHK-BOUNDARY | run root、符号链接、日志脱敏和 Git 工作区边界。 |
| P20X-CHK-AGENT-HANDOFF | Agent 可仅凭机器输出完成 HyperFrames media adopt。 |
| P20X-CHK-SOURCE-REFERENCE | Agent Reach 候选只作为输入，不绕过下载校验，也不成为直接 URL 任务的硬依赖。 |
| P20X-CHK-BACKEND | 外部 CLI 的 profile、healthcheck、超时、退出码和 `backend_unavailable` fail-closed 行为。 |
| P20X-CHK-ORCHESTRATOR | 同步 `fetch/status/retry`、原子 job 状态、中断恢复和同 job 单写者锁。 |
| P20X-CHK-RETENTION | 7×24 小时过期计算、运行中/加锁保护、时钟边界和到期 job 整体清理。 |
| P20X-CHK-CLEANUP-TIMER | `cleanup --expired` 幂等性、路径边界，以及 timer 安装/启用/真实清理的独立授权门。 |
| P20X-CHK-HERMES-PAUSED | 默认路径未调用 Hermes 分析、digest、反馈或飞书能力。 |

## 7. Delivery Plan

1. 固定 Agent 请求、completion envelope 和 manifest 契约。
2. 复用现有下载安全边界，接入首批新视频来源与下载途径。
3. 实现最小 job/item 编排、恢复和失败项重试。
4. 完成 Agent 到 HyperFrames media adopt 的边界验证。
5. 验证原 Hermes 路径保留且默认暂停。

## 8. Out of Scope

- 在本项目内实现视频搜索推荐、语义选材或内容判断。
- HyperFrames composition、剪辑、渲染、发布和成片质量控制。
- Web UI、MCP server、消息队列或分布式调度器。
- 自动发布、飞书写入、Hermes 分析与 RAG。
- 未经确认的平台绕限制、Cookie 分发或登录态托管。
- 在本项目内安装、配置或封装 Agent Reach，或复制其平台后端路由。
- 由运行时自动安装、升级或修改外部下载 CLI；安装与升级保持独立授权。

## 9. Decisions

- **D1（暂行）Agent 输入边界**：首版只接收 Agent 提交的明确视频页面 URL、直接媒体 URL 或等价的结构化来源条目。搜索、账号发现、素材推荐和语义判断由上游 Agent 负责；只有真实工作流证明该边界不足时才重新讨论。
- **D2 Agent Reach 边界**：将 [Agent Reach](https://github.com/Panniantong/agent-reach) 作为上游来源能力与平台工具选型参考，不作为本项目的下载完成凭据、必装依赖或运行时包装层。
- **D3（暂行）首批下载来源**：保留 Douyin，新增 YouTube、Bilibili 和通用 HTTP(S) 媒体直链。Agent Reach 提供的小红书、Twitter 等候选只作为来源参考，首版不承诺下载。
- **D4 外部 CLI 适配器**：仓库保持零 Python 运行时依赖，不自研平台下载协议。外部 CLI 通过 local profile + healthcheck 接入；Agent Reach 可提供选型参考，但安装、升级和登录配置都需要独立授权。
- **D5 最小任务编排器**：首版使用同步 `fetch`、`status`、`retry` CLI，并在外部 run root 保存每 job 原子 JSON 状态；不建设 daemon、消息队列或中心数据库。
- **D6 七天保留期**：终态 job 的媒体与 job 状态从最近一次 `completed_at` 起保留 7×24 小时；每次成功 `retry` 以新的完成时间重新计算。Agent 必须从 envelope 的 `expires_at` 判断可用窗口。
- **D7 到期清理触发**：提供幂等 `cleanup --expired`，由平台原生每日 timer 调用；本项目不常驻 daemon。timer 的安装、启用和首次真实清理不由实现授权隐含。

## 10. Open Decisions

无剩余 material open decision。
