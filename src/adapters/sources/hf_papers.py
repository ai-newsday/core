from __future__ import annotations
from datetime import datetime, timezone
import httpx
from src.core.types import SourceSpec, RunContext, RawItem


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFPapersAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            paper = row.get("paper", {})
            pid, title = paper.get("id"), paper.get("title")
            published = _parse_dt(paper.get("publishedAt") or row.get("publishedAt"))
            if not pid or not title or not published:
                continue
            items.append(RawItem(
                title_en=title, link=f"https://huggingface.co/papers/{pid}",
                source=source.name, source_type=source.type,
                published_at=published, raw_summary=paper.get("summary"),
                fetched_via="native",
            ))
        return items
