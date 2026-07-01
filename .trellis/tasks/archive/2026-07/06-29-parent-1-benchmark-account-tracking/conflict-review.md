# Conflict Review

## PRDv1.3 Conflicts Resolved For Parent 1

| Topic | PRDv1.3 Broad Statement | Parent 1 Resolution |
|---|---|---|
| v1 breadth | v1 includes benchmark tracking, cards, topic-pool supplements, Feishu, digest, ops, and RAG interface reserve. | Parent 1 implements the benchmark line only, with local contracts first. |
| Hotspot line | Hotspot labels, RSSHub, TrendRadar, and hotspot radar are designed for v2. | Excluded from parent 1. |
| Feishu | v1 uses Feishu tables, but live table ability depends on `lark-cli` capability. | Child 6 starts with dry-run mapping. Live writes require separate confirmation. |
| MediaCrawler | Used for internal research/validation, but open-source repo must avoid login state and bypass guidance. | Child 3 imports MediaCrawler-style outputs; child 8 installs/debugs MediaCrawler externally under `/home/jym/workspace/_external` and uses temporary cookies that never enter the repo. |
| Collection execution | Parent needs proof that MediaCrawler can be called as an external tool. | Child 8 keeps dry-run/fake-command mode, then runs the final local real smoke test with 1 account and 1 public video. |
| Whisper runtime | PRD needs local Whisper, while child 4 intentionally excluded real install/GPU optimization. | Child 4 owns transcript contract and temp cleanup; child 8 owns openai-whisper external runtime readiness with GPU detection, device selection, and CPU fallback. |
| Video retention | PRD says Whisper needs video input but v1 does not retain video files. | Child 8 retains transcripts/logs and reusable external runtime/cache state; it deletes raw videos, temp cookies, temp raw artifacts, and other run-specific test artifacts after verification. |
| Topic generation | PRD says topic pool exists, but selection Skill must exist before official topic submission. | Child 5 can supplement fields and create candidate-shaped outputs, not autonomous official topics. |
| RAG | PRD reserves RAG interfaces but postpones deletion/withdrawal and formal tech selection. | Parent 1 only preserves `RAGDocument` placeholder/export-shaped fields. |

## Open Risk

The repo has no product runtime yet. Child 1 must decide the initial runtime shape before later children can implement concrete files. Final external-runtime evidence depends on the user's later temporary cookie, local MediaCrawler availability, openai-whisper installation, and GPU/CPU runtime state.
