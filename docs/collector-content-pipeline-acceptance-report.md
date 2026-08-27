# 验收报告：Collector 统一接入 TrendRadar 内容下载与转录

- 验收时间：2026-08-27 21:45 CST
- 结论：完成

## 一句话结论

TrendRadar 的 `content-pipeline` 已正式接入 `collector/stock_runtime`，Collector 现为抖音采集、媒体下载与本地转录的唯一 Agent 执行入口；旧 OpenClaw 下载/转录 skills 已删除，实时 Collector 已重启并通过无副作用验收。

## 用户侧可见变化

1. 以后向 Collector 提交配置内抖音账号或视频 ID，即可通过统一的 `stock_run_content_pipeline` 完成本地采集、下载和转录。
2. 需要按顺序处理多个视频时，Collector 必须一条视频一次调用，收到结构化回执后再继续下一条，不并行、不批量跳过。
3. 所有输出写入 operator 固定的 TrendRadar 本地内容目录，不默认上传飞书、Wiki 或其他外部渠道。
4. 原有三条重复入口已删除：
   - `openclaw-imports/douyin-local-ingest`
   - `openclaw-imports/local-media-transcript-pipeline`
   - `openclaw-imports/douyin-profile-to-wiki`
5. 仍需保留的热门作品下载 skill 已改为 Collector 路由，不再允许回退到旧 wrapper、Agent Reach 下载、`yt-dlp`、MediaCrawler 或独立 ASR。

## 总体验收步骤

1. 查看 Collector 工具面：只有 `stock_runtime` 插件工具集启用，通用 terminal、file、browser、web、ASR 等工具均禁用。
2. 启动 Collector 后调用 `stock_validate_config`：应返回 `ok=true`、10/10 账号启用、无 errors/warnings。
3. 调用 `stock_healthcheck`：应返回 `ok=true`、`run_eligible=true`；未启动浏览器时允许出现 `runtime_effective_status=launch_required` 与 `CDP_HTTP_UNREACHABLE`。
4. 用非法视频 ID 调用新工具：应在 adapter 边界返回 `content_request_content_ids`，不得启动下载、转录或外发。
5. 搜索全局技能目录：不得再存在三个已删除 skill 的目录或悬空引用。

## 成功表现

- Collector 实时插件注册 8 个工具，其中包括 `stock_run_content_pipeline`。
- Collector 配置已读取：内容流水线 profile、1800 秒媒体任务超时、16 KiB 请求上限均生效。
- Collector profile 已加载 2 个本地 skills，其中包含新的 `trendradar-content-download`。
- 媒体后端 healthcheck 返回 4/4 available：Douyin、Bilibili、YouTube、direct。
- 主工作树集成测试为 `87 passed`。
- 部署文件与仓库源文件 SHA-256 全部一致。
- Collector Gateway 已以新 PID `2889524` 重启，日志确认 `No messaging platforms enabled`、0 个外部 channel target。
- 实际 Collector 会话完成 `stock_validate_config`、`stock_healthcheck` 调用；新工具 fail-closed 会话实际调用 `stock_run_content_pipeline` 并正确拒绝非法 ID。

## 失败表现

- 如果 `stock_run_content_pipeline` 不在工具 schema 中，说明插件或 prefill 未部署/未重载。
- 如果 Collector 出现通用 terminal、browser、web、ASR 工具，说明最小工具面被破坏。
- 如果媒体任务使用 60 秒控制命令超时而不是 1800 秒独立超时，长视频可能被错误终止。
- 如果看到旧 skill 名仍被有效路由引用，说明迁移未清干净。
- 如果 Gateway 日志出现 Feishu/Weixin/其他 messaging adapter 启动，说明“本地-only”边界失效。

## 已知限制

- 本次部署验收没有执行真实视频下载或转录，因此没有新媒体产物；这是为了避免把部署验证变成生产采集。
- `stock_healthcheck` 当前显示 `launch_required / CDP_HTTP_UNREACHABLE`，但 `runner_chrome.launchable=true`、锁可用、profile 可写，属于浏览器尚未由 runner 启动的预期准备态。
- 原计划中的 8 条视频仍未执行；后续应由 Collector 按顺序逐条处理，并继续遵守“不上传飞书，仅留本地”。

## 下一步建议

下一步直接让 Collector 按已确认顺序逐条执行 8 个视频 ID。每条必须读取结构化回执并核验本地媒体、转录文件和哈希后，才能进入下一条。

## 工程附录

### 代码与部署

- 代码仓库：`/home/jym/workspace/Hermes trendradar`
- 主分支集成提交：`811dfe8 feat: expose content pipeline to collector`
- 原功能分支提交：`5534211ea375f16f73d4e7dad09780250b178924`
- Collector live profile：`/home/jym/.hermes/profiles/collector`
- Collector 本地 skill：`/home/jym/.hermes/profiles/collector/skills/trendradar-content-download/SKILL.md`
- 本地 pipeline profile：`profiles/local/hermes-content-pipeline.v1.local.json`（受 `.gitignore` 保护）
- 本地产物根目录：`/home/jym/.local/share/trendradar-content-pipeline`

### 验证结果

- `python3 -m pytest tests/test_stock_runtime.py tests/test_collector_profile_distribution.py tests/test_content_pipeline.py tests/test_cron_handoff_runner.py -q`
  - `87 passed in 1.28s`
- `trendradar-media --profile /home/jym/.config/trendradar/media.v2.0.json healthcheck`
  - `status=succeeded`，4 available，0 unavailable
- 实际 Collector 验收会话：`20260827_214226_88e229`
  - 调用了 `stock_validate_config` 与 `stock_healthcheck`
- 新工具 fail-closed 会话：`20260827_214332_c62279`
  - 调用了 `stock_run_content_pipeline`
  - 返回 `content_request_content_ids`
  - 未执行下载、转录或外发

### 未触碰的现有改动

仓库原有的 `BOARD.md`、runbook 变更、缓存目录、build/egg-info 等未纳入本项目提交，也未被覆盖或清理。
