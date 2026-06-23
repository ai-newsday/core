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
        body="测试正文，一段顺读内容。",
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
    """拉取失败非致命: finalize 不崩、正常返回。失败=无决策=无确认 → 空报告(确认门)。"""

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
        # 非致命: 跑完不抛; 拉取失败 → 无确认 → 空报告
        assert out["item_count"] == 0

    asyncio.run(go())


def test_collect_skips_non_relevant_cards(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        ok = _item("https://x/ok", "AI thing")
        junk = _item("https://x/junk", "Not AI").model_copy(update={"relevant": False})
        notifier = FakeNotifier()
        await run_collect_tick("r1", NOW, [ok, junk], "take", db, [notifier])
        sent_links = [card.get("link") for _id, card in notifier.sent_cards]
        assert "https://x/ok" in sent_links
        assert "https://x/junk" not in sent_links

    asyncio.run(go())


def test_select_report_items_gate():
    """纯函数确认门: keep/edit 进, drop/未决策 排除。"""
    from src.core.types import ReviewDecision
    from src.pipeline.tick import select_report_items

    items = [_item(f"https://x/{n}", n) for n in ("keep", "edit", "drop", "undecided")]
    decisions = {
        "https://x/keep": ReviewDecision(action="keep"),
        "https://x/edit": ReviewDecision(action="edit"),
        "https://x/drop": ReviewDecision(action="drop"),
    }
    out = select_report_items(items, decisions)
    assert [it.link for it in out] == ["https://x/keep", "https://x/edit"]


def test_finalize_only_ships_confirmed_items(tmp_path):
    """确认门(review.md/publish.md 推迟给发布层的"未审拦截"): 报告只收显式 keep,
    未决策 + drop 都排除。修 finalize 把未确认内容总结进去的 bug。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [
            _item("https://x/keep", "Keep me"),
            _item("https://x/drop", "Drop me"),
            _item("https://x/undecided", "Never reviewed"),
        ]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/keep"): "keep", _iid("https://x/drop"): "drop"})
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
        # 只有显式 keep 的进; drop 和 undecided 都不进
        assert out["item_count"] == 1

    asyncio.run(go())


def test_finalize_zero_confirmations_empty_report(tmp_path):
    """零确认 → 空报告(不再默认 keep 全发)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A"), _item("https://x/2", "B")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=FakeDecisionStore({}),
            site_base_url="https://s/",
        )
        assert out["item_count"] == 0

    asyncio.run(go())


def test_finalize_applies_kv_decision_by_item_id_without_pending_rows(tmp_path):
    """决策解耦: 即使没发过卡(无 pending_reviews 行)、date 不匹配, KV 决策仍按 item_id 生效。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "Keep me"), _item("https://x/2", "Drop me")]
        store = FakeDecisionStore({_iid("https://x/2"): "drop"})
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        # x/2 被 drop, 即便从没 collect 过、date_label 与采集日无关
        assert out["item_count"] <= 1

    asyncio.run(go())
