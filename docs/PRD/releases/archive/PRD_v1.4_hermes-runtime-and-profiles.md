# PRD v1.4：Hermes Runtime 与 Profiles 配置

| 字段 | 内容 |
|---|---|
| 文档版本 | v1.4 Hermes Draft |
| 创建日期 | 2026-07-01 |
| 适用范围 | Hermes 运行期、CLI 使用、profiles 配置、调度、Hermes 分析、Feishu live mutation 授权 |
| 主要责任方 | Hermes Runtime / radar-ops / 内容+技术内部群 |
| 非责任方 | Codex 不作为部署后运行期字段责任方；CLI 不作为运行期决策层 |
| 当前平台范围 | 抖音 / Douyin only |
| 当前账号规模 | 10 个 enabled Douyin accounts，由 account profile 配置 |
| 调度策略 | 由 Hermes profile 配置；v1.4 生产 profile 为 daily cadence，具体触发时间不写死 |
| 当前状态 | 拆分草案，可进入 Hermes 侧实现评审 |

---

## 1. 背景

v1.4 需要将 Hermes 对标账号追踪链路从 proof skeleton 推进到生产可运行闭环。Hermes 侧的核心任务不是实现采集或转录工具，而是将工具能力组织成稳定运行期流程：读取 profile、按配置调度、调用 CLI、接管真实分析、签发 Feishu live mutation 授权、审计运行结果并输出异常摘要。

v1.4 拆分后，本文件只描述 Hermes Runtime 如何使用 CLI，以及 Hermes 需要维护哪些 profiles、授权和运行期状态。CLI 的内部实现由 `PRD_v1.4_codex-cli-implementation.md` 约束。

---

## 2. Hermes 侧目标

### 2.1 核心目标

Hermes Runtime 能根据 v1.4 production profile，每日触发 10 个抖音对标账号追踪任务，并完成以下闭环：

```text
读取 runtime profile
  ↓
读取 Douyin account profile
  ↓
按 schedule profile 触发 run-daily
  ↓
解析 CLI JSON 和 handoff package
  ↓
执行转录清洗、摘要归纳、内容拆解
  ↓
生成 validated Feishu operations JSON
  ↓
签发 Hermes authorization
  ↓
调用 apply-limited-live
  ↓
读取 write-audit 和 run summary
  ↓
生成内部日报 / 异常摘要
```

### 2.2 成功指标

| 指标 | 定义 | v1.4 目标 | 统计方式 |
|---|---|---:|---|
| Profile 可控 | 调度时间、时区、retry、账号范围均来自 profile | 100% | profile validation |
| 账号范围 | production profile 中 enabled accounts | 10 个，全部为 Douyin | `validate-config` output |
| 调度执行 | Hermes 按 profile daily cadence 触发 | 每个配置周期 1 次 | scheduler event log |
| 自动 retry | 根据 profile retry policy 执行 | 最多 2 次 | Hermes run log |
| CLI 调用 | Hermes 能调用 `validate-config`、`healthcheck`、`run-daily` | 100% JSON contract valid | CLI result parser |
| 新内容跟踪 | 每账号至少 1 条新内容进入处理链路 | 10 / 10 账号 | run summary |
| 转录处理 | 100 条进入转录队列，至少 80 条成功 | ≥ 80% | transcript summary |
| 分析输出 | Hermes 为表 4 输出清洗、摘要、归纳字段 | 通过 schema validation | analysis validation |
| Feishu 授权 | 所有 live mutation 都带 Hermes-issued authorization | 100% | authorization log |
| 写入审计 | create/update/no-op/fail 均有 write-audit | 100% | write audit summary |

---

## 3. Hermes 侧非目标

1. 不在 Hermes profile 中保存 Cookie、登录态、代理、CDP 明文 endpoint、Feishu token 明文或平台账号敏感信息。
2. 不由 Hermes 直接实现 MediaCrawler、FunASR、SQLite、dedup 或 Feishu API 细节。
3. 不让 CLI 自行决定调度时间、retry 策略、账号等级频率或 live mutation 权限。
4. 不让 CLI 直接执行真实 Hermes LLM 分析；CLI 只输出 handoff package。
5. 不将完整 ASR 分段转录全文或视频文件写入飞书。
6. 不写表 6 / 7 / 9 的生产 live 流程；v1.4 只做表 2 / 3 / 4 limited-live。
7. 不接入热点系统、RSSHub、TrendRadar 或跨源聚类。

---

## 4. Hermes Runtime 角色与权限

| 角色 | 权限 | 可操作范围 |
|---|---|---|
| Hermes Scheduler | 读取 schedule profile，触发 run，执行 retry | 只按 profile 触发，不写死具体时间 |
| Hermes Analyzer | 读取 analysis package，执行转录清洗、摘要、归纳、拆解 | 维护表 4 的 Hermes 字段，不覆盖脚本字段和人工字段 |
| Hermes Feishu Authority | 生成 validated operations，签发 live mutation authorization | 只授权 allowlisted mutation 和 allowlisted fields |
| radar-ops | 查看 run summary、触发手动补跑、处理异常 | 不能绕过 Hermes authorization 直接执行 live write |
| 内容+技术内部群 | 配置账号候选、查看结果、审核异常 | v1.4 账号源仍为本地 profile，v1.5 才转飞书表格 |

---

## 5. Profile 配置体系

### 5.1 Profile 分层

v1.4 建议使用一个 runtime profile 引用多个子 profile，避免把调度、账号、运行环境和飞书字段混在一个文件中。

```text
profiles/
  hermes.v1.4.douyin.production.yaml        # Hermes runtime 主 profile
  accounts.douyin.production.yaml           # 10 个抖音账号
  feishu.v1.4.allowlist.yaml                 # 表 2 / 3 / 4 allowlist + field mapping ref
  analysis.v1.4.hermes-handoff.yaml          # 清洗、摘要、拆解策略
  transcription.v1.4.local-funasr.yaml       # FunASR model / device / batch policy ref
  runtime.v1.4.local.yaml                    # storage / DB / artifact / env ref
```

### 5.2 主 Runtime Profile Schema

> 以下是 schema 示例，不是默认写死值。所有 `<...>` 必须由实际部署 profile 显式配置。

```yaml
schema_version: "1.4"
profile_id: "hermes-v1.4-douyin-production"
profile_type: "hermes_runtime"
project: "hermes-agent"

platform_scope:
  production_platforms: ["douyin"]
  reject_non_production_platforms: true

account_profile_ref: "file:profiles/accounts.douyin.production.yaml"
analysis_profile_ref: "file:profiles/analysis.v1.4.hermes-handoff.yaml"
transcription_profile_ref: "file:profiles/transcription.v1.4.local-funasr.yaml"
runtime_profile_ref: "file:profiles/runtime.v1.4.local.yaml"
feishu_profile_ref: "file:profiles/feishu.v1.4.allowlist.yaml"

schedule:
  enabled: true
  cadence: "daily"
  timezone: "Asia/Shanghai"
  trigger_time_ref: "env:HERMES_V14_DAILY_TRIGGER_TIME"
  # 或者使用 cron_ref，由 Hermes Scheduler 解析。
  cron_ref: "env:HERMES_V14_DAILY_CRON"
  manual_backfill_enabled: true
  lock_scope: "date+profile_hash"

retry:
  max_attempts: 2
  retryable_exit_codes: [1, 3, 4, 5, 6, 8, 9]
  non_retryable_exit_codes: [2, 7, 10]
  backoff_policy_ref: "env:HERMES_V14_RETRY_BACKOFF_POLICY"

cli:
  binary: "hermes-benchmark"
  profile_arg: "--profile"
  json_output_required: true
  analysis_mode: "hermes-handoff"
  feishu_mode: "limited-live"
  command_timeout_ref: "env:HERMES_V14_CLI_TIMEOUT_SECONDS"

observability:
  run_summary_required: true
  write_audit_required: true
  error_summary_required: true
  internal_digest_enabled: true
  alert_channel_ref: "env:HERMES_V14_ALERT_CHANNEL_REF"
```

### 5.3 Account Profile Schema

v1.4 production account profile 只验收抖音账号。账号数为 10 个 enabled Douyin accounts。

```yaml
schema_version: "1.4"
profile_id: "accounts-douyin-production-v1.4"
profile_type: "account_profile"
platform: "douyin"
required_enabled_accounts: 10

accounts:
  - account_id: "douyin_<stable_id_001>"
    platform: "douyin"
    display_name: "<account display name>"
    profile_url: "<douyin profile url>"
    enabled: true
    priority: "A"
    crawl_frequency: "daily"
    owner_note: ""
```

业务规则：

1. `platform` 必须为 `douyin`。
2. enabled accounts 必须等于或不少于 10；验收 profile 按 10 个账号执行。
3. `priority` 字段保留 S/A/B/C，但 v1.4 不按等级差异化调度。
4. `crawl_frequency` 字段由 profile 提供，v1.4 production profile 统一 daily。
5. 账号 profile 不保存 Cookie、登录态、CDP endpoint 或代理。

### 5.4 Analysis Profile Schema

```yaml
schema_version: "1.4"
profile_id: "analysis-v1.4-hermes-handoff"
profile_type: "analysis_profile"
mode: "hermes-handoff"

transcript_cleaning:
  enabled: true
  strategy: "ad_hoc_model_capability"
  model_tier: "cheap"
  future_skill_slot: "transcript_cleaning_skill"

summary:
  enabled: true
  required_fields:
    - title_or_caption_cleaned
    - content_summary
  optional_fields:
    - key_points
    - hook_summary
    - reusable_angle

validation:
  schema_version: "1.4"
  fail_on_missing_required_fields: true
  fail_on_full_transcript_field: true
```

Hermes 分析规则：

1. Hermes 可以把转录全文交给便宜模型做临时清洗和摘要。
2. v1.4 目标是去除明显转录错误、口头噪声、重复文本和断句错误，并生成摘要归纳。
3. 清洗后内容字段可写入表 4；完整 ASR 分段转录全文不得写入飞书。
4. 后续版本将清洗、摘要、拆解沉淀为 versioned Skill。

### 5.5 Feishu Profile Schema

```yaml
schema_version: "1.4"
profile_id: "feishu-v1.4-limited-live"
profile_type: "feishu_limited_live_profile"

base_config_ref: "env:HERMES_FEISHU_BASE_CONFIG"
field_mapping_ref: "file:profiles/feishu.v1.4.field_mapping.yaml"

authority:
  mutation_authority: "hermes"
  authorization_required: true
  authorization_profile_ref: "env:HERMES_V14_AUTHORIZATION_PROFILE"

allowlisted_mutations:
  - upsert_source_health_status
  - update_account_tracking_status
  - create_benchmark_content_row
  - update_content_processing_status

allowlisted_tables:
  table_2_source_health: true
  table_3_benchmark_accounts: true
  table_4_benchmark_content: true

forbidden_mutations:
  - delete_record
  - update_schema
  - bulk_unvalidated_update
  - write_table_6_7_9
  - write_non_allowlisted_field
  - write_without_hermes_authorization
  - write_arbitrary_table
  - write_raw_full_transcript_to_feishu
```

---

## 6. Hermes 调用 CLI 的标准流程

### 6.1 配置校验

```bash
hermes-benchmark validate-config \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --json
```

Hermes 必须校验：

1. profile schema_version = 1.4；
2. production platform scope = Douyin only；
3. enabled Douyin accounts = 10；
4. schedule、retry、runtime、analysis、feishu 子 profile 均存在；
5. 不存在明文 Cookie、登录态、CDP endpoint、代理、token；
6. Feishu live mutation allowlist 存在；
7. storage、artifact、DB、CDP runtime ref、FunASR profile ref、Feishu mapping ref 可解析。

### 6.2 健康检查

```bash
hermes-benchmark healthcheck \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --json
```

Hermes 只根据 JSON 判断是否进入 `run-daily`，不得靠解析 stdout 文本。

### 6.3 Daily Run

```bash
hermes-benchmark run-daily \
  --date <YYYY-MM-DD> \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --analysis-mode hermes-handoff \
  --feishu-mode limited-live \
  --json
```

规则：

1. `<YYYY-MM-DD>` 由 Hermes Scheduler 根据 profile 中 timezone 和 trigger policy 计算。
2. CLI 不决定何时运行，也不写死调度时间。
3. Hermes 必须记录 `trigger_type`：`scheduled`、`manual_backfill` 或 `retry`。
4. 同一 date + profile_hash 只允许一个 active run。
5. retry 上限从 profile 读取，v1.4 production profile 配置为 2。

### 6.4 Hermes 分析 handoff

CLI 的 `run-daily` 输出必须包含：

```json
{
  "schema_version": "1.4",
  "run_id": "...",
  "status": "succeeded|partial_failed|failed",
  "profile_hash": "sha256:...",
  "analysis_mode": "hermes-handoff",
  "analysis_package_ref": "file:artifacts/.../analysis_package.json",
  "transcript_summary": {
    "queued": 100,
    "succeeded": 80,
    "failed": 20
  },
  "content_summary": {
    "accounts_seen": 10,
    "new_content_count": 10,
    "dedup_noop_count": 0
  },
  "errors": []
}
```

Hermes 读取 `analysis_package_ref` 后执行：

1. 转录文本清洗；
2. 标题 / 文案清洗；
3. 内容摘要归纳；
4. 关键点提取；
5. hook summary；
6. reusable angle；
7. 表 4 Hermes 字段组装；
8. operations schema validation。

### 6.5 Feishu limited-live 授权与执行

Hermes 生成 validated operations JSON 后，必须生成 authorization：

```json
{
  "schema_version": "1.4",
  "issuer": "hermes-runtime",
  "profile_id": "hermes-v1.4-douyin-production",
  "run_id": "...",
  "operations_hash": "sha256:...",
  "allowed_mutations": [
    "upsert_source_health_status",
    "update_account_tracking_status",
    "create_benchmark_content_row",
    "update_content_processing_status"
  ],
  "expires_at": "<configured short-lived timestamp>",
  "nonce": "..."
}
```

然后调用：

```bash
hermes-benchmark apply-limited-live \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --operations artifacts/.../feishu_operations.validated.json \
  --authorization artifacts/.../hermes_authorization.json \
  --json
```

Hermes 验收结果必须基于 `write_summary` 和 `write_audit_ref`，不能只看 exit code。

---

## 7. Feishu 表 2 / 3 / 4 运行期写入范围

### 7.1 表 2：数据源健康表

允许 Hermes 通过 operations 写入或更新：

- `schema_version`
- `source_id`
- `platform`
- `health_status`
- `last_run_id`
- `last_success_at`
- `last_failed_at`
- `consecutive_failures`
- `last_fetch_count`
- `error_code`
- `error_summary`

### 7.2 表 3：对标账号表

允许 Hermes 通过 operations 更新：

- `schema_version`
- `account_id`
- `platform`
- `last_checked_at`
- `last_success_at`
- `latest_content_at`
- `new_content_count_24h`
- `fetch_status`
- `health_status`
- `last_error_code`

v1.4 不从表 3 读取账号源；v1.5 再迁移。

### 7.3 表 4：对标内容拆解表

v1.4 允许创建新内容行，并允许写内容字段。

脚本生成字段：

- `schema_version`
- `content_id`
- `dedup_key`
- `platform`
- `account_id`
- `account_display_name`
- `source_url`
- `platform_content_id`
- `publish_at`
- `collected_at`
- `run_id`
- `transcript_status`
- `transcript_artifact_ref`
- `analysis_status`
- `evidence_status`
- `last_write_audit_id`

Hermes 维护字段：

- `title_or_caption_cleaned`
- `content_summary`
- `key_points`
- `hook_summary`
- `reusable_angle`

限制：不得写完整 ASR 分段转录全文或视频文件。

---

## 8. Hermes 侧状态机

### 8.1 Run 状态

```text
scheduled
running
succeeded
partial_failed
failed
retry_scheduled
retry_exhausted
manual_backfill_requested
cancelled
```

### 8.2 Analysis 状态

```text
pending
handoff_ready
cleaning_running
summary_running
analysis_success
analysis_failed
operation_validation_failed
```

### 8.3 Feishu 授权状态

```text
pending
operations_validated
authorization_issued
apply_requested
apply_succeeded
apply_partial_failed
apply_failed_closed
```

---

## 9. Hermes 侧功能需求

### HR-FR-001 Profile Loader 与配置校验

**需求描述**
Hermes Runtime 必须读取 runtime profile，并将 profile path 传给 CLI。调度时间、timezone、retry、账号范围、analysis mode、feishu mode 都来自 profile。

**业务规则**
- 不允许在 Hermes 代码中写死具体触发时间。
- production profile 当前只允许 Douyin。
- 账号验收规模为 10 个 enabled Douyin accounts。

**验收标准**
- Given 修改 profile 中的 trigger time，When 不改代码重新加载 profile，Then 下一次调度按新配置执行。
- Given account profile 中出现非 Douyin enabled account，When Hermes 调用 validate-config，Then 任务不得进入 production run。

**优先级**
P0

### HR-FR-002 Scheduler 与 Retry Orchestration

**需求描述**
Hermes 根据 profile cadence 触发 daily run，并根据 profile retry policy 对可重试错误最多重试 2 次。

**业务规则**
- `date` 由 schedule timezone 决定。
- 同一 date + profile_hash 只允许一个 active run。
- retry 的触发、次数、可重试 exit code 均来自 profile。

**验收标准**
- Given profile cadence 为 daily，When 到达 profile 指定触发窗口，Then Hermes 调用 `run-daily`。
- Given CLI 返回可重试 exit code，When retry_count < 2，Then Hermes 触发 retry。

**优先级**
P0

### HR-FR-003 CLI 调用与 JSON 解析

**需求描述**
Hermes 只能通过稳定 CLI 命令和 JSON output 读取工具结果。

**业务规则**
- 不解析非结构化 stdout。
- 必须保存 raw JSON result artifact。
- exit code 与 JSON status 不一致时必须标记为 `contract_mismatch`。

**验收标准**
- Given CLI 返回合法 JSON，When Hermes 解析，Then 能生成 run summary。
- Given CLI 输出非法 JSON，When Hermes 解析失败，Then 标记为 contract error 并不执行 Feishu live mutation。

**优先级**
P0

### HR-FR-004 Hermes Analysis Handoff

**需求描述**
Hermes 读取 CLI 产出的 analysis package，执行转录清洗、摘要归纳、内容拆解，并生成表 4 Hermes 维护字段。

**业务规则**
- 可临时使用便宜模型按模型自有能力进行清洗和总结。
- 不写完整转录全文到飞书。
- 输出必须通过 schema validation。
- 后续版本沉淀为 Skill。

**验收标准**
- Given transcript artifact ref，When Hermes 分析成功，Then 输出 `title_or_caption_cleaned` 与 `content_summary`。
- Given 分析输出缺少 required fields，When validation 执行，Then 不生成 Feishu live operations。

**优先级**
P0

### HR-FR-005 Feishu Operations 生成与授权

**需求描述**
Hermes 生成 validated operations JSON，并签发 short-lived authorization，随后调用受限 `apply-limited-live`。

**业务规则**
- live mutation authority belongs to Hermes。
- authorization 必须绑定 run_id、profile_id、operations_hash、allowed mutations、过期时间和 nonce。
- 只允许 4 类 mutation。

**验收标准**
- Given operations JSON validated，When Hermes 签发 authorization，Then `operations_hash` 与 operations 文件一致。
- Given operation 包含非 allowlisted mutation，When validation 执行，Then 不签发 authorization。

**优先级**
P0

### HR-FR-006 Run Summary、日报与异常摘要

**需求描述**
Hermes 读取 CLI run summary、analysis result 和 write-audit，生成内部运行摘要。

**业务规则**
- 摘要至少包含 account_count、new_content_count、transcript success/fail、dedup/noop、Feishu write result、error list。
- retry exhausted 必须生成异常提醒。

**验收标准**
- Given run 完成，When Hermes 汇总结果，Then 内部摘要中包含 10 账号覆盖情况、100 转录队列、80 成功阈值、write-audit 链接。

**优先级**
P0

---

## 10. Hermes 侧验收标准

| 模块 | 验收标准 | 优先级 |
|---|---|---|
| Profile 配置化 | 调度时间、timezone、retry、账号范围均由 profile 提供，不写死 | P0 |
| Douyin-only | production profile 中 10 个 enabled accounts 全部为 Douyin | P0 |
| CLI 调用 | Hermes 可调用 `validate-config`、`healthcheck`、`run-daily`、`apply-limited-live` 并解析 JSON | P0 |
| Daily cadence | Hermes 根据 profile daily cadence 触发任务 | P0 |
| Retry | 可重试失败最多 retry 2 次，次数来自 profile | P0 |
| 新内容 | 每个 Douyin 账号至少跟踪 1 条新内容 | P0 |
| 转录 | 100 条进入转录队列，至少 80 条成功，失败有 deterministic error record | P0 |
| 分析 | Hermes 输出清洗标题、摘要、关键点等表 4 内容字段 | P0 |
| 授权 | 所有 live mutations 均由 Hermes-issued authorization 触发 | P0 |
| Feishu limited-live | 只允许表 2 / 3 / 4 的 4 类 mutation | P0 |
| 审计 | write-audit、run summary、error summary 均可追溯 | P0 |

---

## 11. v1.5 预留

1. 从飞书表 3 读取账号源。
2. 按 S/A/B/C 等级配置不同调度频率。
3. 扩展小红书和其他平台 profile。
4. 移除 mock 模式和兼容别名，仅保留生产 handoff / production mode。
5. 将 transcript cleaning、summary、decomposition 沉淀为 versioned Skill。
6. 优化表结构，扩展表 6 / 7 / 9 live 写入。
