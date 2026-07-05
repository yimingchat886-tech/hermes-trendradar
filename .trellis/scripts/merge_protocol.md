# Merge Collision Protocol

## 目标

并行 child 分支发生 merge conflict 时，先还原双方意图，再决定代码形态。目标不是快速消掉 conflict marker，而是不丢任何一个 child 的已验证意图。

## 必走顺序

1. 运行 `python3 ./.trellis/scripts/conflict_checklist.py --repo .`，列出冲突文件、匹配 task card、owner、status、stage-report 路径。
2. 对每个冲突文件，必须先读所有匹配 child 的 `stage-report.md`。脚本若报告匹配 task 少于两张，或 stage report 缺失/不可读，先停下补证据，不要解析冲突。
3. 对每个冲突块，写下双方原意：A 改了什么、B 改了什么、各自验收证据在哪里。
4. 默认策略是 preserve both first：优先保留双方行为、文档证据和验证约束。
5. 不允许用 `git merge --abort` 当作解决捷径；只有在明确决定放弃本次集成尝试、并记录原因后，才可由主集成会话执行。
6. 不允许发明第三种未在任一 child 验证过的新行为来“绕开”冲突。
7. 如果双方意图确实不可兼得，把 trade-off 记录到 parent `governance.md`，包含冲突文件、涉及 child、放弃了哪一侧、原因、后续任务。
8. 冲突解决后，重跑相关 child 的最小验证；若解决触及共享流程，再跑 parent 要求的集成验证。

## 记录模板

```md
### Merge trade-off: <file>

- Conflicted children:
- Stage reports read:
- Preserved:
- Dropped or deferred:
- Reason:
- Follow-up:
```
