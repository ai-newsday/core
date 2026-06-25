import logging
from datetime import datetime, timedelta, timezone

from src.core.config import load_scoring_config
from src.core.types import Genre, NewsItem, RunContext, ScoringConfig
from src.pipeline.score import compute_scores, score
from tests.fakes import DEFAULT_PUBLISHER

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(
        title_en=title,
        link=link,
        source=source,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=published,
        cluster_id="evt-x",
    )


def _cfg():
    return load_scoring_config("tests/golden/data/scoring_golden.yaml")


# Case 1: selected_items = 发卡候选池(按 score top-N), 不再 per-genre 配额
def test_score_selected_is_card_pool_top_n():
    cfg = _cfg()
    cfg.card_pool_limit = 2
    items = [
        _ni("p1", "https://p/1", "s1", Genre.paper, NOW),
        _ni("p2", "https://p/2", "s2", Genre.paper, NOW - timedelta(hours=36)),
        _ni("p3", "https://p/3", "s3", Genre.paper, NOW - timedelta(hours=100)),
    ]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 2
    assert len(res.all_scored) == 3
    assert res.selected_items == res.all_scored[:2]  # all_scored 已按 score 降序
    assert res.quota_report == {}


# Case 2: 候选池未满 -> 全留, 不编造
def test_score_card_pool_keeps_all_when_under_limit():
    cfg = _cfg()
    cfg.card_pool_limit = 25
    items = [_ni("t", "https://t/1", "t1", Genre.writeup, NOW)]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 1
    assert res.selected_items == res.all_scored


# Case 3 (spec §9.3): recency bands
def test_golden_recency_bands():
    items = [
        _ni("fresh", "https://o/1", "s1", Genre.announcement, NOW),
        _ni("mid", "https://o/2", "s2", Genre.announcement, NOW - timedelta(hours=36)),
        _ni("zero", "https://o/3", "s3", Genre.announcement, NOW - timedelta(hours=60)),
        _ni("stale", "https://o/4", "s4", Genre.announcement, NOW - timedelta(hours=100)),
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
        _ni("late", "https://s/3", "dup", Genre.news, NOW - timedelta(hours=1)),
        _ni("early", "https://s/1", "dup", Genre.news, NOW - timedelta(hours=3)),
        _ni("mid", "https://s/2", "dup", Genre.news, NOW - timedelta(hours=2)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0  # earliest
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
    items = [_ni("a", "https://a/1", "s1", Genre.announcement, NOW)]
    # high config -> clamp to 100
    hi = ScoringConfig()
    hi.genre_value = {"announcement": {"一手性": 90, "技术价值": 90, "产业影响": 0, "扩散潜力": 0}}
    hi.publisher_authority = {"lab": 90}
    s1 = compute_scores(items, {}, hi, _ctx())
    assert s1[0].score == 100
    assert s1[0].score == max(0, min(100, round(sum(s1[0].score_breakdown.values()))))
    # low/negative config -> clamp to 0
    lo = ScoringConfig()
    lo.genre_value = {"announcement": {"一手性": -50, "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    lo.publisher_authority = {"lab": -50}
    lo.fresh_bonus = 0
    s2 = compute_scores(items, {}, lo, _ctx())
    assert s2[0].score == 0
    # determinism: same input + same ctx -> identical scores
    again = compute_scores(items, {}, hi, _ctx())
    assert [x.score for x in s1] == [x.score for x in again]
