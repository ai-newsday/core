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
            data = r.json()
            # 响应形状不能假设。2026-09-10 实测 ModelScope 对**不可用模型**返回
            # HTTP 200 + 没有 error 字段 + choices 为 null, 而且所有字段都是零值:
            #   {"object": "", "created": 0, "system_fingerprint": "", "choices": null,
            #    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
            # 模型根本没跑。原来直接下标取值, 抛出 `'NoneType' object is not
            # subscriptable` —— 当晚 78 次, 把"这个模型已经死了"这个真实信号埋成了
            # 一句看不懂的报错。报错必须能让看日志的人直接判断该不该把模型摘掉。
            choices = data.get("choices")
            if not choices:
                usage = data.get("usage") or {}
                raise ValueError(
                    f"model {model_ref} returned no choices "
                    f"(choices={choices!r}, usage={usage}); "
                    "ModelScope 用这种零值 200 表示模型不可用, 考虑从模型链里摘掉"
                )
            choice = choices[0] or {}
            message = choice.get("message") or {}
            content = message.get("content")
            if not content:
                # 推理模型(agnes-*)的 reasoning_tokens 计入 max_tokens: 预算烧完时
                # finish_reason="length" 且一个正文 token 都没产出。这跟"模型没话说"
                # 是两种毛病, 修法也不同(前者调大 max_tokens), 报错必须能区分
                # (2026-08-30 实测 max_tokens=800: reasoning_tokens=800, text_tokens=0)。
                if choice.get("finish_reason") == "length":
                    details = (data.get("usage") or {}).get("completion_tokens_details") or {}
                    used = details.get("reasoning_tokens")
                    raise ValueError(
                        f"model {model_ref} produced no content within max_tokens={max_tokens}"
                        f" (reasoning_tokens={used}); raise max_tokens"
                    )
                raise ValueError(f"model {model_ref} returned empty content")
            return content

    def complete_json(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        validator=None,
    ) -> str:
        """Try each model in [primary, *fallback]. On success (HTTP + optional
        validator both pass), return content. On any failure — HTTP error,
        empty content, or validator raising — log warning and continue chain."""
        models = [self._model] + self._fallback_models
        last_err: Exception | None = None
        for model_ref in models:
            try:
                result = self._call(
                    model_ref, prompt, temperature=temperature, max_tokens=max_tokens
                )
                if validator is not None:
                    validator(result)  # raises → treat as model failure
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
