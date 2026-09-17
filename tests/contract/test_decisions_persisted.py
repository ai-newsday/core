import asyncio

from src.state.db import Database


def test_decisions_survive_the_kv_ttl(tmp_path):
    """决策在 Cloudflare KV 只留 7 天; 要按来源看长期保留率就必须自己留一份
    (2026-09-17: 表里每个来源只有 1-8 次决策, 砍来源的样本不够)。"""
    db = Database(str(tmp_path / "state.db"))
    asyncio.run(db.init())

    async def _run():
        await db.record_decisions({"i1": "keep", "i2": "skip"}, ts="2026-09-17T00:00:00+00:00")
        # 同一条后来改判: 保留最后一次
        await db.record_decisions({"i1": "drop"}, ts="2026-09-18T00:00:00+00:00")
        return await db.get_recorded_decisions()

    assert asyncio.run(_run()) == {"i1": "drop", "i2": "skip"}
