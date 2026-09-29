import asyncio

from src.state.db import Database


def test_snapshot_upsert_overwrites_and_get_returns_only_known(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await db.upsert_snapshot("a", "2026-09-28", '{"v": 1}')
        await db.upsert_snapshot("a", "2026-09-29", '{"v": 2}')
        assert await db.get_snapshots(["a", "missing"]) == {"a": '{"v": 2}'}
        assert await db.get_snapshots([]) == {}

    asyncio.run(go())


def test_init_adds_snapshot_table_to_existing_db(tmp_path):
    """老库(缓存里恢复的 state.db)没有这张表; init() 幂等建表后必须能直接用。"""

    async def go():
        path = str(tmp_path / "s.db")
        await Database(path).init()
        db = Database(path)
        await db.init()
        await db.upsert_snapshot("a", "2026-09-28", "{}")
        assert await db.get_snapshots(["a"]) == {"a": "{}"}

    asyncio.run(go())
