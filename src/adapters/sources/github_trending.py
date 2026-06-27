from __future__ import annotations

import re
from datetime import datetime, timedelta

import httpx

from src.adapters.sources._github import _auth_headers, _parse_dt
from src.adapters.sources.hn import _kw_match
from src.core.types import Publisher, RawItem, RunContext, SourceSpec

# ponytail: canonical Trending endpoint hardcoded (an endpoint, not a tuning knob)
_TRENDING_URL = "https://github.com/trending"
# ponytail: "newness" window — Search sorts by ABSOLUTE stars, so without this the
# query returns ancient high-star giants (AutoGPT etc.) that merely got a recent push.
# created:>=cutoff restricts to repos *created* in the window = genuinely new+rising.
# Widen/narrow this one number to loosen/tighten "new".
_NEW_REPO_DAYS = 180
# /owner/repo inside the trending list heading anchors
_TRENDING_RE = re.compile(
    r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"'
)


def _inject_created_window(url: str, now: datetime) -> str:
    """Add `created:>=<now-_NEW_REPO_DAYS>` to the Search `q=` so only recently-created
    repos surface. No-op if the query already pins `created:` (operator override)."""
    if "created:" in url:
        return url
    cutoff = (now - timedelta(days=_NEW_REPO_DAYS)).date().isoformat()
    return re.sub(
        r"(q=)([^&]*)", lambda m: f"{m.group(1)}{m.group(2)}+created:>={cutoff}", url, count=1
    )


def _scrape_trending(html: str) -> list[str]:
    """Extract owner/repo full names from a github.com/trending HTML page."""
    return _TRENDING_RE.findall(html)


def _is_ai_repo(repo: dict, keywords: list[str] | None) -> bool:
    """抓取路径 AI 闸: repo 有 topic ∈ keywords, 或 description 词边界命中 keyword → 保留。
    keywords 空/None → 全保留(向后兼容, 不影响无 keywords 的源)。"""
    if not keywords:
        return True
    kws = {k.lower() for k in keywords}
    topics = {str(t).lower() for t in (repo.get("topics") or [])}
    if topics & kws:
        return True
    return _kw_match(repo.get("description") or "", keywords)


def _publisher_for_owner(repo: dict, source: SourceSpec) -> Publisher:
    """Repo owner.type → publisher: Organization=company, User=individual.
    缺 owner / 未知 type → source.publisher(registry fallback)。"""
    otype = (repo.get("owner") or {}).get("type")
    if otype == "Organization":
        return Publisher.company
    if otype == "User":
        return Publisher.individual
    return source.publisher


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
        publisher=_publisher_for_owner(r, source),
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
        search_url = _inject_created_window(source.url, ctx.now)
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(search_url)
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
                    repo_json = repo_resp.json() or {}
                    # 抓取路径无 topic 过滤 → 本地 AI 闸丢非 AI repo(Search 路径已服务端过滤)
                    if not _is_ai_repo(repo_json, source.keywords):
                        continue
                    it = _item_from_repo(repo_json, source)
                    if it:
                        items.append(it)
            except (httpx.HTTPError, ValueError) as e:
                ctx.logger.info("trending scrape skipped: %s", e)

        return items
