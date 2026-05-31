import logging
from datetime import datetime, timezone, timedelta
from src.core.types import NewsItem, SourceType, RunContext, ScoringConfig
from src.core.config import load_scoring_config
from src.pipeline.score import score, compute_scores

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(title_en=title, link=link, source=source, source_type=st,
                    published_at=published, cluster_id="evt-x")


def _cfg():
    return load_scoring_config("tests/golden/data/scoring_golden.yaml")


# Case 1 (spec §9.1): over-quota type trimmed to top-scored
def test_golden_quota_trims_top_scored():
    items = [
        _ni("p-fresh", "https://p/1", "p1", SourceType.PAPER, NOW),
        _ni("p-mid", "https://p/2", "p2", SourceType.PAPER, NOW - timedelta(hours=36)),
        _ni("p-stale", "https://p/3", "p3", SourceType.PAPER, NOW - timedelta(hours=100)),
    ]
    res = score(items, _cfg(), _ctx())
    assert res.quota_report["paper"].selected == 2          # quota paper=2
    assert res.quota_report["paper"].available == 3
    kept = {s.link for s in res.selected_items}
    assert kept == {"https://p/1", "https://p/2"}           # stale dropped


# Case 2 (spec §9.2): under-quota type fully kept, no fabrication
def test_golden_under_quota_keeps_all():
    items = [_ni("t", "https://t/1", "t1", SourceType.TOOL, NOW)]   # quota tool=2
    res = score(items, _cfg(), _ctx())
    assert res.quota_report["tool"].available == 1
    assert res.quota_report["tool"].selected == 1
    assert res.selected_count == 1


# Case 3 (spec §9.3): recency bands
def test_golden_recency_bands():
    items = [
        _ni("fresh", "https://o/1", "s1", SourceType.OFFICIAL, NOW),
        _ni("mid", "https://o/2", "s2", SourceType.OFFICIAL, NOW - timedelta(hours=36)),
        _ni("zero", "https://o/3", "s3", SourceType.OFFICIAL, NOW - timedelta(hours=60)),
        _ni("stale", "https://o/4", "s4", SourceType.OFFICIAL, NOW - timedelta(hours=100)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    band = {s.link: s.score_breakdown["时效"] for s in scored}
    assert band["https://o/1"] == 10
    assert band["https://o/2"] == 4
    assert band["https://o/3"] == 0
    assert band["https://o/4"] == -10


# Case 4 (spec §9.4): same-source penalty by published order
def test_golden_same_source_penalty():
    items = [
        _ni("late", "https://s/3", "dup", SourceType.NEWS, NOW - timedelta(hours=1)),
        _ni("early", "https://s/1", "dup", SourceType.NEWS, NOW - timedelta(hours=3)),
        _ni("mid", "https://s/2", "dup", SourceType.NEWS, NOW - timedelta(hours=2)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0       # earliest
    assert pen["https://s/2"] == -5
    assert pen["https://s/3"] == -5


# Case 5 (spec §9.5): empty input -> silent
def test_golden_empty_input_is_silent():
    res = score([], _cfg(), _ctx())
    assert res.selected_items == [] and res.all_scored == []
    assert res.input_count == 0 and res.selected_count == 0
    assert res.is_silent is True


# Case 6 (spec §9.6): determinism + clamp + breakdown sums to score
def test_golden_clamp_and_breakdown_sum_and_determinism():
    items = [_ni("a", "https://a/1", "s1", SourceType.OFFICIAL, NOW)]
    # high config -> clamp to 100
    hi = ScoringConfig()
    hi.dimension_scores = {"official": {"机构影响力": 90, "一手性": 90,
                                        "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    s1 = compute_scores(items, {}, hi, _ctx())
    assert s1[0].score == 100
    assert s1[0].score == max(0, min(100, round(sum(s1[0].score_breakdown.values()))))
    # low/negative config -> clamp to 0
    lo = ScoringConfig()
    lo.dimension_scores = {"official": {"机构影响力": -50, "一手性": -50,
                                        "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    lo.fresh_bonus = 0
    s2 = compute_scores(items, {}, lo, _ctx())
    assert s2[0].score == 0
    # determinism: same input + same ctx -> identical scores
    again = compute_scores(items, {}, hi, _ctx())
    assert [x.score for x in s1] == [x.score for x in again]
