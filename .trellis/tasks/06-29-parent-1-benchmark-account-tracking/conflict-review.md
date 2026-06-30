# Conflict Review

## PRDv1.3 Conflicts Resolved For Parent 1

| Topic | PRDv1.3 Broad Statement | Parent 1 Resolution |
|---|---|---|
| v1 breadth | v1 includes benchmark tracking, cards, topic-pool supplements, Feishu, digest, ops, and RAG interface reserve. | Parent 1 implements the benchmark line only, with local contracts first. |
| Hotspot line | Hotspot labels, RSSHub, TrendRadar, and hotspot radar are designed for v2. | Excluded from parent 1. |
| Feishu | v1 uses Feishu tables, but live table ability depends on `lark-cli` capability. | Child 6 starts with dry-run mapping. Live writes require separate confirmation. |
| MediaCrawler | Used for internal research/validation, but open-source repo must avoid login state and bypass guidance. | Child 3 imports MediaCrawler-style outputs only; no credentials or crawler source embedding. |
| Collection execution | Parent needs proof that MediaCrawler can be called as an external tool. | Child 8 only builds dry-run/fake-command execution and manifest output. Real call is parent final acceptance with 1-2 user-provided accounts. |
| Topic generation | PRD says topic pool exists, but selection Skill must exist before official topic submission. | Child 5 can supplement fields and create candidate-shaped outputs, not autonomous official topics. |
| RAG | PRD reserves RAG interfaces but postpones deletion/withdrawal and formal tech selection. | Parent 1 only preserves `RAGDocument` placeholder/export-shaped fields. |

## Open Risk

The repo has no product runtime yet. Child 1 must decide the initial runtime shape before later children can implement concrete files. Parent final acceptance also depends on the user's later real test account inputs and local MediaCrawler availability.
