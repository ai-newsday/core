import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import Evidence, Genre, InterpretedItem, Publisher
from src.notifiers import FakeNotifier
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


def _item(link: str, title: str, genre: Genre = Genre.news) -> InterpretedItem:
    return InterpretedItem(
        # RawItem fields
        title_en=title,
        link=link,
        source="test-source",
        genre=genre,
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
    """拉取失败非致命: finalize 不崩。失败=零决策 → 兜底自动发(不空报)。"""

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
        # 非致命: 跑完不抛; 拉取失败 → 零决策 → 兜底发
        assert out["item_count"] == 1
        assert out["is_pending"] is True

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


def test_finalize_zero_decisions_falls_back_to_auto_publish(tmp_path):
    """零决策(没碰 TG) → 兜底自动发 top-N 草稿, 而非空报; 标 is_pending。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        # 两条不同 genre(各自 quota>=1), 避免 publish per-genre 配额把同类砍掉
        items = [
            _item("https://x/1", "A", genre=Genre.paper),
            _item("https://x/2", "B", genre=Genre.model),
        ]
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
        assert out["item_count"] == 2  # 两条都过地板(score=80)且不同 genre, 配额不砍
        assert out["is_pending"] is True  # 未审 → 草稿水印 + draft:true

    asyncio.run(go())


def test_finalize_excludes_items_published_on_another_day(tmp_path):
    """已发布去重: 一条在 label 21 发过后, label 22 即便仍被 keep+在窗口内也不再进(修跨天重复)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/a", "A")]
        store = FakeDecisionStore({_iid("https://x/a"): "keep"})
        out1 = await run_finalize_tick(
            "r1",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        out2 = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-22",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        assert out1["item_count"] == 1  # 首日发
        assert out2["item_count"] == 0  # 次日不再发(已在 21 发过)

    asyncio.run(go())


def test_finalize_same_day_rerun_still_ships(tmp_path):
    """同一 date_label 重跑(手动重触发) → 仍发, 不被已发布去重误伤。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/a", "A")]
        store = FakeDecisionStore({_iid("https://x/a"): "keep"})
        out1 = await run_finalize_tick(
            "r1",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        out2 = await run_finalize_tick(
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
        assert out1["item_count"] == 1 and out2["item_count"] == 1

    asyncio.run(go())


def test_published_items_db_roundtrip(tmp_path):
    """db: mark_published + already_published_elsewhere(按 item_id, 排除其它 label)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await db.mark_published(["a", "b"], "2026-06-21")
        # 同 label 不算"别处发过"; 不同 label 才算
        assert await db.already_published_elsewhere(["a", "b", "c"], "2026-06-21") == set()
        assert await db.already_published_elsewhere(["a", "b", "c"], "2026-06-22") == {"a", "b"}
        # 首发 label 固定(INSERT OR IGNORE): 再 mark 到别 label 不改原 label
        await db.mark_published(["a"], "2026-06-22")
        assert await db.already_published_elsewhere(["a"], "2026-06-21") == set()

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
