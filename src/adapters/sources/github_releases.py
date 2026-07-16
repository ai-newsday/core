from __future__ import annotations

import httpx

from src.adapters.sources._github import _auth_headers, _parse_dt
from src.core.types import RawItem, RunContext, SourceSpec


class GithubReleasesAdapter:
    """Watch a curated repo's releases. `source.url` =
    https://api.github.com/repos/{owner}/{repo}/releases. Each published release →
    one (announcement) item carrying the repo's star count as `github_stars`.
    Recency is enforced downstream by collect's window filter via published_at."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        repo_url = source.url.rsplit("/releases", 1)[0]
        headers = _auth_headers()
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            releases = resp.json() or []
            if not releases:
                return []
            repo_resp = await client.get(repo_url)
            repo_resp.raise_for_status()
            stars = (repo_resp.json() or {}).get("stargazers_count")

        signals = {"github_stars": stars} if stars is not None else {}
        items: list[RawItem] = []
        for r in releases:
            if r.get("prerelease"):
                continue
            published = r.get("published_at")
            tag = r.get("tag_name")
            html_url = r.get("html_url")
            if not published or not tag or not html_url:
                continue
            items.append(
                RawItem(
                    title_en=f"{source.name} {tag}",
                    link=html_url,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=_parse_dt(published),
                    raw_summary=r.get("body") or None,
                    signals=dict(signals),
                    fetched_via="native",
                )
            )
        return items
