---
name: trellis-before-dev
description: "Load project coding and lifecycle contracts before an action writes code."
---

# Before Development

1. Read .trellis/workflow.md.
2. Read .trellis/spec/guides/project-development.md and
   .trellis/spec/project/index.md.
3. Read the exact task PRD and authoritative status/action scope.
4. Read each specific spec named by the project index that covers the action.
5. Run GitNexus impact before editing indexed symbols. Treat hidden-path UNKNOWN
   as high risk and inspect callers/tests directly.
6. Check git status for every existing target path and preserve unrelated work.
7. Apply Ponytail full mode. Do not add a dependency, framework, abstraction,
   or broader architecture than the accepted requirement needs.

This step is mandatory before code changes.
