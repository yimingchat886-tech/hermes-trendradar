---
project: Hermes Agent
doc_type: release_prd
version: v2.0
status: draft
created: 2026-07-06
updated: 2026-07-06
owner: Jym
parent: ../PRD_MASTER.md
parent_task: ../../../.trellis/tasks/07-05-parent-v2-0-real-analysis-loop/
---

# PRD v2.0：真实分析闭环

## 0. 文档边界

本文件是 v2.0 release-level source of truth。它只记录版本目标、M0-M3 门、PRD Map 和 parent/child 边界。

不在本文维护的内容：

- child task 执行状态、stage report、commit hash、验证日志。
- 运行期 secret、Cookie、登录态、表 ID、field ID。
- v2.1 热点系统、Twitter/RSSHub/TrendRadar 多源设计。

执行状态唯一事实源是 `.trellis/`；长期路线图唯一事实源是 `docs/PRD/PRD_MASTER.md`。

## 1. 版本目标

v2.0 的目标是闭合 v1.4 留下的最大价值缺口：handoff package 已存在，但真实 Hermes 分析还没有稳定消费者。

最小闭环：

```text
hermes-agent skill / cron
  -> hermes-benchmark CLI
  -> handoff package
  -> Hermes 真实分析结果引用
  -> 内部群 digest
  -> 人工 adopt/reject feedback
  -> SQLite 审计记录
```

v2.0 继续 Douyin-only。集成物是 Hermes skill + 现有 repo CLI；不建设 MCP server，不把写表作为默认主线。

## 2. Non-Goals

- 不做 v2.1 热点/Twitter/RSSHub/TrendRadar 多源系统。
- 不建设 MCP server，除非 Hermes skill 路线被实证证明走不通。
- 不自动发布内容、不自动成片、不做投放。
- 不建设正式 RAG/vector storage。
- 不默认实现 live Bitable writes。
- 不把 child task 的执行证据复制进 `docs/PRD/`。

## 3. Milestones And Gates

| Milestone | Goal | Gate |
|---|---|---|
| M0 Invocation gate | hermes-agent 能以 skill/cron 或手动方式调用 `hermes-benchmark`，并产生一次内部群消息或明确 fallback 记录。 | 若 Hermes cron 无法稳定运行命令并发布 redacted JSON，则记录 `systemd --user` fallback。 |
| M1 Real analysis and digest | Hermes 消费 handoff package，产出真实分析结果引用，并通过 message-first 路径生成内部 digest。 | mock decomposition 不得被当作真实分析验收；digest 必须带 run/content/package/result trace refs。 |
| M2 Feedback loop | 内部群 adopt/reject 反馈通过最小 CLI contract 进入 SQLite 审计记录。 | feedback 必须幂等或 conflict-safe；不得隐式触发 RAG、规则库 promotion 或 Bitable 写入。 |
| M3 Bitable gate | 判断是否启动结构化写表。 | 必须先满足消息流实际运转不少于 2 周、确认需要结构化筛选/回溯、定案授权信任根、定案表 2/3 去留。未满足则只记录 no-go，不创建 live write implementation。 |

## 4. PRD Map

| Requirement ID | Release scope | Child boundary |
|---|---|---|
| P20-REQ-000 | 创建本 release PRD，并在 `PRD_MASTER.md` 建立 v2.0 指针。 | Child 0: v2.0 release PRD and PRD map |
| P20-REQ-010 | 定义 Hermes skill 调用 `hermes-benchmark` 的 no-secret contract。 | Child 1: Hermes skill invocation gate |
| P20-REQ-020 | 证明一次 Hermes 调用 CLI 并推送内部消息；必要时记录 systemd fallback。 | Child 1: Hermes skill invocation gate |
| P20-REQ-030 | 消费 handoff package，并保存 Hermes-owned analysis result refs。 | Child 2: real analysis handoff consumer |
| P20-REQ-040 | 通过 message-first 路径生成内部 digest。 | Child 3: internal digest message path |
| P20-REQ-050 | 将 adopt/reject feedback 持久化到 SQLite 审计记录。 | Child 4: human feedback intake |
| P20-REQ-060 | 仅在 M3 条件满足后才启动 Bitable 写表；否则记录 no-go。 | Child 5: M3 write-table gate |

## 5. Source Boundaries

| Artifact | Owns | Does not own |
|---|---|---|
| `PRD_MASTER.md` | 长期产品定位、路线图、版本指针。 | 版本级验收细节、child 状态。 |
| `PRD_v2.0.md` | v2.0 release scope、M0-M3 gates、PRD Map。 | 执行日志、commit、stage report。 |
| `.trellis/tasks/07-05-parent-v2-0-real-analysis-loop/` | parent PRD、governance、child task plan。 | 长期产品蓝图。 |
| `.trellis/tasks/07-05-child-*/` | child 级 PRD、实现记录、验证证据。 | release-level roadmap。 |

## 6. Open Decisions

| Decision | Required before | Constraint |
|---|---|---|
| Hermes cron 是否足够稳定，还是需要 `systemd --user` fallback | M0 closeout | fallback 只替换调度，不改变 CLI contract。 |
| 飞书写入授权信任根：简化防误操作协议，还是签名/密钥 + nonce 防伪造协议 | M3 implementation child creation | CLI 侧 allowlist + fail-closed 不可削弱。 |
| 表 2 / 表 3 是否继续承担写表职责 | M3 implementation child creation | 日报/告警默认走 bot 消息，不以写表为前置。 |
