import json
from datetime import datetime, timezone

from src.core.types import Evidence, InterpretedItem, SelfCheckConfig, SourceType
from src.pipeline.selfcheck import build_critic_prompt, parse_critic

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _item():
    return InterpretedItem(
        title_en="X",
        link="https://a/1",
        source="s",
        source_type=SourceType.MODEL,
        published_at=NOW,
        raw_summary="原始摘要文本",
        cluster_id="c",
        related_links=[],
        score=80.0,
        score_breakdown={"机构影响力": 80.0},
        is_explore=False,
        title="标题",
        summary="摘要",
        takeaway="用法",
        hot_take="锐评",
        tags=["#a", "#b", "#c"],
        evidence=[Evidence(claim="f", anchor="https://a/1")],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )


def test_build_prompt_substitutes_fields():
    tpl = "T={{title}} S={{summary}} TA={{takeaway}} HT={{hot_take}} RAW={{raw_summary}} EV={{evidence}}"
    out = build_critic_prompt(_item(), tpl)
    assert "T=标题" in out and "TA=用法" in out and "RAW=原始摘要文本" in out


def test_parse_maps_codes_and_severity():
    raw = json.dumps(
        {
            "consistency": [{"field": "takeaway", "message": "原文没说"}],
            "ai_slop": [{"field": "hot_take", "message": "套话"}],
        }
    )
    flags = parse_critic(raw, SelfCheckConfig())
    by = {f.code: f for f in flags}
    assert by["consistency"].severity == "warn" and by["consistency"].field == "takeaway"
    assert by["ai_slop"].severity == "info"


def test_parse_truncates_message_and_caps_count():
    cfg = SelfCheckConfig(message_max_chars=5, max_flags_per_item=2)
    raw = json.dumps({"ai_slop": [{"field": "summary", "message": "一二三四五六七八"}] * 4})
    flags = parse_critic(raw, cfg)
    assert len(flags) == 2  # capped
    assert all(len(f.message) <= 5 for f in flags)


def test_parse_cap_is_per_code_not_global():
    # cap applies independently per code: 3 consistency + 3 ai_slop @ cap=3 -> 6 flags
    cfg = SelfCheckConfig(max_flags_per_item=3)
    raw = json.dumps(
        {
            "consistency": [{"field": "takeaway", "message": "c"}] * 3,
            "ai_slop": [{"field": "summary", "message": "s"}] * 3,
        }
    )
    flags = parse_critic(raw, cfg)
    assert sum(1 for f in flags if f.code == "consistency") == 3
    assert sum(1 for f in flags if f.code == "ai_slop") == 3
    assert len(flags) == 6


def test_parse_illegal_field_becomes_star():
    raw = json.dumps({"consistency": [{"field": "bogus", "message": "x"}]})
    flags = parse_critic(raw, SelfCheckConfig())
    assert flags[0].field == "*"


def test_parse_invalid_json_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_critic("not json", SelfCheckConfig())
