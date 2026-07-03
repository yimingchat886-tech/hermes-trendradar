# PM Intake

## 1. Original Request

将目前生产测试用 whisper 转变为 modelscope/FunASR，更新 `/home/jym/workspace/_external`，删除原先 whisper 项目模型配置，保留 run。同时优化 repo 中有关 whisper 的依赖与文字，避免出现描述/功能错误。

## 2. Real Goal

让当前生产测试转录链路以 FunASR 为唯一当前 ASR runtime，避免外部运行区和 repo 文档/配置继续指向 Whisper。

## 3. Ambiguous Or Risky Wording

| Original | Issue | Proposed Rewrite |
|---|---|---|
| 删除原先 whisper 项目模型配置 | 可能误删运行证据或历史文档 | 删除 `_external` 旧 Whisper uv 项目、venv、模型缓存和当前配置引用；保留 `hermes-stock-runs/` |
| 优化 repo 中有关 whisper 的依赖与文字 | 历史 PRD 是否重写不清楚 | 修正当前代码、profile、runbook、handoff 中会误导运行的文字；历史 PRD 仅在当前验收/需求句中保留历史语境 |

## 4. Optimized Requirement

把当前转录生产测试 runtime 从 `local-whisper` 迁移到 `local-funasr`，更新外部运行区管理脚本、manifest、README、profile、当前 runbook 和测试；不引入 repo 依赖，不删除历史 run 证据。

## 5. Risk Level

T2 high-risk.

## 6. Staged Overlay Needed

Yes. Reason: external runtime/tooling replacement and local deletion of regenerable venv/cache/config.

## 7. Oracle Review Budget Needed

Yes by policy. API failed because `OPENAI_API_KEY` is missing; browser failed because attachments did not finish uploading before timeout. Downgraded to local checklist.

## 8. User Confirmation Points

- [x] FunASR replacement requested explicitly.
- [x] Keep run evidence.
- [x] Delete old Whisper project/model configuration, scoped to `_external` non-run assets.
