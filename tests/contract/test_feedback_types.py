from datetime import datetime, timezone
from src.core.types import (FeedbackEvent, SourceFeedbackStats,
                            FeedbackConfig, FeedbackResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_feedback_event_shape():
    e = FeedbackEvent(link="https://a/1", source="src", action="drop",
                      run_id="r1", ts=NOW)
    assert e.action == "drop" and e.source == "src"
    assert e.link == "https://a/1" and e.run_id == "r1"
    assert e.ts == NOW


def test_source_feedback_stats_shape():
    s = SourceFeedbackStats(source="src", keep=2, edit=1, drop=1, total=4)
    assert s.total == 4 and s.keep == 2 and s.edit == 1 and s.drop == 1


def test_feedback_config_defaults():
    c = FeedbackConfig()
    assert c.events_path == "data/feedback_events.json"
    assert c.weights_path == "data/quality_weights.json"
    assert c.baseline_weight == 1.0
    assert c.min_weight == 0.5 and c.max_weight == 1.5
    assert c.step == 0.2 and c.edit_factor == 0.5
    assert c.min_events == 1


def test_feedback_result_shape():
    res = FeedbackResult(
        source_stats=[SourceFeedbackStats(source="src", keep=1, edit=0,
                                          drop=0, total=1)],
        quality_weights={"src": 1.2}, weight_diff={"src": (1.0, 1.2)},
        event_count=1, source_count=1, is_silent=False)
    assert res.quality_weights == {"src": 1.2}
    assert res.weight_diff["src"] == (1.0, 1.2)
    assert res.event_count == 1 and res.source_count == 1
    assert res.is_silent is False
