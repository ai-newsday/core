"""OpenAI-compatible chat completions client with multi-provider chain.

Model refs are strings of the form ``"<provider>:<model-id>"``. Bare model
IDs without a ``:`` prefix are treated as ``modelscope:<model-id>`` for
backward compatibility with pre-multi-provider callers.

See docs/adr/0001-llm-openai-compatible.md.
"""

from __future__ import annotations

import logging
import os

import httpx

from src.core.types import ProviderSpec

logger = logging.getLogger("ai-newsday")


class OpenAICompatLLM:
    def __init__(
        self,
        providers: dict[str, ProviderSpec],
        model: str,
        timeout_s: int = 60,
        fallback_models: list[str] | None = None,
    ):
        self._providers = providers
        self._model = model
        self._fallback_models = fallback_models or []
        self._timeout = timeout_s

    def _split(self, model_ref: str) -> tuple[str, str]:
        """'modelscope:foo/bar' -> ('modelscope', 'foo/bar'); 'foo/bar' -> ('modelscope', 'foo/bar')."""
        if ":" not in model_ref:
            return "modelscope", model_ref
        provider, _, model_id = model_ref.partition(":")
        return provider, model_id

    def _call(self, model_ref: str, prompt: str, *, temperature: float, max_tokens: int) -> str:
        provider, model_id = self._split(model_ref)
        spec = self._providers.get(provider)
        if spec is None:
            raise ValueError(f"unknown provider: {provider!r} (model_ref={model_ref!r})")
        api_key = os.environ.get(spec.api_key_env, "")
        if not api_key:
            raise ValueError(f"missing API key for provider {provider!r} (env {spec.api_key_env})")
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(spec.base_url, headers=headers, json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            if not content:
                raise ValueError(f"model {model_ref} returned empty content")
            return content

    def complete_json(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        models = [self._model] + self._fallback_models
        last_err: Exception | None = None
        for model_ref in models:
            try:
                result = self._call(
                    model_ref, prompt, temperature=temperature, max_tokens=max_tokens
                )
                if model_ref != self._model:
                    logger.info(
                        "LLM fallback: %s succeeded (primary %s failed)",
                        model_ref,
                        self._model,
                    )
                return result
            except Exception as e:
                logger.warning("LLM %s failed: %s", model_ref, e)
                last_err = e
        raise last_err  # type: ignore[misc]
