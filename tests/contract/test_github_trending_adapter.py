import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.github_trending import GithubTrendingAdapter, _scrape_trending
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_SEARCH = "https://api.github.com/search/repositories?q=topic:llm+sort:stars&sort=stars&order=desc&per_page=30"
_TRENDING_HTML = "https://github.com/trending?since=daily"


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.ght"),
    )


def _spec():
    return SourceSpec(
        name="gh-trending",
        url=_SEARCH,
        genre=Genre.announcement,
        publisher=Publisher.company,
        adapter="github_trending",
    )


def _repo(
    full="openai/whisper",
    url="https://github.com/openai/whisper",
    desc="ASR model",
    pushed="2026-06-23T01:00:00Z",
    stars=70000,
):
    return {
        "full_name": full,
        "html_url": url,
        "description": desc,
        "pushed_at": pushed,
        "stargazers_count": stars,
    }


@respx.mock
async def test_trending_search_maps_fields_and_signal():
    respx.get(_SEARCH).mock(return_value=httpx.Response(200, json={"items": [_repo()]}))
    # Trending HTML best-effort returns nothing here (no extra repos)
    respx.get("https://github.com/trending").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    items = await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "openai/whisper"
    assert it.link == "https://github.com/openai/whisper"
    assert it.raw_summary == "ASR model"
    assert it.genre == Genre.announcement and it.publisher == Publisher.company
    assert it.signals == {"github_stars": 70000}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_trending_scrape_failure_keeps_search_results():
    respx.get(_SEARCH).mock(return_value=httpx.Response(200, json={"items": [_repo()]}))
    respx.get("https://github.com/trending").mock(return_value=httpx.Response(403))
    items = await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    # Search base still yields, scrape 403 is swallowed
    assert len(items) == 1
    assert items[0].title_en == "openai/whisper"


@respx.mock
async def test_trending_search_http_error_raises():
    respx.get(_SEARCH).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)


def test_scrape_trending_extracts_full_names():
    html = """
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/comfyanonymous/ComfyUI">ComfyUI</a></h2>
    </article>
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/ollama/ollama">ollama</a></h2>
    </article>
    """
    assert _scrape_trending(html) == ["comfyanonymous/ComfyUI", "ollama/ollama"]


def test_scrape_trending_empty_on_garbage():
    assert _scrape_trending("<html>nope</html>") == []
