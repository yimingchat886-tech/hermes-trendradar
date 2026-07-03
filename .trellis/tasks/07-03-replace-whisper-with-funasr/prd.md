# brainstorm: replace Whisper production test with FunASR

## Goal

把当前生产测试转录链路从 openai-whisper 切换到 modelscope/FunASR，同时更新本机 `/home/jym/workspace/_external` 外部运行区，保留已有 `hermes-stock-runs/` 运行证据，并清理 repo 中会误导后续执行的 Whisper 依赖、配置和说明文字。

## What I Already Know

- 用户明确要求将生产测试用 Whisper 改为 `modelscope/FunASR`。
- `_external` 当前有 `uv/openai-whisper/`、`venvs/openai-whisper/`、`model-cache/openai-whisper/`、`model_cache/whisper/` 等旧 Whisper 配置/环境/缓存。
- `_external/hermes-stock-runs/` 是运行证据目录，必须保留。
- repo 当前 `transcript_batch.py` 只接受 `provider=local-whisper`，并按 Whisper CLI 输出读取 JSON。
- `external_runtime.py`、runbook、profile 示例和部分 handoff 文档仍以 Whisper 命名。
- 历史 PRD 可以保留为历史记录，但当前运行文档、样例配置和代码不能继续声称生产测试使用 Whisper。

## Requirements

- 在 `_external` 中建立/更新 FunASR 外部 checkout 和本地运行配置。
- 移除旧 Whisper 本地项目/模型配置，不删除 `hermes-stock-runs/`。
- 将 repo 当前转录 runtime/provider 改为 FunASR，不再要求 `provider=local-whisper`。
- 保持现有 transcript artifact、SQLite 状态、错误记录和临时视频清理语义。
- 更新当前运行文档、profile 示例、本地 profile 和测试，避免描述或功能仍指向 Whisper。
- 不把 FunASR 源码、venv、模型缓存或运行产物 vendoring 到 Hermes repo。

## Acceptance Criteria

- [x] `_external/scripts/manage.sh check` 改为检查 MediaCrawler + FunASR，而不是 openai-whisper。
- [x] `_external` README / manifest / AGENTS / `.gitignore` 不再列 openai-whisper 作为当前生产测试运行时。
- [x] `/home/jym/workspace/_external/hermes-stock-runs/` 未被删除。
- [x] `profiles/examples/transcription.v1.4.sample.json` 和 `profiles/local/transcription.v1.4.local.json` 使用 FunASR provider/model/cache。
- [x] `run_transcript_batch` 通过 FunASR 风格 JSON/stdout 输出测试。
- [x] 当前 runbook/handoff 文档不再把生产测试转录描述为 Whisper。
- [x] 相关 Python self-check / pytest / `git diff --check` 通过，或明确说明未跑原因。

## Definition of Done

- 变更范围只覆盖 FunASR 切换、旧 Whisper 配置清理、相关文案修正和必要测试。
- 不新增 repo 依赖；FunASR 依赖只存在 `_external`。
- 不删除运行证据。
- GitNexus `detect_changes` 用于确认符号影响范围。

## Technical Approach

Ponytail 方案：不新增通用 ASR 插件层，只把当前唯一生产测试转录器从 `local-whisper` 改成 `local-funasr`，并复用现有 `run_process`、artifact、state、cleanup 和 redaction 逻辑。FunASR CLI 的结构化输出兼容通过一个小读取函数处理。

## Out of Scope

- 不实现长期队列、daemon、调度器或并发转录。
- 不把 FunASR 源码或模型缓存提交到 Hermes repo。
- 不清理或重写历史运行证据。
- 不修订所有历史 PRD 为“从未使用 Whisper”；只修正当前/未来运行会误导的文字。

## Research References

- [`research/funasr.md`](research/funasr.md) — FunASR 官方 README 当前建议的安装、CLI、模型和部署形态。

## Notes

- GitNexus `runtime_layout` 上游影响为 HIGH；实现时保持函数签名和目录语义稳定，只替换 ASR 运行时命名/路径。
