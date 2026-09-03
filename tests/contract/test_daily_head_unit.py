"""公众号标题 + 摘要 (spec 2026-08-31-wechat-format-design §2/§3)。

长度必须在代码里卡死: 2026-08-31 spike 实测, prompt 里明写"必须 ≤120 字"
模型仍产出 145 字。prompt 约不住长度。"""

import json
import logging

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


def test_overlong_title_fallback_logs_the_rejected_title_and_reason(caplog):
    """回归(2026-09-02/03 生产两次撞见): enforce_title 静默回退成朴素标题时
    完全没写日志, 事后查不出 LLM 到底返回了什么、为什么没通过——只知道
    daily_take_done 报 title_generated: false, 别的什么都看不到。"""
    logger = logging.getLogger("test.daily_head.title")
    t = "标" * 80 + "【AI日报】"
    with caplog.at_level(logging.INFO, logger="test.daily_head.title"):
        out = enforce_title(t, "2026-09-01", logger=logger)
    assert out == "AI Daily · 2026-09-01"
    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "daily_title_rejected"
    assert payload["reason"] == "over_length"
    assert payload["raw"].startswith("标标标")


def test_missing_suffix_fallback_logs_that_specific_reason(caplog):
    logger = logging.getLogger("test.daily_head.title2")
    with caplog.at_level(logging.INFO, logger="test.daily_head.title2"):
        enforce_title("今天有很多事发生", "2026-09-01", logger=logger)
    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "daily_title_rejected"
    assert payload["reason"] == "missing_suffix"


def test_empty_title_fallback_logs_empty_reason(caplog):
    logger = logging.getLogger("test.daily_head.title3")
    with caplog.at_level(logging.INFO, logger="test.daily_head.title3"):
        enforce_title("", "2026-09-01", logger=logger)
    payload = json.loads(caplog.records[-1].message)
    assert payload["reason"] == "empty"


def test_title_within_limit_does_not_log_anything(caplog):
    logger = logging.getLogger("test.daily_head.title4")
    t = "GLM-5.3成本降十倍 | 苹果PROOF-Gen重构蒸馏【AI日报】"
    with caplog.at_level(logging.INFO, logger="test.daily_head.title4"):
        enforce_title(t, "2026-09-01", logger=logger)
    assert caplog.records == []


def test_enforce_title_without_logger_still_works():
    """logger 是可选的(向后兼容旧调用点), 不传时不该报错。"""
    t = "标" * 80 + "【AI日报】"
    assert enforce_title(t, "2026-09-01") == "AI Daily · 2026-09-01"


def test_digest_within_limit_passes_through():
    d = "今日亮点：A 发布了 X；B 提出了 Y。详见正文，参考链接见文末。"
    assert enforce_digest(d) == d


def test_overlong_digest_is_trimmed_to_a_sentence_boundary():
    d = "今日亮点：" + "甲乙丙丁戊己庚辛壬癸。" * 20
    out = enforce_digest(d)
    assert len(out) <= 120
    assert out.endswith("。")


class _SequenceLLM:
    """依次返回不同的 payload, 模拟"第一次超字数, 重试一次给短的"。"""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0
        self.prompts = []

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        self.prompts.append(prompt)
        self.calls += 1
        return self._payloads[min(self.calls - 1, len(self._payloads) - 1)]


def test_overlong_title_triggers_one_retry_with_shorter_result():
    """回归(2026-09-03 实测): 3 事件目标下模型经常一次性写到 100+ 字(远超 64),
    不会自己按 prompt 里"塞不下就退化"的指示重写——必须代码层面重试一次,
    否则"目标 3 个事件"在实践中几乎总是直接摆烂成朴素标题。"""
    overlong = '{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：X。"}'
    short = '{"title": "短标题【AI日报】", "digest": "今日亮点：X。"}'
    llm = _SequenceLLM([overlong, short])
    title, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "短标题【AI日报】"
    assert llm.calls == 2


def test_title_within_limit_on_first_try_does_not_retry():
    ok = '{"title": "短标题【AI日报】", "digest": "今日亮点：X。"}'
    llm = _SequenceLLM([ok])
    title, _ = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "短标题【AI日报】"
    assert llm.calls == 1


def test_retry_also_overlong_falls_back_to_plain_title():
    """重试只给一次机会, 第二次还是不合规就老实回退, 不无限重试。"""
    overlong = '{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：X。"}'
    llm = _SequenceLLM([overlong, overlong])
    title, _ = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "AI Daily · 2026-09-03"
    assert llm.calls == 2


def test_retry_llm_failure_falls_back_to_first_attempts_plain_title():
    """重试请求本身报错(网络/超时) -> 不让整个调用失败, 就当没重试成功处理。"""
    overlong = '{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：X。"}'

    class _FailOnSecond(_SequenceLLM):
        def complete_json(self, *a, **kw):
            if self.calls == 1:
                self.calls += 1
                raise RuntimeError("boom")
            return super().complete_json(*a, **kw)

    llm = _FailOnSecond([overlong])
    title, _ = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "AI Daily · 2026-09-03"


def test_retry_prompt_asks_for_a_shorter_title():
    overlong = '{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：X。"}'
    short = '{"title": "短标题【AI日报】", "digest": "今日亮点：X。"}'
    llm = _SequenceLLM([overlong, short])
    generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert len(llm.prompts) == 2
    assert "64" in llm.prompts[1] and "精简" in llm.prompts[1]


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


def test_generate_daily_head_logs_rejected_title_reason_end_to_end(caplog):
    """回归(2026-09-02/03): generate_daily_head 必须把 logger 转给 enforce_title,
    不能只在自己的 except 分支报 daily_head_error——今天两次生产回退都没进那个
    except 分支(LLM 正常返回了 JSON), 是 enforce_title 自己悄悄拒绝的, 之前
    完全没有日志能看出是这一步。"""
    logger = logging.getLogger("test.daily_head.e2e")
    llm = _CannedLLM('{"title": "' + "标" * 80 + '【AI日报】", "digest": "今日亮点：X。"}')
    with caplog.at_level(logging.INFO, logger="test.daily_head.e2e"):
        title, _ = generate_daily_head(
            [], "tpl {{items}}", InterpretConfig(), llm, "2026-09-01", logger=logger
        )
    assert title == "AI Daily · 2026-09-01"
    events = [json.loads(r.message)["event"] for r in caplog.records]
    assert "daily_title_rejected" in events


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
