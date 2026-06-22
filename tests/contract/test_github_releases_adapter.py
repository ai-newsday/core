import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.github_releases import GithubReleasesAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_RELEASES = "https://api.github.com/repos/comfyanonymous/ComfyUI/releases"
_REPO = "https://api.github.com/repos/comfyanonymous/ComfyUI"


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.ghr"))


def _spec():
    return SourceSpec(name="comfyui", url=_RELEASES, genre=Genre.announcement,
                      publisher=Publisher.individual, adapter="github_releases")


def _release(tag="v0.3.40", body="adds new nodes", date="2026-06-22T10:00:00Z",
             url="https://github.com/comfyanonymous/ComfyUI/releases/tag/v0.3.40"):
    return {"tag_name": tag, "body": body, "published_at": date, "html_url": url}


@respx.mock
async def test_releases_maps_fields_and_star_signal():
    respx.get(_RELEASES).mock(return_value=httpx.Response(200, json=[_release()]))
    respx.get(_REPO).mock(return_value=httpx.Response(200, json={"stargazers_count": 65000}))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "comfyui v0.3.40"
    assert it.link == "https://github.com/comfyanonymous/ComfyUI/releases/tag/v0.3.40"
    assert it.raw_summary == "adds new nodes"
    assert it.genre == Genre.announcement and it.publisher == Publisher.individual
    assert it.signals == {"github_stars": 65000}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_releases_empty_returns_empty():
    respx.get(_RELEASES).mock(return_value=httpx.Response(200, json=[]))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_releases_skips_release_without_published_at():
    # draft / unpublished release has published_at: null
    respx.get(_RELEASES).mock(return_value=httpx.Response(
        200, json=[{"tag_name": "v9", "body": "", "published_at": None, "html_url": "u"}]))
    respx.get(_REPO).mock(return_value=httpx.Response(200, json={"stargazers_count": 1}))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_releases_http_error_raises():
    respx.get(_RELEASES).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
