"""HN Algolia by-URL search 客户端 (免费, 限速 ~10000/h/IP)。
协议: async def search_url(url) -> list[{points, num_comments, objectID, title, url}]。"""

from __future__ import annotations

import httpx

_URL = "https://hn.algolia.com/api/v1/search"


class HNAlgoliaClient:
    def __init__(self, timeout_s: int = 8):
        self._timeout = timeout_s

    async def search_url(self, url: str) -> list[dict]:
        # `tags=story` 限 story 类型; `restrictSearchableAttributes=url` 精确 URL 匹配
        params = {
            "query": url,
            "tags": "story",
            "restrictSearchableAttributes": "url",
            "hitsPerPage": "5",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(_URL, params=params)
            r.raise_for_status()
            return (r.json() or {}).get("hits") or []
