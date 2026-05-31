from __future__ import annotations
import yaml
from src.core.types import DedupConfig


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
