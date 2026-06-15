import json
import logging
from datetime import datetime, timezone

from src.core.types import (
    Evidence,
    InterpretedItem,
    InterpretResult,
    RunContext,
    SelfCheckConfig,
    SourceType,
)
from src.pipeline.selfcheck import self_check
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 6, 16, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-selfcheck"))


def _item(link, eligible=True, status="ok", **over):
    base = dict(
        title_en="X",
        link=link,
        source="s",
        source_type=SourceType.MODEL,
        published_at=NOW,
        raw_summary="原始摘要",
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
        evidence=[Evidence(claim="f", anchor=link)],
        interpretation_status=status,
        eligible_for_must_read=eligible,
    )
    base.update(over)
    return InterpretedItem(**base)


def _result(items):
    ok = sum(1 for i in items if i.interpretation_status == "ok")
    return InterpretResult(
        interpreted_items=items,
        daily_take="看点",
        input_count=len(items),
        interpreted_count=ok,
        fallback_count=len(items) - ok,
        is_silent=False,
    )


CLEAN = json.dumps({"consistency": [], "ai_slop": []})


def test_happy_no_flags_item_unchanged():
    items = [_item("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    out = res.interpreted_items[0]
    assert out.quality_flags == [] and res.flagged_count == 0
    assert res.checked_count == 1
    assert out.model_dump(exclude={"quality_flags"}) == items[0].model_dump(
        exclude={"quality_flags"}
    )
    assert res.daily_take == "看点"


def test_consistency_and_ai_slop_flags():
    raw = json.dumps(
        {
            "consistency": [{"field": "takeaway", "message": "原文没说"}],
            "ai_slop": [{"field": "hot_take", "message": "套话"}],
        }
    )
    items = [_item("https://a/1")]
    res = self_check(
        _result(items), SelfCheckConfig(), _ctx(), FakeLLMProvider({"https://a/1": raw})
    )
    codes = sorted(f.code for f in res.interpreted_items[0].quality_flags)
    assert codes == ["ai_slop", "consistency"]
    assert res.flag_count_by_code == {"consistency": 1, "ai_slop": 1}


def test_non_eligible_skips_critic():
    items = [_item("https://a/1", eligible=False)]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    assert res.checked_count == 0
    assert llm.calls == []
    assert all(f.code != "consistency" for f in res.interpreted_items[0].quality_flags)


def test_critic_failure_no_semantic_flag():
    items = [_item("https://a/1")]
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), FailingLLMProvider())
    flags = res.interpreted_items[0].quality_flags
    assert all(f.code == "format_lock" for f in flags)
    assert res.llm_error_count == 1


def test_format_lint_runs_without_eligible_critic():
    items = [_item("https://a/1", eligible=False, tags=["#a"])]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    assert any(
        f.code == "format_lock" and f.field == "tags"
        for f in res.interpreted_items[0].quality_flags
    )
    assert llm.calls == []


def test_silent_input_skips_llm():
    empty = InterpretResult(
        interpreted_items=[],
        daily_take=None,
        input_count=0,
        interpreted_count=0,
        fallback_count=0,
        is_silent=True,
    )
    llm = FakeLLMProvider({})
    res = self_check(empty, SelfCheckConfig(), _ctx(), llm)
    assert res.is_silent and res.checked_count == 0 and llm.calls == []


def test_determinism():
    items = [_item("https://a/1")]
    llm1 = FakeLLMProvider({"https://a/1": CLEAN})
    llm2 = FakeLLMProvider({"https://a/1": CLEAN})
    r1 = self_check(_result(items), SelfCheckConfig(), _ctx(), llm1)
    r2 = self_check(_result(items), SelfCheckConfig(), _ctx(), llm2)
    assert [f.model_dump() for f in r1.interpreted_items[0].quality_flags] == [
        f.model_dump() for f in r2.interpreted_items[0].quality_flags
    ]
