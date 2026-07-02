# PRD v1.4：Codex CLI 工具实现

| 字段 | 内容 |
|---|---|
| 文档版本 | v1.4 Codex Draft |
| 创建日期 | 2026-07-01 |
| 适用范围 | `hermes-benchmark` CLI 工具实现、配置解析、采集、转录、去重、持久化、受限 Feishu apply backend、测试与部署 |
| 主要责任方 | Codex / 工程执行者 |
| 运行期使用方 | Hermes Runtime |
| 当前平台范围 | 抖音 / Douyin only production profile |
| 当前账号规模 | 10 个 enabled Douyin accounts，由 profile 配置，不在代码写死 |
| 调度策略 | CLI 不实现 scheduler；调度时间、timezone、retry 均由 Hermes profile 提供 |
| 当前状态 | 拆分草案，可进入工程任务拆解 |

---

## 1. 背景

当前 repo 已具备 contracts、fixtures、MediaCrawler fixture import、transcript wrapper、decomposition shape、Feishu dry-run mapping、daily digest/alerts、external runtime smoke 等 proof 能力，但缺少可被 Hermes 稳定调用的生产 CLI、packaging、profile contract、真实账号配置、生产采集 runner、batch transcription、SQLite 持久化、幂等、受限 live Feishu apply backend 和部署验收。

本文件只约束 Codex 需要实现的工具层能力。Hermes 调度、真实分析、Feishu mutation 授权和运行期 profiles 使用规则由 `PRD_v1.4_Hermes_Runtime_and_Profiles.md` 约束。

---

## 2. Codex 实现目标

### 2.1 核心目标

实现一个可安装、可部署、可被 Hermes Runtime 以稳定 JSON contract 调用的 CLI 工具：

```bash
hermes-benchmark validate-config --profile <profile.yaml> --json
hermes-benchmark healthcheck --profile <profile.yaml> --json
hermes-benchmark run-daily --date <YYYY-MM-DD> --profile <profile.yaml> --analysis-mode mock|hermes-handoff --feishu-mode dry-run|limited-live --json
hermes-benchmark apply-limited-live --profile <profile.yaml> --operations <ops.json> --authorization <auth.json> --json
```

### 2.2 实现原则

| 原则 | 要求 |
|---|---|
| CLI 是确定性工具 | 不做不可审计的 LLM 内容决策，不自主触发 live mutation |
| Profile 驱动 | 调度时间、timezone、retry、账号列表、平台范围、模型 profile、CDP ref、Feishu mapping 均从 profile / env / secrets 读取 |
| 不写死业务配置 | 代码不得写死具体触发时间、账号 ID、table_id、field_id、CDP endpoint、token、cookie |
| Douyin-only production | v1.4 production profile 只验收 10 个 Douyin accounts；代码可保留扩展接口，但 P0 不要求小红书生产验收 |
| Hermes authority | `apply-limited-live` 必须校验 Hermes-issued authorization |
| Fail closed | auth、schema、field type、allowlist、operation hash 不匹配时不得写飞书 |
| 幂等可恢复 | run、content、transcript、operation、write-audit 均有唯一键或 hash |
| 接口稳定 | CLI 参数、JSON output、exit code、error schema、状态枚举必须向后兼容 |

---

## 3. 非目标

1. 不实现 Hermes Scheduler。
2. 不写死每日调度具体时间。
3. 不从飞书表 3 读取账号源；v1.4 使用本地 account profile。
4. 不实现通用 Feishu live writer。
5. 不支持任意表、任意字段、delete、schema update 或 bulk unvalidated update。
6. 不直接调用 Hermes LLM 执行真实 decomposition；只生成 handoff package。
7. 不把完整 Whisper 分段转录全文或视频文件写入飞书。
8. 不在源码、示例、测试 fixture 或文档中包含 Cookie、登录态、代理、CDP endpoint 明文、Feishu token 或绕限制说明。
9. 不接入热点系统、RSSHub、TrendRadar 或跨源聚类。
10. 不让 Codex 成为部署后飞书字段责任方。

---

## 4. CLI 命令契约

### 4.1 `validate-config`

```bash
hermes-benchmark validate-config \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --json
```

必须校验：

1. 主 profile 和子 profile 均存在并符合 schema_version 1.4。
2. v1.4 production `platform_scope.production_platforms == ["douyin"]`。
3. enabled Douyin account count 满足 profile 中 `required_enabled_accounts: 10`。
4. 非 Douyin enabled account 在 production profile 中必须报错。
5. schedule、retry、runtime、analysis、transcription、feishu profile 字段完整。
6. 所有敏感值必须为 env/ref/secrets 引用，不得是明文。
7. Feishu mutation allowlist 与 field mapping ref 存在。

输出 schema：

```json
{
  "schema_version": "1.4",
  "ok": true,
  "profile_id": "hermes-v1.4-douyin-production",
  "profile_hash": "sha256:...",
  "platforms": ["douyin"],
  "enabled_account_count": 10,
  "required_enabled_accounts": 10,
  "schedule_configured": true,
  "warnings": [],
  "errors": []
}
```

### 4.2 `healthcheck`

```bash
hermes-benchmark healthcheck \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --json
```

检查项：

- package version；
- profile 可解析；
- DB path 可读写；
- artifact dir 可读写；
- Chrome CDP runtime ref 可解析且 endpoint 可达；
- MediaCrawler runner 可调用；
- Whisper command / model / device profile 可用；
- Feishu mapping ref 可解析；
- lark-cli 或官方 API 薄脚本可用；
- 不泄露 credential / endpoint / token 明文。

### 4.3 `run-daily`

```bash
hermes-benchmark run-daily \
  --date <YYYY-MM-DD> \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --analysis-mode mock|hermes-handoff \
  --feishu-mode dry-run|limited-live \
  --json
```

实现要求：

1. CLI 接收 date，但不决定 date；date 由 Hermes Scheduler 传入。
2. CLI 不读取或执行 schedule trigger time。
3. CLI 可以读取 profile 中限额、账号、runtime、transcription、feishu 配置。
4. CLI 获取 run lock，lock scope 为 date + profile_hash。
5. CLI 执行 Douyin 10 账号采集、normalize、dedup、transcription、handoff package 输出。
6. `--analysis-mode mock` 仅用于测试，生成 deterministic mock fields。
7. `--analysis-mode hermes-handoff` 不做真实 LLM 分析，只输出 handoff package。
8. `--feishu-mode limited-live` 不等于 CLI 自主写飞书；它只表示本次 run 需要生成 live-ready operation inputs / metadata，最终 live apply 仍需 Hermes authorization。

输出 schema 至少包含：

```json
{
  "schema_version": "1.4",
  "command": "run-daily",
  "run_id": "...",
  "date": "YYYY-MM-DD",
  "profile_id": "hermes-v1.4-douyin-production",
  "profile_hash": "sha256:...",
  "status": "succeeded|partial_failed|failed",
  "analysis_mode": "mock|hermes-handoff",
  "feishu_mode": "dry-run|limited-live",
  "account_summary": {
    "configured": 10,
    "enabled": 10,
    "processed": 10,
    "succeeded": 0,
    "partial_failed": 0,
    "failed": 0
  },
  "content_summary": {
    "new_content_count": 0,
    "dedup_noop_count": 0,
    "dedup_conflict_count": 0
  },
  "transcript_summary": {
    "queued": 100,
    "succeeded": 80,
    "failed": 20,
    "success_threshold_met": true
  },
  "analysis_package_ref": "file:artifacts/.../analysis_package.json",
  "errors": [],
  "artifact_refs": []
}
```

### 4.4 `apply-limited-live`

```bash
hermes-benchmark apply-limited-live \
  --profile profiles/hermes.v1.4.douyin.production.yaml \
  --operations artifacts/.../feishu_operations.validated.json \
  --authorization artifacts/.../hermes_authorization.json \
  --json
```

实现要求：

1. 校验 authorization issuer 为 Hermes Runtime。
2. 校验 authorization 未过期。
3. 校验 `operations_hash` 与 operations JSON 文件一致。
4. 校验 mutation type 在 allowlist 内。
5. 校验 target table 和 field 在 profile allowlist 内。
6. 执行 read-before-write。
7. 生成 create/update/no-op/fail 决策。
8. 生成 deterministic write-audit record。
9. auth、schema、field type、record identity、hash 任一异常时 fail closed。

只允许 4 类 mutation：

```text
upsert_source_health_status
update_account_tracking_status
create_benchmark_content_row
update_content_processing_status
```

禁止：

```text
delete_record
update_schema
bulk_unvalidated_update
write_table_6_7_9
write_non_allowlisted_field
write_without_hermes_authorization
write_arbitrary_table
write_raw_full_transcript_to_feishu
```

---

## 5. Exit Code 与错误 Schema

### 5.1 Exit Code

| Code | 含义 | 是否可由 Hermes retry |
|---:|---|---|
| 0 | 成功 | 否 |
| 1 | 部分失败，但有有效 summary | 是 |
| 2 | profile / config invalid | 否 |
| 3 | runtime unavailable | 是 |
| 4 | collection failed | 是 |
| 5 | transcription failed above threshold | 是 |
| 6 | handoff package invalid | 是 |
| 7 | Feishu operation validation failed | 否 |
| 8 | Feishu live mutation failed | 视错误类型 |
| 9 | run lock conflict | 是 |
| 10 | authorization invalid | 否 |
| 11 | contract mismatch / invalid JSON | 是 |

### 5.2 Error Object

```json
{
  "error_id": "err_...",
  "run_id": "...",
  "scope": "profile|runtime|account|content|transcript|handoff|feishu|authorization",
  "object_id": "...",
  "error_code": "...",
  "summary": "...",
  "retryable": true,
  "redacted_details_ref": "file:artifacts/.../error.json"
}
```

---

## 6. Profile Parser 与配置安全

### 6.1 Profile 支持

CLI 必须支持：

- 主 runtime profile；
- account profile；
- runtime profile；
- transcription profile；
- analysis profile；
- Feishu allowlist profile；
- Feishu field mapping profile。

`--profile` 是主参数，`--config` 可作为兼容别名，但内部统一解析为 profile。

### 6.2 敏感信息检测

`validate-config` 和 CI 测试必须检测并拒绝：

- 明文 Cookie；
- 登录态；
- CDP endpoint 明文 URL；
- 代理地址明文；
- Feishu token 明文；
- 平台账号密码；
- 批量绕限制说明。

允许：

- `env:VAR_NAME`；
- `file:/secure/path/ref`；
- `secret:secret_name`；
- `vault:path`；
- redacted runtime ref。

---

## 7. 数据与持久化实现

### 7.1 SQLite / Local DB 对象

| 表 | 关键字段 | 实现要求 |
|---|---|---|
| `runs` | run_id、date、profile_hash、status、started_at、ended_at、retry_count | run lock 与 resume 基础 |
| `accounts` | account_id、platform、display_name、enabled、priority、last_checked_at | account snapshot，不作为长期账号源 |
| `content_ledger` | content_id、dedup_key、platform_content_id、source_url、account_id、status | 去重与内容状态 |
| `transcripts` | transcript_id、content_id、status、artifact_ref、error_code、model_profile | 转录状态与 artifact 索引 |
| `analysis_packages` | package_id、run_id、content_ids、status、artifact_ref | Hermes handoff package |
| `feishu_operations` | operation_id、run_id、mutation_type、target_table、operation_hash、status | operations tracking |
| `write_audit` | audit_id、operation_id、target_record_key、result、error_code、created_at | live write 幂等审计 |
| `errors` | error_id、run_id、scope、object_id、error_code、summary、retryable | deterministic error records |

### 7.2 Dedup 实现

三级 dedup key：

```text
P0 dedup_key = platform + platform_content_id
fallback_1   = platform + normalized_source_url
fallback_2   = platform + account_id + publish_at + normalized_title_or_caption_hash
```

实现规则：

1. 任一级命中，视为同一 content。
2. 命中 dedup 时不得创建新 content row。
3. 命中 dedup 时不得重复创建飞书表 4 行。
4. dedup 冲突必须记录 error，不得静默覆盖。
5. content_id 必须稳定可复现或由 ledger 持久化。

---

## 8. Douyin 采集实现

### 8.1 采集范围

v1.4 P0 只实现 Douyin production path，account profile 中 10 个 enabled accounts。

### 8.2 CDP Runtime

实现要求：

1. 通过 profile 的 runtime ref 解析 CDP endpoint。
2. 日志中必须 redaction。
3. 单账号采集失败不阻断整批。
4. 每账号至少跟踪 1 条新内容。
5. 采集输出必须 normalize 到统一 content schema。

### 8.3 MediaCrawler Runner

Codex 需要实现多账号 runner 或薄封装：

```text
for account in enabled_douyin_accounts:
  run collection
  normalize output
  upsert content ledger
  record account status
  continue on account failure
```

---

## 9. Batch Whisper 转录实现

### 9.1 转录队列

实现规则：

1. 每账号 10 条视频进入转录队列。
2. 总计 100 条视频进入队列。
3. 至少 80 条转录成功。
4. 失败项必须有 deterministic error record。
5. 视频文件不长期保留。
6. Whisper 原始转录 artifact 通过 `transcript_artifact_ref` 索引，不写入飞书全文。

### 9.2 Artifact 策略

| Artifact | 存储位置 | 保留策略 | 飞书写入 |
|---|---|---|---|
| 原始采集 JSON | artifact dir / object storage | 可按周期清理 | 否 |
| 临时视频文件 | temp dir | 转录后删除 | 否 |
| Whisper 原始转录 | artifact dir / object storage | 可长期保留 | 只写 artifact ref |
| analysis package | artifact dir | 保留用于 Hermes 分析 | 只写摘要字段 |
| Feishu operations JSON | artifact dir | 保留审计 | 不直接展示全文 |
| write-audit | SQLite / artifact | 长期保留 | 可写 audit id |

---

## 10. Hermes Handoff Package 实现

CLI 在 `--analysis-mode hermes-handoff` 下必须输出可被 Hermes Runtime 读取的 package：

```json
{
  "schema_version": "1.4",
  "package_id": "pkg_...",
  "run_id": "...",
  "profile_hash": "sha256:...",
  "mode": "hermes-handoff",
  "contents": [
    {
      "content_id": "...",
      "platform": "douyin",
      "account_id": "...",
      "account_display_name": "...",
      "source_url": "...",
      "title_or_caption_raw": "...",
      "publish_at": "...",
      "collected_at": "...",
      "transcript_status": "success",
      "transcript_artifact_ref": "file:artifacts/.../transcript.json",
      "dedup_key": "..."
    }
  ]
}
```

CLI 不生成真实 Hermes 内容字段；真实清洗、摘要、归纳、拆解由 Hermes Runtime 执行。

---

## 11. Feishu Apply Backend 实现

### 11.1 Operation Schema

```json
{
  "schema_version": "1.4",
  "operation_id": "op_...",
  "run_id": "...",
  "mutation_type": "create_benchmark_content_row",
  "target_table": "table_4_benchmark_content",
  "target_record_key": "content_id:...",
  "dedup_key": "...",
  "fields": {
    "content_id": "...",
    "platform": "douyin",
    "source_url": "...",
    "content_summary": "..."
  }
}
```

### 11.2 Write Audit Schema

```json
{
  "schema_version": "1.4",
  "audit_id": "audit_...",
  "operation_id": "op_...",
  "run_id": "...",
  "operation_hash": "sha256:...",
  "mutation_type": "create_benchmark_content_row",
  "target_table": "table_4_benchmark_content",
  "target_record_key": "content_id:...",
  "result": "created|updated|noop|failed_closed",
  "feishu_record_id": "...",
  "error_code": null,
  "created_at": "..."
}
```

### 11.3 Read-before-write

实现规则：

1. 对表 4 以 `content_id` 或 `dedup_key` 查找已存在记录。
2. 已存在且字段一致：noop。
3. 已存在但状态字段可更新：update。
4. 不存在且 mutation 为 create：create。
5. 记录冲突或字段类型不匹配：fail closed。

---

## 12. 状态机

### 12.1 Run Status

```text
pending
running
succeeded
partial_failed
failed
cancelled
```

### 12.2 Account Fetch Status

```text
pending
success
no_new_content
partial_failed
failed
skipped
```

### 12.3 Content Processing Status

```text
discovered
imported
duplicate
transcription_pending
transcription_success
transcription_failed
analysis_handoff_ready
feishu_pending
feishu_written
feishu_noop
feishu_failed
```

### 12.4 Feishu Write Status

```text
validated
authorized
read_before_write_done
created
updated
noop
failed_closed
```

---

## 13. 功能需求

### CX-FR-001 Packaging 与 CLI Entry Point

**需求描述**
提供可安装的 package metadata 和 `hermes-benchmark` entrypoint。

**验收标准**
- Given 新环境安装完成，When 执行 `hermes-benchmark --version`，Then 返回版本号。
- Given 有效 profile，When 执行 `healthcheck --json`，Then 输出合法 JSON。

**优先级**
P0

### CX-FR-002 Profile Parser 与 Validator

**需求描述**
实现主 profile 和子 profile 的解析、hash、校验和敏感信息检测。

**验收标准**
- Given 10 个 enabled Douyin accounts，When validate-config，Then enabled_account_count = 10。
- Given profile 中出现明文 CDP endpoint，When validate-config，Then exit 2。

**优先级**
P0

### CX-FR-003 Run-daily Orchestrator

**需求描述**
实现单次 daily run 编排，但不实现 scheduler。

**验收标准**
- Given Hermes 传入 date 和 profile，When run-daily，Then 生成 run_id、run lock、summary 和 artifact refs。

**优先级**
P0

### CX-FR-004 Douyin Collection Runner

**需求描述**
通过独立 Chrome CDP runtime 对 10 个抖音账号进行采集。

**验收标准**
- Given 10 个 Douyin accounts，When run-daily 完成，Then 每账号至少跟踪 1 条新内容或有明确失败记录。

**优先级**
P0

### CX-FR-005 Normalize、Dedup 与 Content Ledger

**需求描述**
将采集输出标准化，生成 content_id 和 dedup_key，并写入 content ledger。

**验收标准**
- Given 同一日期同一 profile 重跑，When 内容已存在，Then content ledger no-op 且不重复创建表 4 operation。

**优先级**
P0

### CX-FR-006 Batch Transcription Runner

**需求描述**
实现本地 Whisper 批量转录和 transcript artifact 管理。

**验收标准**
- Given 10 个账号，When run-daily，Then 100 条进入转录队列，至少 80 条 success，失败项有 error record。

**优先级**
P0

### CX-FR-007 Hermes Handoff Package

**需求描述**
在 hermes-handoff 模式下生成 analysis package，供 Hermes Runtime 真实分析。

**验收标准**
- Given 转录完成，When run-daily 结束，Then 输出 `analysis_package_ref` 且 package 通过 schema validation。

**优先级**
P0

### CX-FR-008 Limited-live Apply Backend

**需求描述**
实现只接受 Hermes 授权和 validated operations 的受限 Feishu apply backend。

**验收标准**
- Given valid authorization 和 operations，When apply-limited-live，Then 只执行 allowlisted mutation 并写 audit。
- Given unauthorized operations，When apply-limited-live，Then exit 10 且不写飞书。

**优先级**
P0

### CX-FR-009 Observability 与 Recovery

**需求描述**
实现 run summary、error summary、write summary、deterministic error record 和 resume/no-op 行为。

**验收标准**
- Given run 中断，When 同 date + profile 重跑，Then 已完成项 no-op，未完成项继续。

**优先级**
P0

---

## 14. 工程测试要求

| 测试类型 | 覆盖内容 | P0 |
|---|---|---|
| Unit tests | profile parsing、dedup key、operation hash、auth validation、error schema | 是 |
| Contract tests | CLI JSON output、exit code、handoff package schema、write-audit schema | 是 |
| Integration tests | validate-config → healthcheck → run-daily mock → apply-limited-live dry-run | 是 |
| External runtime smoke | CDP ref 可达、MediaCrawler runner 可调用、Whisper 可调用 | 是 |
| Idempotency tests | 同 date + profile 重跑不重复 content / operation / write | 是 |
| Security tests | 明文 Cookie、CDP endpoint、token 被拒绝；日志 redaction | 是 |
| Feishu limited-live tests | allowlist、read-before-write、noop、fail closed | 是 |

---

## 15. Codex 侧验收标准

| 模块 | 验收标准 | 优先级 |
|---|---|---|
| Package | 可安装，提供 `hermes-benchmark` entrypoint | P0 |
| Profile | 支持 `--profile`，调度、账号、runtime、Feishu mapping 均从 profile/ref 读取 | P0 |
| 不写死 | 代码中不写死具体调度时间、账号 ID、CDP endpoint、table_id、field_id、token | P0 |
| Douyin-only | production profile 校验 10 个 enabled Douyin accounts | P0 |
| Healthcheck | DB、artifact、CDP、Whisper、Feishu mapping 可检查 | P0 |
| Run-daily | 生成 run_id、run summary、artifact refs、analysis package | P0 |
| 采集 | 每账号至少跟踪 1 条新内容或记录失败 | P0 |
| 转录 | 100 条入队，≥80 成功，失败有 deterministic error record | P0 |
| Dedup | 重跑不重复创建 content / operation / Feishu row | P0 |
| Handoff | hermes-handoff package schema valid | P0 |
| Apply backend | 只执行 4 类 allowlisted mutations，必须有 Hermes authorization | P0 |
| Write-audit | create/update/no-op/fail 均写 deterministic audit | P0 |
| 安全 | 源码、示例、日志不包含敏感配置 | P0 |

---

## 16. 建议工程里程碑

| 里程碑 | 目标 | 主要交付 |
|---|---|---|
| M1 CLI skeleton | 可安装、可调用、JSON contract 固定 | packaging、entrypoint、exit code、error schema |
| M2 Profile system | 配置全部 profile/ref 化 | parser、validator、sensitive detection、hash |
| M3 Local DB + dedup | 可幂等运行 | SQLite schema、ledger、dedup、run lock |
| M4 Douyin collection | 10 抖音账号采集 | CDP runtime adapter、MediaCrawler runner、normalize |
| M5 Transcription | 100 入队 / 80 成功 | Whisper batch、artifact policy、error records |
| M6 Handoff package | Hermes 可分析 | package schema、mock mode、contract tests |
| M7 Apply limited-live | 受限飞书写入 | auth validation、allowlist、read-before-write、audit |
| M8 Production hardening | 可验收 | recovery、idempotency tests、runbook、security tests |

---

## 17. v1.5 预留接口

1. Account source adapter：从本地 profile 切换到飞书表 3，不破坏 account schema。
2. Schedule profile：支持 S/A/B/C 账号等级差异化频率，但仍由 Hermes profile 决定。
3. Platform adapter：扩展小红书，但不破坏 Douyin content schema。
4. Analysis mode：移除 mock / legacy alias，保留 production handoff。
5. Feishu mapping：表结构优化，但保持 content_id、dedup_key、write-audit 稳定。
