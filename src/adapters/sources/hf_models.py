from __future__ import annotations
from datetime import datetime, timezone
import httpx
from src.core.types import SourceSpec, RunContext, RawItem


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFModelsAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            mid = row.get("id")
            published = _parse_dt(row.get("createdAt"))   # createdAt per decision #11
            if not mid or not published:
                continue
            items.append(RawItem(
                title_en=mid, link=f"https://huggingface.co/{mid}",
                source=source.name, source_type=source.type,
                published_at=published, raw_summary=None, fetched_via="native",
            ))
        return items
