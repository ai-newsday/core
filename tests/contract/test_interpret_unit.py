import json
from datetime import datetime, timezone
import pytest
from src.core.types import ScoredItem, SourceType, InterpretConfig
from src.pipeline.interpret import (build_item_prompt, parse_and_validate,
                                     build_ok_item, extractive_fallback)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(title_en="GLM-5 released", link="https://hf.co/glm5",
                source="Hugging Face", source_type=SourceType.MODEL,
                published_at=NOW, raw_summary="MoE open weights model.",
                cluster_id="evt-1", related_links=["https://blog/glm5"],
                score=88, score_breakdown={"机构影响力": 88}, is_explore=False)
    base.update(over)
    return ScoredItem(**base)


def test_build_item_prompt_substitutes_double_brace_placeholders():
    tpl = "T={{title_en}} L={{link}} R={{related_links}} S={{raw_summary}} ST={{source_type}}"
    out = build_item_prompt(_scored(), tpl)
    assert "T=GLM-5 released" in out
    assert "L=https://hf.co/glm5" in out
    assert "https://blog/glm5" in out
    assert "S=MoE open weights model." in out
    assert "ST=model" in out


def test_build_item_prompt_handles_empty_summary_and_links():
    out = build_item_prompt(_scored(raw_summary=None, related_links=[]), "S={{raw_summary}}|R={{related_links}}")
    assert "S=|" in out


def test_parse_and_validate_ok():
    assert parse_and_validate('{"title": "x"}') == {"title": "x"}


def test_parse_and_validate_rejects_non_json():
    with pytest.raises(ValueError):
        parse_and_validate("not json")


def test_parse_and_validate_rejects_non_object():
    with pytest.raises(ValueError):
        parse_and_validate('[1, 2, 3]')


def test_build_ok_item_full_fields():
    it = _scored()
    parsed = {"title": "智谱发布 GLM-5", "summary": "开源 MoE。",
              "takeaway": "可自建推理。", "hot_take": "护城河变薄。",
              "tags": ["#开源", "#MoE", "#GLM"],
              "evidence": [{"claim": "MoE 开源", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.interpretation_status == "ok"
    assert out.title == "智谱发布 GLM-5" and len(out.tags) == 3
    assert out.evidence[0].anchor == "https://hf.co/glm5"
    assert out.eligible_for_must_read is True


def test_build_ok_item_clamps_title_and_summary():
    it = _scored()
    cfg = InterpretConfig(title_max_chars=5, summary_max_chars=4)
    parsed = {"title": "0123456789", "summary": "abcdefgh", "takeaway": "t",
              "hot_take": "", "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, cfg)
    assert out.title == "01234" and out.summary == "abcd"


def test_build_ok_item_drops_illegal_anchor():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "bad", "anchor": "https://evil/elsewhere"},
                           {"claim": "good", "anchor": "https://blog/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert [e.anchor for e in out.evidence] == ["https://blog/glm5"]


def test_build_ok_item_wrong_tag_count_raises():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#only", "#two"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    with pytest.raises(ValueError):
        build_ok_item(parsed, it, InterpretConfig())


def test_build_ok_item_empty_evidence_not_eligible():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#a", "#b", "#c"], "evidence": []}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.evidence == [] and out.eligible_for_must_read is False


def test_build_ok_item_empty_takeaway_not_eligible():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "", "hot_take": "",
              "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.eligible_for_must_read is False


def test_extractive_fallback_zero_fabrication():
    it = _scored()
    out = extractive_fallback(it, InterpretConfig())
    assert out.interpretation_status == "extractive_fallback"
    assert out.title == "GLM-5 released"
    assert out.summary == "MoE open weights model."
    assert out.takeaway == "" and out.hot_take == ""
    assert out.tags == [] and out.evidence == []
    assert out.eligible_for_must_read is False


def test_extractive_fallback_truncates_summary_and_handles_none():
    it = _scored(raw_summary="x" * 200)
    assert len(extractive_fallback(it, InterpretConfig()).summary) == 120
    it2 = _scored(raw_summary=None)
    assert extractive_fallback(it2, InterpretConfig()).summary == ""
