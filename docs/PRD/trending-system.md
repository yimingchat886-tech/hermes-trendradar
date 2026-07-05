---
doc_type: sub_prd
module: v2.1 热点系统 · 推特首发切片
status: draft
parent: docs/PRD/PRD_MASTER.md
ledger: docs/PRD/_ledger/trending-twitter-source.md
---

# PRD: v2.1 热点系统 · 推特首发切片

> 本文件覆盖用户点名的两个关键设计：**热点系统设计**（§Requirements + §Technical Approach，以推特单源切片验证「源接入范式」）与**表格设计**（REQ-7，只定原则不定字段）。执行状态不在本文件维护。

## Goal

内部群每天自动收到一份**可追溯**的推特 AI 热点 digest：包含高信号账号名单中正在收敛的 AI 讨论热潮、以及 AI 制作者/创作者发布的教程条目；每条带原推链接与判定依据；推特源失效当天 digest 明确降级标注而非静默缺失。

## Requirements

- REQ-1 **推特源接入**：通过自建 RSSHub（或 Nitter）实例订阅账号时间线获取第一手推文。实例部署于 `_external`（对齐 MediaCrawler 外部运行时模式）；小号 cookie/token **只存在于该实例自身的 env**，本 repo 的代码、profiles、fixtures 保持零凭据。官方 X API（pay-per-use）登记为 fallback 路径：换采集头不改上层设计。TODO(Q7)：RSSHub vs Nitter 选型与部署细节开工前定。
- REQ-2 **账号名单**：推特高信号 AI 账号（大 V / 实验室 / 创作者）复用 account_registry 人工登记模式（platform='twitter'），人工维护，不做自动发现。
- REQ-3 **采集导入**：推文进入现有内容 ledger，复用三级去重模型（platform + platform_content_id 为主键路径，推文 id 天然适配）。默认每日一次采集，与对标账号 run 同节奏；不预承诺实时性。
- REQ-4 **Hermes 判定**（热点系统的核心机制）：
  - 热潮 = 名单内**多账号在短时间窗口讨论同一话题**的语义收敛，由 Hermes 判定，不设数值热度阈值；
  - 教程 = 单账号内容类型标签（Hermes 打标）；
  - 判定证据（哪些账号、哪些推文）必须随条目落盘，可回溯到原推；
  - 英文推文的翻译、摘要、中文选题化由 Hermes 分析时一并完成，采集层不翻译。
- REQ-5 **digest 输出**：每日推特热点 digest 推送内部群，复用 v2.0 M1 的消息推送通道（消息流优先，不依赖写表）。
- REQ-6 **降级运行**：推特源失效为**源级 degraded 状态**——digest 当天明确标注 degraded、不阻塞其他链路、不算系统故障。错误码新增稳定字符串，不复用现有码。
- REQ-7 **表格设计原则**（本轮只定原则，字段以实测为准、边测试边修改）：
  - 热点数据先落 SQLite（本 repo 事实源），飞书表只是展示/协作视图；
  - 表 5 热点雷达表**建表条件触发**，对齐 v2.0 M3 节奏：消息流实际运转 ≥2 周且确认需要结构化筛选/回溯后再建；
  - 字段所有权沿用三方归属（script_generated / hermes_managed / manual），任何一方不写他方字段；
  - 不在 PRD 定字段级 schema。

## Acceptance Criteria

- AC-1: 连续 7 天，每日推特 digest 自动推送到内部群（可观察：群消息记录 7/7）。
- AC-2: 任取一条热潮/教程条目，含可点开的原推链接与判定依据（哪些账号在讨论 → 对应 REQ-4 证据链）。
- AC-3: 人为使推特源不可用（停实例或断 token），当日 digest 输出 degraded 标注且其余链路正常（对应 REQ-6）。
- AC-4: repo 内检查（代码 / profiles / fixtures / 报告）无任何推特凭据；`redact_text` 覆盖新增的实例地址类敏感值（对应 REQ-1 边界）。

## Definition of Done

- [ ] 所有 REQ 有对应 AC 且通过
- [ ] `python3 -m pytest tests -q` 全绿（新增采集/导入逻辑有 fixture 化离线测试，对齐 fixtures.py 模式）
- [ ] 无遗留 TODO(Q-ID)（Q7 须在开工前回 ledger 补答）

## Technical Approach

```text
X（小号 cookie，仅存于 _external 实例 env）
  ↓
自建 RSSHub / Nitter 实例 [TODO(Q7) 选型]
  ↓  RSS/JSON（本 repo 消费边界从这里开始，零凭据）
hermes_benchmark CLI 导入（复用 external_runtime 适配模式 + 现有 ledger 去重）
  ↓
SQLite（事实源） → handoff / digest
  ↓
hermes-agent（热潮收敛判定、教程打标、翻译摘要）
  ↓
飞书内部群消息（复用 v2.0 M1 通道）
```

- **源接入范式**：本切片是「源适配器范式」的第一实例——采集头（RSSHub）可替换（官方 API fallback），导入、去重、判定、digest 各层不感知源差异。后续源（国内热榜 / GitHub / HN）按同范式复制，但**第二个源出现前不预建通用抽象层**。
- 源健康感知：只需「可用 / degraded」二态，账号生命周期（注册 / 养号 / 补号）由人工运维，repo 不管理。

## Decision (ADR-lite)

- 选 RSSHub/Nitter + 小号 cookie，弃官方 API 作主路径：省持续成本（官方 pay-per-use $0.005/读、无免费层）；接受源不稳，以 REQ-6 降级 + 官方 API fallback 对冲（ledger Q1）。
- 凭据边界裁定：cookie 只在 `_external` 实例 env，零凭据不变量**不开口子**（Q1）。
- 选账号名单代理热度，弃平台级热度检测：RSSHub 路径拿不到平台级热度信号；目标①②合并为同一条订阅链路（Q2）。
- 表格只定原则，弃字段级 schema：v2.0 已定消息流优先 / 写表条件触发，现在定字段大概率返工（Q3）。
- 选推特单源切片，弃多源一步到位：跨源事件聚类在单源阶段无法验证（Q4）。

## Out of Scope

- 平台级热度检测（推特搜索/趋势路由）。
- TrendRadar、RSSHub 多源热榜、国内热榜接入、跨源事件聚类 → 多源阶段。
- 热点表字段级 schema、热点三态标签的字段化 → 实施期边测边定。
- 实时性 / 日内多次采集承诺。
- 推特账号生命周期管理（注册、养号、封号处置）→ 人工运维。
- 通用源适配抽象层 → 第二个源出现前不做。

## Source

- Parent: [PRD_MASTER.md](PRD_MASTER.md) §6（v2.1 行）
- Ledger: [_ledger/trending-twitter-source.md](_ledger/trending-twitter-source.md)（Q0-Q7；Q7 open）
