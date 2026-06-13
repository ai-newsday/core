from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

from src.core.types import RawItem, RunContext, SourceSpec


def _published_utc(entry) -> datetime | None:
    tm = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if tm is None:
        return None
    return datetime.fromtimestamp(timegm(tm), tz=timezone.utc)


def _image_url(entry) -> str | None:
    for m in getattr(entry, "media_content", []) or []:
        if m.get("url"):
            return m["url"]
    for enc in getattr(entry, "enclosures", []) or []:
        if enc.get("href"):
            return enc["href"]
    return None


class RSSAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        items: list[RawItem] = []
        for entry in feed.entries:
            published = _published_utc(entry)
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not published or not title or not link:
                continue  # drop undated/incomplete
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    source_type=source.type,
                    published_at=published,
                    raw_summary=getattr(entry, "summary", None),
                    image_url=_image_url(entry),
                    fetched_via="native",
                )
            )
        return items
