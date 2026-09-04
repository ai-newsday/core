import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.rss import RSSAdapter, _image_url
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.rss"),
    )


def _spec():
    return SourceSpec(
        name="openai",
        url="https://openai.com/news/rss.xml",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        adapter="rss",
    )


@respx.mock
async def test_rss_parses_and_drops_undated():
    xml = open("fixtures/sources/rss_sample.xml", "rb").read()
    respx.get("https://openai.com/news/rss.xml").mock(return_value=httpx.Response(200, content=xml))
    items = await RSSAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1  # undated item dropped
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "Introducing GPT-X"
    assert it.source == "openai"
    assert it.genre == Genre.announcement
    assert it.published_at.tzinfo is not None  # tz-aware (UTC)
    assert it.fetched_via == "native"


@respx.mock
async def test_rss_http_error_raises():
    respx.get("https://openai.com/news/rss.xml").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await RSSAdapter().fetch(_spec(), _ctx(), timeout_s=15)


class _Entry:
    """feedparser entry 的最小替身: 只有被 _image_url 读到的那几个属性。"""

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


def test_image_url_skips_youtube_player_and_takes_the_thumbnail():
    """回归(2026-09-04 生产): YouTube 的 media:content 是**播放器**不是图——
    实测 runway-yt feed 原样返回:
        media_content = [{url: .../v/<id>?version=3, type: application/x-shockwave-flash}]
        media_thumbnail = [{url: i3.ytimg.com/vi/<id>/hqdefault.jpg}]
    旧实现直接取 media_content[0]["url"], 于是正文里出现了一个 ![](...) 指向
    text/html 的播放器地址。真图在 media_thumbnail 里, 只过滤类型会让 YouTube
    条目彻底丢图, 所以两件事都要做。"""
    e = _Entry(
        media_content=[
            {
                "url": "https://www.youtube.com/v/fE59FYMIttQ?version=3",
                "type": "application/x-shockwave-flash",
                "width": "640",
                "height": "390",
            }
        ],
        media_thumbnail=[{"url": "https://i3.ytimg.com/vi/fE59FYMIttQ/hqdefault.jpg"}],
    )
    assert _image_url(e) == "https://i3.ytimg.com/vi/fE59FYMIttQ/hqdefault.jpg"


def test_image_url_prefers_an_image_typed_media_content():
    e = _Entry(
        media_content=[
            {"url": "https://x/video.mp4", "type": "video/mp4"},
            {"url": "https://x/real.png", "type": "image/png"},
        ],
        media_thumbnail=[{"url": "https://x/thumb.jpg"}],
    )
    assert _image_url(e) == "https://x/real.png"


def test_image_url_accepts_medium_image_without_a_type():
    """部分 feed 只给 medium 不给 type。"""
    e = _Entry(media_content=[{"url": "https://x/pic.jpg", "medium": "image"}])
    assert _image_url(e) == "https://x/pic.jpg"


def test_image_url_filters_enclosures_by_type_too():
    e = _Entry(
        enclosures=[
            {"href": "https://x/pod.mp3", "type": "audio/mpeg"},
            {"href": "https://x/cover.jpg", "type": "image/jpeg"},
        ]
    )
    assert _image_url(e) == "https://x/cover.jpg"


def test_image_url_returns_none_when_nothing_is_an_image():
    e = _Entry(
        media_content=[{"url": "https://x/v.swf", "type": "application/x-shockwave-flash"}],
        enclosures=[{"href": "https://x/pod.mp3", "type": "audio/mpeg"}],
    )
    assert _image_url(e) is None


def test_image_url_none_when_entry_has_no_media_at_all():
    assert _image_url(_Entry()) is None
