from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec


class HNAdapter:
    """Hacker News front-page via Algolia. Keeps AI-relevant, high-point stories
    as (writeup, individual) items carrying `points` as signal."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            hits = (resp.json() or {}).get("hits") or []

        kws = [k.lower() for k in (source.keywords or [])]
        items: list[RawItem] = []
        for h in hits:
            title = h.get("title")
            created = h.get("created_at_i")
            if not title or created is None:
                continue
            points = h.get("points") or 0
            if source.min_score is not None and points < source.min_score:
                continue
            url = h.get("url")
            haystack = f"{title} {url or ''}".lower()
            if kws and not any(k in haystack for k in kws):
                continue
            link = url or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            # key MUST be `hn_points` — that's what scoring popularity_weights + enrich read
            signals = {"hn_points": points, "num_comments": h.get("num_comments")}
            signals = {k: v for k, v in signals.items() if v not in (None, "")}
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=datetime.fromtimestamp(int(created), tz=timezone.utc),
                    signals=signals,
                    fetched_via="native",
                )
            )
        return items
