from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Genre(str, Enum):
    paper = "paper"
    model = "model"
    announcement = "announcement"
    writeup = "writeup"
    news = "news"


class Publisher(str, Enum):
    lab = "lab"
    company = "company"
    individual = "individual"
    media = "media"


class RawItem(BaseModel):
    title_en: str = Field(min_length=1)
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    genre: Genre
    publisher: Publisher
    published_at: datetime  # MUST be tz-aware
    raw_summary: str | None = None
    image_url: str | None = None
    fetched_via: Literal["native", "firecrawl"] = "native"
    # 源端原生量化信号 (popularity / quality), 后续层可读不可改。
    # 约定键: upvotes / num_comments / github_stars / likes / downloads / ai_keywords
    signals: dict[str, Any] = Field(default_factory=dict)

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
    genre: Genre
    publisher: Publisher
    adapter: Literal["rss", "hf_papers", "hf_models", "hn", "reddit"]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False
    max_items: int | None = None  # truncate fetched items to this cap (e.g. arXiv firehose)
    min_score: int | None = None  # HN points / Reddit ups 下限; None = 不过滤
    keywords: list[str] | None = None  # HN AI 关键词(标题/URL 命中); Reddit 不填


@dataclass
class CollectionConfig:
    sources_registry_path: str
    window_hours: int = 72  # 拉宽到 3 天: paper/tool/blog 周更慢更不漏 (原 24 把它们都砍了)
    max_window_hours: int = 96  # 同步上调; spec §7.1 不变量仍按此参数
    concurrency: int = 10
    timeout_s: int = 15
    firecrawl_enabled: bool = False


@dataclass
class RunContext:
    run_id: str
    now: datetime  # injected for determinism; MUST be tz-aware
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
    genre_rank: list[str] = field(
        default_factory=lambda: ["paper", "model", "announcement", "writeup", "news"]
    )
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
    genre_value: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "paper": {"一手性": 20, "技术价值": 16, "产业影响": 8, "扩散潜力": 7},
            "model": {"一手性": 18, "技术价值": 14, "产业影响": 10, "扩散潜力": 9},
            "announcement": {"一手性": 20, "技术价值": 10, "产业影响": 12, "扩散潜力": 9},
            "writeup": {"一手性": 12, "技术价值": 12, "产业影响": 8, "扩散潜力": 9},
            "news": {"一手性": 8, "技术价值": 6, "产业影响": 12, "扩散潜力": 11},
        }
    )
    publisher_authority: dict[str, float] = field(
        default_factory=lambda: {"lab": 18, "company": 14, "individual": 8, "media": 12}
    )
    priority_bonus: dict[int, int] = field(default_factory=lambda: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4})
    priority_bonus_default: int = 0
    fresh_hours: int = 24
    fresh_bonus: float = 10
    mid_hours: int = 48
    mid_bonus: float = 4
    stale_hours: int = 72
    stale_penalty: float = -10
    same_source_penalty: float = -5
    # 可见指标 = sum(weight * sqrt(signals[key]))  → 接 popularity 信号到 "可见指标" 维度。
    # 缺省空 = 0 (向后兼容)。production yaml 里配上 weights 才激活。
    popularity_weights: dict[str, float] = field(default_factory=dict)
    popularity_cap: float = 15.0  # 单条最高加 15 分, 防异常超大数值
    quota: dict[str, int] = field(
        default_factory=lambda: {
            "paper": 2,
            "announcement": 2,
            "writeup": 2,
            "model": 1,
            "news": 1,
        }
    )
    total_limit: int = 8
    sources_registry_path: str = "config/sources.yaml"
    topic_keywords: list[str] = field(default_factory=list)
    topic_bonus: float = 5.0


@dataclass
class QuotaLine:
    genre: str
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
    anchor: str = Field(min_length=1)  # must be ∈ item.link ∪ related_links


class QualityFlag(BaseModel):
    code: str  # "consistency" | "ai_slop" | "format_lock"
    severity: str  # "warn" | "info"  (advisor 版无 "block")
    field: str  # 命中字段: takeaway|summary|hot_take|tags|evidence|*
    message: str = Field(min_length=1)  # 给人看的一句话(中文)


class InterpretedItem(ScoredItem):  # ScoredItem 的下游演进; 本圈加解读字段
    title: str  # 中文钩子标题, ≤ title_max_chars; 术语保留英文原文
    body: str  # 一段顺读正文(事实→实用→可选判断); 回退时为抽取式原文
    tags: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    interpretation_status: str
    eligible_for_must_read: bool
    quality_flags: list[QualityFlag] = Field(default_factory=list)


@dataclass
class InterpretConfig:
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_s: int = 60
    title_max_chars: int = 64
    body_max_chars: int = 240
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


@dataclass
class SelfCheckConfig:
    model: str = "deepseek-ai/DeepSeek-V4-Flash"
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 600
    timeout_s: int = 60
    title_max_chars: int = 64
    body_max_chars: int = 240
    tags_count: int = 3
    min_evidence: int = 1
    message_max_chars: int = 120
    max_flags_per_item: int = 3
    prompt_path: str = "src/prompts/selfcheck.md"


@dataclass
class SelfCheckResult:
    interpreted_items: list[InterpretedItem]
    daily_take: str | None
    checked_count: int
    flagged_count: int
    flag_count_by_code: dict[str, int]
    llm_error_count: int
    is_silent: bool


# --- review layer (Circle 5) ---
class ReviewDecision(BaseModel):
    action: Literal["keep", "drop", "edit"] = "keep"
    order: int | None = None  # 重排序号(升序); None=不指定
    edits: dict = Field(default_factory=dict)  # action==edit 时覆盖的字段


class ReviewedItem(InterpretedItem):  # InterpretedItem 的下游演进
    review_action: Literal["keep", "edit"]  # drop 的条目不进结果
    was_edited: bool
    edited_fields: list[str] = Field(default_factory=list)


@dataclass
class ReviewConfig:
    decisions_path: str = "data/review_decisions.json"
    title_max_chars: int = 64
    body_max_chars: int = 240
    tags_count: int = 3
    min_evidence: int = 1


@dataclass
class ReviewResult:
    reviewed_items: list[ReviewedItem]
    daily_take: str | None
    input_count: int
    kept_count: int
    dropped_count: int
    edited_count: int
    is_reviewed: bool
    is_pending: bool
    is_silent: bool


# --- publish layer (Circle 6) ---
class Overview(BaseModel):
    genre_distribution: dict[str, int] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)


class CategorySection(BaseModel):
    genre: str
    label: str
    items: list[ReviewedItem] = Field(default_factory=list)


class DailyReport(BaseModel):
    date_label: str
    daily_take: str | None
    must_read: list[ReviewedItem] = Field(default_factory=list)
    categories: list[CategorySection] = Field(default_factory=list)
    overview: Overview
    is_pending: bool
    item_count: int
    explore_count: int


@dataclass
class PublishConfig:
    must_read_count: int = 3
    top_keywords: int = 4
    pending_watermark: str = "⚠ 未审草稿（待人工定稿，勿直接发布）"
    genre_labels: dict[str, str] = field(
        default_factory=lambda: {
            "paper": "论文",
            "model": "模型",
            "announcement": "官方",
            "writeup": "博客 / 工具",
            "news": "新闻",
        }
    )


@dataclass
class PublishResult:
    report: DailyReport
    markdown: str
    is_pending: bool
    is_silent: bool


# --- feedback layer (Circle 7) ---
class FeedbackEvent(BaseModel):
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    action: Literal["keep", "drop", "edit"]
    run_id: str = Field(min_length=1)
    ts: datetime  # injected; layer never calls now()


class SourceFeedbackStats(BaseModel):
    source: str
    keep: int = 0
    edit: int = 0
    drop: int = 0
    total: int = 0


@dataclass
class EnrichConfig:
    """RSS 类源天然无 popularity, 用 HN Algolia by URL 反查补 signals.hn_*。"""

    enabled: bool = True
    concurrency: int = 5
    timeout_s: int = 8
    # 已经带原生 popularity 信号的 genre 不查 HN (省请求, 不覆盖)
    skip_genres: list[str] = field(default_factory=lambda: ["paper", "model"])


@dataclass
class FeedbackConfig:
    events_path: str = "data/feedback_events.json"
    weights_path: str = "data/quality_weights.json"
    baseline_weight: float = 1.0
    min_weight: float = 0.5
    max_weight: float = 1.5
    step: float = 0.2
    edit_factor: float = 0.5
    min_events: int = 1


@dataclass
class FeedbackResult:
    source_stats: list[SourceFeedbackStats]
    quality_weights: dict[str, float]
    weight_diff: dict[str, tuple[float, float]]
    event_count: int
    source_count: int
    is_silent: bool


# --- delivery layer (P1) ---
@dataclass
class TelegramConfig:
    bot_token: str = ""  # 优先从 TELEGRAM_BOT_TOKEN 环境变量读
    chat_id: str = ""  # 优先从 TELEGRAM_CHAT_ID 环境变量读
    mode: str = "polling"  # "polling" | "webhook"
    webhook_url: str = ""  # mode=webhook 时填


@dataclass
class WebsiteConfig:
    enabled: bool = True
    output_dir: str = "content/posts"
    git_push: bool = False  # True = finalize 后自动 git add + commit
    site_base_url: str = "https://ai-newsday.github.io/core/"


@dataclass
class DecisionsApiConfig:
    url: str = ""
    secret: str = ""  # 优先从 DECISIONS_API_SECRET 环境变量读


@dataclass
class DeliveryConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    website: WebsiteConfig = field(default_factory=WebsiteConfig)
    decisions_api: DecisionsApiConfig = field(default_factory=DecisionsApiConfig)
