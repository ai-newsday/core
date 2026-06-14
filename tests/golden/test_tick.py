import asyncio
from datetime import datetime, timezone

from src.core.types import (
    Evidence,
    InterpretedItem,
    SourceType,
)
from src.notifiers import FakeNotifier
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database

NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-06-05"


def _make_item(link, source="hf-models", st=SourceType.MODEL, cluster_id=None, signals=None):
    return InterpretedItem(
        title_en="DeepSeek V4 released",
        link=link,
        source=source,
        source_type=st,
        published_at=NOW,
        raw_summary="A.",
        cluster_id=cluster_id or link,
        related_links=[],
        score=90,
        score_breakdown={
            "可见指标": 15.0,
            "机构影响力": 15.0,
            "一手性": 18.0,
            "技术价值": 14.0,
            "产业影响": 10.0,
            "扩散潜力": 9.0,
            "时效": 10.0,
            "惩罚": 0.0,
            "读者相关度": 0.0,
        },
        signals=signals or {"likes": 4622},
        is_explore=False,
        title="DeepSeek V4 发布",
        summary="旗舰模型发布。",
        takeaway="可替换 API。",
        hot_take="护城河变薄。",
        tags=["#模型"],
        evidence=[Evidence(claim="发布了", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=True,
        review_action=None,
        was_edited=False,
        edited_fields=[],
    )


def test_collect_tick_pushes_cards_and_saves_to_db(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1", cluster_id="c1")
        await run_collect_tick(
            run_id="r1",
            now=NOW,
            interpreted_items=[item],
            daily_take="今天看点。",
            db=db,
            notifiers=[notifier],
        )
        assert len(notifier.sent_cards) == 1
        rows = await db.get_pending_reviews_for_date(TODAY)
        assert len(rows) == 1
        assert rows[0]["link"] == "https://a/1"
        assert rows[0]["status"] == "pending"

    asyncio.run(go())


def test_collect_tick_skips_already_sent_item(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1")
        await run_collect_tick("r1", NOW, [item], None, db, [notifier])
        await run_collect_tick("r2", NOW, [item], None, db, [notifier])
        assert len(notifier.sent_cards) == 1

    asyncio.run(go())


def test_finalize_tick_builds_report_and_notifies(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        await db.insert_run("r1", "collect")
        for item_id, link, status in [
            ("id1", "https://a/1", "keep"),
            ("id2", "https://a/2", "pending"),
        ]:
            await db.upsert_pending_review(
                item_id=item_id,
                run_id="r1",
                link=link,
                source="openai",
                title_en="X",
                title_zh="X",
                summary_zh="s",
                takeaway="t",
                hot_take="h",
                score=80,
                signals={},
                date=TODAY,
            )
            if status != "pending":
                await db.update_decision(item_id, status)
        notifier = FakeNotifier()
        items = [
            _make_item(link, source="openai", st=SourceType.OFFICIAL, signals={})
            for link in ["https://a/1", "https://a/2"]
        ]
        result = await run_finalize_tick(
            run_id="r2",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take=None,
            db=db,
            notifiers=[notifier],
        )
        assert result["item_count"] >= 0
        assert notifier.final_report is not None
        assert "AI Daily" in notifier.final_report

    asyncio.run(go())


def test_finalize_tick_returns_dict_keys(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        result = await run_finalize_tick(
            run_id="r1",
            now=NOW,
            date_label=TODAY,
            interpreted_items=[],
            daily_take=None,
            db=db,
            notifiers=[notifier],
        )
        for k in ("run_id", "date_label", "item_count", "must_read_count", "is_pending"):
            assert k in result

    asyncio.run(go())


def test_finalize_tick_persists_feedback_and_is_idempotent(tmp_path):
    async def go():
        import aiosqlite
        from src.pipeline.tick import _item_id

        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1", source="hf-models", cluster_id="c1")

        # seed a 'keep' decision for this item under run id "r-fin"
        iid = _item_id(item)
        await db.insert_run("r-fin", "finalize")
        await db.upsert_pending_review(
            item_id=iid, run_id="r-fin", link=item.link, source=item.source,
            title_en=item.title_en, title_zh=item.title, summary_zh=item.summary,
            takeaway=item.takeaway, hot_take=item.hot_take, score=item.score,
            signals=item.signals, date=TODAY,
        )
        await db.update_decision(iid, "keep")

        # run finalize twice with the SAME run_id
        for _ in range(2):
            await run_finalize_tick(
                run_id="r-fin", now=NOW, date_label=TODAY,
                interpreted_items=[item], daily_take="x", db=db, notifiers=[notifier],
            )

        # keep -> 升权 from baseline 1.0 by step 0.2
        weights = await db.get_quality_weights()
        assert weights["hf-models"] == 1.2

        # idempotent: exactly one event row for (run_id, link)
        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM feedback_events WHERE run_id=? AND link=?",
                ("r-fin", item.link),
            ) as cur:
                (n,) = await cur.fetchone()
        assert n == 1

    asyncio.run(go())
