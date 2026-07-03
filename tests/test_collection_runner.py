from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_benchmark.collection_runner import (
    CollectionSkipped,
    enabled_douyin_accounts,
    load_mediacrawler_jsonl,
    mediacrawler_creator_command,
    run_douyin_collection,
)
from hermes_benchmark.profile import load_profile
from hermes_benchmark.state import begin_run, connect, init_schema

SAMPLE_PROFILE = ROOT / "profiles" / "examples" / "hermes.v1.4.douyin.sample.json"
CONFIRMED_NAMES = [
    "Ai小白Lab",
    "阿川同学",
    "柱子哥TzFilm",
    "懂点大模型",
    "晓辉博士",
    "马克的技术工作坊",
    "Josh的AI笔记",
    "山海有灵AI",
    "KK学姐",
    "木子不写代码",
]


def test_confirmed_accounts_are_read_from_validated_profile_and_upserted() -> None:
    profile = confirmed_profile()
    accounts = enabled_douyin_accounts(profile)
    conn = memory_db()
    run = begin_run(conn, "2026-07-02", "sha256:profile")

    result = run_douyin_collection(profile, conn, run_id=run["run_id"], collector=one_row_collector)
    rerun = run_douyin_collection(profile, conn, run_id=run["run_id"], collector=one_row_collector)

    assert [account["display_name"] for account in accounts] == CONFIRMED_NAMES
    assert result["status"] == "success"
    assert result["account_count"] == 10
    assert {item["status"] for item in result["accounts"]} == {"attempted_success"}
    assert result["contents_upserted"] == 10
    assert rerun["contents_upserted"] == 10
    assert conn.execute("SELECT COUNT(*) FROM content_ledger").fetchone()[0] == 10


def test_single_account_failure_does_not_fail_batch() -> None:
    profile = confirmed_profile()
    conn = memory_db()
    run = begin_run(conn, "2026-07-02", "sha256:profile")

    def collector(account):
        if account["display_name"] == "木子不写代码":
            raise RuntimeError("temporary crawler failure")
        return one_row_collector(account)

    result = run_douyin_collection(profile, conn, run_id=run["run_id"], collector=collector)

    assert result["status"] == "partial_success"
    assert result["account_count"] == 10
    assert sum(1 for item in result["accounts"] if item["status"] == "attempted_failed") == 1
    assert conn.execute("SELECT COUNT(*) FROM content_ledger").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM errors WHERE scope = 'account'").fetchone()[0] == 1


def test_skipped_account_errors_are_redacted() -> None:
    profile = confirmed_profile()
    conn = memory_db()
    run = begin_run(conn, "2026-07-02", "sha256:profile")

    def collector(account):
        raise CollectionSkipped(
            "missing_runtime",
            "cookie secret-token at http://127.0.0.1:9222/json was not usable",
        )

    result = run_douyin_collection(
        profile,
        conn,
        run_id=run["run_id"],
        collector=collector,
        sensitive_values=["secret-token"],
    )
    summaries = "\n".join(row["summary"] for row in conn.execute("SELECT summary FROM errors"))

    assert result["status"] == "failed"
    assert {item["status"] for item in result["accounts"]} == {"skipped_with_error"}
    assert "secret-token" not in summaries
    assert "127.0.0.1" not in summaries
    assert "<redacted>" in summaries


def test_mediacrawler_jsonl_output_maps_to_runner_rows() -> None:
    profile = confirmed_profile()
    account = enabled_douyin_accounts(profile)[-1]

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        jsonl_path = output_dir / "dy" / "jsonl" / "creator_contents_2026-07-02.jsonl"
        jsonl_path.parent.mkdir(parents=True)
        jsonl_path.write_text(
            json.dumps(
                {
                    "aweme_id": "7525538910311632128",
                    "title": "MediaCrawler smoke",
                    "desc": "MediaCrawler smoke desc",
                    "create_time": 1782950400,
                    "liked_count": "11",
                    "comment_count": "2",
                    "share_count": "3",
                    "last_modify_ts": 1782950500,
                    "aweme_url": "https://www.douyin.com/video/7525538910311632128",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        rows = load_mediacrawler_jsonl(output_dir, account)

    assert rows == [
        {
            "platform": "douyin",
            "platform_content_id": "7525538910311632128",
            "url": "https://www.douyin.com/video/7525538910311632128",
            "title": "MediaCrawler smoke",
            "caption": "MediaCrawler smoke desc",
            "published_at": "1782950400",
            "crawled_at": "1782950500",
            "author_handle": "木子不写代码",
            "metrics": {"likes": 11, "comments": 2, "shares": 3},
            "comments_summary": "comment_count=2",
        }
    ]


def test_mediacrawler_command_uses_safe_low_volume_creator_args() -> None:
    account = enabled_douyin_accounts(confirmed_profile())[-1]

    command = mediacrawler_creator_command(
        account,
        python_executable="/external/venv/bin/python",
        output_dir="/external/run/raw",
        max_notes=1,
    )

    assert command[:2] == ["/external/venv/bin/python", "main.py"]
    assert "--creator_id" in command
    assert account["profile_url"] in command
    assert command[command.index("--crawler_max_notes_count") + 1] == "1"
    assert command[command.index("--max_concurrency_num") + 1] == "1"
    assert command[command.index("--get_comment") + 1] == "false"
    assert command[command.index("--get_sub_comment") + 1] == "false"


def memory_db():
    conn = connect()
    init_schema(conn)
    return conn


def confirmed_profile():
    root_profile = json.loads(SAMPLE_PROFILE.read_text(encoding="utf-8"))
    accounts = {
        "schema_version": "1.4",
        "profile_id": "accounts-douyin-confirmed-v1.4",
        "profile_type": "account_profile",
        "platform": "douyin",
        "required_enabled_accounts": 10,
        "accounts": [
            {
                "account_id": f"douyin_confirmed_{index:03d}",
                "platform": "douyin",
                "display_name": name,
                "handle": name,
                "profile_url": f"https://www.douyin.com/user/confirmed-{index:03d}",
                "enabled": True,
                "priority": "A",
                "crawl_frequency": "daily",
                "owner_note": "confirmed public Douyin account for child 4 fixture",
            }
            for index, name in enumerate(CONFIRMED_NAMES, start=1)
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        account_path = Path(tmp) / "accounts.json"
        root_path = Path(tmp) / "root.json"
        account_path.write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
        root_profile["account_profile_ref"] = f"file:{account_path}"
        root_path.write_text(json.dumps(root_profile, ensure_ascii=False), encoding="utf-8")
        return load_profile(root_path)


def one_row_collector(account):
    return [
        {
            "platform": "douyin",
            "platform_content_id": f"aweme-{account['id']}",
            "url": f"https://www.douyin.com/video/{account['id']}",
            "title": f"{account['display_name']} sample",
            "caption": f"{account['display_name']} sample caption",
            "published_at": "2026-07-02T00:00:00-07:00",
            "crawled_at": "2026-07-02T00:00:00-07:00",
            "author_handle": account["handle"],
            "metrics": {"likes": 1, "comments": 1, "shares": 1},
            "comments_summary": "fixture comments",
        }
    ]


if __name__ == "__main__":
    test_confirmed_accounts_are_read_from_validated_profile_and_upserted()
    test_single_account_failure_does_not_fail_batch()
    test_skipped_account_errors_are_redacted()
