import asyncio

from src.state.db import Database
from src.tools.decision_stats import keep_rate_table


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
        "decided": 3,
        "keep": 1,
        "drop": 2,
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
            "decided": 0,
            "keep": 0,
            "drop": 0,
            "keep_rate": None,
        }
    ]
