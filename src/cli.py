from __future__ import annotations
import argparse, asyncio, json, logging, os, sys, uuid
from datetime import datetime, timezone
from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect
from src.core.config import load_dedup_config
from src.pipeline.dedup import dedup
from src.adapters.embedding.modelscope import ModelScopeEmbedder
from src.adapters.vectorstore.memory import InMemoryVectorStore


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-newsday-collect")
    p.add_argument("--registry", default="config/sources.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="collect + print result JSON; no side effects (only mode this circle)")
    p.add_argument("--dedup", action="store_true",
                   help="chain collect -> dedup, print DedupResult JSON")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if not args.dry_run:
        print("This circle supports --dry-run only (publishing is a later layer).",
              file=sys.stderr)
        return 2
    if args.dry_run and args.dedup:
        out = run_dry_dedup(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    out = run_dry(registry_path=args.registry)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
