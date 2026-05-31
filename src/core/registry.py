from __future__ import annotations
import yaml
from src.core.types import RunContext, SourceSpec, SourceType
from src.observability.events import emit

FALLBACK_SOURCES: list[SourceSpec] = [
    SourceSpec(name="hf-papers", url="https://huggingface.co/api/papers",
               type=SourceType.PAPER, adapter="hf_papers", status="working", priority=1),
    SourceSpec(name="openai", url="https://openai.com/news/rss.xml",
               type=SourceType.OFFICIAL, adapter="rss", status="working", priority=2),
    SourceSpec(name="deepmind", url="https://deepmind.google/blog/rss.xml",
               type=SourceType.OFFICIAL, adapter="rss", status="working", priority=2),
]


def load_registry(path: str, ctx: RunContext) -> list[SourceSpec]:
    """Load enabled (status=working) sources. On any load/parse error, fall back."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or []
        specs = [SourceSpec(**entry) for entry in raw]
    except Exception as e:  # noqa: BLE001 - load must never be fatal
        emit(ctx.logger, "registry_load_failed", path=path, error=str(e))
        return FALLBACK_SOURCES
    return [s for s in specs if s.status == "working"]
