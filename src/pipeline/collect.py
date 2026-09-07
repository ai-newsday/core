from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from src.adapters.sources import ADAPTERS
from src.core.registry import load_registry
from src.core.types import (
    CollectionConfig,
    CollectionResult,
    RawItem,
    RunContext,
    SourceReport,
    SourceSpec,
)
from src.observability.events import emit


async def _run_one(
    source: SourceSpec, config: CollectionConfig, ctx: RunContext, sem: asyncio.Semaphore
) -> tuple[SourceReport, list[RawItem]]:
    start = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    if source.needs_firecrawl and not config.firecrawl_enabled:
        emit(ctx.logger, "source_fetch_fail", name=source.name, error_code="firecrawl_disabled")
        return SourceReport(
            name=source.name,
            status="failed",
            item_count=0,
            error="needs_firecrawl but firecrawl_enabled=false",
            elapsed_ms=elapsed(),
        ), []

    adapter = ADAPTERS[source.adapter]
    try:
        async with sem:
            items = await asyncio.wait_for(
                adapter.fetch(source, ctx, config.timeout_s), timeout=config.timeout_s
            )
            items = [it.model_copy(update={"adapter": source.adapter}) for it in items]
    except Exception as e:  # noqa: BLE001 - single source failure is non-fatal
        emit(ctx.logger, "source_fetch_fail", name=source.name, error_code=str(e))
        return SourceReport(
            name=source.name, status="failed", item_count=0, error=str(e), elapsed_ms=elapsed()
        ), []

    window_hours = config.window_hours_by_adapter.get(source.adapter, config.window_hours)
    cutoff = ctx.now - timedelta(hours=window_hours)
    kept = [it for it in items if it.published_at >= cutoff]
    # 单源条数上限(firehose 阀): 按最新优先, 防 arXiv/HN 一类把池子打爆。
    if source.max_items is not None and len(kept) > source.max_items:
        kept.sort(key=lambda it: it.published_at, reverse=True)
        kept = kept[: source.max_items]
    status = "working" if kept else "empty"
    emit(ctx.logger, "source_fetch_success", name=source.name, item_count=len(kept))
    return SourceReport(
        name=source.name, status=status, item_count=len(kept), elapsed_ms=elapsed()
    ), kept


async def collect(config: CollectionConfig, run_ctx: RunContext) -> CollectionResult:
    emit(
        run_ctx.logger,
        "pipeline_start",
        run_id=run_ctx.run_id,
        now=run_ctx.now,
        window_hours=config.window_hours,
    )
    sources = load_registry(config.sources_registry_path, run_ctx)
    sem = asyncio.Semaphore(config.concurrency)
    results = await asyncio.gather(*[_run_one(s, config, run_ctx, sem) for s in sources])
    items: list[RawItem] = [it for _, kept in results for it in kept]
    reports = [rep for rep, _ in results]
    is_silent = len(items) == 0
    emit(run_ctx.logger, "collection_done", total_items=len(items), silent=is_silent)
    return CollectionResult(items=items, source_reports=reports, is_silent=is_silent)


def check_zero_yield(
    yields: dict[str, int], state: dict[str, int], threshold: int
) -> tuple[dict[str, int], list[str]]:
    """跟踪各监控组连续产出为 0 的次数, 返回 (新状态, 本次要告警的组)。

    纯函数, 状态由调用方持久化(kv_state)。

    存在的理由: 采集失败在这条流水线里是**非致命**的——某个源抓不到东西, 日志写的是
    `source_fetch_success, item_count: 0`, 一切看起来正常。x-extension 因此连续 17 天
    产出 0 无人察觉; 2026-09-07 五个 X 源再次全部归零, 同样无人察觉。而发卡池是固定
    取前 100, 好源交白卷时位置不会空着, 会被二手新闻填满(实测二手占比从 24% 涨到
    77%)——静默归零不是"少几条", 是换了一份报纸。

    一次停摆只报一次(计数恰好越过阈值的那一刻)。连续 17 天报 17×8 条消息, 结果只会
    是开始忽略告警; 恢复后再次归零算新的一次事故, 会重新报。

    只更新 `yields` 里出现的组: 状态里可能留着已经不再监控的组, 不该凭空冒出来。"""
    new_state = dict(state)
    alerts: list[str] = []
    for group, count in yields.items():
        if count > 0:
            new_state[group] = 0
            continue
        n = new_state.get(group, 0) + 1
        new_state[group] = n
        if n == threshold:
            alerts.append(group)
    return new_state, alerts
