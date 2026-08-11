import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import Evidence, Genre, InterpretedItem, Publisher
from src.notifiers import FakeNotifier
from src.pipeline.tick import count_undecided, run_collect_tick, run_reminder_tick
from src.state.db import Database

NOW = datetime(2026, 8, 7, 22, tzinfo=timezone.utc)


def _item(link: str, title: str) -> InterpretedItem:
    return InterpretedItem(
        title_en=title,
        link=link,
        source="test-source",
        genre=Genre.news,
        publisher=Publisher.media,
        published_at=NOW,
        signals={},
        cluster_id=hashlib.sha256(link.encode()).hexdigest()[:16],
        related_links=[],
        score=80,
        score_breakdown={"技术价值": 80.0},
        title=title,
        body="测试正文，一段顺读内容。",
        tags=["AI", "测试", "新闻"],
        evidence=[Evidence(claim="测试声明", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )


def _iid(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def test_count_undecided_pure_excludes_decided_links():
    """纯函数: 已有 keep/drop 决策的条目不算待审; 决策以外的(含 pending/无记录)都算。"""
    rows = [
        {"item_id": "a", "link": "https://x/a"},
        {"item_id": "b", "link": "https://x/b"},
        {"item_id": "c", "link": "https://x/c"},
    ]
    decisions_raw = {"a": "keep", "b": "drop"}  # c 未决策
    assert count_undecided(rows, decisions_raw) == 1


def test_count_undecided_ignores_non_keep_drop_actions():
    rows = [{"item_id": "a", "link": "https://x/a"}]
    decisions_raw = {"a": "snooze"}  # 非 keep/drop 的值不算已决策(实际协议只有 keep/drop, 防御性)
    assert count_undecided(rows, decisions_raw) == 1


def test_count_undecided_empty_rows_is_zero():
    assert count_undecided([], {"a": "keep"}) == 0


def test_reminder_tick_sends_count_when_undecided_exist(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A"), _item("https://x/2", "B")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/1"): "keep"})  # x/2 未决策
        notifier = FakeNotifier()
        out = await run_reminder_tick(now=NOW, db=db, decision_store=store, notifiers=[notifier])
        assert out["undecided_count"] == 1
        assert notifier.reminder_count == 1

    asyncio.run(go())


def test_reminder_tick_silent_when_nothing_undecided(tmp_path):
    """全审完了(或今天没推过卡): 不发提醒, 不制造噪音。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/1"): "keep"})
        notifier = FakeNotifier()
        out = await run_reminder_tick(now=NOW, db=db, decision_store=store, notifiers=[notifier])
        assert out["undecided_count"] == 0
        assert notifier.reminder_count is None  # 从没调用过

    asyncio.run(go())


def test_reminder_tick_decision_fetch_failure_is_non_fatal(tmp_path):
    """拉取失败: 不崩, 保守起见把当天全部条目算作待审(而不是假装 0 条不提醒)。"""

    class BoomStore:
        async def fetch(self):
            raise RuntimeError("worker down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        notifier = FakeNotifier()
        out = await run_reminder_tick(
            now=NOW, db=db, decision_store=BoomStore(), notifiers=[notifier]
        )
        assert out["undecided_count"] == 1
        assert notifier.reminder_count == 1

    asyncio.run(go())
