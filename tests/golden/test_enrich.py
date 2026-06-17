"""golden: enrich_with_hn 用伪 HN 客户端, 验证 signals 注入 + 跳过规则 + 不修上游字段。"""

import asyncio
import logging
from datetime import datetime, timezone

from src.core.types import EnrichConfig, Genre, Publisher, RawItem, RunContext
from src.pipeline.enrich import enrich_with_hn

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


class FakeHNClient:
    """注入式: url → hits。模拟 HN Algolia search_by_url。"""

    def __init__(self, mapping: dict[str, list[dict]]):
        self._map = mapping
        self.calls: list[str] = []

    async def search_url(self, url: str) -> list[dict]:
        self.calls.append(url)
        return self._map.get(url, [])


def _item(link, source="src", genre=Genre.writeup, publisher=Publisher.individual, signals=None):
    return RawItem(
        title_en="X",
        link=link,
        source=source,
        genre=genre,
        publisher=publisher,
        published_at=NOW,
        signals=signals or {},
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("g-enrich"))


def test_enrich_injects_hn_points_and_comments():
    items = [_item("https://a/1"), _item("https://a/2")]
    client = FakeHNClient(
        {
            "https://a/1": [
                {"points": 120, "num_comments": 30, "objectID": "111", "title": "post1"},
                {"points": 80, "num_comments": 10, "objectID": "112", "title": "post1 repost"},
            ],
            "https://a/2": [{"points": 5, "num_comments": 1, "objectID": "222", "title": "post2"}],
        }
    )
    out = asyncio.run(enrich_with_hn(items, client, EnrichConfig(), _ctx()))
    a1 = next(i for i in out if i.link == "https://a/1")
    a2 = next(i for i in out if i.link == "https://a/2")
    # max points 聚合 (多条 HN 帖子取最高), 评论求和
    assert a1.signals["hn_points"] == 120
    assert a1.signals["hn_comments"] == 40
    assert a1.signals["hn_url"] == "https://news.ycombinator.com/item?id=111"
    assert a2.signals["hn_points"] == 5
    assert a2.signals["hn_comments"] == 1


def test_enrich_no_match_no_signals():
    items = [_item("https://nothing/here")]
    client = FakeHNClient({})
    out = asyncio.run(enrich_with_hn(items, client, EnrichConfig(), _ctx()))
    assert out[0].signals == {}  # 没匹配, 不注入 hn_* (不污染)


def test_enrich_skips_by_genre():
    items = [
        _item("https://p/1", genre=Genre.paper, publisher=Publisher.company),
        _item("https://m/1", genre=Genre.model, publisher=Publisher.company),
        _item("https://b/1", genre=Genre.writeup, publisher=Publisher.individual),
    ]
    client = FakeHNClient(
        {
            "https://p/1": [{"points": 99, "num_comments": 1, "objectID": "1"}],
            "https://m/1": [{"points": 99, "num_comments": 1, "objectID": "2"}],
            "https://b/1": [{"points": 99, "num_comments": 1, "objectID": "3"}],
        }
    )
    cfg = EnrichConfig(skip_genres=["paper", "model"])
    out = asyncio.run(enrich_with_hn(items, client, cfg, _ctx()))
    by_link = {i.link: i for i in out}
    # 跳过 paper/model: 不调 HN, 不注入
    assert "hn_points" not in by_link["https://p/1"].signals
    assert "hn_points" not in by_link["https://m/1"].signals
    assert by_link["https://b/1"].signals["hn_points"] == 99
    # 只对 blog 调过一次
    assert client.calls == ["https://b/1"]


def test_enrich_skips_items_already_having_popularity():
    # hf-papers 上游已经带 upvotes, 不再查 HN
    items = [
        _item(
            "https://hf/p/1",
            source="hf-papers",
            genre=Genre.paper,
            publisher=Publisher.company,
            signals={"upvotes": 88},
        )
    ]
    client = FakeHNClient({"https://hf/p/1": [{"points": 99, "num_comments": 1, "objectID": "1"}]})
    cfg = EnrichConfig(skip_genres=[])  # 不靠类型跳, 靠已有信号跳
    out = asyncio.run(enrich_with_hn(items, client, cfg, _ctx()))
    # 不覆盖 upvotes, 也不重复查
    assert out[0].signals["upvotes"] == 88
    assert "hn_points" not in out[0].signals
    assert client.calls == []


def test_enrich_disabled_passes_through():
    items = [_item("https://a/1")]
    client = FakeHNClient({"https://a/1": [{"points": 50, "num_comments": 2, "objectID": "1"}]})
    out = asyncio.run(enrich_with_hn(items, client, EnrichConfig(enabled=False), _ctx()))
    assert out[0].signals == {}  # 关了就完全跳过
    assert client.calls == []


def test_enrich_preserves_order_and_other_fields():
    items = [_item("https://a/1"), _item("https://a/2"), _item("https://a/3")]
    client = FakeHNClient({})
    out = asyncio.run(enrich_with_hn(items, client, EnrichConfig(), _ctx()))
    assert [i.link for i in out] == ["https://a/1", "https://a/2", "https://a/3"]
    assert all(i.title_en == "X" and i.published_at == NOW for i in out)


def test_enrich_client_failure_does_not_crash():
    class BoomClient:
        async def search_url(self, url):
            raise RuntimeError("network down")

    items = [_item("https://a/1")]
    out = asyncio.run(enrich_with_hn(items, BoomClient(), EnrichConfig(), _ctx()))
    # 单个失败 = 该条不加 signals, 不挂整批
    assert out[0].signals == {}
