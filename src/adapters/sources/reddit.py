from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec

# Public .json is IP-blocked (403) from datacenter/CI, so OAuth (app-only) is required.
_USER_AGENT = "ai-newsday/1.0 (https://github.com/ai-newsday/core)"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


async def _get_app_token(client: httpx.AsyncClient, client_id: str, client_secret: str) -> str:
    """App-only OAuth token (client_credentials grant). HTTP Basic = (client_id, secret)."""
    resp = await client.post(
        _TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    return (resp.json() or {})["access_token"]


class RedditAdapter:
    """Subreddit top via OAuth (oauth.reddit.com) → (writeup, individual) items carrying
    `upvotes` as signal. Requires REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in the env
    (a Reddit 'script' app); the unauthenticated .json endpoint returns 403 from CI."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")

        # source.url is the public www endpoint; OAuth reads go through oauth.reddit.com.
        url = source.url.replace("https://www.reddit.com", "https://oauth.reddit.com")

        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            token = await _get_app_token(client, client_id, client_secret)
            resp = await client.get(url, headers={"Authorization": f"bearer {token}"})
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
