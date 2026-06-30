# RTM Guidelines

## Paths

Preferred staged overlay paths:

- `docs/requirements-traceability-matrix.md`
- `docs/requirements-traceability-matrix.json`

This repo currently also has `docs/requirements traceability matrix.md`. Do not rename or replace it without explicit scope.

Decision:

- New staged overlay work should use the hyphenated Markdown/JSON pair above.
- Existing references to `docs/requirements traceability matrix.md` are legacy-compatible and should be read when relevant.
- Migrating or renaming the legacy spaced path is a separate scoped task.

## Markdown RTM

Markdown is user-facing and may use Chinese.

```md
| 需求ID | 来源 | 子阶段 | 子任务 | 实现提交 | 验证证据 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
```

Status values:

- `计划中`
- `进行中`
- `完成`
- `部分完成`
- `延期`
- `阻塞`
- `移除`

## JSON RTM

JSON is machine-facing and uses English.

```json
{
  "requirements": [
    {
      "id": "REQ-001",
      "source": "docs/prd/main-prd.md#section",
      "subphase": "v7",
      "child_task": "v7.2-login-api",
      "implementation_commit": "abc1234",
      "verification_evidence": [
        {
          "command": "npm run test:backend",
          "result": "pass"
        }
      ],
      "status": "done",
      "notes": []
    }
  ]
}
```

Status values:

- `planned`
- `in_progress`
- `done`
- `partial`
- `deferred`
- `blocked`
- `removed`

## Done Rule

Do not mark a requirement done unless it has:

- linked child task or clear parent closeout note
- implementation commit hash
- verification evidence
- soft archive evidence for staged overlay children
- no unresolved blocker
