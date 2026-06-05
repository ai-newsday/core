import asyncio
import pytest
from src.state.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_create_run_and_query(db):
    async def go():
        await db.init()
        await db.insert_run("r1", "collect")
        row = await db.get_run("r1")
        assert row["run_id"] == "r1"
        assert row["tick"] == "collect"
        assert row["status"] == "running"
    asyncio.run(go())


def test_insert_and_query_pending_review(db):
    async def go():
        await db.init()
        await db.insert_run("r1", "collect")
        await db.upsert_pending_review(
            item_id="abc123", run_id="r1",
            link="https://a/1", source="openai",
            title_en="X released", title_zh="X 发布",
            summary_zh="摘要。", takeaway="对你。",
            hot_take="锐评。", score=85,
            signals={"upvotes": 10}, date="2026-06-05")
        rows = await db.get_pending_reviews_for_date("2026-06-05")
        assert len(rows) == 1
        assert rows[0]["item_id"] == "abc123"
        assert rows[0]["status"] == "pending"
    asyncio.run(go())


def test_update_decision(db):
    async def go():
        await db.init()
        await db.insert_run("r1", "collect")
        await db.upsert_pending_review(
            item_id="abc123", run_id="r1",
            link="https://a/1", source="openai",
            title_en="X", title_zh="X", summary_zh="s",
            takeaway="t", hot_take="h", score=80,
            signals={}, date="2026-06-05")
        await db.update_decision("abc123", "keep")
        rows = await db.get_pending_reviews_for_date("2026-06-05")
        assert rows[0]["status"] == "keep"
        assert rows[0]["decided_at"] is not None
    asyncio.run(go())


def test_upsert_is_idempotent(db):
    async def go():
        await db.init()
        await db.insert_run("r1", "collect")
        for _ in range(3):
            await db.upsert_pending_review(
                item_id="abc123", run_id="r1",
                link="https://a/1", source="openai",
                title_en="X", title_zh="X", summary_zh="s",
                takeaway="t", hot_take="h", score=80,
                signals={}, date="2026-06-05")
        rows = await db.get_pending_reviews_for_date("2026-06-05")
        assert len(rows) == 1
    asyncio.run(go())


def test_get_decisions_as_dict(db):
    async def go():
        await db.init()
        await db.insert_run("r1", "collect")
        await db.upsert_pending_review(
            item_id="id1", run_id="r1", link="https://a/1", source="s",
            title_en="X", title_zh="X", summary_zh="s",
            takeaway="t", hot_take="h", score=80,
            signals={}, date="2026-06-05")
        await db.upsert_pending_review(
            item_id="id2", run_id="r1", link="https://a/2", source="s",
            title_en="Y", title_zh="Y", summary_zh="s",
            takeaway="t", hot_take="h", score=70,
            signals={}, date="2026-06-05")
        await db.update_decision("id1", "keep")
        await db.update_decision("id2", "drop")
        d = await db.get_decisions_dict("2026-06-05")
        assert d["https://a/1"] == "keep"
        assert d["https://a/2"] == "drop"
    asyncio.run(go())
