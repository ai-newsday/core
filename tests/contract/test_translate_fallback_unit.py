"""英文回退条目的纯翻译 (2026-09-02 用户要求): 只做语言转换, 不解读不补充信息。
任何失败保留英文原文, 不阻塞发布——同 generate_daily_head 的 fail-closed 原则。"""

from src.core.types import InterpretConfig
from src.pipeline.interpret import translate_fallback_item, translate_fallback_items
from tests.fakes import FailingLLMProvider


class _CannedLLM:
    def __init__(self, payload: str):
        self._payload = payload
        self.calls = 0

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        self.calls += 1
        return self._payload


def _fallback_item(title_en="OpenClaw 2.0 has arrived", body="Original English body."):
    from datetime import datetime, timezone

    from src.core.types import Genre, InterpretedItem, Publisher

    return InterpretedItem(
        title_en=title_en,
        link="https://x/1",
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        signals={},
        cluster_id="c1",
        related_links=[],
        score=80,
        score_breakdown={},
        title=title_en,
        body=body,
        tags=[],
        evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
    )


TPL = "T:{{title_en}} B:{{body_en}}"


def test_successful_translation_replaces_title_and_body():
    item = _fallback_item()
    llm = _CannedLLM('{"title": "OpenClaw 2.0 已发布", "body": "中文正文。"}')
    translate_fallback_item(item, TPL, llm, InterpretConfig())
    assert item.title == "OpenClaw 2.0 已发布"
    assert item.body == "中文正文。"
    assert item.interpretation_status == "extractive_fallback"  # 状态不变, 仍是回退


def test_llm_failure_keeps_english_original():
    item = _fallback_item()
    translate_fallback_item(item, TPL, FailingLLMProvider(), InterpretConfig())
    assert item.title == "OpenClaw 2.0 has arrived"
    assert item.body == "Original English body."


def test_malformed_json_keeps_english_original():
    item = _fallback_item()
    translate_fallback_item(item, TPL, _CannedLLM("not json"), InterpretConfig())
    assert item.title == "OpenClaw 2.0 has arrived"


def test_empty_title_or_body_keeps_english_original():
    item = _fallback_item()
    translate_fallback_item(item, TPL, _CannedLLM('{"title": "", "body": "x"}'), InterpretConfig())
    assert item.title == "OpenClaw 2.0 has arrived"


def test_non_fallback_item_is_untouched_and_llm_never_called():
    item = _fallback_item()
    item.interpretation_status = "ok"
    llm = _CannedLLM('{"title": "x", "body": "y"}')
    translate_fallback_item(item, TPL, llm, InterpretConfig())
    assert item.title == "OpenClaw 2.0 has arrived"
    assert llm.calls == 0


def test_translate_fallback_items_only_touches_fallback_items_in_the_list():
    ok_item = _fallback_item(title_en="OK item")
    ok_item.interpretation_status = "ok"
    fb_item = _fallback_item(title_en="Fallback item")
    llm = _CannedLLM('{"title": "已译标题", "body": "已译正文。"}')
    translate_fallback_items([ok_item, fb_item], InterpretConfig(), llm, logger=None)
    assert ok_item.title == "OK item"
    assert fb_item.title == "已译标题"
    assert llm.calls == 1
