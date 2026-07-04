# Input Source Preflight: Child 6 Threshold Smoke

## User-Selected Source

- Source: production Child 4 MediaCrawler full-comments output.
- Raw root: `/home/jym/workspace/_external/hermes-stock-runs/run-child4-douyin-full-comments-20260702-local/raw`
- Selection rule: choose up to the 10 highest-`liked_count` videos per enabled account.
- Video eligibility: row must have `video_download_url` because Child 6 transcribes videos.

## Current Count Check

| Account raw dir | Valid video rows |
|---|---:|
| `01-douyin_ai_xiaobai_lab` | 9 |
| `02-douyin_achuan_ai` | 16 |
| `03-douyin_zhuzi_tzfilm` | 70 |
| `04-douyin_dongdian_damoxing` | 32 |
| `05-douyin_xiaohui_boshi` | 398 |
| `06-douyin_mark_tech_workshop` | 32 |
| `07-douyin_josh_ai_notes` | 75 |
| `08-douyin_shanhai_youling_ai` | 50 |
| `09-douyin_kk_xuejie_codex` | 122 |
| `10-douyin_muzi_no_code` | 36 |

## Result

- Current source can produce 99 eligible videos.
- User approved proceeding with 99 videos on 2026-07-02.
- Distribution for the current smoke: `01-douyin_ai_xiaobai_lab` has 9 eligible videos; each other enabled account has at least 10 eligible videos.
- This 99-item run is current-smoke evidence, not proof that the original 100-queued production threshold is fully satisfied.
- Child 6 must not download media. Child 6a owns localizing the selected 99 URLs into concrete local media paths.
