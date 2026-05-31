from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (ScoredItem, QuotaLine, ScoreResult, ScoringConfig,
                            SourceType)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(title_en="T", link="https://a/1", source="openai",
                source_type=SourceType.PAPER, published_at=NOW,
                cluster_id="evt-2026-05-30-001", score=88,
                score_breakdown={"时效": 10.0}, is_explore=False)
    base.update(over)
    return ScoredItem(**base)


def test_scored_item_extends_newsitem():
    si = _scored()
    assert si.score == 88
    assert si.cluster_id == "evt-2026-05-30-001"   # inherited from NewsItem
    assert si.title_en == "T"                       # inherited from RawItem
    assert si.is_explore is False


def test_scored_item_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        _scored(score=150)
    with pytest.raises(ValidationError):
        _scored(score=-1)


def test_quota_line_and_result_shapes():
    line = QuotaLine(source_type="paper", available=3, quota=2, selected=2)
    res = ScoreResult(selected_items=[_scored()], all_scored=[_scored()],
                      quota_report={"paper": line}, input_count=1,
                      selected_count=1, is_silent=False)
    assert res.quota_report["paper"].selected == 2
    assert res.selected_count == 1


def test_scoring_config_defaults():
    c = ScoringConfig()
    assert c.quota["paper"] == 2
    assert c.total_limit == 8
    assert c.fresh_bonus == 10
    assert c.dimension_scores["official"]["一手性"] == 20
    assert c.priority_bonus[1] == 6
    assert c.sources_registry_path == "config/sources.yaml"
