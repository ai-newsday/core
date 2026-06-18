import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.reddit import RedditAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_URL = "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=25"


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.reddit"),
    )


def _spec(min_score=50):
    return SourceSpec(
        name="reddit-localllama",
        url=_URL,
        genre=Genre.writeup,
        publisher=Publisher.individual,
        adapter="reddit",
        min_score=min_score,
    )


def _listing(*posts):
    return {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


def _post(title, ups, url="https://ex.com/a", is_self=False, permalink="/r/x/comments/1/p/",
          selftext="", comments=3, created=1_750_000_000.0):
    return {
        "title": title, "url": url, "ups": ups, "is_self": is_self,
        "permalink": permalink, "selftext": selftext, "num_comments": comments,
        "created_utc": created,
    }


@respx.mock
async def test_reddit_maps_fields_and_signals():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("New 70B model dropped", 420)
    )))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "New 70B model dropped"
    assert it.link == "https://ex.com/a"
    assert it.genre == Genre.writeup and it.publisher == Publisher.individual
    assert it.signals == {"upvotes": 420, "num_comments": 3}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_reddit_filters_by_upvotes_threshold():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("minor question", 10)
    )))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_reddit_self_post_uses_permalink_and_selftext():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("Guide: running LLMs locally", 300, is_self=True,
              permalink="/r/LocalLLaMA/comments/abc/guide/", selftext="Step 1 ...")
    )))
    it = (await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15))[0]
    assert it.link == "https://www.reddit.com/r/LocalLLaMA/comments/abc/guide/"
    assert it.raw_summary == "Step 1 ..."


@respx.mock
async def test_reddit_sends_user_agent():
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing()))
    await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert route.called
    ua = route.calls.last.request.headers.get("user-agent", "")
    assert "ai-newsday" in ua


@respx.mock
async def test_reddit_http_error_raises():
    respx.get(_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
