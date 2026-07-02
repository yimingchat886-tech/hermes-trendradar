# PRD v1.4 拆分索引：Hermes 对标账号追踪生产化闭环

| 字段 | 内容 |
|---|---|
| 文档版本 | v1.4 Split Draft |
| 创建日期 | 2026-07-01 |
| 产品名称 | Hermes Agent / Benchmark Account Tracking |
| 拆分原因 | 将运行期 Hermes 责任与工具层 Codex 实现责任分离，避免 CLI 工具、调度、飞书授权、分析决策边界混淆 |
| 当前版本平台范围 | 抖音 / Douyin only |
| 当前版本账号规模 | production profile 中配置 10 个 enabled Douyin accounts |
| 调度策略 | 由 Hermes profile 配置，不在 PRD 流程或 CLI 代码中写死具体触发时间 |
| 责任边界 | Hermes 负责调度、profiles、真实分析、授权和运行期决策；Codex 负责 CLI 工具实现、Schema、持久化、幂等、测试和部署 |
| 后续版本 | v1.5 接入飞书账号源、表结构优化、S/A/B/C 差异化频率、生产模式收敛、Skill 版本化 |

---

## 1. 拆分后的文档结构

| 文档 | 目标读者 | 主要内容 | 不包含 |
|---|---|---|---|
| `PRD_v1.4_Hermes_Runtime_and_Profiles.md` | Hermes Runtime、radar-ops、内容+技术内部群 | CLI 使用方式、profiles 配置、调度配置、Hermes 分析职责、Feishu limited-live 授权、运行期验收 | CLI 内部实现细节、DB 表结构迁移、工程测试清单 |
| `PRD_v1.4_Codex_CLI_Implementation.md` | Codex / 工程执行者 | CLI 命令、配置解析、运行编排、CDP 采集、Whisper 转录、dedup、SQLite、apply-limited-live backend、测试与验收 | Hermes 调度策略决策、LLM 内容判断、运行期字段决策 |

---

## 2. v1.4 总目标

v1.4 目标是在不引入热点系统、不重构飞书全表、不把 CLI 变成通用写表器的前提下，跑通对标账号追踪的工程生产闭环：

```text
Hermes 读取 v1.4 production profile
  ↓
Hermes 按 profile 中的 schedule 配置触发 daily run
  ↓
Hermes 调用 hermes-benchmark CLI
  ↓
CLI 读取 Douyin-only 10 账号 profile
  ↓
CLI 通过独立 Chrome CDP runtime 采集、归一化、去重、转录并生成 handoff package
  ↓
Hermes Runtime 执行转录清洗、摘要、归纳、拆解
  ↓
Hermes 生成 validated Feishu operations JSON 并签发授权
  ↓
Hermes 调用受限 apply-limited-live backend
  ↓
表 2 / 3 / 4 执行 allowlisted create/update/no-op
  ↓
write-audit、run summary、error summary 持久化
```

---

## 3. 不再写死的内容

以下内容必须从 profile / config / env / secrets 引用读取，不得写死在代码、PRD 流程、示例数据或默认常量中。

| 配置项 | 配置位置 | 规则 |
|---|---|---|
| 调度触发时间 | Hermes runtime profile | v1.4 要求 daily cadence，但具体触发时间由 profile 指定 |
| 时区 | Hermes runtime profile | production profile 当前使用 `Asia/Shanghai`，但代码不得硬编码 |
| retry 上限 | Hermes runtime profile | v1.4 profile 配置为 2 次，代码只读取配置 |
| 账号列表 | account profile | v1.4 production profile 为 10 个 Douyin accounts |
| 平台范围 | account profile | v1.4 production profile 为 `douyin` only，v1.5 可扩展 |
| CDP endpoint / profile | env / secrets / runtime ref | 源码和示例不保存敏感值 |
| Feishu table / field mapping | Feishu mapping profile | 不在业务代码中写死 table_id / field_id |
| Whisper model / device / 并发 | transcription profile | 由 profile 控制，不在流程中写死 |
| Hermes model / 清洗策略 | analysis profile | v1.4 可临时用模型自有能力，后续 Skill 化 |

---

## 4. 版本内稳定边界

| 边界 | v1.4 规则 |
|---|---|
| Hermes 是运行期 authority | 调度、真实分析、Feishu live mutation 授权都属于 Hermes |
| CLI 是确定性工具 | CLI 不做不可审计的内容决策，不自主执行 live Feishu mutation |
| Codex 是开发期工程执行者 | Codex 实现工具、测试、部署，不成为部署后的飞书字段责任方 |
| Feishu limited-live | 只允许表 2 / 3 / 4 的 allowlisted mutation；表 4 允许创建新内容行 |
| Douyin-only | v1.4 production profile 只验收 10 个抖音账号 |
| Dedup | 三级去重策略稳定：`platform + platform_content_id` → `platform + normalized_source_url` → `platform + account_id + publish_at + normalized_title_or_caption_hash` |
| 转录验收 | 100 条视频进入转录队列，至少 80 条成功，失败项必须有 deterministic error record |
| 调度配置化 | daily cadence、timezone、trigger time、retry 都由 profile 配置，代码不得写死 |

---

## 5. 文档链接

- Hermes 运行期与 Profiles PRD：`PRD_v1.4_Hermes_Runtime_and_Profiles.md`
- Codex CLI 实现 PRD：`PRD_v1.4_Codex_CLI_Implementation.md`
