"""公众号标题 + 摘要 (spec 2026-08-31-wechat-format-design §2/§3)。

长度必须在代码里卡死: 2026-08-31 spike 实测, prompt 里明写"必须 ≤120 字"
模型仍产出 145 字。prompt 约不住长度。"""

import json
import logging

import pytest

from src.core.prompts import load_prompt
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
    overlong = (
        '{"title": "'
        + "标" * 80
        + '【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    )
    short = '{"title": "短标题【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    llm = _SequenceLLM([overlong, short])
    title, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "短标题【AI日报】"
    assert llm.calls == 2


def test_title_within_limit_on_first_try_does_not_retry():
    ok = '{"title": "短标题【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    llm = _SequenceLLM([ok])
    title, _ = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "短标题【AI日报】"
    assert llm.calls == 1


def test_retry_also_overlong_falls_back_to_plain_title():
    """重试只给一次机会, 第二次还是不合规就老实回退, 不无限重试。"""
    overlong = (
        '{"title": "'
        + "标" * 80
        + '【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    )
    llm = _SequenceLLM([overlong, overlong])
    title, _ = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert title == "AI Daily · 2026-09-03"
    assert llm.calls == 2


def test_retry_llm_failure_falls_back_to_first_attempts_plain_title():
    """重试请求本身报错(网络/超时) -> 不让整个调用失败, 就当没重试成功处理。"""
    overlong = (
        '{"title": "'
        + "标" * 80
        + '【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    )

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
    overlong = (
        '{"title": "'
        + "标" * 80
        + '【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    )
    short = '{"title": "短标题【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    llm = _SequenceLLM([overlong, short])
    generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-03")
    assert len(llm.prompts) == 2
    assert "64" in llm.prompts[1] and "精简" in llm.prompts[1]


def test_generate_daily_head_returns_both_fields():
    llm = _CannedLLM(
        '{"title": "A发布X | B提出Y【AI日报】",'
        ' "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
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
    llm = _CannedLLM(
        '{"title": "'
        + "标" * 80
        + '【AI日报】", "digest": "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"}'
    )
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


def test_three_median_length_events_cannot_fit_the_limit():
    """把算术钉死 (#161)。

    2026-09-02/03/04 连续三晚标题回退成朴素标题, 诊断日志显示两次尝试分别是
    75 字和 88 字。根因是 prompt 的目标从"2 个事件"改成了"3 个事件":
    `【AI日报】` 占 6 字、每个 ` | ` 占 3 字, 64 字里只剩约 52 字分给事件, 而
    单个模型名就可能占 13 字(`GLM-5.3-Flash`)。三个中等长度事件必然超。

    这条测试不测 prompt 文案, 测的是**物理上装不下**——将来谁再把目标改回 3 个,
    这里会红。"""
    event = "OpenAI 推出 Daybreak 计划"  # 21 字, 真实产出里的中等长度
    title = " | ".join([event] * 3) + "【AI日报】"
    assert len(title) > 64, f"三个 {len(event)} 字事件只占 {len(title)} 字, 前提变了请重算"
    assert enforce_title(title, "2026-09-04") == "AI Daily · 2026-09-04"


def test_two_median_length_events_do_fit():
    """两个同样长度的事件放得下——这是把目标定在 2 个的依据。"""
    event = "OpenAI 推出 Daybreak 计划"
    title = " | ".join([event] * 2) + "【AI日报】"
    assert len(title) <= 64
    assert enforce_title(title, "2026-09-04") == title


def test_prompt_targets_two_events_not_three():
    """回归: prompt 的目标数量与上面的算术必须一致, 否则模型每天产出必然被拒。"""
    tpl = load_prompt("src/prompts/daily_take.md")
    assert "目标是 2 个事件" in tpl
    assert "目标是 3 个事件" not in tpl


# --- 摘要格式强制 (#174) ---

DIGEST_CLOSER = "详见正文，参考链接见文末。"


def test_digest_missing_the_closing_clause_gets_it_appended():
    """回归(2026-09-08 生产): 成品摘要停在 `；`, 没有固定收尾。

    它只有 102 字、远没到 120 上限, 所以不是被截断——是模型直接没写, 而
    enforce_digest 只校验长度不校验格式, 就放行了。跟标题当初一模一样: 只写在
    prompt 里的规则, 模型总有一定比例的日子不遵守, 必须有代码兜。"""
    d = "今日亮点：A 发布 X；B 提出 Y；C 开源 Z；"
    out = enforce_digest(d)
    assert out.endswith(DIGEST_CLOSER)
    assert "；详见正文" not in out, "补收尾前要去掉悬空的分隔符"
    assert out == "今日亮点：A 发布 X；B 提出 Y；C 开源 Z。" + DIGEST_CLOSER


def test_digest_that_already_closes_properly_is_untouched():
    d = "今日亮点：A 发布 X；B 提出 Y。" + DIGEST_CLOSER
    assert enforce_digest(d) == d


def test_repaired_digest_still_respects_the_length_limit():
    """补收尾会加 13 字, 不能因此把摘要顶过 120。"""
    d = "今日亮点：" + "甲乙丙丁戊己庚辛壬癸；" * 11
    out = enforce_digest(d)
    assert len(out) <= 120, f"补完收尾后 {len(out)} 字, 超了"
    assert out.endswith(DIGEST_CLOSER)


def test_missing_closer_is_logged_like_the_title_is(caplog):
    """静默修复等于下次还会发生却看不见——跟 daily_title_rejected 一样要留痕。"""
    import logging

    logger = logging.getLogger("test.digest.closer")
    with caplog.at_level(logging.INFO, logger="test.digest.closer"):
        enforce_digest("今日亮点：A 发布 X；", logger=logger)
    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "daily_digest_repaired"
    assert payload["reason"] == "missing_closer"


def test_empty_digest_stays_empty():
    assert enforce_digest("") == ""
    assert enforce_digest("   ") == ""


def test_prompt_and_enforcement_agree_on_the_closing_string():
    """两处必须是同一个字符串, 否则 prompt 改了措辞、代码还在补旧的, 会产出两种收尾。"""
    tpl = load_prompt("src/prompts/daily_take.md")
    assert DIGEST_CLOSER in tpl


# --- 摘要段数 (2026-09-09) ---


def _digest_json(digest, title="短标题【AI日报】"):
    return json.dumps({"title": title, "digest": digest})


def test_thin_digest_triggers_one_retry_for_more_segments():
    """2026-09-09 实测: 成品摘要只有 3 段, 96 字——上限 120, 还剩 24 字空间。
    不是塞不下, 是模型没按 prompt 的"目标 4-5 段"写。跟标题同一类问题: 只写在
    prompt 里的规则, 模型总有一定比例的日子不遵守, 得代码层面再要一次。"""
    thin = "今日亮点：A 发布 X；B 提出 Y；C 开源 Z。详见正文，参考链接见文末。"
    rich = "今日亮点：A 发布 X；B 提出 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"
    llm = _SequenceLLM([_digest_json(thin), _digest_json(rich)])
    _, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-09")
    assert digest.count("；") == 3, f"应当拿到 4 段的版本, 实际 {digest}"
    assert llm.calls == 2


def test_digest_with_enough_segments_does_not_retry():
    rich = "今日亮点：A 发 X；B 提 Y；C 开源 Z；D 上线 W。详见正文，参考链接见文末。"
    llm = _SequenceLLM([_digest_json(rich)])
    generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-09")
    assert llm.calls == 1, "已经够段数就不该多花一次调用"


def test_no_retry_when_the_digest_is_already_near_the_length_limit():
    """段数少但已经接近 120 字时不重试——那是真的塞不下, 再要一段只会被截掉。"""
    long_thin = "今日亮点：" + "甲乙丙丁戊己庚辛壬癸" * 9 + "。详见正文，参考链接见文末。"
    assert len(long_thin) > 102, f"夹具必须真的接近上限, 实际 {len(long_thin)} 字"
    llm = _SequenceLLM([_digest_json(long_thin)])
    generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-09")
    assert llm.calls == 1


def test_retry_that_comes_back_worse_keeps_the_first_digest():
    """重试只给一次机会, 回来更差就用第一次的——不能因为想要更多段反而丢掉内容。"""
    thin = "今日亮点：A 发布 X；B 提出 Y；C 开源 Z。详见正文，参考链接见文末。"
    worse = "今日亮点：A 发布 X。详见正文，参考链接见文末。"
    llm = _SequenceLLM([_digest_json(thin), _digest_json(worse)])
    _, digest = generate_daily_head([], "tpl {{items}}", InterpretConfig(), llm, "2026-09-09")
    assert digest == thin


def test_digest_separator_before_the_closer_is_normalised():
    """2026-09-09 成品: `…基金会；详见正文…`。收尾在但前面挂着分号, 之前会放行。"""
    assert enforce_digest("今日亮点：A 发 X；B 提 Y；详见正文，参考链接见文末。") == (
        "今日亮点：A 发 X；B 提 Y。详见正文，参考链接见文末。"
    )


def test_trimming_must_not_reintroduce_a_separator_before_the_closer():
    """回归(2026-09-09 生产, 是 #176 自己的 bug): 成品摘要是 `…成为白金会员；详见正文…`。

    根因不是模型写错, 是修补顺序错了: `_trim_to_sentence` 把 `；` 也当句末标点,
    所以剥收尾 -> strip 分隔符 -> **trim** 这一步会重新截在一个 `；` 上, 之后拼收尾
    就得到 `；详见正文`。分隔符必须在 trim **之后**再normalise 一次。"""
    long_thin = "今日亮点：" + "甲乙丙丁戊己庚辛壬癸；" * 12
    out = enforce_digest(long_thin)
    assert len(out) <= 120
    assert "；详见正文" not in out, f"trim 之后又冒出分隔符: ...{out[-24:]}"
    assert out.endswith("。" + DIGEST_CLOSER[:0] + DIGEST_CLOSER) or out.endswith(DIGEST_CLOSER)
    idx = out.find("详见正文")
    assert out[idx - 1] == "。", f"收尾前应当是句号, 实际 {out[idx - 1]!r}"
