import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.reddit import RedditAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_URL = "https://old.reddit.com/r/LocalLLaMA/top/?t=day&limit=25"


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


def _thing(
    fullname, score, title, url, permalink, comments=12, ts=1_750_000_000_000, promoted="false"
):
    return (
        f'<div class="thing id-{fullname} link" data-fullname="{fullname}" '
        f'data-score="{score}" data-comments-count="{comments}" data-timestamp="{ts}" '
        f'data-promoted="{promoted}" data-url="{url}" data-permalink="{permalink}">'
        f'<p class="title"><a class="title may-blank" href="{url}">{title}</a></p></div>'
    )


def _page(*things):
    return "<html><body>" + "".join(things) + "</body></html>"


@respx.mock
async def test_reddit_maps_fields_and_signals():
    html = _page(
        _thing(
            "t3_aaa",
            420,
            "New 70B model dropped",
            "https://ex.com/a",
            "/r/LocalLLaMA/comments/aaa/x/",
        )
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, text=html))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "New 70B model dropped"
    assert it.link == "https://ex.com/a"
    assert it.genre == Genre.writeup and it.publisher == Publisher.individual
    assert it.signals == {"upvotes": 420, "num_comments": 12}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_reddit_filters_by_upvotes_threshold():
    html = _page(_thing("t3_low", 10, "minor question", "https://ex.com/b", "/r/x/comments/low/"))
    respx.get(_URL).mock(return_value=httpx.Response(200, text=html))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_reddit_skips_promoted_ads():
    html = _page(
        _thing("t3_ad", 999, "Buy now", "https://ad.com", "/r/x/comments/ad/", promoted="true")
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, text=html))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_reddit_self_or_reddit_hosted_uses_permalink():
    # data-url is a reddit-hosted media / relative permalink -> link should be the permalink
    perma = "/r/LocalLLaMA/comments/ddd/guide/"
    html = _page(
        _thing(
            "t3_ddd", 300, "Guide: running LLMs locally", f"https://www.reddit.com{perma}", perma
        )
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, text=html))
    it = (await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15))[0]
    assert it.link == "https://www.reddit.com/r/LocalLLaMA/comments/ddd/guide/"


@respx.mock
async def test_reddit_sends_user_agent():
    route = respx.get(_URL).mock(return_value=httpx.Response(200, text=_page()))
    await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert route.called
    assert "ai-newsday" in route.calls.last.request.headers.get("user-agent", "")


@respx.mock
async def test_reddit_http_error_raises():
    respx.get(_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
