from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFModelsAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            mid = row.get("id")
            if not mid:
                continue
            created = _parse_dt(row.get("createdAt"))
            # surfacing time = ctx.now (这是 trending 榜的语义, 不是 firehose 创建时间)
            # 老 createdAt 进 signals 保留信息
            signals = {
                "likes": row.get("likes"),
                "downloads": row.get("downloads"),
                "downloads_all_time": row.get("downloadsAllTime"),
                "pipeline_tag": row.get("pipeline_tag"),
                "library_name": row.get("library_name"),
                "tags": row.get("tags") or [],
                "trending_score": row.get("trendingScore"),
                "created_at": created.isoformat() if created else None,
            }
            signals = {k: v for k, v in signals.items() if v not in (None, [], "")}
            items.append(
                RawItem(
                    title_en=mid,
                    link=f"https://huggingface.co/{mid}",
                    source=source.name,
                    source_type=source.type,
                    published_at=ctx.now,
                    raw_summary=None,
                    fetched_via="native",
                    signals=signals,
                )
            )
        return items
