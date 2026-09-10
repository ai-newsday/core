import httpx
import pytest
import respx

from src.adapters.llm.openai_compat import OpenAICompatLLM
from src.core.types import ProviderSpec
from tests.fakes import FailingLLMProvider, FakeLLMProvider

URL = "https://api-inference.modelscope.cn/v1/chat/completions"
PROVIDERS = {
    "modelscope": ProviderSpec(base_url=URL, api_key_env="MODELSCOPE_API_KEY"),
}


@respx.mock
def test_openai_compat_returns_message_content(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"title": "ok"}'}}]}
        )
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="m")
    out = llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert out == '{"title": "ok"}'


@respx.mock
def test_openai_compat_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(return_value=httpx.Response(500))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="m")
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete_json("hi", temperature=0.3, max_tokens=100)


@respx.mock
def test_empty_content_from_exhausted_budget_names_the_cause(monkeypatch):
    """推理模型(如 agnes)的 reasoning_tokens 计入 max_tokens; 预算烧完时
    finish_reason="length" 且 content 为空。这跟"模型真的没话说"是两回事,
    报错要能区分, 否则线上只看到一句 returned empty content 无从下手
    (2026-08-30 实测: max_tokens=800 时 reasoning_tokens=800, text_tokens=0)。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 800}},
            },
        )
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="m")
    with pytest.raises(ValueError) as ei:
        llm.complete_json("hi", temperature=0.3, max_tokens=800)
    msg = str(ei.value)
    assert "max_tokens" in msg
    assert "800" in msg
    assert "reasoning" in msg


@respx.mock
def test_empty_content_without_length_finish_still_reports_empty(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        )
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="m")
    with pytest.raises(ValueError, match="returned empty content"):
        llm.complete_json("hi", temperature=0.3, max_tokens=100)


def test_fake_llm_returns_keyed_response():
    fake = FakeLLMProvider({"https://a/1": '{"x": 1}'}, default='{"y": 2}')
    assert fake.complete_json("... https://a/1 ...", temperature=0, max_tokens=1) == '{"x": 1}'
    assert fake.complete_json("no key here", temperature=0, max_tokens=1) == '{"y": 2}'
    assert len(fake.calls) == 2


def test_failing_llm_raises_and_records_calls():
    f = FailingLLMProvider()
    with pytest.raises(RuntimeError):
        f.complete_json("p", temperature=0, max_tokens=1)
    assert f.calls == ["p"]


# --- 空信封 200 (2026-09-10, #A) ---

# 2026-09-10 从 ModelScope 实测抓下来的完整响应体(只去掉 id)。三个模型
# (Ring-2.6-1T / DeepSeek-V4-Pro / Qwen3.5-397B-A17B)返回的形状完全一样:
# HTTP **200**、没有 error 字段、choices 为 null、而且所有字段都是零值 ——
# object 空串、created 0、token 全 0, 说明模型根本没跑。这是 ModelScope 对
# 不可用模型的表示方式, 不是被截断的生成。
EMPTY_ENVELOPE = {
    "object": "",
    "created": 0,
    "model": "inclusionAI/Ring-2.6-1T",
    "system_fingerprint": "",
    "choices": None,
    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
}


@respx.mock
def test_empty_envelope_raises_a_diagnosable_error(monkeypatch):
    """回归(2026-09-10 生产 78 次): 直接 `data["choices"][0]` 在 choices 为 null 时
    抛出 `'NoneType' object is not subscriptable` —— 一句看不出真因的报错, 而且被
    记成 "LLM ... failed", 把"这个模型已经死了"这个真实信号完全埋掉。

    报错必须说清是空信封, 并带上 token 计数, 这样看日志的人能直接判断该把模型
    从链里摘掉。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(return_value=httpx.Response(200, json=EMPTY_ENVELOPE))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    with pytest.raises(Exception) as ei:
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    msg = str(ei.value)
    assert "NoneType" not in msg, f"仍是不知所云的报错: {msg}"
    assert "choices" in msg or "空" in msg or "empty envelope" in msg.lower(), msg


@respx.mock
def test_missing_choices_key_is_also_handled(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(return_value=httpx.Response(200, json={"usage": {}}))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    with pytest.raises(Exception) as ei:
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert "NoneType" not in str(ei.value)


@respx.mock
def test_empty_choices_list_is_also_handled(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    with pytest.raises(Exception) as ei:
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert "IndexError" not in str(ei.value) and "NoneType" not in str(ei.value)


@respx.mock
def test_null_message_is_also_handled(monkeypatch):
    """choice 在但 message 为 null —— 同一类形状假设。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(return_value=httpx.Response(200, json={"choices": [{"message": None}]}))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    with pytest.raises(Exception) as ei:
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert "NoneType" not in str(ei.value)


# --- 429 退避重试 (2026-09-10) ---


@respx.mock
def test_rate_limited_call_is_retried_before_moving_on(monkeypatch):
    """2026-09-10 实测: agnes 被 429 了 97 次, 导致 30/60 条目解读失败。前两晚同样
    并发 4 却只有 0-1 次 429, 所以不是并发引起的, 是 agnes 侧当晚的限流。

    原实现遇 429 直接换下一个模型。用户 2026-09-10 决定不再用 ModelScope, 链上只剩
    agnes —— 没有兜底时, 一次 429 就等于这条目彻底失败。短暂限流应当靠退避重试吸收,
    而不是靠另一个 provider 兜。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":1}'}}]}),
    ]
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x", retry_sleep=lambda s: None)
    assert llm.complete_json("p", temperature=0.1, max_tokens=100) == '{"ok":1}'
    assert route.call_count == 2


@respx.mock
def test_rate_limit_retries_are_bounded(monkeypatch):
    """一直 429 不能无限重试——退避完仍失败就老实往下走/报错。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    route = respx.post(URL).mock(return_value=httpx.Response(429, json={"e": 1}))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x", retry_sleep=lambda s: None)
    with pytest.raises(Exception):
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert route.call_count <= 4, f"重试次数失控: {route.call_count}"


@respx.mock
def test_non_rate_limit_errors_are_not_retried(monkeypatch):
    """400 这类确定性错误重试毫无意义, 只会拖慢每次失败。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"e": 1}))
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x", retry_sleep=lambda s: None)
    with pytest.raises(Exception):
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert route.call_count == 1


# --- 记录产出模型 (2026-09-10, 一期一模型方案的第 1 步) ---


@respx.mock
def test_last_model_reports_which_model_actually_answered(monkeypatch):
    """ "质量参差不齐"至今无法量化: 从没记录过每条内容是哪个模型写的。
    主模型失败、备用模型成功时, 必须能知道是备用那个写的。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(400, json={"e": 1}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":1}'}}]}),
        ]
    )
    llm = OpenAICompatLLM(
        providers=PROVIDERS, model="modelscope/primary", fallback_models=["modelscope/backup"]
    )
    llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert llm.last_model() == "modelscope/backup"


@respx.mock
def test_last_model_is_none_after_total_failure(monkeypatch):
    """全部失败时不能残留上一次的值——否则回退条目会被误记成某个模型写的。"""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":1}'}}]}),
            httpx.Response(400, json={"e": 1}),
        ]
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert llm.last_model() == "modelscope/x"
    with pytest.raises(Exception):
        llm.complete_json("p", temperature=0.1, max_tokens=100)
    assert llm.last_model() is None


@respx.mock
def test_last_model_is_per_thread(monkeypatch):
    """interpret 用线程池并发, 多个线程共享同一个 llm 实例 (#153)。用共享属性记
    "刚才用了哪个模型"会串线——A 线程读到的可能是 B 线程刚写的。必须按线程隔离。"""
    import threading

    monkeypatch.setenv("MODELSCOPE_API_KEY", "k")
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":1}'}}]})
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope/x")
    llm.complete_json("p", temperature=0.1, max_tokens=100)
    seen = {}
    t = threading.Thread(target=lambda: seen.setdefault("other", llm.last_model()))
    t.start()
    t.join()
    assert llm.last_model() == "modelscope/x"
    assert seen["other"] is None, "别的线程不该看到本线程的模型"
