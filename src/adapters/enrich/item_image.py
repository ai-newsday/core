"""逐条配图的唯一 IO: 取原文页面 HTML (spec 2026-08-31-per-item-images-design)。

抽取规则在 src/pipeline/item_image.py, 是纯函数; 这里只负责把 HTML 拿回来。
与 HFReadmeClient 同构。"""

from __future__ import annotations

import httpx

# 有些站(marktechpost)对无 UA 的请求直接 403
_UA = "Mozilla/5.0 (compatible; ai-newsday/1.0; +https://github.com/ai-newsday/core)"


class ItemImageClient:
    def __init__(self, timeout_s: int = 8):
        self._timeout = timeout_s

    async def fetch_html(self, url: str) -> str | None:
        """取页面 HTML; 非 200 或非 HTML 返回 None(调用方据此判为无图)。"""
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            if "html" not in resp.headers.get("content-type", ""):
                return None
            return resp.text
