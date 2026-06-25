from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from src.core.registry import load_source_priorities
from src.core.types import NewsItem, QuotaLine, RunContext, ScoredItem, ScoreResult, ScoringConfig
from src.observability.events import emit

# PRD §5.5 fixed breakdown dimension keys.
DIMENSION_KEYS = [
    "机构影响力",
    "一手性",
    "技术价值",
    "产业影响",
    "扩散潜力",
    "可见指标",
    "时效",
    "惩罚",
    "读者相关度",
]
# Dimensions sourced directly from the per-type matrix (spec §5.1).
_MATRIX_DIMS = ["一手性", "技术价值", "产业影响", "扩散潜力"]


def recency_band(published_at: datetime, now: datetime, config: ScoringConfig) -> float:
    """时效分: 4 档 (spec §5.2). Uses injected `now` for determinism."""
    age_h = (now - published_at).total_seconds() / 3600.0
    if age_h <= config.fresh_hours:
        return float(config.fresh_bonus)
    if age_h <= config.mid_hours:
        return float(config.mid_bonus)
    if age_h <= config.stale_hours:
        return 0.0
    return float(config.stale_penalty)


def _popularity_proxy(item: NewsItem, config: ScoringConfig) -> float:
    """Same-source tie-break 用. 复用 popularity_weights 信号集 (upvotes/likes/...),
    与 _visibility 同源但**不开 sqrt/cap** —— 只作排序键, 区分度比压缩后的 _visibility 强。
    无 popularity_weights 配置或无信号 → 0 (退化到 (published_at, link) 行为, 向后兼容)."""
    if not config.popularity_weights:
        return 0.0
    total = 0.0
    for key, weight in config.popularity_weights.items():
        v = item.signals.get(key)
        try:
            fv = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            continue
        if fv > 0:
            total += float(weight) * fv
    return total


def _same_source_penalty(items: list[NewsItem], config: ScoringConfig) -> dict[str, float]:
    """link -> 同源惩罚. Earliest per source = 0, rest = same_source_penalty (spec §5.3).
    Ordered by (published_at asc, -popularity desc, link asc): 最早发的免罚; 同 published_at
    时取最高人气 (issue #11: HF 每日精选全部同 submittedOnDailyAt, 退化到 link 字母序不合理);
    仍同则 link 字母序兜底 (确定性)."""
    by_source: dict[str, list[NewsItem]] = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)
    out: dict[str, float] = {}
    for grp in by_source.values():
        ordered = sorted(
            grp,
            key=lambda it: (it.published_at, -_popularity_proxy(it, config), it.link),
        )
        for i, it in enumerate(ordered):
            out[it.link] = 0.0 if i == 0 else float(config.same_source_penalty)
    return out


def _visibility(item: NewsItem, config: ScoringConfig) -> float:
    """signals 加权: sum(weight * sqrt(value)) → cap。脏数据/缺失/负数 = 0 (不抛)。
    sqrt 压缩长尾, cap 防极值。weights 缺省空 → 总是 0 (向后兼容)。"""
    if not config.popularity_weights:
        return 0.0
    total = 0.0
    for key, weight in config.popularity_weights.items():
        v = item.signals.get(key)
        try:
            fv = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        total += float(weight) * (fv**0.5)
    return min(total, config.popularity_cap)


def _topic_relevance(item: NewsItem, config: ScoringConfig) -> float:
    if not config.topic_keywords:
        return 0.0
    text = (item.title_en or "").lower()
    if item.raw_summary:
        text += " " + item.raw_summary.lower()
    for kw in config.topic_keywords:
        if kw.lower() in text:
            return float(config.topic_bonus)
    return 0.0


def compute_scores(
    items: list[NewsItem],
    priority_of: dict[str, int],
    config: ScoringConfig,
    ctx: RunContext,
    quality_of: dict[str, float] | None = None,
) -> list[ScoredItem]:
    """Pure scoring (spec §5.1). Returns ScoredItems sorted by (score desc,
    published_at asc, link asc)."""
    penalty_of = _same_source_penalty(items, config)
    scored: list[ScoredItem] = []
    for it in items:
        gdims = config.genre_value.get(it.genre.value, {})
        authority = config.publisher_authority.get(it.publisher.value, 0.0)
        prio = priority_of.get(it.source)
        prio_bonus = (
            config.priority_bonus.get(prio, config.priority_bonus_default)
            if prio is not None
            else config.priority_bonus_default
        )
        qw = (quality_of or {}).get(it.source, 1.0)
        firehose = (
            float(config.firehose_penalty)
            if it.genre.value in ("model", "writeup")
            and it.publisher.value == "individual"
            and _popularity_proxy(it, config) == 0
            else 0.0
        )
        breakdown = {
            "机构影响力": round((float(authority) + float(prio_bonus)) * qw, 4),
            "可见指标": round(_visibility(it, config), 4),
            "时效": recency_band(it.published_at, ctx.now, config),
            "惩罚": penalty_of[it.link] + firehose,
            "读者相关度": _topic_relevance(it, config),
        }
        for k in _MATRIX_DIMS:
            breakdown[k] = float(gdims.get(k, 0))
        # normalize key order to the fixed PRD set
        breakdown = {k: breakdown[k] for k in DIMENSION_KEYS}
        raw = round(sum(breakdown.values()))
        score = max(0, min(100, raw))
        scored.append(
            ScoredItem(**it.model_dump(), score=score, score_breakdown=breakdown, is_explore=False)
        )
    scored.sort(key=lambda s: (-s.score, s.published_at, s.link))
    return scored


def apply_quota(
    scored: list[ScoredItem], quota: dict[str, int], total_limit: int
) -> tuple[list[ScoredItem], dict[str, QuotaLine]]:
    """Strict per-type quota selection (spec §5.4). No cross-type fill.
    纯函数: 按 genre 分组, 每组按 (score desc, published_at, link) 取 top-N(quota[genre]),
    再总量截到 total_limit。score 阶段已不调用(发卡池=top-N); publish 阶段在人 keep 后复用。"""
    by_genre: dict[str, list[ScoredItem]] = defaultdict(list)
    for s in scored:
        by_genre[s.genre.value].append(s)

    selected: list[ScoredItem] = []
    report: dict[str, QuotaLine] = {}
    for g, group in by_genre.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = quota.get(g, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[g] = QuotaLine(genre=g, available=len(group), quota=q, selected=len(take))

    selected.sort(key=lambda s: (-s.score, s.published_at, s.link))
    if len(selected) > total_limit:
        selected = selected[:total_limit]
    return selected, report


def score(
    items: list[NewsItem],
    config: ScoringConfig,
    ctx: RunContext,
    quality_of: dict[str, float] | None = None,
) -> ScoreResult:
    """Orchestrate scoring: load registry priority map, run pure compute_scores +
    apply_quota, emit runs events (spec §3, §11)."""
    emit(ctx.logger, "score_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "score_done", input_count=0, selected_count=0, silent=True)
        return ScoreResult(
            selected_items=[],
            all_scored=[],
            quota_report={},
            input_count=0,
            selected_count=0,
            is_silent=True,
        )

    priority_of = load_source_priorities(config.sources_registry_path)
    scored = compute_scores(items, priority_of, config, ctx, quality_of=quality_of)
    for s in scored:
        emit(ctx.logger, "item_scored", link=s.link, genre=s.genre.value, score=s.score)

    # 发卡候选池: 按 score top-N(成本上界); per-genre 配额已移到 publish 阶段(人 keep 后施加)。
    selected = scored[: config.card_pool_limit]
    for s in selected:
        emit(ctx.logger, "item_selected", link=s.link, genre=s.genre.value, score=s.score)

    result = ScoreResult(
        selected_items=selected,
        all_scored=scored,
        quota_report={},
        input_count=len(items),
        selected_count=len(selected),
        is_silent=False,
    )
    emit(
        ctx.logger,
        "score_done",
        input_count=result.input_count,
        selected_count=result.selected_count,
        silent=False,
    )
    return result
