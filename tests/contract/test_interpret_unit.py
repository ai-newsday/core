import json
from datetime import datetime, timezone

import pytest

from src.core.types import Genre, InterpretConfig, Publisher, ScoredItem
from src.pipeline.interpret import (
    build_item_prompt,
    build_ok_item,
    extractive_fallback,
    parse_and_validate,
)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(
        title_en="GLM-5 released",
        link="https://hf.co/glm5",
        source="Hugging Face",
        genre=Genre.model,
        publisher=Publisher.company,
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


def test_build_item_prompt_substitutes_double_brace_placeholders():
    tpl = "T={{title_en}} L={{link}} R={{related_links}} S={{raw_summary}} ST={{genre}}"
    out = build_item_prompt(_scored(), tpl)
    assert "T=GLM-5 released" in out
    assert "L=https://hf.co/glm5" in out
    assert "https://blog/glm5" in out
    assert "S=MoE open weights model." in out
    assert "ST=model" in out


def test_build_item_prompt_handles_empty_summary_and_links():
    out = build_item_prompt(
        _scored(raw_summary=None, related_links=[]), "S={{raw_summary}}|R={{related_links}}"
    )
    assert "S=|" in out


def test_parse_and_validate_ok():
    assert parse_and_validate('{"title": "x"}') == {"title": "x"}


def test_parse_and_validate_rejects_non_json():
    with pytest.raises(ValueError):
        parse_and_validate("not json")


def test_parse_and_validate_rejects_non_object():
    with pytest.raises(ValueError):
        parse_and_validate("[1, 2, 3]")


def test_build_ok_item_full_fields():
    it = _scored()
    parsed = {
        "title": "智谱发布 GLM-5",
        "body": "开源 MoE，推理性能大幅提升。",
        "tags": ["#开源", "#MoE", "#GLM"],
        "evidence": [{"claim": "MoE 开源", "anchor": "https://hf.co/glm5"}],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.interpretation_status == "ok"
    assert out.title == "智谱发布 GLM-5" and len(out.tags) == 3
    assert out.evidence[0].anchor == "https://hf.co/glm5"
    assert out.eligible_for_must_read is True


def test_build_ok_item_clamps_title_and_body():
    it = _scored()
    cfg = InterpretConfig(title_max_chars=5, body_max_chars=4)
    parsed = {
        "title": "0123456789",
        "body": "abcdefgh",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
    }
    out = build_ok_item(parsed, it, cfg)
    # title still hard-sliced; body uses sentence-aware trim: no punctuation → hard cut + ellipsis
    assert out.title == "01234" and out.body == "abc…"


def test_build_ok_item_drops_illegal_anchor():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [
            {"claim": "bad", "anchor": "https://evil/elsewhere"},
            {"claim": "good", "anchor": "https://blog/glm5"},
        ],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert [e.anchor for e in out.evidence] == ["https://blog/glm5"]


def test_build_ok_item_wrong_tag_count_raises():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#only", "#two"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
    }
    with pytest.raises(ValueError):
        build_ok_item(parsed, it, InterpretConfig())


def test_build_ok_item_empty_evidence_not_eligible():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.evidence == [] and out.eligible_for_must_read is False


def test_build_ok_item_empty_body_not_eligible():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.eligible_for_must_read is False


def test_extractive_fallback_zero_fabrication():
    it = _scored()
    out = extractive_fallback(it, InterpretConfig())
    assert out.interpretation_status == "extractive_fallback"
    assert out.title == "GLM-5 released"
    assert out.body == "MoE open weights model."
    assert out.tags == [] and out.evidence == []
    assert out.eligible_for_must_read is False


def test_extractive_fallback_truncates_body_and_handles_none():
    it = _scored(raw_summary="x" * 300)
    assert len(extractive_fallback(it, InterpretConfig()).body) == 240
    it2 = _scored(raw_summary=None)
    assert extractive_fallback(it2, InterpretConfig()).body == ""


from src.core.prompts import load_prompt
from src.pipeline.interpret import build_daily_prompt, generate_daily_take, interpret_item
from tests.fakes import FailingLLMProvider, FakeLLMProvider

_OK_JSON = json.dumps(
    {
        "title": "智谱 GLM-5",
        "body": "开源 MoE，推理性能大幅提升，可自建推理。",
        "tags": ["#开源", "#MoE", "#GLM"],
        "evidence": [{"claim": "MoE", "anchor": "https://hf.co/glm5"}],
    }
)


def test_interpret_item_ok_path():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    llm = FakeLLMProvider({"https://hf.co/glm5": _OK_JSON})
    out = interpret_item(it, tpl, InterpretConfig(), llm)
    assert out.interpretation_status == "ok" and out.eligible_for_must_read is True


def test_interpret_item_llm_failure_falls_back():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    out = interpret_item(it, tpl, InterpretConfig(), FailingLLMProvider())
    assert out.interpretation_status == "extractive_fallback"
    assert out.title == "GLM-5 released"


def test_interpret_item_bad_json_falls_back():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    llm = FakeLLMProvider({"https://hf.co/glm5": "not json"})
    out = interpret_item(it, tpl, InterpretConfig(), llm)
    assert out.interpretation_status == "extractive_fallback"


def test_build_daily_prompt_uses_titles():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = "Today:\n{{items}}"
    out = build_daily_prompt([it_ok], tpl)
    assert "智谱 GLM-5" in out


def test_generate_daily_take_ok():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = load_prompt("src/prompts/daily_take.md")
    llm = FakeLLMProvider({}, default=json.dumps({"highlights": "今天开源大爆发。"}))
    assert generate_daily_take([it_ok], tpl, InterpretConfig(), llm) == "今天开源大爆发。"


def test_generate_daily_take_failure_returns_none():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = load_prompt("src/prompts/daily_take.md")
    assert generate_daily_take([it_ok], tpl, InterpretConfig(), FailingLLMProvider()) is None


def test_trim_to_sentence_cuts_at_punctuation():
    from src.pipeline.interpret import _trim_to_sentence

    assert _trim_to_sentence("第一句。第二句很长很长很长。", 6) == "第一句。"
    assert _trim_to_sentence("短句。", 50) == "短句。"
    assert _trim_to_sentence("没有标点的很长一段文字内容", 5) == "没有标点…"


def test_build_ok_item_reads_relevant_and_defaults_true():
    from src.core.types import InterpretConfig
    from src.pipeline.interpret import build_ok_item

    cfg = InterpretConfig()
    item = _scored(link="https://x/1")
    parsed = {
        "title": "T",
        "body": "正文。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://x/1"}],
        "relevant": False,
    }
    assert build_ok_item(parsed, item, cfg).relevant is False
    parsed.pop("relevant")
    assert build_ok_item(parsed, item, cfg).relevant is True
