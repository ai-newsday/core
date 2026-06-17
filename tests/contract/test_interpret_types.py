from datetime import datetime, timezone

from src.core.types import (
    Evidence,
    InterpretConfig,
    InterpretedItem,
    InterpretResult,
    ScoredItem,
    Genre, Publisher,
)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(
        title_en="GLM-5 released",
        link="https://hf.co/glm5",
        source="Hugging Face",
        genre=Genre.model, publisher=Publisher.company,
        published_at=NOW,
        raw_summary="MoE open weights model.",
        cluster_id="evt-1",
        related_links=["https://blog/glm5"],
        score=88,
        score_breakdown={"机构影响力": 88},
        is_explore=False,
    )
    base.update(over)
    return ScoredItem(**base)


def test_interpret_config_defaults():
    c = InterpretConfig()
    assert c.title_max_chars == 64 and c.summary_max_chars == 120
    assert c.tags_count == 3 and c.min_evidence == 1
    assert c.item_prompt_path == "src/prompts/interpret_item.md"
    assert c.daily_prompt_path == "src/prompts/daily_take.md"


def test_evidence_schema():
    e = Evidence(claim="MoE open weights", anchor="https://hf.co/glm5")
    assert e.claim and e.anchor


def test_interpreted_item_extends_scored_item():
    it = _scored()
    interp = InterpretedItem(
        **it.model_dump(),
        title="智谱发布 GLM-5",
        summary="开源 MoE 模型。",
        takeaway="可自建推理。",
        hot_take="护城河又薄了。",
        tags=["#开源", "#MoE", "#GLM"],
        evidence=[Evidence(claim="MoE", anchor="https://hf.co/glm5")],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )
    # inherits ScoredItem invariants
    assert interp.score == 88 and interp.cluster_id == "evt-1"
    assert interp.is_explore is False
    assert interp.interpretation_status == "ok"
    assert interp.tags == ["#开源", "#MoE", "#GLM"]


def test_interpreted_item_defaults_for_fallback():
    it = _scored()
    interp = InterpretedItem(
        **it.model_dump(),
        title="GLM-5 released",
        summary="MoE open weights model.",
        takeaway="",
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
    )
    assert interp.hot_take == "" and interp.tags == [] and interp.evidence == []


def test_interpret_result_shape():
    r = InterpretResult(
        interpreted_items=[],
        daily_take=None,
        input_count=0,
        interpreted_count=0,
        fallback_count=0,
        is_silent=True,
    )
    assert r.is_silent is True and r.daily_take is None
