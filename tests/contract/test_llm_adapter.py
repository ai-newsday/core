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
