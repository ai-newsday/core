from __future__ import annotations

from pathlib import Path

import yaml

from src.core.types import Genre, Publisher, RunContext, SourceSpec
from src.observability.events import emit


def _read_raw_with_dir_overlay(path: str) -> list[dict]:
    """主 yaml + 同级 `<stem>.d/*.yaml`(若存在)合并成 raw entry list。
    .d/ 目录用于把 community 等大类源拆出独立文件维护,避免主 yaml 膨胀。"""
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        rows.extend(yaml.safe_load(f) or [])
    main = Path(path)
    extra_dir = main.with_name(main.stem + ".d")
    if extra_dir.is_dir():
        for sub in sorted(extra_dir.glob("*.yaml")):
            with open(sub, encoding="utf-8") as f:
                rows.extend(yaml.safe_load(f) or [])
    return rows


FALLBACK_SOURCES: list[SourceSpec] = [
    SourceSpec(
        name="hf-papers",
        url="https://huggingface.co/api/papers",
        genre=Genre.paper,
        publisher=Publisher.company,
        adapter="hf_papers",
        status="working",
        priority=1,
    ),
    SourceSpec(
        name="openai",
        url="https://openai.com/news/rss.xml",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        adapter="rss",
        status="working",
        priority=2,
    ),
    SourceSpec(
        name="deepmind",
        url="https://deepmind.google/blog/rss.xml",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        adapter="rss",
        status="working",
        priority=2,
    ),
]


def load_registry(path: str, ctx: RunContext) -> list[SourceSpec]:
    """Load enabled (status=working) sources. On any load/parse error, fall back."""
    try:
        raw = _read_raw_with_dir_overlay(path)
        specs = [SourceSpec(**entry) for entry in raw]
    except Exception as e:  # noqa: BLE001 - load must never be fatal
        emit(ctx.logger, "registry_load_failed", path=path, error=str(e))
        return FALLBACK_SOURCES
    return [s for s in specs if s.status == "working"]


def load_source_priorities(path: str) -> dict[str, int]:
    """name -> priority for ALL registry entries (any status). Missing file -> {}."""
    try:
        rows = _read_raw_with_dir_overlay(path)
    except FileNotFoundError:
        return {}
    return {r["name"]: r.get("priority", 3) for r in rows if "name" in r}
