# Oracle Review Budget

## Default

Oracle review is not required for parent planning artifacts.

## Per-Child Guidance

| Child | Oracle | Reason |
|---|---|---|
| Child 1 | skip | Contract and fixture planning can be checked locally. |
| Child 2 | skip | Registry config/state is low-risk if no credentials are stored. |
| Child 8 | decide before implementation | Final child uses external installs, temporary cookies, real MediaCrawler execution, and openai-whisper GPU/runtime checks. |
| Child 3 | decide before implementation | Adapter boundary and platform data handling may need extra review. |
| Child 4 | skip | Local Whisper wrapper is bounded if temp file deletion is tested. |
| Child 5 | decide before implementation | Hermes output boundary and prompt/schema behavior may affect product quality. |
| Child 6 | decide before implementation | Feishu write behavior, permissions, and idempotency may deserve review. |
| Child 7 | skip | Digest/alert formatting is downstream of existing objects. |
| Parent closeout review | decide before closeout | Parent should review child 8 real local deployment evidence instead of running a separate parent-only real call. |
