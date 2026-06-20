from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from src.adapters.decisions.worker import WorkerDecisionStore
from src.adapters.embedding.modelscope import ModelScopeEmbedder
from src.adapters.enrich.hn_algolia import HNAlgoliaClient
from src.adapters.llm.openai_compat import OpenAICompatLLM
from src.adapters.vectorstore.memory import InMemoryVectorStore
from src.core.config import (
    load_dedup_config,
    load_delivery_config,
    load_enrich_config,
    load_feedback_config,
    load_feedback_events,
    load_interpret_config,
    load_publish_config,
    load_quality_weights,
    load_review_config,
    load_review_decisions,
    load_scoring_config,
    load_selfcheck_config,
)
from src.core.types import CollectionConfig, InterpretConfig, RunContext
from src.notifiers import FakeNotifier
from src.notifiers.telegram_polling import TelegramPollingNotifier
from src.notifiers.website import WebsiteNotifier
from src.observability.persist import dump_json, dump_jsonl, run_dir
from src.pipeline.collect import collect
from src.pipeline.dedup import dedup
from src.pipeline.enrich import enrich_with_hn
from src.pipeline.feedback import derive_events, feedback
from src.pipeline.interpret import interpret
from src.pipeline.publish import publish
from src.pipeline.review import review
from src.pipeline.score import score
from src.pipeline.selfcheck import self_check
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database


def _make_llm(icfg: InterpretConfig) -> OpenAICompatLLM:
    if icfg.models:
        primary = icfg.models[0]
        fallbacks = icfg.models[1:] + icfg.fallback_models
    else:
        primary = icfg.model
        fallbacks = icfg.fallback_models
    return OpenAICompatLLM(
        api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
        model=primary,
        timeout_s=icfg.timeout_s,
        fallback_models=fallbacks,
    )


def _new_ctx(now: datetime | None = None) -> tuple[RunContext, datetime]:
    """Fresh RunContext + resolved `now` for a dry-run invocation."""
    now = now or datetime.now(timezone.utc)
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logging.getLogger("ai-newsday"))
    return ctx, now


def _make_embedder(dcfg, embedder=None):
    """Injected embedder passes through; otherwise build the ModelScope one from dedup config."""
    if embedder is not None:
        return embedder
    return ModelScopeEmbedder(
        api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
        model=dcfg.embedding_model,
        batch_size=dcfg.batch_size,
    )


def _envelope(ctx: RunContext, now: datetime, **fields) -> dict:
    """Common dry-run response header (run_id + now) merged with layer-specific fields."""
    return {"run_id": ctx.run_id, "now": now.isoformat(), **fields}


def _dump_pipeline_artifacts(rd, coll, dres, sres, ires) -> None:
    """Persist the shared collect→interpret artifacts (01-04) into the run dir."""
    dump_jsonl(coll.items, rd / "01_collected.jsonl")
    dump_jsonl(coll.source_reports, rd / "01_source_reports.jsonl")
    dump_jsonl(dres.deduped_items, rd / "02_deduped.jsonl")
    dump_jsonl(sres.selected_items, rd / "03_scored.jsonl")
    dump_jsonl(ires.interpreted_items, rd / "04_interpreted.jsonl")


_STAGES = ["collect", "dedup", "score", "interpret"]


def _dry_run_prefix(
    registry_path: str,
    ctx: RunContext,
    embedder=None,
    llm=None,
    *,
    enrich: bool = False,
    stop_at: str = "interpret",
):
    """Shared dry-run pipeline prefix: collect -> [enrich] -> dedup -> score -> interpret.
    Runs only up to `stop_at` (so early layers don't pay for dedup embeddings / interpret LLM).
    Returns (coll, dres, sres, ires, llm); stages past `stop_at` are None."""
    want = _STAGES.index(stop_at)
    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    if enrich:
        ecfg = load_enrich_config("config/enrich.yaml")

        async def _collect_then_enrich():
            c = await collect(coll_cfg, ctx)
            if ecfg.enabled and c.items:
                await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
            return c

        coll = asyncio.run(_collect_then_enrich())
    else:
        coll = asyncio.run(collect(coll_cfg, ctx))

    dres = sres = ires = None
    if want >= _STAGES.index("dedup"):
        dcfg = load_dedup_config("config/dedup.yaml")
        dcfg.sources_registry_path = registry_path
        embedder = _make_embedder(dcfg, embedder)
        dres = dedup(coll.items, dcfg, ctx, embedder=embedder, store=InMemoryVectorStore())

    if want >= _STAGES.index("score"):
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        sres = score(dres.deduped_items, scfg, ctx)

    if want >= _STAGES.index("interpret"):
        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        ires = interpret(sres.selected_items, icfg, ctx, llm)

    return coll, dres, sres, ires, llm


def run_dry(registry_path: str, now: datetime | None = None) -> dict:
    ctx, now = _new_ctx(now)
    coll, _, _, _, _ = _dry_run_prefix(registry_path, ctx, stop_at="collect")
    return _envelope(
        ctx,
        now,
        is_silent=coll.is_silent,
        total_items=len(coll.items),
        items=[it.model_dump(mode="json") for it in coll.items],
        source_reports=[r.model_dump() for r in coll.source_reports],
    )


def run_dry_dedup(registry_path: str, now: datetime | None = None, embedder=None) -> dict:
    ctx, now = _new_ctx(now)
    _, res, _, _, _ = _dry_run_prefix(registry_path, ctx, embedder, stop_at="dedup")
    return _envelope(
        ctx,
        now,
        input_count=res.input_count,
        cluster_count=res.cluster_count,
        duplicate_count=res.duplicate_count,
        deduped_items=[ni.model_dump(mode="json") for ni in res.deduped_items],
    )


def run_dry_score(registry_path: str, now: datetime | None = None, embedder=None) -> dict:
    ctx, now = _new_ctx(now)
    _, _, sres, _, _ = _dry_run_prefix(registry_path, ctx, embedder, stop_at="score")
    return _envelope(
        ctx,
        now,
        input_count=sres.input_count,
        selected_count=sres.selected_count,
        is_silent=sres.is_silent,
        quota_report={k: asdict(v) for k, v in sres.quota_report.items()},
        selected_items=[si.model_dump(mode="json") for si in sres.selected_items],
    )


def run_dry_interpret(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None
) -> dict:
    ctx, now = _new_ctx(now)
    _, _, _, ires, _ = _dry_run_prefix(registry_path, ctx, embedder, llm)
    return _envelope(
        ctx,
        now,
        input_count=ires.input_count,
        interpreted_count=ires.interpreted_count,
        fallback_count=ires.fallback_count,
        is_silent=ires.is_silent,
        daily_take=ires.daily_take,
        interpreted_items=[it.model_dump(mode="json") for it in ires.interpreted_items],
    )


def run_dry_selfcheck(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None
) -> dict:
    ctx, now = _new_ctx(now)
    _, _, _, ires, _ = _dry_run_prefix(registry_path, ctx, embedder, llm)

    sccfg = load_selfcheck_config("config/selfcheck.yaml")
    # critic runs on its own (cheaper) model per config; not the interpret LLM
    critic_llm = OpenAICompatLLM(
        api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
        model=sccfg.model,
        timeout_s=sccfg.timeout_s,
        fallback_models=sccfg.fallback_models,
    )
    sc = self_check(ires, sccfg, ctx, critic_llm)
    return _envelope(
        ctx,
        now,
        checked_count=sc.checked_count,
        flagged_count=sc.flagged_count,
        flag_count_by_code=sc.flag_count_by_code,
        llm_error_count=sc.llm_error_count,
        is_silent=sc.is_silent,
        daily_take=sc.daily_take,
        interpreted_items=[it.model_dump(mode="json") for it in sc.interpreted_items],
    )


def run_dry_review(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None, decisions_path=None
) -> dict:
    ctx, now = _new_ctx(now)
    _, _, _, ires, _ = _dry_run_prefix(registry_path, ctx, embedder, llm)

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)
    rres = review(ires.interpreted_items, ires.daily_take, decisions, rcfg, ctx)
    return _envelope(
        ctx,
        now,
        input_count=rres.input_count,
        kept_count=rres.kept_count,
        dropped_count=rres.dropped_count,
        edited_count=rres.edited_count,
        is_reviewed=rres.is_reviewed,
        is_pending=rres.is_pending,
        is_silent=rres.is_silent,
        daily_take=rres.daily_take,
        reviewed_items=[it.model_dump(mode="json") for it in rres.reviewed_items],
    )


def run_dry_publish(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None, decisions_path=None
) -> dict:
    ctx, now = _new_ctx(now)
    coll, dres, sres, ires, _ = _dry_run_prefix(registry_path, ctx, embedder, llm, enrich=True)

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)
    rres = review(ires.interpreted_items, ires.daily_take, decisions, rcfg, ctx)

    pcfg = load_publish_config("config/publish.yaml")
    date_label = now.date().isoformat()
    pres = publish(rres, date_label, pcfg, ctx)

    # 落盘各层产物 (signals 都在 RawItem.signals 中带着, 跨层透传)
    rd = run_dir(ctx.run_id)
    _dump_pipeline_artifacts(rd, coll, dres, sres, ires)
    dump_jsonl(rres.reviewed_items, rd / "05_reviewed.jsonl")
    dump_json(pres.report, rd / "06_report.json")
    (rd / "06_report.md").write_text(pres.markdown, encoding="utf-8")
    dump_json(
        {
            "run_id": ctx.run_id,
            "now": now.isoformat(),
            "collected": len(coll.items),
            "deduped": len(dres.deduped_items),
            "selected": len(sres.selected_items),
            "interpreted_ok": ires.interpreted_count,
            "fallback": ires.fallback_count,
            "daily_take": ires.daily_take,
            "must_read_count": len(pres.report.must_read),
            "item_count": pres.report.item_count,
            "is_pending": pres.is_pending,
            "is_silent": pres.is_silent,
        },
        rd / "run.json",
    )
    ctx.logger.info('{"event": "run_persisted", "dir": "%s"}', str(rd))

    return _envelope(
        ctx,
        now,
        input_count=pres.report.item_count,
        item_count=pres.report.item_count,
        must_read_count=len(pres.report.must_read),
        is_pending=pres.is_pending,
        is_silent=pres.is_silent,
        markdown=pres.markdown,
        run_dir=str(rd),
    )


def run_dry_feedback(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None, decisions_path=None
) -> dict:
    ctx, now = _new_ctx(now)
    coll, dres, sres, ires, _ = _dry_run_prefix(registry_path, ctx, embedder, llm, enrich=True)

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)

    fcfg = load_feedback_config("config/feedback.yaml")
    # 本轮事件从"进审阅前"全量条目派生(被删条目也回收), 并入历史账本
    run_events = derive_events(ires.interpreted_items, decisions, run_id=ctx.run_id, now=now)
    history = load_feedback_events(fcfg.events_path)
    prior = load_quality_weights(fcfg.weights_path)
    fres = feedback(history + run_events, prior, fcfg, ctx)

    # 全链产物落盘 (P1 接 SQLite 前的最简方案)
    rd = run_dir(ctx.run_id)
    _dump_pipeline_artifacts(rd, coll, dres, sres, ires)
    dump_jsonl(run_events, rd / "07_feedback_events.jsonl")
    dump_json(
        {
            "run_id": ctx.run_id,
            "now": now.isoformat(),
            "collected": len(coll.items),
            "deduped": len(dres.deduped_items),
            "selected": len(sres.selected_items),
            "interpreted_ok": ires.interpreted_count,
            "fallback": ires.fallback_count,
            "daily_take": ires.daily_take,
            "feedback_event_count": fres.event_count,
            "source_count": fres.source_count,
            "is_silent": fres.is_silent,
            "quality_weights": fres.quality_weights,
            "weight_diff": {k: list(v) for k, v in fres.weight_diff.items()},
        },
        rd / "run.json",
    )
    ctx.logger.info('{"event": "run_persisted", "dir": "%s"}', str(rd))

    return _envelope(
        ctx,
        now,
        event_count=fres.event_count,
        source_count=fres.source_count,
        is_silent=fres.is_silent,
        quality_weights=fres.quality_weights,
        weight_diff={k: list(v) for k, v in fres.weight_diff.items()},
        run_dir=str(rd),
    )


def run_tick(
    tick: str,
    registry_path: str,
    now: datetime | None = None,
    db_path: str = "data/state.db",
    embedder=None,
    llm=None,
) -> dict:
    """统一 tick 入口: collect 或 finalize。
    Notifier 根据 TELEGRAM_BOT_TOKEN 环境变量决定用真 bot 还是 FakeNotifier。"""
    ctx, now = _new_ctx(now)

    # 初始化 DB
    db = Database(db_path)
    asyncio.run(db.init())

    # 初始化 Notifier
    dcfg = load_delivery_config("config/delivery.yaml")
    decision_store = None
    if dcfg.decisions_api.url and dcfg.decisions_api.secret:
        decision_store = WorkerDecisionStore(dcfg.decisions_api.url, dcfg.decisions_api.secret)
    notifiers = []
    if dcfg.telegram.bot_token:
        tg = TelegramPollingNotifier(dcfg.telegram, db=db)
        notifiers.append(tg)
    else:
        notifiers.append(FakeNotifier())  # 无 token = dry 模式
    if dcfg.website.enabled:
        notifiers.append(WebsiteNotifier(dcfg.website))

    # 完整 pipeline (collect → interpret)
    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    ecfg = load_enrich_config("config/enrich.yaml")

    async def _collect_and_interpret():
        c = await collect(coll_cfg, ctx)
        if ecfg.enabled and c.items:
            await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
        dcfg2 = load_dedup_config("config/dedup.yaml")
        dcfg2.sources_registry_path = registry_path
        _embedder = _make_embedder(dcfg2, embedder)
        dres = dedup(c.items, dcfg2, ctx, embedder=_embedder, store=InMemoryVectorStore())
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        quality_of = await db.get_quality_weights()
        sres = score(dres.deduped_items, scfg, ctx, quality_of=quality_of)
        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(sres.selected_items, icfg, ctx, _llm)
        return ires

    ires = asyncio.run(_collect_and_interpret())
    date_label = now.date().isoformat()

    if tick == "collect":
        asyncio.run(
            run_collect_tick(
                run_id=ctx.run_id,
                now=now,
                interpreted_items=ires.interpreted_items,
                daily_take=ires.daily_take,
                db=db,
                notifiers=notifiers,
            )
        )
        return {
            "run_id": ctx.run_id,
            "tick": "collect",
            "pushed": len(ires.interpreted_items),
            "date": date_label,
        }

    elif tick == "finalize":
        result = asyncio.run(
            run_finalize_tick(
                run_id=ctx.run_id,
                now=now,
                date_label=date_label,
                interpreted_items=ires.interpreted_items,
                daily_take=ires.daily_take,
                db=db,
                notifiers=notifiers,
                decision_store=decision_store,
                site_base_url=dcfg.website.site_base_url,
            )
        )
        result["tick"] = "finalize"
        return result
    else:
        raise ValueError(f"Unknown tick: {tick!r}. Use 'collect' or 'finalize'.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-newsday-collect")
    p.add_argument("--registry", default="config/sources.yaml")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="collect + print result JSON; no side effects (only mode this circle)",
    )
    p.add_argument(
        "--dedup", action="store_true", help="chain collect -> dedup, print DedupResult JSON"
    )
    p.add_argument(
        "--score",
        action="store_true",
        help="chain collect -> dedup -> score, print ScoreResult JSON",
    )
    p.add_argument(
        "--interpret",
        action="store_true",
        help="chain collect -> dedup -> score -> interpret, print InterpretResult JSON",
    )
    p.add_argument(
        "--selfcheck",
        action="store_true",
        help="chain collect -> ... -> interpret -> self_check, print SelfCheckResult JSON",
    )
    p.add_argument(
        "--review",
        action="store_true",
        help="chain collect -> ... -> review, print ReviewResult JSON",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="chain collect -> ... -> publish, print daily-report Markdown",
    )
    p.add_argument(
        "--feedback",
        action="store_true",
        help="chain collect -> ... -> review -> feedback, print quality_weights JSON",
    )
    p.add_argument(
        "--tick",
        choices=["collect", "finalize"],
        help="run collect or finalize tick (HITL pipeline)",
    )
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.tick:
        out = run_tick(tick=args.tick, registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not args.dry_run:
        print("This circle supports --dry-run only (publishing is a later layer).", file=sys.stderr)
        return 2
    if args.dry_run and args.feedback:
        out = run_dry_feedback(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run and args.publish:
        out = run_dry_publish(registry_path=args.registry)
        print(out["markdown"])
        return 0
    if args.dry_run and args.review:
        out = run_dry_review(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run and args.selfcheck:
        out = run_dry_selfcheck(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run and args.interpret:
        out = run_dry_interpret(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run and args.score:
        out = run_dry_score(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run and args.dedup:
        out = run_dry_dedup(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    out = run_dry(registry_path=args.registry)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
