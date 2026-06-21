from datetime import datetime, timezone

from src.core.types import (
    Genre,
    InterpretedItem,
    Publisher,
    QualityFlag,
    SelfCheckConfig,
    SelfCheckResult,
)

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _interpreted(**over):
    base = dict(
        title_en="X",
        link="https://a/1",
        source="s",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary="r",
        cluster_id="c",
        related_links=[],
        score=80.0,
        score_breakdown={"机构影响力": 80.0},
        is_explore=False,
        title="标题",
        body="正文内容",
        tags=["#a", "#b", "#c"],
        evidence=[],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )
    base.update(over)
    return InterpretedItem(**base)


def test_quality_flag_schema():
    f = QualityFlag(code="ai_slop", severity="info", field="hot_take", message="太空洞")
    assert f.code == "ai_slop" and f.severity == "info"


def test_interpreted_item_quality_flags_defaults_empty():
    item = _interpreted()
    assert item.quality_flags == []  # new field, default empty -> backward compatible


def test_selfcheck_config_defaults():
    c = SelfCheckConfig()
    assert c.message_max_chars == 120 and c.max_flags_per_item == 3
    assert c.prompt_path == "src/prompts/selfcheck.md"


def test_selfcheck_result_shape():
    item = _interpreted(
        quality_flags=[
            QualityFlag(code="consistency", severity="warn", field="body", message="原文没说")
        ]
    )
    res = SelfCheckResult(
        interpreted_items=[item],
        daily_take="看点",
        checked_count=1,
        flagged_count=1,
        flag_count_by_code={"consistency": 1},
        llm_error_count=0,
        is_silent=False,
    )
    assert res.flagged_count == 1 and res.flag_count_by_code["consistency"] == 1
