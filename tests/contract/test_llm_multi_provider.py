import httpx
import pytest
import respx

from src.adapters.llm.openai_compat import OpenAICompatLLM
from src.core.types import ProviderSpec

PROVIDERS = {
    "modelscope": ProviderSpec(
        base_url="https://api-inference.modelscope.cn/v1/chat/completions",
        api_key_env="MODELSCOPE_API_KEY",
    ),
    "agnes": ProviderSpec(
        base_url="https://apihub.agnes-ai.com/v1/chat/completions",
        api_key_env="AGNES_API_KEY",
    ),
}


def _ok(content: str = '{"ok": true}') -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
def test_call_routes_modelscope_prefix_to_modelscope_url(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    monkeypatch.setenv("AGNES_API_KEY", "ag-key")
    route = respx.post("https://api-inference.modelscope.cn/v1/chat/completions").mock(
        return_value=_ok()
    )
    llm = OpenAICompatLLM(
        providers=PROVIDERS, model="modelscope:deepseek-ai/DeepSeek-V4-Pro", timeout_s=10
    )
    result = llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert result == '{"ok": true}'
    assert route.called
    # Verify Bearer token = ms-key, model in body = deepseek-ai/DeepSeek-V4-Pro
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer ms-key"
    import json as _json

    body = _json.loads(req.content)
    assert body["model"] == "deepseek-ai/DeepSeek-V4-Pro"


@respx.mock
def test_call_routes_agnes_prefix_to_agnes_url(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    monkeypatch.setenv("AGNES_API_KEY", "ag-key")
    route = respx.post("https://apihub.agnes-ai.com/v1/chat/completions").mock(return_value=_ok())
    llm = OpenAICompatLLM(providers=PROVIDERS, model="agnes:agnes-2.0-flash", timeout_s=10)
    llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert route.called
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer ag-key"


@respx.mock
def test_primary_fails_chain_falls_through_to_agnes(monkeypatch):
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    monkeypatch.setenv("AGNES_API_KEY", "ag-key")
    respx.post("https://api-inference.modelscope.cn/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "no provider"})
    )
    agnes_route = respx.post("https://apihub.agnes-ai.com/v1/chat/completions").mock(
        return_value=_ok('{"agnes": true}')
    )
    llm = OpenAICompatLLM(
        providers=PROVIDERS,
        model="modelscope:deepseek-ai/DeepSeek-V4-Pro",
        timeout_s=10,
        fallback_models=["agnes:agnes-2.0-flash"],
    )
    result = llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert result == '{"agnes": true}'
    assert agnes_route.called


def test_missing_api_key_raises_on_that_provider(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    llm = OpenAICompatLLM(providers=PROVIDERS, model="agnes:agnes-2.0-flash", timeout_s=10)
    with pytest.raises(Exception):
        llm.complete_json("hi", temperature=0.3, max_tokens=100)


@respx.mock
def test_bare_model_id_defaults_to_modelscope(monkeypatch):
    """Backward compat: 'foo/bar' without prefix → modelscope:foo/bar."""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    route = respx.post("https://api-inference.modelscope.cn/v1/chat/completions").mock(
        return_value=_ok()
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="deepseek-ai/DeepSeek-V4-Pro", timeout_s=10)
    llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert route.called


@respx.mock
def test_validator_failure_counts_as_model_failure(monkeypatch):
    """When primary returns 200 but validator raises, chain moves to next model."""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    monkeypatch.setenv("AGNES_API_KEY", "ag-key")
    # Primary returns truncated JSON (invalid), fallback returns valid
    respx.post("https://api-inference.modelscope.cn/v1/chat/completions").mock(
        return_value=_ok('{"title": "trunc')  # broken JSON
    )
    agnes_route = respx.post("https://apihub.agnes-ai.com/v1/chat/completions").mock(
        return_value=_ok('{"title": "good", "body": "b"}')
    )
    llm = OpenAICompatLLM(
        providers=PROVIDERS,
        model="modelscope:deepseek-ai/DeepSeek-V4-Pro",
        timeout_s=10,
        fallback_models=["agnes:agnes-2.0-flash"],
    )

    import json as _json

    def validator(raw: str):
        _json.loads(raw)  # raises on truncated

    result = llm.complete_json("hi", temperature=0.3, max_tokens=100, validator=validator)
    assert result == '{"title": "good", "body": "b"}'
    assert agnes_route.called


@respx.mock
def test_no_validator_keeps_current_behavior(monkeypatch):
    """Validator None → any 200 response is returned even if it wouldn't parse."""
    monkeypatch.setenv("MODELSCOPE_API_KEY", "ms-key")
    respx.post("https://api-inference.modelscope.cn/v1/chat/completions").mock(
        return_value=_ok("not-json")
    )
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope:foo", timeout_s=10)
    result = llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert result == "not-json"
