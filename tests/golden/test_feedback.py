from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, FeedbackConfig)
from src.pipeline.feedback import derive_events, aggregate_by_source

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
