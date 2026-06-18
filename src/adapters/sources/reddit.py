from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec

# Reddit 429s generic UAs; a descriptive one is required even for public .json.
_USER_AGENT = "ai-newsday/1.0 (https://github.com/ai-newsday/core)"


class RedditAdapter:
    """Subreddit top.json → (writeup, individual) items carrying `upvotes` as signal."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            children = ((resp.json() or {}).get("data") or {}).get("children") or []

        items: list[RawItem] = []
        for c in children:
            d = c.get("data") or {}
            title = d.get("title")
            created = d.get("created_utc")
            if not title or created is None:
                continue
            ups = d.get("ups") or 0
            if source.min_score is not None and ups < source.min_score:
                continue
            if d.get("is_self"):
                link = f"https://www.reddit.com{d.get('permalink', '')}"
                raw_summary = (d.get("selftext") or "")[:500] or None
            else:
                link = d.get("url") or f"https://www.reddit.com{d.get('permalink', '')}"
                raw_summary = None
            signals = {"upvotes": ups, "num_comments": d.get("num_comments")}
            signals = {k: v for k, v in signals.items() if v not in (None, "")}
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=datetime.fromtimestamp(float(created), tz=timezone.utc),
                    raw_summary=raw_summary,
                    signals=signals,
                    fetched_via="native",
                )
            )
        return items
