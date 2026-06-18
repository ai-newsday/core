from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec

# Reddit's unauthenticated .json/API is 403-blocked (auth-gated) and self-service OAuth
# apps are closed. old.reddit.com still serves listing HTML unauthenticated with the
# score in `data-score`, so we scrape that (best-effort; needs a descriptive UA).
_USER_AGENT = "ai-newsday/1.0 (https://github.com/ai-newsday/core)"

_THING_RE = re.compile(r'<div [^>]*\bclass="[^"]*\bthing\b[^"]*"[^>]*>')
_ATTR_RE = re.compile(r'data-([\w-]+)="([^"]*)"')
_TITLE_RE = re.compile(r'<a [^>]*\bclass="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _int_or_none(s: str | None) -> int | None:
    try:
        return int(s)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class RedditAdapter:
    """Scrape old.reddit.com listing HTML → (writeup, individual) items carrying `upvotes`
    (from `data-score`) as signal. Best-effort: unofficial HTML scraping, since the .json
    API is auth-gated and OAuth app creation is closed."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            text = resp.text

        things = list(_THING_RE.finditer(text))
        items: list[RawItem] = []
        for i, m in enumerate(things):
            attrs = dict(_ATTR_RE.findall(m.group(0)))
            if attrs.get("promoted") == "true":  # skip ads
                continue
            if not attrs.get("fullname", "").startswith("t3_"):  # link posts only
                continue
            ups = _int_or_none(attrs.get("score"))
            ts = _int_or_none(attrs.get("timestamp"))  # epoch ms
            if ups is None or ts is None:
                continue
            if source.min_score is not None and ups < source.min_score:
                continue
            end = things[i + 1].start() if i + 1 < len(things) else len(text)
            tm = _TITLE_RE.search(text, m.end(), end)
            if not tm:
                continue
            title = _html.unescape(_TAG_RE.sub("", tm.group(1))).strip()
            if not title:
                continue
            url = attrs.get("url", "")
            permalink = attrs.get("permalink", "")
            is_external = (
                url.startswith("http") and "reddit.com" not in url and "redd.it" not in url
            )
            link = url if is_external else f"https://www.reddit.com{permalink}"
            signals = {"upvotes": ups, "num_comments": _int_or_none(attrs.get("comments-count"))}
            signals = {k: v for k, v in signals.items() if v is not None}
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    signals=signals,
                    fetched_via="native",
                )
            )
        return items
