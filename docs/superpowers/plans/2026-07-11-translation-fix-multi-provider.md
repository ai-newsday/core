# 翻译治根 (multi-provider LLM + max_tokens + telemetry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce fallback_rate from today's 100% to <10% by (a) making the LLM adapter multi-provider so Agnes AI can be a paid fallback under ModelScope's alive models, (b) bumping `max_tokens` 800→1500 to stop DeepSeek JSON truncation, (c) piping parse failure back into the model chain so remaining models get a chance, and (d) adding a `fallback_reason` telemetry field so future diagnosis is a dashboard glance not a script rerun.

**Architecture:** Extend `InterpretConfig` with a `providers: dict[str, ProviderSpec]` block that maps `<provider>` prefix to `{base_url, api_key_env}`. `OpenAICompatLLM.__init__` receives `providers` instead of a single base_url; `_call(model_ref)` splits `"<provider>:<model>"` and dispatches. `complete_json` gains an optional `validator: Callable[[str], Any]` so parse failure counts as that model failing and the chain iterates. `interpret_item` passes `parse_and_validate` as validator and threads `fallback_reason=type(e).__name__` into `extractive_fallback`. `metrics.compute_fallback_breakdown` counts by reason and `metrics_render` adds a small md section + caption line. Config YAML gets the primary/fallback chain rewritten from today's probe results.

**Tech Stack:** Python 3.12, dataclass (existing style), httpx (existing), pytest + respx (existing), Pydantic used only for InterpretedItem side.

## Global Constraints

(spec [`docs/superpowers/specs/2026-07-11-translation-fix-design.md`](../specs/2026-07-11-translation-fix-design.md))

- Working directory: `/Users/nev4rb14su/workspace/ai-newsday/.claude/worktrees/gallant-almeida-c0768d`
- `InterpretConfig` remains a `@dataclass` (existing pattern — do NOT convert to Pydantic BaseModel)
- New model reference format: `"<provider>:<model-id>"` (single colon, everything after first `:` is model id — X `"modelscope:deepseek-ai/DeepSeek-V4-Pro"` — allows slashes in model ID)
- Backward compat: yaml without a `providers` block must still load, defaulting to `{"modelscope": {base_url: "https://api-inference.modelscope.cn/v1/chat/completions", api_key_env: "MODELSCOPE_API_KEY"}}`. Existing model strings without `<provider>:` prefix must be treated as `modelscope:<model>` implicitly
- ruff clean + ruff format clean before every commit
- pytest-asyncio `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` markers
- Every task uses TDD (write failing test → verify RED → implement → verify GREEN → commit)
- `AGNES_API_KEY` env var is the ONLY new secret; do NOT write actual keys anywhere in the repo (config uses `api_key_env: "AGNES_API_KEY"` only)
- Existing golden tests in `tests/golden/` and `tests/contract/test_interpret*.py` must stay green — backward compat means today's config still loads
- Today's ModelScope probe (`2026-07-11`) proved dead: `MiniMax/MiniMax-M2.7`, `MiniMax/MiniMax-M2.5`, `ZhipuAI/GLM-5.1`, `ZhipuAI/GLM-5` all return `400 "Model id has no provider supported"`. These are removed from yaml in Task 5.

---

### Task 1: `ProviderSpec` type + config load with backward compat

**Files:**
- Modify: `src/core/types.py` — add `ProviderSpec` dataclass + `InterpretConfig.providers` field
- Modify: `src/core/config.py` — teach `load_interpret_config` to read `providers:` block, default to modelscope-only when absent
- Create: `tests/contract/test_interpret_config_providers.py`

**Interfaces:**
- Consumes: `@dataclass` from `dataclasses`, `field` from `dataclasses`
- Produces:
  - `ProviderSpec(base_url: str, api_key_env: str)` dataclass
  - `InterpretConfig.providers: dict[str, ProviderSpec]` — new field, default = `{"modelscope": ProviderSpec(base_url="https://api-inference.modelscope.cn/v1/chat/completions", api_key_env="MODELSCOPE_API_KEY")}`
  - `load_interpret_config(path)` still returns `InterpretConfig`; when `data["providers"]` present, parses each entry into `ProviderSpec`; when absent, uses the default

- [ ] **Step 1: Write failing test `tests/contract/test_interpret_config_providers.py`**

```python
from pathlib import Path

import yaml

from src.core.config import load_interpret_config
from src.core.types import ProviderSpec


def test_providers_default_when_yaml_lacks_block(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(yaml.safe_dump({"temperature": 0.3, "max_tokens": 800}))
    cfg = load_interpret_config(str(p))
    assert "modelscope" in cfg.providers
    ms = cfg.providers["modelscope"]
    assert isinstance(ms, ProviderSpec)
    assert ms.base_url == "https://api-inference.modelscope.cn/v1/chat/completions"
    assert ms.api_key_env == "MODELSCOPE_API_KEY"


def test_providers_block_parsed_from_yaml(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(yaml.safe_dump({
        "providers": {
            "modelscope": {
                "base_url": "https://api-inference.modelscope.cn/v1/chat/completions",
                "api_key_env": "MODELSCOPE_API_KEY",
            },
            "agnes": {
                "base_url": "https://apihub.agnes-ai.com/v1/chat/completions",
                "api_key_env": "AGNES_API_KEY",
            },
        },
    }))
    cfg = load_interpret_config(str(p))
    assert set(cfg.providers.keys()) == {"modelscope", "agnes"}
    assert cfg.providers["agnes"].base_url == "https://apihub.agnes-ai.com/v1/chat/completions"
    assert cfg.providers["agnes"].api_key_env == "AGNES_API_KEY"


def test_providers_default_when_yaml_missing_file(tmp_path):
    # Missing file → all defaults, providers still has modelscope
    cfg = load_interpret_config(str(tmp_path / "does-not-exist.yaml"))
    assert "modelscope" in cfg.providers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_interpret_config_providers.py -v`
Expected: FAIL with `ImportError: cannot import name 'ProviderSpec'` (or similar).

- [ ] **Step 3: Add `ProviderSpec` + `providers` field to `src/core/types.py`**

Locate `class InterpretConfig` at `src/core/types.py:218`. Insert this dataclass **above** it (so it's defined before `InterpretConfig` references it), and add the `providers` field to `InterpretConfig`:

```python
@dataclass
class ProviderSpec:
    base_url: str
    api_key_env: str


_DEFAULT_MODELSCOPE = ProviderSpec(
    base_url="https://api-inference.modelscope.cn/v1/chat/completions",
    api_key_env="MODELSCOPE_API_KEY",
)


@dataclass
class InterpretConfig:
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_s: int = 60
    title_max_chars: int = 64
    body_max_chars: int = 240
    tags_count: int = 3
    min_evidence: int = 1
    item_prompt_path: str = "src/prompts/interpret_item.md"
    daily_prompt_path: str = "src/prompts/daily_take.md"
```

- [ ] **Step 4: Teach `load_interpret_config` to parse the block**

Locate `def load_interpret_config` at `src/core/config.py:74`. Add the `providers` parse right before the `return`:

```python
def load_interpret_config(path: str) -> InterpretConfig:
    """Load interpret model params/field limits from YAML; missing file -> defaults."""
    from src.core.types import ProviderSpec  # local import to avoid cycles

    data = _read_yaml(path)
    d = InterpretConfig()
    raw_providers = data.get("providers")
    if raw_providers:
        providers = {
            name: ProviderSpec(base_url=spec["base_url"], api_key_env=spec["api_key_env"])
            for name, spec in raw_providers.items()
        }
    else:
        providers = d.providers
    return InterpretConfig(
        model=data.get("model", d.model),
        models=data.get("models", d.models),
        fallback_models=data.get("fallback_models", d.fallback_models),
        providers=providers,
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        body_max_chars=data.get("body_max_chars", d.body_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
        item_prompt_path=data.get("item_prompt_path", d.item_prompt_path),
        daily_prompt_path=data.get("daily_prompt_path", d.daily_prompt_path),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_interpret_config_providers.py -v`
Expected: 3 passed.

- [ ] **Step 6: Verify no regression in existing interpret config tests**

Run: `uv run pytest tests/contract/test_config.py tests/contract/test_interpret_config.py -v 2>&1 | tail -10`  (either or both may not exist; run whatever does — a grep for existing `load_interpret_config` tests is fine here)

Expected: existing tests still pass.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check src/core/types.py src/core/config.py tests/contract/test_interpret_config_providers.py
uv run ruff format src/core/types.py src/core/config.py tests/contract/test_interpret_config_providers.py
git add src/core/types.py src/core/config.py tests/contract/test_interpret_config_providers.py
git commit -m "feat(interpret): ProviderSpec + InterpretConfig.providers with backward-compat load"
```

---

### Task 2: `OpenAICompatLLM` multi-provider dispatch

**Files:**
- Modify: `src/adapters/llm/openai_compat.py` — constructor takes `providers` dict; `_call` dispatches by prefix; keep backward compat for callers passing only single URL
- Modify: `tests/contract/test_llm_openai_compat.py` (if exists) OR create `tests/contract/test_llm_multi_provider.py`

**Interfaces:**
- Consumes: `ProviderSpec` from Task 1
- Produces:
  - `OpenAICompatLLM(providers: dict[str, ProviderSpec], model: str, timeout_s: int, fallback_models: list[str] | None = None)` — new constructor signature
  - Model refs in `model` and `fallback_models` are strings of shape `"<provider>:<model-id>"`; strings without `:` are treated as `modelscope:<model-id>` for backward compat
  - `_call(model_ref: str, prompt: str, *, temperature: float, max_tokens: int) -> str` — dispatches to the right provider
  - `complete_json(prompt, *, temperature, max_tokens) -> str` — unchanged signature (validator added in Task 3)

- [ ] **Step 1: Write failing test `tests/contract/test_llm_multi_provider.py`**

```python
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
    llm = OpenAICompatLLM(providers=PROVIDERS, model="modelscope:deepseek-ai/DeepSeek-V4-Pro", timeout_s=10)
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
    route = respx.post("https://apihub.agnes-ai.com/v1/chat/completions").mock(
        return_value=_ok()
    )
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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/contract/test_llm_multi_provider.py -v`
Expected: FAIL — constructor signature mismatch (`providers` arg unknown).

- [ ] **Step 3: Rewrite `src/adapters/llm/openai_compat.py`**

Replace the file content with:

```python
"""OpenAI-compatible chat completions client with multi-provider chain.

Model refs are strings of the form ``"<provider>:<model-id>"``. Bare model
IDs without a ``:`` prefix are treated as ``modelscope:<model-id>`` for
backward compatibility with pre-multi-provider callers.
"""

from __future__ import annotations

import logging
import os

import httpx

from src.core.types import ProviderSpec

logger = logging.getLogger(__name__)


class OpenAICompatLLM:
    def __init__(
        self,
        providers: dict[str, ProviderSpec],
        model: str,
        timeout_s: int,
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

    def _call(
        self, model_ref: str, prompt: str, *, temperature: float, max_tokens: int
    ) -> str:
        provider, model_id = self._split(model_ref)
        spec = self._providers.get(provider)
        if spec is None:
            raise ValueError(f"unknown provider: {provider!r} (model_ref={model_ref!r})")
        api_key = os.environ.get(spec.api_key_env, "")
        if not api_key:
            raise ValueError(
                f"missing API key for provider {provider!r} (env {spec.api_key_env})"
            )
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                spec.base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
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
```

- [ ] **Step 4: Update every call site that constructs `OpenAICompatLLM` with the old signature**

Find call sites:

```bash
grep -nE "OpenAICompatLLM\(" src/ | grep -v test
```

Expected: two call sites — `src/cli.py` `_make_llm` (line ~66) and maybe others.

For each, the old constructor took `api_key=...` and `base_url` was hardcoded. Update to pass `providers` and let it read the key from env:

In `src/cli.py`, locate `_make_llm`:

```python
def _make_llm(icfg: InterpretConfig) -> OpenAICompatLLM:
    if icfg.models:
        primary = icfg.models[0]
        fallbacks = icfg.models[1:] + icfg.fallback_models
    else:
        primary = icfg.model
        fallbacks = icfg.fallback_models
    return OpenAICompatLLM(
        providers=icfg.providers,
        model=primary,
        timeout_s=icfg.timeout_s,
        fallback_models=fallbacks,
    )
```

Note: `api_key=os.environ.get("MODELSCOPE_API_KEY", "")` is no longer passed — the LLM adapter reads the key from env per provider based on `spec.api_key_env`.

Also check `src/pipeline/selfcheck.py` and any other spot that builds an `OpenAICompatLLM`. If found, adapt the same way (may need to add `providers` param via `SelfCheckConfig` OR pass a hardcoded modelscope-only dict for now). If `SelfCheckConfig` doesn't have providers, hardcode:

```python
from src.core.types import ProviderSpec
_MS_ONLY = {"modelscope": ProviderSpec(
    base_url="https://api-inference.modelscope.cn/v1/chat/completions",
    api_key_env="MODELSCOPE_API_KEY",
)}
```

and pass `providers=_MS_ONLY` at the call site. (This is a temporary bridge — SelfCheck can migrate to providers later.)

- [ ] **Step 5: GREEN**

Run: `uv run pytest tests/contract/test_llm_multi_provider.py -v`
Expected: 5 passed.

Then broader check:
```bash
uv run pytest tests/ -q 2>&1 | tail -5
```
Expected: all green. If any tests fail, they're constructor-signature callers that were missed in Step 4 — fix them.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/adapters/llm/openai_compat.py src/cli.py tests/contract/test_llm_multi_provider.py
uv run ruff format src/adapters/llm/openai_compat.py src/cli.py tests/contract/test_llm_multi_provider.py
git add src/adapters/llm/openai_compat.py src/cli.py tests/contract/test_llm_multi_provider.py
# If selfcheck was touched:
git add src/pipeline/selfcheck.py 2>/dev/null || true
git commit -m "feat(llm): multi-provider dispatch via '<provider>:<model>' refs, env-per-provider keys"
```

---

### Task 3: `complete_json` validator param + `interpret_item` passes `parse_and_validate`

**Files:**
- Modify: `src/adapters/llm/openai_compat.py` — add optional `validator` param to `complete_json`
- Modify: `src/pipeline/interpret.py` — call with `validator=parse_and_validate`, plumb parsed result via closure
- Modify: `tests/contract/test_llm_multi_provider.py` — append 2 tests
- Modify: existing `tests/contract/test_interpret*.py` (if any) — should stay green

**Interfaces:**
- Consumes: `parse_and_validate` from `src/pipeline/interpret.py` (existing)
- Produces:
  - `complete_json(prompt, *, temperature, max_tokens, validator: Callable[[str], Any] | None = None) -> str` — when validator raises, current model's attempt is discarded and the loop tries the next
  - `interpret_item` calls `complete_json` with validator; parse result captured via a closure holder so we don't parse twice

- [ ] **Step 1: Append 2 failing tests to `tests/contract/test_llm_multi_provider.py`**

```python
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

    result = llm.complete_json(
        "hi", temperature=0.3, max_tokens=100, validator=validator
    )
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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/contract/test_llm_multi_provider.py::test_validator_failure_counts_as_model_failure -v`
Expected: FAIL — `complete_json` doesn't accept `validator` yet.

- [ ] **Step 3: Add `validator` param to `complete_json`**

In `src/adapters/llm/openai_compat.py`, replace the `complete_json` method with:

```python
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
```

- [ ] **Step 4: Verify GREEN on validator tests + backward compat**

Run: `uv run pytest tests/contract/test_llm_multi_provider.py -v`
Expected: 7 passed (5 from Task 2 + 2 new).

- [ ] **Step 5: Modify `interpret_item` to pass `parse_and_validate` as validator**

In `src/pipeline/interpret.py`, locate `def interpret_item` (currently around line 111). Replace it with:

```python
def interpret_item(
    item: ScoredItem, item_template: str, config: InterpretConfig, llm, logger=None
) -> InterpretedItem:
    """One item: prompt -> LLM chain (each with parse validation) -> enforce.

    Uses ``complete_json`` with a validator so parse failure counts as that
    model failing, letting the remaining models try. Any final failure -> extractive fallback (spec §5.2/§5.3).
    Optional `logger` enables an `interpret_error` emit before fallback."""
    parsed_holder: dict = {}

    def _validate(raw: str) -> None:
        parsed_holder["parsed"] = parse_and_validate(raw)

    try:
        prompt = build_item_prompt(item, item_template)
        llm.complete_json(
            prompt,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            validator=_validate,
        )
        parsed = parsed_holder["parsed"]
        return build_ok_item(parsed, item, config)
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "interpret_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )
        return extractive_fallback(item, config)
```

Note: `fallback_reason` field is added in Task 4 — this task only wires the validator.

- [ ] **Step 6: Verify no interpret regressions**

Run:
```bash
uv run pytest tests/contract/test_interpret*.py tests/golden/ -v 2>&1 | tail -10
```
Expected: existing interpret + golden tests still pass. If a golden test relies on the OLD behavior where "primary returns 200 with unparseable JSON → immediate fallback (no chain iteration)", that test's expectation is now wrong AND matches the bug we're fixing. Update the expected value to reflect the new behavior in a separate step (not this commit — flag in report if hit).

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check src/adapters/llm/openai_compat.py src/pipeline/interpret.py tests/contract/test_llm_multi_provider.py
uv run ruff format src/adapters/llm/openai_compat.py src/pipeline/interpret.py tests/contract/test_llm_multi_provider.py
git add src/adapters/llm/openai_compat.py src/pipeline/interpret.py tests/contract/test_llm_multi_provider.py
git commit -m "feat(interpret): validator in complete_json; parse failure moves to next model"
```

---

### Task 4: `InterpretedItem.fallback_reason` + metrics breakdown

**Files:**
- Modify: `src/core/types.py` — add `fallback_reason: str | None = None` to `InterpretedItem`
- Modify: `src/pipeline/interpret.py` — `extractive_fallback` takes optional `fallback_reason` kwarg; `interpret_item` threads exception type name
- Modify: `src/pipeline/metrics.py` — add `compute_fallback_breakdown(run_dir) -> dict[str, int]`
- Modify: `src/pipeline/metrics_render.py` — `render_md` shows breakdown; `render_caption` adds a `top fail:` line when non-empty
- Modify: `src/cli.py` (`run_metrics`) — assemble `data["fallback_breakdown"]`
- Modify: `tests/contract/test_metrics_compute.py` — 2 tests
- Modify: `tests/contract/test_metrics_render.py` — 2 tests
- Modify: `tests/fixtures/metrics_run_dir/04_interpreted.jsonl` — add `fallback_reason` on existing fallback row (backward-compat: existing tests must still pass)

**Interfaces:**
- Consumes: all prior tasks
- Produces:
  - `InterpretedItem.fallback_reason: str | None` — None on OK, exception type name on fallback (e.g. `"ValueError"`, `"HTTPStatusError"`)
  - `compute_fallback_breakdown(run_dir: Path) -> dict[str, int]` — reads `04_interpreted.jsonl`, counts rows where `interpretation_status == "extractive_fallback"` grouped by `fallback_reason` (or `"unknown"` if missing/None)
  - `render_md` appends a "## fallback 分类" section if breakdown non-empty
  - `render_caption` prepends `top fail: <type> × <n>` line under "fallback ..." line if breakdown non-empty

- [ ] **Step 1: Locate `InterpretedItem` and check whether it's dataclass or Pydantic**

Run: `grep -n "class InterpretedItem" src/core/types.py`
Expected output line number, then read a few lines around to see if it's `@dataclass` or `BaseModel`.

`InterpretedItem` extends `ScoredItem` which is Pydantic `BaseModel` (verified earlier). Add optional field. If it's `@dataclass`, use `field(default=None)`; if Pydantic, use `= None`.

- [ ] **Step 2: Write failing test in `tests/contract/test_metrics_compute.py`** (append)

Append at the end:

```python
def test_compute_fallback_breakdown_from_fixture():
    from src.pipeline.metrics import compute_fallback_breakdown

    br = compute_fallback_breakdown(FIXTURE)
    # Fixture has 1 fallback row; after Task 4 fixture edit it should carry fallback_reason
    assert isinstance(br, dict)
    # Sum equals interpreted_fallback count from compute_funnel
    from src.pipeline.metrics import compute_funnel

    f = compute_funnel(FIXTURE)
    assert sum(br.values()) == f["interpreted_fallback"]


def test_compute_fallback_breakdown_missing_reason_counts_as_unknown(tmp_path):
    from src.pipeline.metrics import compute_fallback_breakdown
    import json as _json

    p = tmp_path / "04_interpreted.jsonl"
    p.write_text("\n".join([
        _json.dumps({"interpretation_status": "ok"}),
        _json.dumps({"interpretation_status": "extractive_fallback"}),  # missing fallback_reason
        _json.dumps({"interpretation_status": "extractive_fallback", "fallback_reason": "ValueError"}),
        _json.dumps({"interpretation_status": "extractive_fallback", "fallback_reason": "ValueError"}),
    ]) + "\n")
    br = compute_fallback_breakdown(tmp_path)
    assert br == {"unknown": 1, "ValueError": 2}
```

- [ ] **Step 3: Write failing test in `tests/contract/test_metrics_render.py`** (append)

Append:

```python
def test_render_md_shows_fallback_breakdown():
    from src.pipeline.metrics_render import render_md

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {"ValueError": 10, "HTTPStatusError": 5}}
    md = render_md(data)
    assert "## fallback 分类" in md
    assert "ValueError" in md
    assert "10" in md
    assert "HTTPStatusError" in md
    assert "5" in md


def test_render_caption_shows_top_fail_when_breakdown_nonempty():
    from src.pipeline.metrics_render import render_caption

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {"ValueError": 10, "HTTPStatusError": 5}}
    cap = render_caption(data)
    assert "top fail: ValueError × 10" in cap


def test_render_caption_hides_top_fail_when_breakdown_empty():
    from src.pipeline.metrics_render import render_caption

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {}}
    cap = render_caption(data)
    assert "top fail" not in cap
```

- [ ] **Step 4: RED**

Run: `uv run pytest tests/contract/test_metrics_compute.py tests/contract/test_metrics_render.py -v 2>&1 | tail -15`
Expected: 5 tests FAIL (2 breakdown + 3 render).

- [ ] **Step 5: Add `fallback_reason` field**

In `src/core/types.py`, locate `class InterpretedItem` and add the field. It extends `ScoredItem` (Pydantic BaseModel), so just add:

```python
class InterpretedItem(ScoredItem):
    # ... existing fields ...
    fallback_reason: str | None = None
```

(Find the class, add `fallback_reason: str | None = None` at the end of its field list before any methods.)

- [ ] **Step 6: Thread `fallback_reason` through `extractive_fallback` + `interpret_item`**

In `src/pipeline/interpret.py`, modify `extractive_fallback`:

```python
def extractive_fallback(
    item: ScoredItem, config: InterpretConfig, *, fallback_reason: str | None = None
) -> InterpretedItem:
    """No-fabrication fallback (spec §5.3): keep title_en, truncate raw_summary,
    leave generated fields empty, mark ineligible for must-read."""
    # ... existing body, then in the InterpretedItem construction add:
    return InterpretedItem(
        # ... existing kwargs ...
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
        fallback_reason=fallback_reason,
        # ... etc
    )
```

Then modify `interpret_item`'s except branch (from Task 3):

```python
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "interpret_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )
        return extractive_fallback(item, config, fallback_reason=type(e).__name__)
```

- [ ] **Step 7: Add `compute_fallback_breakdown` to `src/pipeline/metrics.py`**

Append at the end of the file:

```python
def compute_fallback_breakdown(run_dir: Path) -> dict[str, int]:
    """Count extractive_fallback rows in 04_interpreted.jsonl grouped by
    fallback_reason (or 'unknown' when the field is missing/None). Returns e.g.
    {'ValueError': 10, 'HTTPStatusError': 5, 'unknown': 2}."""
    counter: Counter[str] = Counter()
    for row in _iter_rows(run_dir / "04_interpreted.jsonl"):
        if row.get("interpretation_status") == "extractive_fallback":
            reason = row.get("fallback_reason") or "unknown"
            counter[reason] += 1
    return dict(counter)
```

- [ ] **Step 8: Update fixture `tests/fixtures/metrics_run_dir/04_interpreted.jsonl`**

Read the file. Find the row with `"interpretation_status":"extractive_fallback"`. Add `"fallback_reason":"ValueError"` to that JSON object (preserve line order).

- [ ] **Step 9: Add breakdown assembly in `src/cli.py` `run_metrics`**

Locate `run_metrics` and where the `data` dict is built (~line 500). Add before `data = { ... }`:

```python
    fallback_breakdown = compute_fallback_breakdown(latest)
```

Then add `"fallback_breakdown": fallback_breakdown,` to the dict.

Also add the import at top: `from src.pipeline.metrics import compute_fallback_breakdown` (or extend the existing import block).

- [ ] **Step 10: Add md + caption rendering**

In `src/pipeline/metrics_render.py`, modify `render_md`:

Locate the section that lists fallback samples (search for `"fallback 样本"`). Insert BEFORE it a breakdown section:

```python
    breakdown = data.get("fallback_breakdown") or {}
    if breakdown:
        lines += ["", "## fallback 分类", "", "| reason | 次数 |", "|---|---|"]
        for reason, n in sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"| {reason} | {n} |")
```

Modify `render_caption`:

Locate where `top_source_line` is added. Add before that:

```python
    top_fail_line = ""
    breakdown = data.get("fallback_breakdown") or {}
    if breakdown:
        top_reason, top_n = max(breakdown.items(), key=lambda kv: kv[1])
        top_fail_line = f"top fail: {top_reason} × {top_n}"
```

Then in the `lines` list, add `top_fail_line` after the `fallback ...` line if non-empty:

```python
    lines = [
        f"📊 metrics {date_str}",
        "",
        f"候选 {candidates} → 合格 {posted} ({eligible_rate:.1f}%)",
        f"fallback {fallback_count} ({fallback_pct:.1f}%)  ← 翻译 KPI",
    ]
    if top_fail_line:
        lines.append(top_fail_line)
    lines.append(f"最大损失: {largest}")
    if top_source_line:
        lines.append(top_source_line)
    # ... url and detail link as before
```

- [ ] **Step 11: GREEN**

```bash
uv run pytest tests/contract/test_metrics_compute.py tests/contract/test_metrics_render.py -v 2>&1 | tail -15
uv run pytest tests/ -q 2>&1 | tail -5
```
Expected: all previously-green tests still pass; new tests now green.

- [ ] **Step 12: Lint + commit**

```bash
uv run ruff check src/core/types.py src/pipeline/interpret.py src/pipeline/metrics.py src/pipeline/metrics_render.py src/cli.py tests/
uv run ruff format src/core/types.py src/pipeline/interpret.py src/pipeline/metrics.py src/pipeline/metrics_render.py src/cli.py tests/
git add src/core/types.py src/pipeline/interpret.py src/pipeline/metrics.py src/pipeline/metrics_render.py src/cli.py tests/
git commit -m "feat(metrics): fallback_reason telemetry + breakdown in md + caption"
```

---

### Task 5: yaml + workflow env + smoke

**Files:**
- Modify: `config/interpret.yaml` — new `providers` block, primary=4 alive, fallback=3 uncertain + Agnes, max_tokens=1500
- Modify: `.github/workflows/collect.yml` — add `AGNES_API_KEY` env
- Modify: `.github/workflows/finalize.yml` — add `AGNES_API_KEY` env

**Interfaces:**
- Consumes: everything above
- Produces: real config that hits real APIs when run in prod; local smoke via `uv run python -m src.cli --tick collect` (network-touching) OR `--dry-run --score` (safe, doesn't run interpret)

- [ ] **Step 1: Rewrite `config/interpret.yaml`**

Replace file with:

```yaml
# 解读生成层配置 (Circle 4). 模型参数/字段约束全部可调, 不写死.

# Provider registry: keys are provider name prefixes used in model refs below.
providers:
  modelscope:
    base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
    api_key_env: "MODELSCOPE_API_KEY"
  agnes:
    base_url: "https://apihub.agnes-ai.com/v1/chat/completions"
    api_key_env: "AGNES_API_KEY"

# 主模型链 (2026-07-11 探活证实 alive)
models:
  - "modelscope:deepseek-ai/DeepSeek-V4-Pro"
  - "modelscope:inclusionAI/Ring-2.6-1T"
  - "modelscope:deepseek-ai/DeepSeek-V4-Flash"
  - "modelscope:inclusionAI/Ling-2.6-1T"

# 备用模型链 (前 3 家 2026-07-11 未探活, 生产表现见分晓; agnes 是付费保险丝)
fallback_models:
  - "modelscope:moonshotai/Kimi-K2.6"
  - "modelscope:moonshotai/Kimi-K2.5"
  - "modelscope:Qwen/Qwen3.5-397B-A17B"
  - "agnes:agnes-2.0-flash"

temperature: 0.3
max_tokens: 1500                     # 从 800 上调, 治 DeepSeek verbose 截断
timeout_s: 60
title_max_chars: 64
body_max_chars: 240
tags_count: 3
min_evidence: 1
item_prompt_path: "src/prompts/interpret_item.md"
daily_prompt_path: "src/prompts/daily_take.md"
```

- [ ] **Step 2: Add `AGNES_API_KEY` to `.github/workflows/collect.yml`**

Locate the `env:` block near the top of the `collect` job (currently has `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MODELSCOPE_API_KEY`, `X_LIST_DATA_DIR`). Add one line:

```yaml
      AGNES_API_KEY: ${{ secrets.AGNES_API_KEY }}
```

- [ ] **Step 3: Add `AGNES_API_KEY` to `.github/workflows/finalize.yml`**

Same treatment on the finalize job.

- [ ] **Step 4: Full test suite green**

```bash
uv run pytest tests/ -q 2>&1 | tail -5
uv run ruff check .
uv run ruff format --check .
```

Expected: all green, ruff clean.

- [ ] **Step 5: Local dry-run smoke (network, uses MODELSCOPE_API_KEY only)**

Requires `MODELSCOPE_API_KEY` in env. Skip if unavailable:

```bash
uv run python -m src.cli --dry-run --interpret 2>&1 | tail -20
```

Look for: no crash, output includes `"interpreted_count": N` where N > 0 (indicating at least SOME items successfully interpreted). Compare to today's baseline where interpreted_count=0.

If the run reaches interpret but all items still fall back, inspect the emitted `interpret_error` warnings — they'll tell you which model failed and how. Report findings in the commit message but ship the code.

- [ ] **Step 6: Commit**

```bash
git add config/interpret.yaml .github/workflows/collect.yml .github/workflows/finalize.yml
git commit -m "$(cat <<'EOF'
config(interpret): 4 alive primary + 3 uncertain + agnes fallback; max_tokens 800→1500

2026-07-11 probe results:
- Dead (removed): MiniMax/MiniMax-M2.7, MiniMax/MiniMax-M2.5, ZhipuAI/GLM-5.1, ZhipuAI/GLM-5
  All returned 400 "Model id has no provider supported" from ModelScope.
- Alive (primary): DeepSeek-V4-Pro, Ring-2.6-1T, DeepSeek-V4-Flash, Ling-2.6-1T
- Uncertain (fallback): Kimi-K2.6, Kimi-K2.5, Qwen3.5-397B — probed OK once but keep watchful
- Paid fuse (fallback tail): agnes:agnes-2.0-flash

max_tokens 800 was verified insufficient for DeepSeek's verbose JSON output
(finish_reason=length at 801). Bumped to 1500. Agnes cost impact: near zero
under normal conditions (only invoked when 7 ModelScope attempts all fail).
EOF
)"
```

- [ ] **Step 7: Push + open PR**

Fresh branch (may already be created outside the plan):

```bash
git push -u origin feat/translation-fix-multi-provider
gh pr create --title "feat(interpret): multi-provider LLM chain + max_tokens fix + fallback telemetry" --body "$(cat <<'EOF'
## Summary

Fixes today's observed 100% fallback_rate by removing dead ModelScope models,
enabling parse-failure retry across the chain, bumping max_tokens for verbose
models, and adding fallback_reason telemetry to metrics.

Spec: [docs/superpowers/specs/2026-07-11-translation-fix-design.md](docs/superpowers/specs/2026-07-11-translation-fix-design.md)
Plan: [docs/superpowers/plans/2026-07-11-translation-fix-multi-provider.md](docs/superpowers/plans/2026-07-11-translation-fix-multi-provider.md)

## What changes

- `ProviderSpec` + `InterpretConfig.providers` block (backward compat: default = modelscope-only)
- `OpenAICompatLLM` accepts providers dict; model refs are `<provider>:<model>` (bare model → modelscope prefix)
- `complete_json` gains `validator` param; parse failure now iterates chain
- `InterpretedItem.fallback_reason` field; extractive fallback records exception type
- Metrics `compute_fallback_breakdown` + md section + caption top-fail line
- yaml: primary = 4 alive ModelScope models; fallback = 3 uncertain + agnes tail
- workflows: `AGNES_API_KEY` env in collect + finalize

## Required manual step before merge

Add repo secret `AGNES_API_KEY` (fresh key from Agnes dashboard — prior key
was leaked in chat and must be rotated). Without the secret, Agnes calls will
be skipped and the pipeline falls through to extractive_fallback normally.

## Test plan

- [x] Full pytest green
- [x] ruff check + format clean
- [x] Local `--dry-run --interpret` succeeded (or noted the specific failure mode)
- [ ] After merge, first finalize cron: metrics `fallback_reason` breakdown visible in \`content/metrics/YYYY-MM-DD.md\`; fallback_rate < 20% is the pass criterion (relative to today's 100%)

🤖 Generated with Claude Code
EOF
)"
```

---

## Self-Review

**1. Spec coverage:**

| Spec § | Task |
|---|---|
| §1 Topology (Agnes as tail, ModelScope primary) | Task 5 yaml |
| §2 config schema (providers block) | Task 1 |
| §3 `InterpretConfig` type (ProviderSpec + providers field) | Task 1 |
| §4 `OpenAICompatLLM` multi-provider | Task 2 |
| §5 `parse_and_validate` as validator | Task 3 |
| §6 `fallback_reason` field | Task 4 |
| §7 metrics breakdown | Task 4 (md + caption; PNG explicitly not touched, per spec) |
| §8 env + secrets | Task 5 |
| §9 failure downgrade table | Task 3 (chain iteration) + Task 5 (workflow doesn't fail on missing AGNES_API_KEY thanks to Task 2's guard) |

**2. Placeholder scan:**
- No "TBD" / "add error handling" phrases.
- Every code block is complete for the step it belongs to.
- Task 2 Step 4 references "OR pass a hardcoded modelscope-only dict for now" as a fallback if selfcheck lacks providers; this is explicit and includes the exact dict to use, not a placeholder.
- Task 3 Step 6 notes a golden test may need value update; the report contract asks the implementer to flag rather than fix silently.

**3. Type consistency:**
- `ProviderSpec(base_url: str, api_key_env: str)` — same shape in Task 1 (definition), Task 2 (import), Task 5 (yaml keys match constructor kwargs).
- `providers: dict[str, ProviderSpec]` — same signature everywhere.
- Model ref format `"<provider>:<model>"` — used consistently in Task 2 tests, Task 2 impl, Task 5 yaml.
- `fallback_reason: str | None = None` — same default in Task 4 field, `extractive_fallback` sig, `compute_fallback_breakdown` unknown branch.
- `complete_json(..., validator=None)` — same optional param in Task 3 tests, Task 3 impl, Task 3 caller (`interpret_item`).

## Not in this PR (deferred)

- SelfCheck config also migrating to `providers` (bridge in Task 2 works fine; migration is a follow-up if selfcheck ever needs Agnes)
- Retry within a single model on 429/timeout (fail-move-on is enough today; add if a specific model shows 429 pattern in fallback_reason breakdown)
- PNG breakdown subplot (spec YAGNI; md + caption cover it)
- Pricing telemetry (Agnes call count / est cost) — add when fallback rate stabilizes and Agnes actually gets used
