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
            # HF 端的量化信号: 给下游排名/取舍用; 不写则缺省 {}
            signals = {
                "upvotes": paper.get("upvotes"),
                "num_comments": paper.get("numComments") or row.get("numComments"),
                "github_stars": paper.get("githubStars"),
                "github_repo": paper.get("githubRepo"),
                "ai_keywords": paper.get("ai_keywords") or [],
                "ai_summary": paper.get("ai_summary"),
                "submitted_on_daily_at": paper.get("submittedOnDailyAt"),
            }
            signals = {k: v for k, v in signals.items() if v not in (None, [], "")}
            items.append(RawItem(
                title_en=title, link=f"https://huggingface.co/papers/{pid}",
                source=source.name, source_type=source.type,
                published_at=published, raw_summary=paper.get("summary"),
                fetched_via="native", signals=signals,
            ))
        return items
