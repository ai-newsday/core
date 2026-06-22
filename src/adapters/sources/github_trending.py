from __future__ import annotations

import re
from datetime import datetime

import httpx

from src.adapters.sources._github import _auth_headers, _parse_dt
from src.core.types import RawItem, RunContext, SourceSpec

# ponytail: canonical Trending endpoint hardcoded (an endpoint, not a tuning knob)
_TRENDING_URL = "https://github.com/trending"
# /owner/repo inside the trending list heading anchors
_TRENDING_RE = re.compile(r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"')


def _scrape_trending(html: str) -> list[str]:
    """Extract owner/repo full names from a github.com/trending HTML page."""
    return _TRENDING_RE.findall(html)


def _item_from_repo(r: dict, source: SourceSpec) -> RawItem | None:
    full = r.get("full_name")
    pushed = r.get("pushed_at")
    html_url = r.get("html_url")
    if not full or not pushed or not html_url:
        return None
    stars = r.get("stargazers_count")
    return RawItem(
        title_en=full,
        link=html_url,
        source=source.name,
        genre=source.genre,
        publisher=source.publisher,
        published_at=_parse_dt(pushed),
        raw_summary=r.get("description") or None,
        signals={"github_stars": stars} if stars is not None else {},
        fetched_via="native",
    )


class GithubTrendingAdapter:
    """Discover trending AI repos. Base: Search API (source.url, reliable, token'd).
    Best-effort bonus: scrape github.com/trending for repos the search missed, resolved
    via the repo API. Scrape failures (403 from Actions IP, layout change) are swallowed —
    the Search base still produces. Recency (闸 ii) is enforced downstream by collect's
    window filter via published_at=pushed_at."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        headers = _auth_headers()
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            repos = (resp.json() or {}).get("items") or []

            items: list[RawItem] = []
            seen: set[str] = set()
            for r in repos:
                it = _item_from_repo(r, source)
                if it:
                    items.append(it)
                    seen.add(r.get("full_name"))

            # best-effort Trending HTML scrape
            try:
                t_resp = await client.get(_TRENDING_URL, params={"since": "daily"})
                t_resp.raise_for_status()
                for full in _scrape_trending(t_resp.text):
                    if full in seen:
                        continue
                    seen.add(full)
                    repo_resp = await client.get(f"https://api.github.com/repos/{full}")
                    repo_resp.raise_for_status()
                    it = _item_from_repo(repo_resp.json() or {}, source)
                    if it:
                        items.append(it)
            except (httpx.HTTPError, ValueError) as e:
                ctx.logger.info("trending scrape skipped: %s", e)

        return items
