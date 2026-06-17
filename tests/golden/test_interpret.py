import json
import logging
from datetime import datetime, timezone

from src.core.types import Genre, InterpretConfig, Publisher, RunContext, ScoredItem
from src.pipeline.interpret import interpret
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-interpret"))


def _scored(link, title_en="X released", score=80, related=None, raw="A summary."):
    return ScoredItem(
        title_en=title_en,
        link=link,
        source="src",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=raw,
        cluster_id="evt-1",
        related_links=related or [],
        score=score,
        score_breakdown={"机构影响力": float(score)},
        is_explore=False,
    )


def _ok_json(anchor):
    return json.dumps(
        {
            "title": "中文标题",
            "summary": "中文摘要。",
            "takeaway": "怎么用。",
            "hot_take": "锐评。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "事实", "anchor": anchor}],
        }
    )


# Case 1 (spec §9.1): happy full fields
def test_golden_happy_full_fields():
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider(
        {"https://a/1": _ok_json("https://a/1")}, default=json.dumps({"highlights": "看点。"})
    )
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    one = res.interpreted_items[0]
    assert one.interpretation_status == "ok"
    assert len(one.tags) == 3 and one.eligible_for_must_read is True
    assert res.interpreted_count == 1 and res.fallback_count == 0
    assert res.daily_take == "看点。"


# Case 2 (spec §9.2): wrong tag count -> fallback
def test_golden_wrong_tags_falls_back():
    bad = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "takeaway": "x",
            "hot_take": "",
            "tags": ["#one"],
            "evidence": [],
        }
    )
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": bad}, default=json.dumps({"highlights": "h"}))
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "extractive_fallback"
    assert res.interpreted_items[0].tags == []


# Case 3 (spec §9.3): total LLM failure -> all fallback, daily None
def test_golden_total_failure_all_fallback():
    items = [
        _scored("https://a/1", title_en="T1", raw="R1."),
        _scored("https://b/2", title_en="T2", raw="R2."),
    ]
    res = interpret(items, InterpretConfig(), _ctx(), FailingLLMProvider())
    assert res.fallback_count == 2 and res.interpreted_count == 0
    assert all(i.interpretation_status == "extractive_fallback" for i in res.interpreted_items)
    assert res.interpreted_items[0].title == "T1"
    assert res.interpreted_items[0].summary == "R1."
    assert res.daily_take is None
    # zero fabrication
    assert all(
        i.takeaway == "" and i.tags == [] and i.evidence == [] for i in res.interpreted_items
    )


# Case 4 (spec §9.4): evidence empty -> not must-read
def test_golden_empty_evidence_not_must_read():
    j = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "takeaway": "x",
            "hot_take": "",
            "tags": ["#a", "#b", "#c"],
            "evidence": [],
        }
    )
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "ok"
    assert res.interpreted_items[0].eligible_for_must_read is False


# Case 5 (spec §9.5): empty input -> silent, LLM not called
def test_golden_empty_input_silent_no_llm_call():
    llm = FailingLLMProvider()
    res = interpret([], InterpretConfig(), _ctx(), llm)
    assert res.is_silent is True and res.interpreted_items == []
    assert res.daily_take is None and res.input_count == 0
    assert llm.calls == []  # never called on silent


# Case 6 (spec §9.6): illegal anchor dropped + determinism
def test_golden_illegal_anchor_dropped_and_deterministic():
    j = _ok_json("https://evil/x")  # anchor not in link∪related
    items = [_scored("https://a/1", related=["https://r/1"])]
    llm = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res1 = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res1.interpreted_items[0].evidence == []
    assert res1.interpreted_items[0].eligible_for_must_read is False
    llm2 = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res2 = interpret(items, InterpretConfig(), _ctx(), llm2)
    assert [e.model_dump() for e in res2.interpreted_items[0].evidence] == []
    assert res1.interpreted_items[0].title == res2.interpreted_items[0].title
