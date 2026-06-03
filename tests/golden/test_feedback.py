from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, FeedbackConfig, SourceFeedbackStats)
from src.pipeline.feedback import (derive_events, aggregate_by_source,
                                   compute_quality_weights)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
CFG = FeedbackConfig()


def _ii(link="https://a/1", source="src", source_type=SourceType.MODEL):
    return InterpretedItem(
        title_en="X", link=link, source=source, source_type=source_type,
        published_at=NOW, raw_summary="A.", cluster_id="evt-1",
        related_links=[], score=80, score_breakdown={"机构影响力": 80.0},
        is_explore=False, title="标题", summary="摘要。", takeaway="用法。",
        hot_take="锐评。", tags=["#a"],
        evidence=[Evidence(claim="事实", anchor=link)],
        interpretation_status="ok", eligible_for_must_read=True)


def test_derive_events_includes_drop_and_default_keep():
    items = [_ii("https://a/1", source="s1"),
             _ii("https://a/2", source="s2"),
             _ii("https://a/3", source="s3")]
    decisions = {"https://a/1": ReviewDecision(action="drop"),
                 "https://a/2": ReviewDecision(action="edit")}
    evs = derive_events(items, decisions, run_id="r1", now=NOW)
    # 每条进审阅前条目恰产一个事件(被删的也在)
    assert len(evs) == 3
    by_link = {e.link: e for e in evs}
    assert by_link["https://a/1"].action == "drop"   # 删也回收
    assert by_link["https://a/2"].action == "edit"
    assert by_link["https://a/3"].action == "keep"   # 无决策默认 keep
    assert by_link["https://a/1"].source == "s1"
    assert all(e.run_id == "r1" and e.ts == NOW for e in evs)


def test_aggregate_by_source_counts_and_alpha_order():
    evs = derive_events(
        [_ii("https://a/1", source="b"), _ii("https://a/2", source="b"),
         _ii("https://a/3", source="a")],
        {"https://a/1": ReviewDecision(action="drop")},
        run_id="r1", now=NOW)
    stats = aggregate_by_source(evs)
    # source 字母序: a 在 b 前(与输入序无关)
    assert [s.source for s in stats] == ["a", "b"]
    a = [s for s in stats if s.source == "a"][0]
    b = [s for s in stats if s.source == "b"][0]
    assert a.keep == 1 and a.total == 1
    assert b.keep == 1 and b.drop == 1 and b.total == 2
    # 聚合不漏: 总 total == 事件数
    assert sum(s.total for s in stats) == len(evs)


def _stats(source, keep=0, edit=0, drop=0):
    return SourceFeedbackStats(source=source, keep=keep, edit=edit, drop=drop,
                               total=keep + edit + drop)


def test_compute_all_keep_raises_weight():
    stats = [_stats("a", keep=3)]
    w, diff = compute_quality_weights(stats, {}, CFG)
    # 冷启动 baseline 1.0; 全 keep → 升; 夹界内
    assert w["a"] == 1.2                       # 1.0 + 0.2*(1) = 1.2
    assert diff["a"] == (1.0, 1.2)
    assert CFG.min_weight <= w["a"] <= CFG.max_weight


def test_compute_all_drop_lowers_weight():
    stats = [_stats("b", drop=3)]
    w, diff = compute_quality_weights(stats, {}, CFG)
    assert w["b"] == 0.8                        # 1.0 + 0.2*(-1) = 0.8
    assert diff["b"] == (1.0, 0.8)


def test_compute_edit_is_half_positive():
    stats = [_stats("e", edit=4)]
    w, _ = compute_quality_weights(stats, {}, CFG)
    assert w["e"] == 1.1                        # 1.0 + 0.2*(0.5) = 1.1
    # edit 升幅 < 全 keep 升幅
    assert w["e"] < 1.2


def test_compute_clamp_upper_bound():
    stats = [_stats("c", keep=5)]
    w, _ = compute_quality_weights(stats, {"c": 1.45}, CFG)
    # 1.45 + 0.2 = 1.65 → 夹到 1.5
    assert w["c"] == 1.5


def test_compute_clamp_lower_bound():
    stats = [_stats("d", drop=5)]
    w, _ = compute_quality_weights(stats, {"d": 0.55}, CFG)
    # 0.55 - 0.2 = 0.35 → 夹到 0.5
    assert w["d"] == 0.5


def test_compute_insufficient_sample_unchanged():
    cfg = FeedbackConfig(min_events=2)
    stats = [_stats("f", keep=1)]               # total=1 < 2
    w, diff = compute_quality_weights(stats, {"f": 1.3}, cfg)
    assert w["f"] == 1.3                         # 不动
    assert diff["f"] == (1.3, 1.3)


def test_compute_preserves_unseen_prior_sources():
    stats = [_stats("a", keep=2)]
    w, diff = compute_quality_weights(stats, {"a": 1.0, "g": 0.9}, CFG)
    # 本轮没出现的 g 原样保留, 但不进 diff
    assert w["g"] == 0.9
    assert "g" not in diff
