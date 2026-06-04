from __future__ import annotations
import asyncio
import time
from datetime import timedelta
from src.core.types import (CollectionConfig, RunContext, CollectionResult,
                            SourceReport, SourceSpec, RawItem)
from src.core.registry import load_registry
from src.adapters.sources import ADAPTERS
from src.observability.events import emit


async def _run_one(source: SourceSpec, config: CollectionConfig,
                   ctx: RunContext, sem: asyncio.Semaphore
                   ) -> tuple[SourceReport, list[RawItem]]:
    start = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    if source.needs_firecrawl and not config.firecrawl_enabled:
        emit(ctx.logger, "source_fetch_fail", name=source.name,
             error_code="firecrawl_disabled")
        return SourceReport(name=source.name, status="failed", item_count=0,
                            error="needs_firecrawl but firecrawl_enabled=false",
                            elapsed_ms=elapsed()), []

    adapter = ADAPTERS[source.adapter]
    try:
        async with sem:
            items = await asyncio.wait_for(
                adapter.fetch(source, ctx, config.timeout_s),
                timeout=config.timeout_s)
    except Exception as e:  # noqa: BLE001 - single source failure is non-fatal
        emit(ctx.logger, "source_fetch_fail", name=source.name, error_code=str(e))
        return SourceReport(name=source.name, status="failed", item_count=0,
                            error=str(e), elapsed_ms=elapsed()), []

    cutoff = ctx.now - timedelta(hours=config.window_hours)
    kept = [it for it in items if it.published_at >= cutoff]
    # 单源条数上限(firehose 阀): 按最新优先, 防 arXiv/HN 一类把池子打爆。
    if source.max_items is not None and len(kept) > source.max_items:
        kept.sort(key=lambda it: it.published_at, reverse=True)
        kept = kept[:source.max_items]
    status = "working" if kept else "empty"
    emit(ctx.logger, "source_fetch_success", name=source.name, item_count=len(kept))
    return SourceReport(name=source.name, status=status, item_count=len(kept),
                        elapsed_ms=elapsed()), kept


async def collect(config: CollectionConfig, run_ctx: RunContext) -> CollectionResult:
    emit(run_ctx.logger, "pipeline_start", run_id=run_ctx.run_id,
         now=run_ctx.now, window_hours=config.window_hours)
    sources = load_registry(config.sources_registry_path, run_ctx)
    sem = asyncio.Semaphore(config.concurrency)
    results = await asyncio.gather(
        *[_run_one(s, config, run_ctx, sem) for s in sources])
    items: list[RawItem] = [it for _, kept in results for it in kept]
    reports = [rep for rep, _ in results]
    is_silent = len(items) == 0
    emit(run_ctx.logger, "collection_done",
         total_items=len(items), silent=is_silent)
    return CollectionResult(items=items, source_reports=reports, is_silent=is_silent)
