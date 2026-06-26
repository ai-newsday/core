import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.github_trending import (
    GithubTrendingAdapter,
    _inject_created_window,
    _scrape_trending,
)
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

# already pins created: → adapter injection is a no-op, so respx exact-match stays stable
_SEARCH = "https://api.github.com/search/repositories?q=topic:llm+created:>=2026-01-01+sort:stars&sort=stars&order=desc&per_page=30"


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


def test_inject_created_window_adds_recency_filter():
    """新仓库闸: 注入 created:>=<now-180d> 到 q=, 不破坏其余参数 (修 trending 出老 repo)。"""
    url = "https://api.github.com/search/repositories?q=topic:llm+sort:stars&per_page=30"
    out = _inject_created_window(url, datetime(2026, 6, 24, tzinfo=timezone.utc))
    assert "q=topic:llm+sort:stars+created:>=2025-12-26" in out
    assert out.endswith("&per_page=30")


def test_inject_created_window_respects_operator_created():
    """已显式写 created: → 不再注入 (operator override)。"""
    url = "https://api.github.com/search/repositories?q=topic:llm+created:>=2026-05-01&per_page=30"
    assert _inject_created_window(url, datetime(2026, 6, 24, tzinfo=timezone.utc)) == url


def test_trending_fetch_injects_created_when_absent():
    """fetch 对无 created 的 source.url 注入新仓库闸; 验证打到 API 的 URL 带 created:>=。"""

    @respx.mock
    async def go():
        base = "https://api.github.com/search/repositories?q=topic:llm&per_page=30"
        route = respx.get(url__regex=r"https://api\.github\.com/search/repositories.*").mock(
            return_value=httpx.Response(200, json={"items": [_repo()]})
        )
        respx.get("https://github.com/trending").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        spec = SourceSpec(
            name="t",
            url=base,
            genre=Genre.announcement,
            publisher=Publisher.company,
            adapter="github_trending",
        )
        await GithubTrendingAdapter().fetch(spec, _ctx(), timeout_s=15)
        called = str(route.calls.last.request.url)
        # httpx percent-encodes '>' → '%3E'; assert encoding-robustly. 2026-06-23 - 180d
        assert "created:" in called and "2025-12-25" in called

    import asyncio

    asyncio.run(go())


from src.adapters.sources.github_trending import _item_from_repo, _publisher_for_owner


def _repo_with_owner(otype):
    r = _repo()
    r["owner"] = {"type": otype} if otype else {}
    return r


def test_publisher_from_owner_type():
    src = _spec()  # source.publisher == Publisher.company (fallback)
    assert _publisher_for_owner(_repo_with_owner("Organization"), src) == Publisher.company
    assert _publisher_for_owner(_repo_with_owner("User"), src) == Publisher.individual
    # 缺 owner / 未知 type → 回退 source.publisher
    assert _publisher_for_owner(_repo_with_owner(None), src) == src.publisher
    assert _publisher_for_owner({}, src) == src.publisher


def test_item_from_repo_uses_owner_publisher():
    src = _spec()
    user_repo = _repo_with_owner("User")
    it = _item_from_repo(user_repo, src)
    assert it.publisher == Publisher.individual  # 个人 repo, 不随 source.publisher=company
