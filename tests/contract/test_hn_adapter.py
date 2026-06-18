import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.hn import HNAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50"


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.hn"),
    )


def _spec(min_score=100, keywords=("AI", "LLM", "model")):
    return SourceSpec(
        name="hackernews",
        url=_URL,
        genre=Genre.writeup,
        publisher=Publisher.individual,
        adapter="hn",
        min_score=min_score,
        keywords=list(keywords),
    )


def _hits(*hits):
    return {"hits": list(hits)}


def _hit(title, points, url="https://ex.com/a", oid="111", comments=5, created=1_750_000_000):
    return {
        "title": title,
        "url": url,
        "points": points,
        "num_comments": comments,
        "objectID": oid,
        "created_at_i": created,
    }


@respx.mock
async def test_hn_maps_fields_and_signals():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("New LLM model breaks records", 250)
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "New LLM model breaks records"
    assert it.link == "https://ex.com/a"
    assert it.genre == Genre.writeup and it.publisher == Publisher.individual
    assert it.signals == {"points": 250, "num_comments": 5}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_hn_filters_by_points_threshold():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("AI breakthrough", 50)
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_hn_filters_by_keyword():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("New Rust web framework released", 300)
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_hn_self_post_falls_back_to_discussion_link():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        {"title": "Ask HN: best LLM tooling?", "url": None, "points": 200,
         "num_comments": 9, "objectID": "999", "created_at_i": 1_750_000_000}
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items[0].link == "https://news.ycombinator.com/item?id=999"


@respx.mock
async def test_hn_http_error_raises():
    respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
