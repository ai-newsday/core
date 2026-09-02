"""逐条配图的编排层(spec 2026-08-31-per-item-images-design)。
用假 client 测, 不走网络; 网络隔离在 ItemImageClient, 这里只测编排逻辑。"""

import asyncio
from datetime import datetime, timezone

from src.core.types import Genre, ItemImageConfig, Publisher, RunContext, ScoredItem
from src.pipeline.item_image import enrich_item_images

NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def _item(link, adapter, genre=Genre.model):
    return ScoredItem(
        title_en="x",
        link=link,
        source="src",
        adapter=adapter,
        genre=genre,
        publisher=Publisher.company,
        published_at=NOW,
        cluster_id="c1",
        related_links=[],
        score=80,
        score_breakdown={},
    )


def _ctx():
    import logging

    return RunContext(run_id="r1", now=NOW, logger=logging.getLogger("test"))


class _FakeClient:
    """按 URL 返回预设的 (html, 能打开的图片 URL 集合)。"""

    def __init__(self, html_by_url: dict, ok_images: set):
        self._html = html_by_url
        self._ok = ok_images
        self.fetch_calls: list[str] = []
        self.check_calls: list[str] = []

    async def fetch_html(self, url: str) -> str | None:
        self.fetch_calls.append(url)
        return self._html.get(url)

    async def check_image(self, url: str) -> bool:
        self.check_calls.append(url)
        return url in self._ok


OG_HTML = '<html><head><meta property="og:image" content="{img}"></head></html>'


def test_github_items_are_never_fetched():
    """GitHub 的 og:image 是自动生成的仓库卡片, 连请求都不该发——省一次网络调用。"""
    items = [_item("https://github.com/x/releases/tag/v1", "github_releases")]
    client = _FakeClient({}, set())
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url is None
    assert client.fetch_calls == []


def test_og_image_found_and_verified():
    url = "https://blog.example.com/post"
    img = "https://blog.example.com/cover.png"
    items = [_item(url, "some_blog_rss")]
    client = _FakeClient({url: OG_HTML.format(img=img)}, {img})
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url == img


def test_candidate_url_that_404s_is_not_used():
    """找到 URL 和拿到图是两回事——候选存在但打不开时不该写进 image_url。"""
    url = "https://blog.example.com/post"
    img = "https://blog.example.com/dead.png"
    items = [_item(url, "some_blog_rss")]
    client = _FakeClient({url: OG_HTML.format(img=img)}, set())  # 没有任何图能打开
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url is None


def test_fetch_failure_leaves_image_url_none_and_does_not_drop_item():
    items = [_item("https://blog.example.com/post", "some_blog_rss")]

    class _RaisingClient(_FakeClient):
        async def fetch_html(self, url):
            raise TimeoutError("boom")

    client = _RaisingClient({}, set())
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert len(out) == 1
    assert out[0].image_url is None


def test_news_media_skipped_by_default():
    url = "https://techcrunch.com/post"
    img = "https://techcrunch.com/photo.jpg"
    items = [_item(url, "techcrunch-ai")]
    client = _FakeClient({url: OG_HTML.format(img=img)}, {img})
    cfg = ItemImageConfig(allow_news_media=False, news_media_adapters=["techcrunch-ai"])
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url is None
    assert client.fetch_calls == []


def test_news_media_allowed_when_enabled():
    url = "https://techcrunch.com/post"
    img = "https://techcrunch.com/photo.jpg"
    items = [_item(url, "techcrunch-ai")]
    client = _FakeClient({url: OG_HTML.format(img=img)}, {img})
    cfg = ItemImageConfig(allow_news_media=True, news_media_adapters=["techcrunch-ai"])
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url == img


def test_disabled_config_short_circuits():
    items = [_item("https://blog.example.com/post", "some_blog_rss")]
    client = _FakeClient({}, set())
    cfg = ItemImageConfig(enabled=False)
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url is None
    assert client.fetch_calls == []


def test_paper_tries_arxiv_html_not_the_hf_page():
    """论文的 og:image 是通用渐变占位图(spec 已验证), 必须走 arXiv HTML 首图。"""
    hf_link = "https://huggingface.co/papers/2608.12345"
    arxiv_url = "https://arxiv.org/html/2608.12345v1"
    img = "https://arxiv.org/html/2608.12345v1/teaser.png"
    items = [_item(hf_link, "hf_papers", genre=Genre.paper)]
    arxiv_html = '<html><body><img src="teaser.png"></body></html>'
    client = _FakeClient({arxiv_url: arxiv_html}, {img})
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url == img
    assert hf_link not in client.fetch_calls


def test_paper_with_no_arxiv_html_gets_no_image_not_a_placeholder():
    """spec: 没有 HTML 版的论文应当留白, 不回退到 og:image(那是三篇共用的渐变占位图)。"""
    hf_link = "https://huggingface.co/papers/2608.99999"
    items = [_item(hf_link, "hf_papers", genre=Genre.paper)]
    client = _FakeClient({}, set())  # arXiv HTML 请求返回 None(如 404)
    cfg = ItemImageConfig()
    out = asyncio.run(enrich_item_images(items, client, cfg, _ctx()))
    assert out[0].image_url is None
