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
