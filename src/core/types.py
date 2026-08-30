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
    adapter: str | None = (
        None  # 回填自 SourceSpec.adapter, 供下游按"采集渠道"分组(如 GitHub 封顶, spec §5)
    )
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
    adapter: Literal[
        "rss", "hf_papers", "hf_models", "hn", "github_releases", "github_trending", "x_list"
    ]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False
    max_items: int | None = None  # truncate fetched items to this cap (e.g. arXiv firehose)
    min_score: int | None = None  # HN points / Reddit ups 下限; None = 不过滤
    keywords: list[str] | None = None  # HN AI 关键词(标题/URL 命中); Reddit 不填
    # github_releases 专用: release tag_name 必须匹配才保留, 缺省不过滤。
    # monorepo(如 langchain-ai/langchain)每个子包独立打 tag, 主包反而最少见,
    # 用这个把噪声子包挡在外面(2026-07-25 实测)。
    tag_pattern: str | None = None


@dataclass
class CollectionConfig:
    sources_registry_path: str
    window_hours: int = 72  # 拉宽到 3 天: paper/tool/blog 周更慢更不漏 (原 24 把它们都砍了)
    # per-adapter 覆盖: github_releases 读者预期"今天/昨天", 不该套用慢更新源的 72h 窗口,
    # 否则 2-3 天前的 release 仍会被当"新"候选采到、审阅、发布 (2026-08-27 用户反馈实锤)。
    window_hours_by_adapter: dict[str, int] = field(default_factory=lambda: {"github_releases": 48})
    max_window_hours: int = (
        96  # 同步上调; spec §7.1 不变量仍按此参数(per-adapter 覆盖只会更紧, 不会突破这个上限)
    )
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
    # 故事线合并 id(同一模型/产品同一轮动态的多条独立发布); None=不属于任何故事组
    # (绝大多数条目)。由 src/pipeline/storylink.py::link_stories() 写入(score 之后,
    # interpret 之前); publish.py::merge_story_groups() 在渲染层按此分组合并。
    story_id: str | None = None


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
    firehose_penalty: float = -20.0  # 信号闸: 个人+零人气的 model/writeup 扣分(压 firehose 噪声)
    uncertain_content_penalty: float = -15.0  # body 自我保留/信息稀薄时的固定扣分(不是加权维度)
    # 可见指标 = sum(weight * sqrt(signals[key]))  → 接 popularity 信号到 "可见指标" 维度。
    # 缺省空 = 0 (向后兼容)。production yaml 里配上 weights 才激活。
    popularity_weights: dict[str, float] = field(default_factory=dict)
    popularity_cap: float = 15.0  # 单条最高加 15 分, 防异常超大数值
    # 发卡候选池: 按 score 取 top-N 进 interpret(成本上界)。per-genre 配额/总量已移到 PublishConfig。
    card_pool_limit: int = 25
    sources_registry_path: str = "config/sources.yaml"
    topic_keywords: list[str] = field(default_factory=list)
    topic_bonus: float = 5.0
    # 机构影响力按 adapter 打折(0.0-1.0, 缺省 1.0=不打折)。一条推文不等于一篇官方博客/论文
    # 的机构背书分量, 但两者共用同一套 genre_value+publisher_authority 固定分, 导致高热度
    # 推文和普通推文都被这部分固定分顶到分数上限、可见指标的区分度被吃掉(2026-07-24 实测:
    # 78 分固定 + 10 时效 + 5 关键词 = 93, 只剩 7 分给可见指标, 冷门/爆款推文只差 1 分)。
    adapter_authority_factor: dict[str, float] = field(default_factory=dict)
    # 按 adapter 豁免同源惩罚(spec §5.3 的例外): 缺省不豁免。为一天报多条不同大新闻的
    # 采集渠道(如 X list)设计——同事件重复已由 dedup 聚类挡掉, 这里的"同源"只是"同账号",
    # 惩罚"发得多"跟内容质量无关。
    same_source_penalty_exempt_adapters: list[str] = field(default_factory=list)


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
    code: str  # "consistency" | "ai_slop" | "format_lock" | "entity_uncertain"
    severity: str  # "warn" | "info"  (advisor 版无 "block")
    field: str  # 命中字段: body|title|tags|evidence|*
    message: str = Field(min_length=1)  # 给人看的一句话(中文)


class InterpretedItem(ScoredItem):  # ScoredItem 的下游演进; 本圈加解读字段
    title: str  # 中文钩子标题, ≤ title_max_chars; 术语保留英文原文
    body: str  # 一段顺读正文(事实→实用→可选判断); 回退时为抽取式原文
    relevant: bool = True  # LLM 判定: 是否 AI/ML 相关且有真实内容; False → 不进卡片/正刊
    tags: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    interpretation_status: str
    eligible_for_must_read: bool
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    fallback_reason: str | None = None  # exception type name when extractive_fallback ran


@dataclass
class ProviderSpec:
    base_url: str
    api_key_env: str


_DEFAULT_MODELSCOPE = ProviderSpec(
    base_url="https://api-inference.modelscope.cn/v1/chat/completions",
    api_key_env="MODELSCOPE_API_KEY",
)


@dataclass
class InterpretConfig:
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_s: int = 60
    title_max_chars: int = 64
    body_max_chars: int = 240
    raw_summary_max_chars: int = 1500  # 防任意 adapter 的超长 raw_summary 撑爆 prompt
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
    pending_watermark: str = "草稿待定稿"
    # 报告日期标签所在时区(IANA 名, 不用固定 UTC 偏移 —— 夏令时要自动跟随)。
    # 2026-08-01: 用户所在地由北京改为 UK; date_label 与 metrics 日期都读这里, 保证一致。
    timezone: str = "Europe/London"
    min_display_score: int = (
        40  # 人工 keep 条目的质量底(确认门已保证全是 keep; 60 太高会吞 keep 的低分首发)
    )
    quota: dict[str, int] = field(
        default_factory=lambda: {
            "paper": 3,
            "model": 3,
            "announcement": 3,
            "writeup": 2,
            "news": 1,
        }
    )
    total_limit: int = 11  # 刊物总条目硬上限(人 keep 后施加)
    genre_labels: dict[str, str] = field(
        default_factory=lambda: {
            "paper": "论文",
            "model": "模型",
            "announcement": "官方",
            "writeup": "博客 / 工具",
            "news": "新闻",
        }
    )
    adapter_quota: dict[str, int] = field(
        default_factory=dict
    )  # 按采集渠道封顶(spec §5), 不占用 genre 配额名额
    story_merge_max_support: int = (
        3  # 故事线合并: 每组最多附带几个"已支持"平台提及(spec 2026-08-28)
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
class ReleaseImportanceConfig:
    """LLM 判定 github_releases 条目的实质重要性(spec 2026-07-22)。
    4 个独立布尔维度(scale/refactor/new_concept/bugfix_only) -> tier() 纯函数映射。"""

    enabled: bool = True
    model: str = "modelscope:deepseek-ai/DeepSeek-V4-Flash"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.1
    max_tokens: int = 300
    timeout_s: int = 30
    empty_body_min_chars: int = (
        30  # 去掉 Full Changelog 链接后正文短于此 -> 短路判 tier 0, 不调 LLM
    )
    hard_filter_max_tier: int = 1  # tier <= 此值从候选池剔除
    tier_score: dict[int, float] = field(default_factory=lambda: {2: 4.0, 3: 9.0})
    prompt_path: str = "src/prompts/release_importance.md"


@dataclass
class StoryLinkConfig:
    """故事线合并(spec 2026-08-28): 同一模型/产品当天的"原始发布"+"第三方支持公告"
    在发布渲染层合并成一条。两阶段: 正则抓 entity token 找候选对, 候选对过一次
    轻量 LLM 是非确认(不产出新文字)。跟 release_importance 同款多 provider 结构。"""

    enabled: bool = True
    # 默认: 字母前缀 + 可选连字符/空格 + 数字(可含小数点), 覆盖 "GLM-5.3" / "v0.28.0" / "Llama 4"
    # 这类"名称+版本号"模式。真实数据上需要反复调(2026-08-28 brainstorm 已知非最终值)。
    entity_token_pattern: str = r"\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b"
    prompt_path: str = "src/prompts/story_link_confirm.md"
    model: str = "modelscope:deepseek-ai/DeepSeek-V4-Flash"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.0
    max_tokens: int = 200
    timeout_s: int = 30
    summary_max_chars: int = 500  # 喂给确认 prompt 的摘要截断长度(防超长撑爆)


@dataclass
class HFReadmeConfig:
    """hf-models 条目本身没有描述文本(adapter 只调用模型列表 API, raw_summary 恒为 None)。
    抓取模型的 HF README 作为 interpret 的素材来源; 抓不到/清洗后内容太短的条目直接从候选
    列表剔除(不带着空 body 进审阅卡池), 而不是放行后指望下游过滤器兜底。"""

    enabled: bool = True
    timeout_s: int = 8
    concurrency: int = 5
    min_body_chars: int = 80  # 清洗 frontmatter/图片/HTML 后剩余正文长度下限; 上线前用真实数据核实
    # 真实 README 可以到 10k+ 字符(2026-07-27 实测 unsloth/Kimi-K3 清洗后仍 14492 字符),
    # 不封顶会把超大文本一路带进 dedup 的 embedding(全 batch 出错即整批退化成无 embedding,
    # 见 src/pipeline/dedup.py) ——interpret 反正只读前 raw_summary_max_chars(1500)个字符,
    # 留出比它宽松但不夸张的余量即可, 不需要更多。
    max_body_chars: int = 2500


@dataclass
class EnrichConfig:
    """RSS 类源天然无 popularity, 用 HN Algolia by URL 反查补 signals.hn_*。"""

    enabled: bool = True
    concurrency: int = 5
    timeout_s: int = 8
    # 已经带原生 popularity 信号的 genre 不查 HN (省请求, 不覆盖)
    skip_genres: list[str] = field(default_factory=lambda: ["paper", "model"])
    release_importance: ReleaseImportanceConfig = field(default_factory=ReleaseImportanceConfig)
    hf_readme: HFReadmeConfig = field(default_factory=HFReadmeConfig)


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
