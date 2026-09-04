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


def _is_image(m: dict) -> bool:
    """media:content / enclosure 里只有声明为图片的才是图片。

    YouTube 的 media:content 是**播放器**不是图 (2026-09-04 实测 runway-yt feed):
        {url: .../v/<id>?version=3, type: application/x-shockwave-flash}
    照单全收会把一个返回 text/html 的地址当封面发出去。"""
    if str(m.get("type", "")).startswith("image/"):
        return True
    return m.get("medium") == "image"  # 部分 feed 只给 medium 不给 type


def _image_url(entry) -> str | None:
    for m in getattr(entry, "media_content", []) or []:
        if m.get("url") and _is_image(m):
            return m["url"]
    # YouTube 的真图在 media:thumbnail 里; 少了这一条, 过滤完播放器就彻底没图了
    for t in getattr(entry, "media_thumbnail", []) or []:
        if t.get("url"):
            return t["url"]
    for enc in getattr(entry, "enclosures", []) or []:
        if enc.get("href") and _is_image(enc):
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
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=published,
                    raw_summary=getattr(entry, "summary", None),
                    image_url=_image_url(entry),
                    fetched_via="native",
                )
            )
        return items
