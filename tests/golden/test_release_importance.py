"""golden: judge_release_importance 用伪 LLM, 验证硬过滤 + 打分信号注入 + 容错。"""

import json
import logging
from datetime import datetime, timezone

from src.core.types import Genre, Publisher, RawItem, ReleaseImportanceConfig, RunContext
from src.pipeline.release_importance import judge_release_importance
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _release_item(title_en, raw_summary, link=None, adapter="github_releases"):
    return RawItem(
        title_en=title_en,
        link=link or f"https://github.com/x/y/releases/tag/{title_en}",
        source="x-gh",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=raw_summary,
        adapter=adapter,
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-release-importance"))


def _dims_json(scale, refactor, new_concept, bugfix_only, reason="test"):
    return json.dumps(
        {
            "scale": scale,
            "refactor": refactor,
            "new_concept": new_concept,
            "bugfix_only": bugfix_only,
            "reason": reason,
        }
    )


def test_non_release_adapter_passthrough_no_llm_call():
    items = [_release_item("v1", "some body " * 10, adapter="rss")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert out == items
    assert llm.calls == []


def test_empty_body_short_circuits_to_tier0_filtered_no_llm_call():
    items = [_release_item("v0.18.2", "**Full Changelog**: https://github.com/x/y/compare/a...b")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert out == []  # tier 0 <= hard_filter_max_tier(1) -> 剔除
    assert llm.calls == []  # 短路, 不调 LLM


def test_bugfix_only_filtered_out():
    # raw_summary 故意写够 30+ 字, 确保走到 LLM 判定路径而不是空 body 短路
    # (空 body 短路本身由另一个用例单独测, 这里要测的是 LLM 判 bugfix_only=True 之后的硬过滤)
    items = [
        _release_item("v3.0.44", "Fixed a crash that occurred when loading malformed config files.")
    ]
    llm = FakeLLMProvider({"v3.0.44": _dims_json(False, False, False, True)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认真的走了 LLM 判定, 不是空 body 短路蒙对
    assert out == []  # tier 1 <= hard_filter_max_tier(1) -> 剔除


def test_new_concept_with_scale_kept_with_tier3_score():
    items = [_release_item("v0.11.0", "Support zimage omni base model, huge refactor batch")]
    llm = FakeLLMProvider({"v0.11.0": _dims_json(True, False, True, False)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert len(out) == 1
    assert out[0].signals["release_tier_score"] == 9.0  # tier 3 -> tier_score 默认 {2:4.0,3:9.0}


def test_refactor_without_scale_kept_with_tier2_score():
    items = [
        _release_item("v0.21.0-mini", "A small but real refactor of one internal module, no scale.")
    ]
    llm = FakeLLMProvider({"v0.21.0-mini": _dims_json(False, True, False, False)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认走了 LLM 判定
    assert len(out) == 1
    assert out[0].signals["release_tier_score"] == 4.0  # tier 2


def test_llm_failure_fails_open_to_tier2_kept():
    items = [
        _release_item(
            "v9.9.9", "Some real changelog content here, long enough to skip short-circuit."
        )
    ]
    llm = FailingLLMProvider()
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认真的调用了 LLM(才失败), 不是被短路跳过
    assert len(out) == 1  # fail-open: 不硬删
    assert out[0].signals["release_tier_score"] == 4.0  # 视为 tier 2, 中性打分


def test_disabled_passthrough_no_llm_call():
    items = [_release_item("v1", "bugfix only content")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(enabled=False), _ctx())
    assert out == items
    assert llm.calls == []


def test_mixed_list_preserves_order_for_kept_items():
    items = [
        _release_item("a", "**Full Changelog**: https://x/compare/1...2"),  # tier0, 剔除
        _release_item("b", "big new concept work", adapter="rss"),  # 非 release, 透传(不检查长度)
        _release_item(
            "c", "A large refactor batch touching several core subsystems at once."
        ),  # tier3, 保留
    ]
    llm = FakeLLMProvider({"c": _dims_json(True, True, False, False)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert [i.title_en for i in out] == ["b", "c"]
