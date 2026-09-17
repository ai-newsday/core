import asyncio

from src.state.db import Database
from src.tools.decision_stats import (
    keep_rate_table,
    merge_decision_sources,
    unmatched_count,
)


def _db(tmp_path, rows):
    db = Database(str(tmp_path / "state.db"))
    asyncio.run(db.init())

    async def _fill():
        await db.insert_run("r1", "collect")
        for item_id, source, link in rows:
            await db.upsert_pending_review(
                item_id=item_id,
                run_id="r1",
                link=link,
                source=source,
                title_en="t",
                title_zh=None,
                summary_zh=None,
                takeaway="",
                hot_take="",
                score=70,
                signals={},
                date="2026-09-15",
            )

    asyncio.run(_fill())
    return db


def test_all_pending_reviews_returns_every_date(tmp_path):
    db = _db(tmp_path, [("i1", "x_list", "https://x.com/a/status/1")])
    rows = asyncio.run(db.get_all_pending_reviews())
    assert [r["item_id"] for r in rows] == ["i1"]
    assert rows[0]["source"] == "x_list"


def test_keep_rate_table_groups_by_publisher_and_marks_undecided(tmp_path):
    """决策只按 item_id 存(KV, 7 天 TTL), 来源要靠 pending_reviews 关联;
    X 条目要按账号分组, 不然 34 条 X 挤在一个 'x_list' 里看不出是谁的问题。"""
    db = _db(
        tmp_path,
        [
            ("i1", "x_list", "https://x.com/LangChain/status/1"),
            ("i2", "x_list", "https://x.com/LangChain/status/2"),
            ("i3", "x_list", "https://x.com/LangChain/status/3"),
            ("i4", "hf-papers", "https://huggingface.co/papers/1"),
            ("i5", "hf-papers", "https://huggingface.co/papers/2"),
        ],
    )
    rows = asyncio.run(db.get_all_pending_reviews())
    table = keep_rate_table(rows, {"i1": "keep", "i2": "drop", "i3": "drop", "i4": "keep"})
    by_key = {r["publisher"]: r for r in table}
    assert by_key["x:langchain"] == {
        "publisher": "x:langchain",
        "pushed": 3,
        "seen": 3,
        "decided": 3,
        "keep": 1,
        "drop": 2,
        "skip": 0,
        "keep_rate": 1 / 3,
    }
    assert by_key["hf-papers"]["pushed"] == 2 and by_key["hf-papers"]["decided"] == 1
    assert by_key["hf-papers"]["keep_rate"] == 1.0
    # 保留率最低的排在最前面: 要砍的来源一眼看到
    assert table[0]["publisher"] == "x:langchain"


def test_undecided_only_publisher_has_no_keep_rate(tmp_path):
    db = _db(tmp_path, [("i9", "openai", "https://openai.com/news/1")])
    rows = asyncio.run(db.get_all_pending_reviews())
    table = keep_rate_table(rows, {})
    assert table == [
        {
            "publisher": "openai",
            "pushed": 1,
            "seen": 0,
            "decided": 0,
            "keep": 0,
            "drop": 0,
            "skip": 0,
            "keep_rate": None,
        }
    ]


def test_skip_is_counted_separately_not_as_undecided(tmp_path):
    """worker 的三个动作是 keep/drop/skip。skip 原来被当成"没决策", 于是
    "你主动跳过 30 次"和"从没推给你看过"在表里长得一模一样(2026-09-17)。"""
    db = _db(
        tmp_path,
        [
            ("i1", "lobe-chat-gh", "https://github.com/lobehub/lobe-chat/releases/1"),
            ("i2", "lobe-chat-gh", "https://github.com/lobehub/lobe-chat/releases/2"),
            ("i3", "lobe-chat-gh", "https://github.com/lobehub/lobe-chat/releases/3"),
        ],
    )
    rows = asyncio.run(db.get_all_pending_reviews())
    table = keep_rate_table(rows, {"i1": "skip", "i2": "skip", "i3": "keep"})
    row = table[0]
    assert row["skip"] == 2
    # 保留率的分母只算 keep/drop: skip 不是"不要", 但也不是"要"
    assert row["decided"] == 1 and row["keep_rate"] == 1.0
    # 但看过的次数要看得见, 否则刷屏又被你跳过的来源看起来像"没数据"
    assert row["seen"] == 3


def test_unmatched_decisions_are_reported(tmp_path):
    """决策对不上推送记录时要报出来: 2026-09-17 实测 231 条决策只有 47 条对上,
    静默丢掉的话统计会看起来"样本很薄", 而不是"数据有缺口"。"""
    db = _db(tmp_path, [("i1", "hf-papers", "https://huggingface.co/papers/1")])
    rows = asyncio.run(db.get_all_pending_reviews())
    assert unmatched_count(rows, {"i1": "keep", "zz": "drop"}) == 1


def test_history_comes_from_pending_review_status_not_only_kv(tmp_path):
    """pending_reviews.status 每次 finalize 拉到决策就写一次, 不受 KV 的 7 天 TTL 限制。
    2026-09-17 第一版统计只看 KV, 231 条只对上 47 条, 看起来"要攒两周",
    其实历史一直在库里。"""
    db = _db(
        tmp_path,
        [
            ("i1", "hf-papers", "https://huggingface.co/papers/1"),
            ("i2", "hf-papers", "https://huggingface.co/papers/2"),
            ("i3", "openai", "https://openai.com/news/1"),
        ],
    )
    asyncio.run(db.update_decision("i1", "keep"))
    asyncio.run(db.update_decision("i3", "drop"))
    rows = asyncio.run(db.get_all_pending_reviews())
    # KV 里只剩最近那条; 历史由 status 补上
    merged = merge_decision_sources(rows, live={"i2": "keep"}, recorded={})
    assert merged == {"i1": "keep", "i2": "keep", "i3": "drop"}


def test_live_kv_wins_over_older_stored_status(tmp_path):
    """同一条后来改判, 以最新的为准。"""
    db = _db(tmp_path, [("i1", "openai", "https://openai.com/news/1")])
    asyncio.run(db.update_decision("i1", "keep"))
    rows = asyncio.run(db.get_all_pending_reviews())
    assert merge_decision_sources(rows, live={"i1": "drop"}, recorded={})["i1"] == "drop"
