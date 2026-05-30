from __future__ import annotations
import argparse, asyncio, json, logging, sys, uuid
from datetime import datetime, timezone
from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-newsday-collect")
    p.add_argument("--registry", default="config/sources.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="collect + print result JSON; no side effects (only mode this circle)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if not args.dry_run:
        print("This circle supports --dry-run only (publishing is a later layer).",
              file=sys.stderr)
        return 2
    out = run_dry(registry_path=args.registry)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
