import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import Evidence, Genre, InterpretedItem, Publisher
from src.notifiers import FakeNotifier
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


def _item(link: str, title: str) -> InterpretedItem:
    return InterpretedItem(
        # RawItem fields
        title_en=title,
        link=link,
        source="test-source",
        genre=Genre.news,
        publisher=Publisher.media,
        published_at=NOW,
        signals={},
        # NewsItem fields
        cluster_id=hashlib.sha256(link.encode()).hexdigest()[:16],
        related_links=[],
        # ScoredItem fields
        score=80,
        score_breakdown={"技术价值": 80.0},
        # InterpretedItem fields
        title=title,
        summary="测试摘要",
        takeaway="测试 takeaway",
        tags=["AI", "测试", "新闻"],
        evidence=[Evidence(claim="测试声明", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )


def _iid(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def test_finalize_merges_remote_decision(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "Keep me"), _item("https://x/2", "Drop me")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/2"): "drop"})
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        assert store.fetch_count == 1
        assert out["item_count"] <= 1

    asyncio.run(go())


def test_finalize_decision_fetch_failure_is_non_fatal(tmp_path):
    class BoomStore:
        async def fetch(self):
            raise RuntimeError("worker down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=BoomStore(),
            site_base_url="https://s/",
        )
        assert out["item_count"] >= 1

    asyncio.run(go())


def test_collect_no_longer_polls_decisions(tmp_path):
    """collect 只发卡片, 不再消费 FakeNotifier 里排队的决策。"""
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        notifier = FakeNotifier()
        notifier.queue_decision(_iid("https://x/1"), "drop")
        await run_collect_tick("r1", NOW, items, "take", db, [notifier])
        decided = await db.get_decisions_dict("2026-06-19")
        assert decided == {}
    asyncio.run(go())
