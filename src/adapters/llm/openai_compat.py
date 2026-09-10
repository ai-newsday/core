"""OpenAI-compatible chat completions client with multi-provider chain.

Model refs are strings of the form ``"<provider>:<model-id>"``. Bare model
IDs without a ``:`` prefix are treated as ``modelscope:<model-id>`` for
backward compatibility with pre-multi-provider callers.

See docs/adr/0001-llm-openai-compatible.md.
"""

from __future__ import annotations

import logging
import os
import threading
import time

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
        retry_sleep=None,
    ):
        self._providers = providers
        self._model = model
        self._fallback_models = fallback_models or []
        self._timeout = timeout_s
        # 可注入以便测试不用真的睡
        self._sleep = retry_sleep or time.sleep
        # 记录本线程最近一次 complete_json 实际是哪个模型答的。必须按线程隔离:
        # interpret 用线程池并发, 多个线程共享同一个实例 (#153), 共享属性会串线。
        self._local = threading.local()

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

    # 短暂限流应当靠退避吸收, 不该靠"换下一个模型"兜。2026-09-10 实测 agnes 被 429
    # 了 97 次, 导致 30/60 条目解读失败; 前两晚同样并发 4 只有 0-1 次, 所以不是并发
    # 引起的。换下一个模型也兜不住: 当晚 ModelScope 链同样在 429/400 里, 而且每换一个
    # 模型就多一次注定失败的往返。所以短暂限流在出事的那一层就地扛住。
    _RATE_LIMIT_BACKOFF = (2.0, 5.0, 10.0)

    def _call_with_rate_limit_retry(self, model_ref, prompt, *, temperature, max_tokens):
        """只对 429 退避重试。400 这类确定性错误重试毫无意义, 只会拖慢每次失败。"""
        for i, wait in enumerate(self._RATE_LIMIT_BACKOFF):
            try:
                return self._call(model_ref, prompt, temperature=temperature, max_tokens=max_tokens)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    raise
                logger.info(
                    "LLM %s rate limited, backing off %.0fs (attempt %d)", model_ref, wait, i + 1
                )
                self._sleep(wait)
        return self._call(model_ref, prompt, temperature=temperature, max_tokens=max_tokens)

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
        # 先清空: 全部失败时不能残留上一次的值, 否则回退条目会被误记成某个模型写的
        self._local.model = None
        for model_ref in models:
            try:
                result = self._call_with_rate_limit_retry(
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
                self._local.model = model_ref
                return result
            except Exception as e:
                logger.warning("LLM %s failed: %s", model_ref, e)
                last_err = e
        raise last_err  # type: ignore[misc]

    def last_model(self) -> str | None:
        """本线程最近一次 complete_json 成功时实际作答的模型; 失败或未调用过为 None。

        不通过 complete_json 的返回值或新参数传出来: 仓库里 12 个测试替身只有 1 个
        接受 **kwargs, 改签名会让另外 11 个抛 TypeError。调用方用 getattr 取这个方法,
        没有它的 llm 就当作不知道。"""
        return getattr(self._local, "model", None)
