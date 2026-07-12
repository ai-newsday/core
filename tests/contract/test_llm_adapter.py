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
