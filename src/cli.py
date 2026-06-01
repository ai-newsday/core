from __future__ import annotations
import argparse, asyncio, json, logging, os, sys, uuid
from datetime import datetime, timezone
from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect
from src.core.config import load_dedup_config
from src.pipeline.dedup import dedup
from src.adapters.embedding.modelscope import ModelScopeEmbedder
from src.adapters.vectorstore.memory import InMemoryVectorStore
from dataclasses import asdict
from src.core.config import load_scoring_config
from src.pipeline.score import score
from src.core.config import load_interpret_config
from src.pipeline.interpret import interpret
from src.adapters.llm.openai_compat import OpenAICompatLLM
from src.core.config import load_review_config, load_review_decisions
from src.pipeline.review import review
from src.core.config import load_publish_config
from src.pipeline.publish import publish


def run_dry(registry_path: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    cfg = CollectionConfig(sources_registry_path=registry_path)
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)
    res = asyncio.run(collect(cfg, ctx))
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "is_silent": res.is_silent,
        "total_items": len(res.items),
        "items": [it.model_dump(mode="json") for it in res.items],
        "source_reports": [r.model_dump() for r in res.source_reports],
    }


def run_dry_dedup(registry_path: str, now: datetime | None = None,
                  embedder=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    res = dedup(coll.items, dcfg, ctx,
                embedder=embedder, store=InMemoryVectorStore())
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": res.input_count,
        "cluster_count": res.cluster_count,
        "duplicate_count": res.duplicate_count,
        "deduped_items": [ni.model_dump(mode="json") for ni in res.deduped_items],
    }


def run_dry_score(registry_path: str, now: datetime | None = None,
                  embedder=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": sres.input_count,
        "selected_count": sres.selected_count,
        "is_silent": sres.is_silent,
        "quota_report": {k: asdict(v) for k, v in sres.quota_report.items()},
        "selected_items": [si.model_dump(mode="json") for si in sres.selected_items],
    }


def run_dry_interpret(registry_path: str, now: datetime | None = None,
                      embedder=None, llm=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)

    icfg = load_interpret_config("config/interpret.yaml")
    if llm is None:
        llm = OpenAICompatLLM(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""), model=icfg.model,
            timeout_s=icfg.timeout_s)
    ires = interpret(sres.selected_items, icfg, ctx, llm)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": ires.input_count,
        "interpreted_count": ires.interpreted_count,
        "fallback_count": ires.fallback_count,
        "is_silent": ires.is_silent,
        "daily_take": ires.daily_take,
        "interpreted_items": [it.model_dump(mode="json")
                              for it in ires.interpreted_items],
    }


def run_dry_review(registry_path: str, now: datetime | None = None,
                   embedder=None, llm=None, decisions_path=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)

    icfg = load_interpret_config("config/interpret.yaml")
    if llm is None:
        llm = OpenAICompatLLM(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""), model=icfg.model,
            timeout_s=icfg.timeout_s)
    ires = interpret(sres.selected_items, icfg, ctx, llm)

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)
    rres = review(ires.interpreted_items, ires.daily_take, decisions, rcfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": rres.input_count,
        "kept_count": rres.kept_count,
        "dropped_count": rres.dropped_count,
        "edited_count": rres.edited_count,
        "is_reviewed": rres.is_reviewed,
        "is_pending": rres.is_pending,
        "is_silent": rres.is_silent,
        "daily_take": rres.daily_take,
        "reviewed_items": [it.model_dump(mode="json")
                           for it in rres.reviewed_items],
    }


def run_dry_publish(registry_path: str, now: datetime | None = None,
                    embedder=None, llm=None, decisions_path=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)

    icfg = load_interpret_config("config/interpret.yaml")
    if llm is None:
        llm = OpenAICompatLLM(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""), model=icfg.model,
            timeout_s=icfg.timeout_s)
    ires = interpret(sres.selected_items, icfg, ctx, llm)

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)
    rres = review(ires.interpreted_items, ires.daily_take, decisions, rcfg, ctx)

    pcfg = load_publish_config("config/publish.yaml")
    date_label = now.date().isoformat()
    pres = publish(rres, date_label, pcfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": pres.report.item_count,
        "item_count": pres.report.item_count,
        "must_read_count": len(pres.report.must_read),
        "is_pending": pres.is_pending,
        "is_silent": pres.is_silent,
        "markdown": pres.markdown,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-newsday-collect")
    p.add_argument("--registry", default="config/sources.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="collect + print result JSON; no side effects (only mode this circle)")
    p.add_argument("--dedup", action="store_true",
                   help="chain collect -> dedup, print DedupResult JSON")
    p.add_argument("--score", action="store_true",
                   help="chain collect -> dedup -> score, print ScoreResult JSON")
    p.add_argument("--interpret", action="store_true",
                   help="chain collect -> dedup -> score -> interpret, print InterpretResult JSON")
    p.add_argument("--review", action="store_true",
                   help="chain collect -> ... -> review, print ReviewResult JSON")
    p.add_argument("--publish", action="store_true",
                   help="chain collect -> ... -> publish, print daily-report Markdown")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if not args.dry_run:
        print("This circle supports --dry-run only (publishing is a later layer).",
              file=sys.stderr)
        return 2
    if args.dry_run and args.publish:
        out = run_dry_publish(registry_path=args.registry)
        print(out["markdown"])
        return 0
    if args.dry_run and args.review:
        out = run_dry_review(registry_path=args.registry)
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
