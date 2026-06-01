from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal
import logging
from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    PAPER = "paper"
    MODEL = "model"
    TOOL = "tool"
    COMMUNITY = "community"
    OFFICIAL = "official"
    NEWS = "news"
    BLOG = "blog"


class RawItem(BaseModel):
    title_en: str = Field(min_length=1)
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_type: SourceType
    published_at: datetime              # MUST be tz-aware
    raw_summary: str | None = None
    image_url: str | None = None
    fetched_via: Literal["native", "firecrawl"] = "native"

    @field_validator("published_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return v


class SourceReport(BaseModel):
    name: str
    status: Literal["working", "failed", "empty"]
    item_count: int
    error: str | None = None
    elapsed_ms: int


class SourceSpec(BaseModel):
    name: str
    url: str
    type: SourceType
    adapter: Literal["rss", "hf_papers", "hf_models"]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False


@dataclass
class CollectionConfig:
    sources_registry_path: str
    window_hours: int = 24
    max_window_hours: int = 36
    concurrency: int = 10
    timeout_s: int = 15
    firecrawl_enabled: bool = False


@dataclass
class RunContext:
    run_id: str
    now: datetime                       # injected for determinism; MUST be tz-aware
    logger: logging.Logger


@dataclass
class CollectionResult:
    items: list[RawItem]
    source_reports: list[SourceReport]
    is_silent: bool


# --- dedup layer (Circle 2) ---
class NewsItem(RawItem):
    cluster_id: str = Field(min_length=1)
    related_links: list[str] = Field(default_factory=list)
    embedding_id: str | None = None


@dataclass
class DedupConfig:
    similarity_threshold: float = 0.83
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    batch_size: int = 32
    source_type_rank: list[str] = field(default_factory=lambda: [
        "official", "paper", "model", "tool", "news", "community", "blog"])
    sources_registry_path: str = "config/sources.yaml"


@dataclass
class Cluster:
    cluster_id: str
    primary: NewsItem
    members: list[RawItem]
    related_links: list[str]
    size: int


@dataclass
class DedupResult:
    clusters: list[Cluster]
    deduped_items: list[NewsItem]
    input_count: int
    cluster_count: int
    duplicate_count: int


# --- score layer (Circle 3) ---
class ScoredItem(NewsItem):
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, float]
    is_explore: bool = False


@dataclass
class ScoringConfig:
    dimension_scores: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "official":  {"机构影响力": 18, "一手性": 20, "技术价值": 10, "产业影响": 12, "扩散潜力": 9},
        "paper":     {"机构影响力": 14, "一手性": 20, "技术价值": 16, "产业影响": 8,  "扩散潜力": 7},
        "model":     {"机构影响力": 15, "一手性": 18, "技术价值": 14, "产业影响": 10, "扩散潜力": 9},
        "tool":      {"机构影响力": 10, "一手性": 14, "技术价值": 12, "产业影响": 10, "扩散潜力": 10},
        "news":      {"机构影响力": 12, "一手性": 8,  "技术价值": 6,  "产业影响": 12, "扩散潜力": 11},
        "community": {"机构影响力": 6,  "一手性": 10, "技术价值": 8,  "产业影响": 6,  "扩散潜力": 12},
        "blog":      {"机构影响力": 6,  "一手性": 8,  "技术价值": 8,  "产业影响": 6,  "扩散潜力": 8},
    })
    priority_bonus: dict[int, int] = field(default_factory=lambda: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4})
    priority_bonus_default: int = 0
    fresh_hours: int = 24
    fresh_bonus: float = 10
    mid_hours: int = 48
    mid_bonus: float = 4
    stale_hours: int = 72
    stale_penalty: float = -10
    same_source_penalty: float = -5
    quota: dict[str, int] = field(default_factory=lambda: {
        "paper": 2, "model": 1, "tool": 2, "official": 1, "community": 1, "news": 1, "blog": 0})
    total_limit: int = 8
    sources_registry_path: str = "config/sources.yaml"


@dataclass
class QuotaLine:
    source_type: str
    available: int
    quota: int
    selected: int


@dataclass
class ScoreResult:
    selected_items: list[ScoredItem]
    all_scored: list[ScoredItem]
    quota_report: dict[str, QuotaLine]
    input_count: int
    selected_count: int
    is_silent: bool


# --- interpret layer (Circle 4) ---
class Evidence(BaseModel):
    claim: str = Field(min_length=1)
    anchor: str = Field(min_length=1)        # must be ∈ item.link ∪ related_links


class InterpretedItem(ScoredItem):           # ScoredItem 的下游演进; 本圈加解读字段
    title: str                               # 中文标题, ≤ title_max_chars
    summary: str                             # 中文摘要, ≤ summary_max_chars
    takeaway: str                            # 对你意味着什么/怎么用; 回退时 ""
    hot_take: str = ""                       # 锐评 AI 草稿(待人工定稿)
    tags: list[str] = Field(default_factory=list)        # 恰好 tags_count 个或回退时 []
    evidence: list[Evidence] = Field(default_factory=list)
    interpretation_status: str               # "ok" | "extractive_fallback"
    eligible_for_must_read: bool


@dataclass
class InterpretConfig:
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_s: int = 60
    title_max_chars: int = 64
    summary_max_chars: int = 120
    tags_count: int = 3
    min_evidence: int = 1
    item_prompt_path: str = "src/prompts/interpret_item.md"
    daily_prompt_path: str = "src/prompts/daily_take.md"


@dataclass
class InterpretResult:
    interpreted_items: list[InterpretedItem]
    daily_take: str | None
    input_count: int
    interpreted_count: int
    fallback_count: int
    is_silent: bool
