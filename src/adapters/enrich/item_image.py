"""逐条配图的唯一 IO: 取原文页面 HTML (spec 2026-08-31-per-item-images-design)。

抽取规则在 src/pipeline/item_image.py, 是纯函数; 这里只负责把 HTML 拿回来。
与 HFReadmeClient 同构。"""

from __future__ import annotations

import httpx

# 有些站(marktechpost)对无 UA 的请求直接 403
_UA = "Mozilla/5.0 (compatible; ai-newsday/1.0; +https://github.com/ai-newsday/core)"


class ItemImageClient:
    def __init__(self, timeout_s: int = 8, max_bytes: int = 2_000_000):
        self._timeout = timeout_s
        self._max_bytes = max_bytes

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

    async def check_image(self, url: str) -> bool:
        """校验候选图 URL 真的能打开: 找到 URL 和拿到图是两回事(2026-09-01 实测
        arXiv 的畸形拼接返回 404、也拿到过合法但没用的赞助商 logo)。HEAD 优先,
        部分站点(如某些 CDN)不支持 HEAD 时退化成 GET; 超过 max_bytes 视为不合适
        (实测 arXiv 图有到 1.6MB 的, 微信素材有大小限制)。"""
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            try:
                resp = await client.head(url)
                if resp.status_code == 405:  # 部分站不支持 HEAD, 退化成 GET
                    resp = await client.get(url)
            except httpx.HTTPError:
                return False
            if resp.status_code != 200:
                return False
            if "image" not in resp.headers.get("content-type", ""):
                return False
            length = resp.headers.get("content-length")
            if length is not None and int(length) > self._max_bytes:
                return False
            return True
