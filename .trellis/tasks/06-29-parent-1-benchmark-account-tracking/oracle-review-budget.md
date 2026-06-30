# Oracle Review Budget

## Default

Oracle review is not required for parent planning artifacts.

## Per-Child Guidance

| Child | Oracle | Reason |
|---|---|---|
| Child 1 | skip | Contract and fixture planning can be checked locally. |
| Child 2 | skip | Registry config/state is low-risk if no credentials are stored. |
| Child 8 | skip | Child scope is dry-run plus local fake-command/fixture output only. |
| Child 3 | decide before implementation | Adapter boundary and platform data handling may need extra review. |
| Child 4 | skip | Local Whisper wrapper is bounded if temp file deletion is tested. |
| Child 5 | decide before implementation | Hermes output boundary and prompt/schema behavior may affect product quality. |
| Child 6 | decide before implementation | Feishu write behavior, permissions, and idempotency may deserve review. |
| Child 7 | skip | Digest/alert formatting is downstream of existing objects. |
| Parent real-call acceptance | decide before running | Real external MediaCrawler execution may need extra review depending on account/input and local setup. |
