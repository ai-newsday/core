import logging
from datetime import datetime, timezone, timedelta
from src.core.types import RawItem, NewsItem, SourceType, ScoringConfig, RunContext
from src.pipeline.score import recency_band, compute_scores

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="u", now=NOW, logger=logging.getLogger("unit-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(title_en=title, link=link, source=source, source_type=st,
                    published_at=published, cluster_id="evt-x")


def test_recency_band_four_zones():
    c = ScoringConfig()
    assert recency_band(NOW, NOW, c) == c.fresh_bonus                       # 0h
    assert recency_band(NOW - timedelta(hours=36), NOW, c) == c.mid_bonus   # 36h
    assert recency_band(NOW - timedelta(hours=60), NOW, c) == 0.0           # 60h
    assert recency_band(NOW - timedelta(hours=100), NOW, c) == c.stale_penalty


def test_compute_scores_breakdown_has_nine_keys_and_sums_to_score():
    items = [_ni("A", "https://a/1", "openai", SourceType.OFFICIAL)]
    scored = compute_scores(items, {"openai": 1}, ScoringConfig(), _ctx())
    bd = scored[0].score_breakdown
    assert set(bd) == {"机构影响力", "一手性", "技术价值", "产业影响", "扩散潜力",
                       "可见指标", "时效", "惩罚", "读者相关度"}
    assert bd["可见指标"] == 0.0 and bd["读者相关度"] == 0.0
    assert scored[0].is_explore is False
    assert scored[0].score == max(0, min(100, round(sum(bd.values()))))
    # official base 一手性=20, priority 1 bonus folded into 机构影响力
    assert bd["机构影响力"] == 18 + 6


def test_compute_scores_missing_priority_uses_default():
    items = [_ni("A", "https://a/1", "unknown-src", SourceType.PAPER)]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())   # source not in map
    # priority_bonus_default == 0 -> 机构影响力 == paper base 14 + 0
    assert scored[0].score_breakdown["机构影响力"] == 14


def test_compute_scores_same_source_penalty_by_published_order():
    t1 = NOW - timedelta(hours=3)
    t2 = NOW - timedelta(hours=2)
    t3 = NOW - timedelta(hours=1)
    items = [
        _ni("late", "https://s/3", "blog-x", SourceType.BLOG, t3),
        _ni("early", "https://s/1", "blog-x", SourceType.BLOG, t1),
        _ni("mid", "https://s/2", "blog-x", SourceType.BLOG, t2),
    ]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0.0                          # earliest: no penalty
    assert pen["https://s/2"] == ScoringConfig().same_source_penalty
    assert pen["https://s/3"] == ScoringConfig().same_source_penalty


def test_compute_scores_sorted_desc_by_score():
    items = [
        _ni("blog", "https://b/1", "b", SourceType.BLOG),       # low base
        _ni("official", "https://o/2", "o", SourceType.OFFICIAL),  # high base
    ]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())
    assert [s.source_type for s in scored][0] == SourceType.OFFICIAL
    assert scored[0].score >= scored[1].score
