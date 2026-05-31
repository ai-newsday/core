from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from src.core.types import (NewsItem, ScoredItem, ScoringConfig, RunContext)

# PRD §5.5 fixed breakdown dimension keys.
DIMENSION_KEYS = ["机构影响力", "一手性", "技术价值", "产业影响", "扩散潜力",
                  "可见指标", "时效", "惩罚", "读者相关度"]
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


def _same_source_penalty(items: list[NewsItem], config: ScoringConfig) -> dict[str, float]:
    """link -> 同源惩罚. Earliest per source = 0, rest = same_source_penalty (spec §5.3).
    Ordered by (published_at, link) so it is independent of score (deterministic)."""
    by_source: dict[str, list[NewsItem]] = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)
    out: dict[str, float] = {}
    for grp in by_source.values():
        ordered = sorted(grp, key=lambda it: (it.published_at, it.link))
        for i, it in enumerate(ordered):
            out[it.link] = 0.0 if i == 0 else float(config.same_source_penalty)
    return out


def compute_scores(items: list[NewsItem], priority_of: dict[str, int],
                   config: ScoringConfig, ctx: RunContext) -> list[ScoredItem]:
    """Pure scoring (spec §5.1). Returns ScoredItems sorted by (score desc,
    published_at asc, link asc)."""
    penalty_of = _same_source_penalty(items, config)
    scored: list[ScoredItem] = []
    for it in items:
        dims = config.dimension_scores.get(it.source_type.value, {})
        prio = priority_of.get(it.source)
        prio_bonus = (config.priority_bonus.get(prio, config.priority_bonus_default)
                      if prio is not None else config.priority_bonus_default)
        breakdown = {
            "机构影响力": float(dims.get("机构影响力", 0)) + float(prio_bonus),
            "可见指标": 0.0,
            "时效": recency_band(it.published_at, ctx.now, config),
            "惩罚": penalty_of[it.link],
            "读者相关度": 0.0,
        }
        for k in _MATRIX_DIMS:
            breakdown[k] = float(dims.get(k, 0))
        # normalize key order to the fixed PRD set
        breakdown = {k: breakdown[k] for k in DIMENSION_KEYS}
        raw = round(sum(breakdown.values()))
        score = max(0, min(100, raw))
        scored.append(ScoredItem(**it.model_dump(), score=score,
                                 score_breakdown=breakdown, is_explore=False))
    scored.sort(key=lambda s: (-s.score, s.published_at, s.link))
    return scored


from src.core.types import QuotaLine


def apply_quota(scored: list[ScoredItem], config: ScoringConfig
                ) -> tuple[list[ScoredItem], dict[str, QuotaLine]]:
    """Strict per-type quota selection (spec §5.4). No cross-type fill.
    `scored` is assumed sorted (compute_scores output) but we re-sort defensively."""
    by_type: dict[str, list[ScoredItem]] = defaultdict(list)
    for s in scored:
        by_type[s.source_type.value].append(s)

    selected: list[ScoredItem] = []
    report: dict[str, QuotaLine] = {}
    for stype, group in by_type.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = config.quota.get(stype, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[stype] = QuotaLine(source_type=stype, available=len(group),
                                  quota=q, selected=len(take))

    selected.sort(key=lambda s: (-s.score, s.published_at, s.link))
    if len(selected) > config.total_limit:
        selected = selected[:config.total_limit]
    return selected, report
