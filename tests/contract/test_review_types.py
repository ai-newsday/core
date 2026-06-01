import logging
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, ReviewedItem, ReviewConfig,
                            ReviewResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _interp(**over):
    base = dict(title_en="GLM-5 released", link="https://hf.co/glm5",
                source="Hugging Face", source_type=SourceType.MODEL,
                published_at=NOW, raw_summary="MoE open weights model.",
                cluster_id="evt-1", related_links=["https://blog/glm5"],
                score=88, score_breakdown={"机构影响力": 88.0}, is_explore=False,
                title="智谱发布 GLM-5", summary="开源 MoE 模型。",
                takeaway="可自建推理。", hot_take="护城河又薄了。",
                tags=["#开源", "#MoE", "#GLM"],
                evidence=[Evidence(claim="MoE", anchor="https://hf.co/glm5")],
                interpretation_status="ok", eligible_for_must_read=True)
    base.update(over)
    return InterpretedItem(**base)


def test_review_config_defaults():
    c = ReviewConfig()
    assert c.decisions_path == "data/review_decisions.json"
    assert c.title_max_chars == 64 and c.summary_max_chars == 120
    assert c.tags_count == 3 and c.min_evidence == 1


def test_review_decision_defaults_and_enum():
    d = ReviewDecision()
    assert d.action == "keep" and d.order is None and d.edits == {}
    e = ReviewDecision(action="edit", order=2, edits={"title": "新标题"})
    assert e.action == "edit" and e.order == 2 and e.edits["title"] == "新标题"


def test_review_decision_rejects_unknown_action():
    with pytest.raises(ValidationError):
        ReviewDecision(action="frobnicate")


def test_reviewed_item_extends_interpreted_item():
    it = _interp()
    r = ReviewedItem(**it.model_dump(), review_action="keep",
                     was_edited=False, edited_fields=[])
    # 继承上游不变量
    assert r.score == 88 and r.cluster_id == "evt-1"
    assert r.interpretation_status == "ok"
    assert r.review_action == "keep" and r.was_edited is False
    assert r.edited_fields == []


def test_reviewed_item_edited_fields_recorded():
    it = _interp()
    r = ReviewedItem(**it.model_dump(), review_action="edit",
                     was_edited=True, edited_fields=["title", "summary"])
    assert r.review_action == "edit" and r.was_edited is True
    assert r.edited_fields == ["title", "summary"]


def test_review_result_shape():
    res = ReviewResult(reviewed_items=[], daily_take=None, input_count=0,
                       kept_count=0, dropped_count=0, edited_count=0,
                       is_reviewed=False, is_pending=True, is_silent=True)
    assert res.is_silent is True and res.is_pending is True
    assert res.is_reviewed is False and res.daily_take is None
