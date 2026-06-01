from __future__ import annotations
import yaml
from src.core.types import (DedupConfig, ScoringConfig, InterpretConfig,
                            ReviewConfig, ReviewDecision, PublishConfig)


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


def load_interpret_config(path: str) -> InterpretConfig:
    """Load interpret model params/field limits from YAML; missing file -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return InterpretConfig()
    d = InterpretConfig()
    return InterpretConfig(
        model=data.get("model", d.model),
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
        item_prompt_path=data.get("item_prompt_path", d.item_prompt_path),
        daily_prompt_path=data.get("daily_prompt_path", d.daily_prompt_path),
    )


def load_review_config(path: str) -> ReviewConfig:
    """Load review field limits / decisions path from YAML; missing -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return ReviewConfig()
    d = ReviewConfig()
    return ReviewConfig(
        decisions_path=data.get("decisions_path", d.decisions_path),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
    )


def load_review_decisions(path: str) -> dict[str, ReviewDecision]:
    """Read审阅决策 JSON(按 link 索引); 缺文件 -> {}(全 keep/待审).
    每个 value 过 ReviewDecision 校验(非法 action 即抛 ValidationError)。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}
    except FileNotFoundError:
        return {}
    return {k: ReviewDecision(**v) for k, v in raw.items()}


def load_publish_config(path: str) -> PublishConfig:
    """Load publish display constants from YAML; missing/empty file -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return PublishConfig()
    d = PublishConfig()
    return PublishConfig(
        must_read_count=data.get("must_read_count", d.must_read_count),
        top_keywords=data.get("top_keywords", d.top_keywords),
        pending_watermark=data.get("pending_watermark", d.pending_watermark),
        type_labels=data.get("type_labels", d.type_labels),
    )
