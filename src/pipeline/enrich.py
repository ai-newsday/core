"""enrich: 给无 popularity 信号的源(RSS 类)用 HN Algolia by URL 反查补 signals。
纯流程 + 注入式 HN 客户端 (协议: async def search_url(url) -> list[hit])。
失败容错: 单条出错不挂整批; 已有 popularity 信号 / skip_genres 的 genre 跳过。"""

from __future__ import annotations

import asyncio

from src.core.types import EnrichConfig, RawItem, RunContext
from src.observability.events import emit

# 已经具备 popularity 信号的键 (任一存在即跳过 HN 查)
_POPULARITY_KEYS = {"upvotes", "likes", "hn_points", "downloads", "github_stars"}


def _has_popularity(item: RawItem) -> bool:
    return any(k in item.signals for k in _POPULARITY_KEYS)


async def _enrich_one(item: RawItem, client, sem: asyncio.Semaphore, ctx: RunContext) -> None:
    async with sem:
        try:
            hits = await client.search_url(item.link)
        except Exception as e:
            emit(
                ctx.logger,
                "enrich_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:120],
            )
            return
    if not hits:
        return
    # 同一 URL 在 HN 可能多次提交, 取 max points + sum comments
    pts = max((h.get("points") or 0) for h in hits)
    cmts = sum((h.get("num_comments") or 0) for h in hits)
    obj_id = hits[0].get("objectID")
    item.signals["hn_points"] = pts
    item.signals["hn_comments"] = cmts
    if obj_id:
        item.signals["hn_url"] = f"https://news.ycombinator.com/item?id={obj_id}"


async def enrich_with_hn(
    items: list[RawItem], client, config: EnrichConfig, ctx: RunContext
) -> list[RawItem]:
    """关 / 空输入 → 直通; 否则按 skip_genres + 已有 popularity 跳过, 并发查 HN。"""
    emit(ctx.logger, "enrich_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "enrich_done", enriched=0, skipped=len(items))
        return items
    skip = set(config.skip_genres or [])
    to_enrich = [it for it in items if it.genre.value not in skip and not _has_popularity(it)]
    if not to_enrich:
        emit(ctx.logger, "enrich_done", enriched=0, skipped=len(items))
        return items
    sem = asyncio.Semaphore(max(1, config.concurrency))
    await asyncio.gather(*(_enrich_one(it, client, sem, ctx) for it in to_enrich))
    enriched = sum(1 for it in to_enrich if "hn_points" in it.signals)
    emit(
        ctx.logger,
        "enrich_done",
        enriched=enriched,
        skipped=len(items) - len(to_enrich),
        queried=len(to_enrich),
    )
    return items
