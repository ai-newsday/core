"""公众号标题 + 摘要 (spec 2026-08-31-wechat-format-design §2/§3)。

长度必须在代码里卡死: 2026-08-31 spike 实测, prompt 里明写"必须 ≤120 字"
模型仍产出 145 字。prompt 约不住长度。"""

import pytest

from src.core.types import InterpretConfig
from src.pipeline.interpret import enforce_digest, enforce_title, generate_daily_head
from tests.fakes import FailingLLMProvider


class _CannedLLM:
    def __init__(self, payload: str):
        self._payload = payload
        self.calls = 0

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        self.calls += 1
        return self._payload


def test_title_within_limit_passes_through():
    t = "GLM-5.3成本降十倍 | 苹果PROOF-Gen重构蒸馏【AI日报】"
    assert enforce_title(t, "2026-09-01") == t


def test_overlong_title_falls_back_rather_than_truncating():
    """截断会切掉【AI日报】后缀, 留下半截标题——比朴素标题更糟。"""
    t = "标" * 80 + "【AI日报】"
    assert enforce_title(t, "2026-09-01") == "AI Daily · 2026-09-01"


def test_title_without_suffix_falls_back():
    assert enforce_title("今天有很多事发生", "2026-09-01") == "AI Daily · 2026-09-01"


def test_empty_title_falls_back():
    assert enforce_title("", "2026-09-01") == "AI Daily · 2026-09-01"


def test_digest_within_limit_passes_through():
    d = "今日亮点：A 发布了 X；B 提出了 Y。详见正文，参考链接见文末。"
    assert enforce_digest(d) == d


def test_overlong_digest_is_trimmed_to_a_sentence_boundary():
    d = "今日亮点：" + "甲乙丙丁戊己庚辛壬癸。" * 20
    out = enforce_digest(d)
    assert len(out) <= 120
    assert out.endswith("。")


def test_generate_daily_head_returns_both_fields():
    llm = _CannedLLM(
        '{"title": "A发布X | B提出Y【AI日报】",'
        ' "digest": "今日亮点：A 发布 X；B 提出 Y。详见正文，参考链接见文末。"}'
    )
    title, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-01")
    assert title == "A发布X | B提出Y【AI日报】"
    assert digest.startswith("今日亮点：")
    assert llm.calls == 1, "标题和摘要必须一次调用产出, 不是两次"


def test_generate_daily_head_enforces_limits_on_llm_output():
    llm = _CannedLLM(
        '{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：' + "甲乙丙丁。" * 40 + '"}'
    )
    title, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-01")
    assert title == "AI Daily · 2026-09-01"
    assert len(digest) <= 120


def test_generate_daily_head_fails_closed_to_plain_title_and_no_digest():
    """LLM 失败 -> 朴素标题 + 无摘要, 绝不编造(同 daily_take 的既有行为)。"""
    title, digest = generate_daily_head(
        [], "tpl {{items}}", InterpretConfig(), FailingLLMProvider(), "2026-09-01"
    )
    assert title == "AI Daily · 2026-09-01"
    assert digest is None


@pytest.mark.parametrize("raw", ["not json", "[]", "{}", '{"title": 5, "digest": null}'])
def test_generate_daily_head_survives_malformed_output(raw):
    title, digest = generate_daily_head(
        [], "tpl {{items}}", InterpretConfig(), _CannedLLM(raw), "2026-09-01"
    )
    assert title == "AI Daily · 2026-09-01"
    assert digest is None
