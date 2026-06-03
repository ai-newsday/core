from __future__ import annotations
from datetime import datetime
from src.core.types import (InterpretedItem, ReviewDecision, FeedbackEvent,
                            SourceFeedbackStats, FeedbackConfig,
                            FeedbackResult, RunContext)
from src.observability.events import emit


def derive_events(items: list[InterpretedItem],
                  decisions: dict[str, ReviewDecision],
                  run_id: str, now: datetime) -> list[FeedbackEvent]:
    """从进审阅前全量条目派生事件; 无决策默认 keep; 带 source; ts 注入。
    遍历审阅前条目(非保留结果)→ 被删条目也产 drop 事件。"""
    out: list[FeedbackEvent] = []
    for it in items:
        dec = decisions.get(it.link)
        action = dec.action if dec is not None else "keep"
        out.append(FeedbackEvent(link=it.link, source=it.source,
                                 action=action, run_id=run_id, ts=now))
    return out


def aggregate_by_source(events: list[FeedbackEvent]
                        ) -> list[SourceFeedbackStats]:
    """按 source 聚合 keep/edit/drop/total; 输出按 source 字母序(确定性)。"""
    buckets: dict[str, dict[str, int]] = {}
    for e in events:
        b = buckets.setdefault(e.source, {"keep": 0, "edit": 0, "drop": 0})
        b[e.action] += 1
    out: list[SourceFeedbackStats] = []
    for source in sorted(buckets):
        b = buckets[source]
        out.append(SourceFeedbackStats(
            source=source, keep=b["keep"], edit=b["edit"], drop=b["drop"],
            total=b["keep"] + b["edit"] + b["drop"]))
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_quality_weights(
        stats: list[SourceFeedbackStats],
        prior_weights: dict[str, float],
        config: FeedbackConfig
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """增量更新每源权重: 留升/删降/改半正; 样本不足不动; 夹界 [min,max]。
    本轮未出现的 prior 源原样保留进结果但不进 diff。"""
    weights: dict[str, float] = dict(prior_weights)   # 历史不丢
    diff: dict[str, tuple[float, float]] = {}
    for s in stats:
        old = prior_weights.get(s.source, config.baseline_weight)
        if s.total < config.min_events:
            new = old
        else:
            kr = s.keep / s.total
            er = s.edit / s.total
            dr = s.drop / s.total
            raw = old + config.step * (kr + config.edit_factor * er - dr)
            new = _clamp(raw, config.min_weight, config.max_weight)
        new = round(new, 10)                          # 抹去浮点尾噪, 确定性
        weights[s.source] = new
        diff[s.source] = (old, new)
    return weights, diff


def feedback(events: list[FeedbackEvent], prior_weights: dict[str, float],
             config: FeedbackConfig, ctx: RunContext) -> FeedbackResult:
    """编排: 聚合 → 增量算权重 → 组装结果。空事件→静默, 权重原样透传。
    无网络/LLM/落盘副作用。"""
    emit(ctx.logger, "feedback_start", run_id=ctx.run_id,
         event_count=len(events))
    if not events:
        emit(ctx.logger, "feedback_done", event_count=0, source_count=0,
             silent=True)
        return FeedbackResult(
            source_stats=[], quality_weights=dict(prior_weights),
            weight_diff={}, event_count=0, source_count=0, is_silent=True)
    stats = aggregate_by_source(events)
    weights, diff = compute_quality_weights(stats, prior_weights, config)
    changed = sum(1 for old, new in diff.values() if old != new)
    emit(ctx.logger, "weights_computed", source_count=len(stats),
         changed_count=changed)
    emit(ctx.logger, "feedback_done", event_count=len(events),
         source_count=len(stats), silent=False)
    return FeedbackResult(
        source_stats=stats, quality_weights=weights, weight_diff=diff,
        event_count=len(events), source_count=len(stats), is_silent=False)
