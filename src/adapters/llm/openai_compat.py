from __future__ import annotations

import logging

import httpx

_BASE_URL = "https://api-inference.modelscope.cn/v1/chat/completions"
logger = logging.getLogger("ai-newsday")


class OpenAICompatLLM:
    """Chat completion via an OpenAI-compatible endpoint (ModelScope by default).
    See docs/adr/0001-llm-openai-compatible.md."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = _BASE_URL,
        timeout_s: int = 60,
        fallback_models: list[str] | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._url = base_url
        self._timeout = timeout_s
        self._fallback_models = fallback_models or []

    def _call(self, model: str, prompt: str, *, temperature: float, max_tokens: int) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(self._url, headers=headers, json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            if content is None:
                raise ValueError(f"model {model} returned content=null")
            return content

    def complete_json(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        models = [self._model] + self._fallback_models
        last_err: Exception | None = None
        for model in models:
            try:
                result = self._call(model, prompt, temperature=temperature, max_tokens=max_tokens)
                if model != self._model:
                    logger.info("LLM fallback: %s succeeded (primary %s failed)", model, self._model)
                return result
            except Exception as e:
                logger.warning("LLM %s failed: %s", model, e)
                last_err = e
        raise last_err  # type: ignore[misc]
