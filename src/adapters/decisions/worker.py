from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class DecisionStore(Protocol):
    async def fetch(self) -> dict[str, str]:
        """返回 {item_id: action}，近 7 天内全部决策。"""
        ...


class FakeDecisionStore:
    """测试用，记录 fetch 次数。"""

    def __init__(self, decisions: dict[str, str] | None = None):
        self._decisions = dict(decisions or {})
        self.fetch_count = 0

    async def fetch(self) -> dict[str, str]:
        self.fetch_count += 1
        return dict(self._decisions)


class WorkerDecisionStore:
    """从 Cloudflare Worker 的 GET /decisions 拉决策。"""

    def __init__(self, url: str, secret: str, timeout_s: float = 10.0):
        self._url = url.rstrip("/") + "/decisions"
        self._secret = secret
        self._timeout = timeout_s

    async def fetch(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._secret}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return {str(k): str(v) for k, v in data.items()}
