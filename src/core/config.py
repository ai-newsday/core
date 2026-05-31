from __future__ import annotations
import yaml
from src.core.types import DedupConfig, ScoringConfig


def load_dedup_config(path: str) -> DedupConfig:
    """Load dedup thresholds from YAML; missing/empty file -> dataclass defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return DedupConfig()
    defaults = DedupConfig()
    return DedupConfig(
        similarity_threshold=data.get("similarity_threshold", defaults.similarity_threshold),
        embedding_model=data.get("embedding_model", defaults.embedding_model),
        batch_size=data.get("batch_size", defaults.batch_size),
        source_type_rank=data.get("source_type_rank", defaults.source_type_rank),
        sources_registry_path=data.get("sources_registry_path", defaults.sources_registry_path),
    )


def load_scoring_config(path: str) -> ScoringConfig:
    """Load scoring weights/quota from YAML; missing file -> dataclass defaults.
    Flattens nested `recency`/`penalty` blocks into flat dataclass fields."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return ScoringConfig()
    d = ScoringConfig()
    recency = data.get("recency", {})
    penalty = data.get("penalty", {})
    return ScoringConfig(
        dimension_scores=data.get("dimension_scores", d.dimension_scores),
        priority_bonus=data.get("priority_bonus", d.priority_bonus),
        priority_bonus_default=data.get("priority_bonus_default", d.priority_bonus_default),
        fresh_hours=recency.get("fresh_hours", d.fresh_hours),
        fresh_bonus=recency.get("fresh_bonus", d.fresh_bonus),
        mid_hours=recency.get("mid_hours", d.mid_hours),
        mid_bonus=recency.get("mid_bonus", d.mid_bonus),
        stale_hours=recency.get("stale_hours", d.stale_hours),
        stale_penalty=recency.get("stale_penalty", d.stale_penalty),
        same_source_penalty=penalty.get("same_source", d.same_source_penalty),
        quota=data.get("quota", d.quota),
        total_limit=data.get("total_limit", d.total_limit),
        sources_registry_path=data.get("sources_registry_path", d.sources_registry_path),
    )
