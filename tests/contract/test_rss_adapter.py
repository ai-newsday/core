import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.rss import RSSAdapter
from src.core.types import RawItem, RunContext, SourceSpec, Genre, Publisher


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
        genre=Genre.announcement, publisher=Publisher.lab,
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
