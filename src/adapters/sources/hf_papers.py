from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFPapersAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            paper = row.get("paper", {})
            pid, title = paper.get("id"), paper.get("title")
            upvotes = paper.get("upvotes")
            if source.min_score is not None and (upvotes or 0) < source.min_score:
                continue
            # 当日精选论文按"精选日"算新鲜度(submittedOnDailyAt), 否则 arxiv 原始
            # publishedAt 常早于采集时间窗, 整批精选集会被砍光。回退保旧行为。
            published = _parse_dt(
                paper.get("submittedOnDailyAt")
                or paper.get("publishedAt")
                or row.get("publishedAt")
            )
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
            items.append(
                RawItem(
                    title_en=title,
                    link=f"https://huggingface.co/papers/{pid}",
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=published,
                    raw_summary=paper.get("summary"),
                    fetched_via="native",
                    signals=signals,
                )
            )
        return items
