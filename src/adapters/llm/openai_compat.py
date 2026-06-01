from __future__ import annotations
import httpx

_BASE_URL = "https://api-inference.modelscope.cn/v1/chat/completions"


class OpenAICompatLLM:
    """Chat completion via an OpenAI-compatible endpoint (ModelScope by default).
    See docs/adr/0001-llm-openai-compatible.md."""

    def __init__(self, api_key: str, model: str, base_url: str = _BASE_URL,
                 timeout_s: int = 60):
        self._api_key = api_key
        self._model = model
        self._url = base_url
        self._timeout = timeout_s

    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(self._url, headers=headers, json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
