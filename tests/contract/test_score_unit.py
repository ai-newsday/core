import logging
from datetime import datetime, timedelta, timezone

from src.core.types import NewsItem, RunContext, ScoringConfig, SourceType
from src.pipeline.score import _topic_relevance, apply_quota, compute_scores, recency_band

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
    assert bd["可见指标"] == 0.0 and bd["读者相关度"] == 0.0  # no topic_keywords configured
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


def _scored_list(ctx, *specs):
    # specs: (title, link, source, source_type, published)
    items = [_ni(*s) for s in specs]
    return compute_scores(items, {}, ScoringConfig(), ctx)


def test_apply_quota_trims_to_quota_keeping_top_scored():
    ctx = _ctx()
    # 3 papers from distinct sources, varying recency -> distinct scores
    fresh = NOW
    mid = NOW - timedelta(hours=36)
    stale = NOW - timedelta(hours=100)
    scored = _scored_list(
        ctx,
        ("p-fresh", "https://p/1", "p1", SourceType.PAPER, fresh),
        ("p-mid", "https://p/2", "p2", SourceType.PAPER, mid),
        ("p-stale", "https://p/3", "p3", SourceType.PAPER, stale),
    )
    cfg = ScoringConfig()
    cfg.quota = {"paper": 2}
    selected, report = apply_quota(scored, cfg)
    assert report["paper"].available == 3
    assert report["paper"].quota == 2
    assert report["paper"].selected == 2
    links = {s.link for s in selected}
    assert links == {"https://p/1", "https://p/2"}      # stale dropped (lowest)


def test_apply_quota_keeps_all_when_under_quota():
    ctx = _ctx()
    scored = _scored_list(ctx, ("t", "https://t/1", "t1", SourceType.TOOL, NOW))
    cfg = ScoringConfig()
    cfg.quota = {"tool": 2}
    selected, report = apply_quota(scored, cfg)
    assert report["tool"].available == 1
    assert report["tool"].selected == 1                 # min(quota, available)
    assert len(selected) == 1


def test_apply_quota_zero_for_unlisted_type():
    ctx = _ctx()
    scored = _scored_list(ctx, ("n", "https://n/1", "n1", SourceType.NEWS, NOW))
    cfg = ScoringConfig()
    cfg.quota = {"paper": 2}                             # news not listed
    selected, report = apply_quota(scored, cfg)
    assert report["news"].quota == 0 and report["news"].selected == 0
    assert selected == []


def test_apply_quota_respects_total_limit():
    ctx = _ctx()
    scored = _scored_list(
        ctx,
        ("a", "https://a/1", "s1", SourceType.PAPER, NOW),
        ("b", "https://b/2", "s2", SourceType.MODEL, NOW),
        ("c", "https://c/3", "s3", SourceType.TOOL, NOW),
    )
    cfg = ScoringConfig()
    cfg.quota = {"paper": 1, "model": 1, "tool": 1}
    cfg.total_limit = 2
    selected, _ = apply_quota(scored, cfg)
    assert len(selected) == 2                            # trimmed to total_limit
    # kept the 2 highest-scored
    assert selected[0].score >= selected[1].score


# --- topic relevance tests ---


def test_topic_relevance_returns_zero_when_no_keywords():
    item = _ni("Unified Image Generation Model", "https://a/1", "hf", SourceType.PAPER)
    cfg = ScoringConfig()  # topic_keywords defaults to []
    assert _topic_relevance(item, cfg) == 0.0


def test_topic_relevance_matches_keyword_in_title():
    item = _ni("A New Unified Generation Framework", "https://a/1", "hf", SourceType.PAPER)
    cfg = ScoringConfig(topic_keywords=["unified generation", "multimodal"], topic_bonus=5.0)
    assert _topic_relevance(item, cfg) == 5.0


def test_topic_relevance_case_insensitive():
    item = _ni("MULTIMODAL Learning at Scale", "https://a/1", "hf", SourceType.PAPER)
    cfg = ScoringConfig(topic_keywords=["multimodal"], topic_bonus=7.0)
    assert _topic_relevance(item, cfg) == 7.0


def test_topic_relevance_no_match_returns_zero():
    item = _ni("Optimizing SQL Queries", "https://a/1", "hf", SourceType.PAPER)
    cfg = ScoringConfig(topic_keywords=["unified generation", "multimodal", "agent"], topic_bonus=5.0)
    assert _topic_relevance(item, cfg) == 0.0


def test_topic_relevance_integrated_in_scoring():
    """读者相关度 dimension reflects topic_bonus when keyword matches."""
    items = [
        _ni("Agent Framework for Automation", "https://a/1", "s1", SourceType.PAPER),
        _ni("Database Internals", "https://b/2", "s2", SourceType.PAPER),
    ]
    cfg = ScoringConfig(topic_keywords=["agent"], topic_bonus=5.0)
    scored = compute_scores(items, {}, cfg, _ctx())
    by_link = {s.link: s for s in scored}
    assert by_link["https://a/1"].score_breakdown["读者相关度"] == 5.0
    assert by_link["https://b/2"].score_breakdown["读者相关度"] == 0.0
    assert by_link["https://a/1"].score > by_link["https://b/2"].score


def test_recency_band_with_production_config():
    """Verify tightened recency windows from production scoring.yaml."""
    from src.core.config import load_scoring_config
    cfg = load_scoring_config("config/scoring.yaml")
    assert cfg.fresh_hours == 18
    assert cfg.stale_hours == 48
    # 20h old -> mid band (18 < 20 <= 36)
    assert recency_band(NOW - timedelta(hours=20), NOW, cfg) == cfg.mid_bonus
    # 40h old -> neutral (36 < 40 <= 48)
    assert recency_band(NOW - timedelta(hours=40), NOW, cfg) == 0.0
    # 50h old -> stale penalty (> 48)
    assert recency_band(NOW - timedelta(hours=50), NOW, cfg) == cfg.stale_penalty
