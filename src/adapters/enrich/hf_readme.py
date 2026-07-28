"""HF README 抓取客户端。协议: async def fetch_readme(model_id) -> str | None。
404(模型没有 README 文件)一律返回 None, 交给调用方硬过滤; 其它 HTTP 错误向上抛出,
由调用方(enrich_hf_models_readme)按单条失败处理, 不挂整批。"""

from __future__ import annotations

import httpx


class HFReadmeClient:
    def __init__(self, timeout_s: int = 8):
        self._timeout = timeout_s

    async def fetch_readme(self, model_id: str) -> str | None:
        url = f"https://huggingface.co/{model_id}/raw/main/README.md"
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
