"""golden: 把 item.signals 接进 score "可见指标" 维度。
不写 popularity_weights = 老行为 (=0); 写了 = sqrt-加权 + cap。"""

import logging
from datetime import datetime, timezone

from src.core.types import Genre, NewsItem, RunContext, ScoringConfig
from src.pipeline.score import compute_scores
from tests.fakes import DEFAULT_PUBLISHER

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _it(link, source="src", st=Genre.paper, signals=None):
    return NewsItem(
        title_en="X",
        link=link,
        source=source,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=NOW,
        cluster_id="c1",
        related_links=[],
        signals=signals or {},
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-score-pop"))


def test_no_popularity_weights_keeps_visibility_zero():
    items = [_it("a", signals={"upvotes": 100})]
    cfg = ScoringConfig()  # 默认 popularity_weights={}
    scored = compute_scores(items, {}, cfg, _ctx())
    assert scored[0].score_breakdown["可见指标"] == 0.0


def test_popularity_weight_lifts_visibility():
    items = [_it("low", signals={"upvotes": 0}), _it("hi", signals={"upvotes": 100})]
    cfg = ScoringConfig(popularity_weights={"upvotes": 1.0})
    scored = compute_scores(items, {}, cfg, _ctx())
    by_link = {s.link: s for s in scored}
    # sqrt(100) * 1.0 = 10; sqrt(0) = 0
    assert by_link["hi"].score_breakdown["可见指标"] == 10.0
    assert by_link["low"].score_breakdown["可见指标"] == 0.0
    # 高 popularity 总分更高 (其它维度相同)
    assert by_link["hi"].score > by_link["low"].score


def test_popularity_sums_multiple_signal_keys():
    items = [_it("a", signals={"upvotes": 25, "hn_points": 49, "likes": 16})]
    cfg = ScoringConfig(popularity_weights={"upvotes": 0.5, "hn_points": 1.0, "likes": 0.25})
    scored = compute_scores(items, {}, cfg, _ctx())
    # 0.5*sqrt(25) + 1.0*sqrt(49) + 0.25*sqrt(16) = 2.5 + 7 + 1 = 10.5
    assert scored[0].score_breakdown["可见指标"] == 10.5


def test_popularity_cap_caps_at_max():
    items = [_it("crazy", signals={"hn_points": 100000})]
    cfg = ScoringConfig(popularity_weights={"hn_points": 1.0}, popularity_cap=15.0)
    scored = compute_scores(items, {}, cfg, _ctx())
    # sqrt(100000) ≈ 316, 被夹到 15
    assert scored[0].score_breakdown["可见指标"] == 15.0


def test_popularity_ignores_missing_signal_keys():
    items = [_it("a", signals={})]  # 一个信号都没有
    cfg = ScoringConfig(popularity_weights={"upvotes": 1.0, "hn_points": 1.0})
    scored = compute_scores(items, {}, cfg, _ctx())
    assert scored[0].score_breakdown["可见指标"] == 0.0


def test_popularity_handles_non_numeric_gracefully():
    items = [
        _it("a", signals={"upvotes": "not-a-number"}),  # 脏数据
        _it("b", signals={"upvotes": None}),
        _it("c", signals={"upvotes": -5}),
    ]  # 负数
    cfg = ScoringConfig(popularity_weights={"upvotes": 1.0})
    scored = compute_scores(items, {}, cfg, _ctx())
    by_link = {s.link: s for s in scored}
    # 非数 / None / 负数 → 0 (不抛, 不污染)
    assert by_link["a"].score_breakdown["可见指标"] == 0.0
    assert by_link["b"].score_breakdown["可见指标"] == 0.0
    assert by_link["c"].score_breakdown["可见指标"] == 0.0
