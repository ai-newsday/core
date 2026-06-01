# Interpret / Generation Layer (Circle 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 4th pipeline layer — turn upstream `ScoreResult.selected_items` into per-item Chinese interpretations (title/summary/takeaway/hot_take draft/tags/evidence) plus one daily "今日看点", via a swappable LLM provider, with strict no-fabrication fallback.

**Architecture:** Pure helpers (`build_item_prompt`/`parse_and_validate`/`build_ok_item`/`extractive_fallback`) isolated from the only side effect (`LLMProvider.complete_json`, in `src/adapters/llm/`). Per-item try/except → extractive fallback on any failure. Orchestrator `interpret()` loads runtime prompts from `src/prompts/`, emits `runs` events, returns `InterpretResult`. Tests inject `FakeLLMProvider`/`FailingLLMProvider` for offline determinism. Spec: `docs/specs/interpret.md`.

**Tech Stack:** Python 3.12 (uv), pydantic v2, pyyaml, httpx (OpenAI-compatible chat via ModelScope), pytest + respx.

> **CRITICAL for every task:** run tests with `uv run pytest` — bare `pytest` selects the wrong interpreter and fails imports.
> **Branch:** all implementation happens on branch `circle4-interpret` (create it before Task 1: `git checkout -b circle4-interpret`). The spec is already committed to `master` (`29b431c`).

---

### Task 1: ADR + core types

**Files:**
- Create: `docs/adr/0001-llm-openai-compatible.md`
- Modify: `src/core/types.py` (append after `ScoreResult`, the last class, ~line 163)
- Test: `tests/contract/test_interpret_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_interpret_types.py`:

```python
import logging
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (RawItem, NewsItem, ScoredItem, SourceType,
                            Evidence, InterpretedItem, InterpretConfig,
                            InterpretResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(title_en="GLM-5 released", link="https://hf.co/glm5",
                source="Hugging Face", source_type=SourceType.MODEL,
                published_at=NOW, raw_summary="MoE open weights model.",
                cluster_id="evt-1", related_links=["https://blog/glm5"],
                score=88, score_breakdown={"机构影响力": 88}, is_explore=False)
    base.update(over)
    return ScoredItem(**base)


def test_interpret_config_defaults():
    c = InterpretConfig()
    assert c.title_max_chars == 64 and c.summary_max_chars == 120
    assert c.tags_count == 3 and c.min_evidence == 1
    assert c.item_prompt_path == "src/prompts/interpret_item.md"
    assert c.daily_prompt_path == "src/prompts/daily_take.md"


def test_evidence_schema():
    e = Evidence(claim="MoE open weights", anchor="https://hf.co/glm5")
    assert e.claim and e.anchor


def test_interpreted_item_extends_scored_item():
    it = _scored()
    interp = InterpretedItem(**it.model_dump(), title="智谱发布 GLM-5",
                             summary="开源 MoE 模型。", takeaway="可自建推理。",
                             hot_take="护城河又薄了。",
                             tags=["#开源", "#MoE", "#GLM"],
                             evidence=[Evidence(claim="MoE", anchor="https://hf.co/glm5")],
                             interpretation_status="ok",
                             eligible_for_must_read=True)
    # inherits ScoredItem invariants
    assert interp.score == 88 and interp.cluster_id == "evt-1"
    assert interp.is_explore is False
    assert interp.interpretation_status == "ok"
    assert interp.tags == ["#开源", "#MoE", "#GLM"]


def test_interpreted_item_defaults_for_fallback():
    it = _scored()
    interp = InterpretedItem(**it.model_dump(), title="GLM-5 released",
                             summary="MoE open weights model.", takeaway="",
                             interpretation_status="extractive_fallback",
                             eligible_for_must_read=False)
    assert interp.hot_take == "" and interp.tags == [] and interp.evidence == []


def test_interpret_result_shape():
    r = InterpretResult(interpreted_items=[], daily_take=None, input_count=0,
                        interpreted_count=0, fallback_count=0, is_silent=True)
    assert r.is_silent is True and r.daily_take is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_interpret_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'Evidence'`.

- [ ] **Step 3a: Write the ADR**

Create `docs/adr/0001-llm-openai-compatible.md`:

```markdown
# ADR 0001 — 解读层 LLM 走 OpenAI 兼容端点（而非 Anthropic 原生）

- 状态：已接受
- 日期：2026-06-01
- 关联：`docs/specs/interpret.md`、CLAUDE.md「技术栈（已锁定）· LLM：Claude」

## 背景
CLAUDE.md 锁定「LLM：Claude（便宜步用 Haiku，解读用 Sonnet），走 provider 适配器可换」。
Circle 4 解读层是首个引入 LLM 的层。

## 决策
MVP 阶段解读层真实适配器走 **OpenAI 兼容 `/chat/completions` 端点**（默认 ModelScope），
复用 embedding 已有的 `MODELSCOPE_API_KEY` 与 httpx 调用方式。

## 理由
- 复用同一套认证/SDK，降低接入与成本（embedding 已在 ModelScope）。
- `LLMProvider` 是 Protocol，业务层只依赖契约；换回 Anthropic 原生只需新增一个适配器，不动 orchestrator。
- 结构化 JSON 输出 + schema 校验 + 抽取式回退的纪律与具体厂商无关。

## 后果
- 默认模型为可配置的 OpenAI 兼容 chat 模型（`config/interpret.yaml: model`）。
- 后续如需 Anthropic 原生（Sonnet/Haiku），新增 `src/adapters/llm/anthropic.py` 实现同一 `LLMProvider` 协议即可，无需改本层逻辑。
```

- [ ] **Step 3b: Implement the types**

Append to `src/core/types.py` (after `ScoreResult`, the current last class):

```python
# --- interpret layer (Circle 4) ---
class Evidence(BaseModel):
    claim: str = Field(min_length=1)
    anchor: str = Field(min_length=1)        # must be ∈ item.link ∪ related_links


class InterpretedItem(ScoredItem):           # ScoredItem 的下游演进; 本圈加解读字段
    title: str                               # 中文标题, ≤ title_max_chars
    summary: str                             # 中文摘要, ≤ summary_max_chars
    takeaway: str                            # 对你意味着什么/怎么用; 回退时 ""
    hot_take: str = ""                       # 锐评 AI 草稿(待人工定稿)
    tags: list[str] = Field(default_factory=list)        # 恰好 tags_count 个或回退时 []
    evidence: list[Evidence] = Field(default_factory=list)
    interpretation_status: str               # "ok" | "extractive_fallback"
    eligible_for_must_read: bool


@dataclass
class InterpretConfig:
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_s: int = 60
    title_max_chars: int = 64
    summary_max_chars: int = 120
    tags_count: int = 3
    min_evidence: int = 1
    item_prompt_path: str = "src/prompts/interpret_item.md"
    daily_prompt_path: str = "src/prompts/daily_take.md"


@dataclass
class InterpretResult:
    interpreted_items: list[InterpretedItem]
    daily_take: str | None
    input_count: int
    interpreted_count: int
    fallback_count: int
    is_silent: bool
```

> `BaseModel`/`Field`/`dataclass` are already imported at the top of `types.py`. Do not re-import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_interpret_types.py -v`
Expected: PASS (5 tests). Then `uv run pytest -q` → expect 88 passed (83 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0001-llm-openai-compatible.md src/core/types.py tests/contract/test_interpret_types.py
git commit -m "feat(interpret): core types (Evidence/InterpretedItem/InterpretConfig/InterpretResult) + ADR"
```

---

### Task 2: Config loader + `config/interpret.yaml`

**Files:**
- Create: `config/interpret.yaml`
- Modify: `src/core/config.py`
- Test: `tests/contract/test_interpret_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_interpret_config.py`:

```python
from src.core.config import load_interpret_config
from src.core.types import InterpretConfig


def test_missing_file_returns_defaults():
    c = load_interpret_config("does/not/exist.yaml")
    assert isinstance(c, InterpretConfig)
    assert c.tags_count == 3 and c.min_evidence == 1


def test_loads_overrides(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(
        "model: my-model\n"
        "temperature: 0.0\n"
        "max_tokens: 500\n"
        "timeout_s: 30\n"
        "title_max_chars: 50\n"
        "summary_max_chars: 100\n"
        "tags_count: 2\n"
        "min_evidence: 2\n"
        "item_prompt_path: a.md\n"
        "daily_prompt_path: b.md\n",
        encoding="utf-8")
    c = load_interpret_config(str(p))
    assert c.model == "my-model" and c.temperature == 0.0
    assert c.max_tokens == 500 and c.timeout_s == 30
    assert c.title_max_chars == 50 and c.summary_max_chars == 100
    assert c.tags_count == 2 and c.min_evidence == 2
    assert c.item_prompt_path == "a.md" and c.daily_prompt_path == "b.md"


def test_repo_default_config_loads():
    c = load_interpret_config("config/interpret.yaml")
    assert c.tags_count == 3 and c.title_max_chars == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_interpret_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_interpret_config'`.

- [ ] **Step 3a: Create `config/interpret.yaml`**

```yaml
# 解读生成层配置 (Circle 4). 模型参数/字段约束全部可调, 不写死.
model: "Qwen/Qwen2.5-72B-Instruct"   # OpenAI 兼容 chat 模型 (ModelScope); 可换
temperature: 0.3
max_tokens: 800
timeout_s: 60
title_max_chars: 64                  # PRD §5.5 中文标题 ≤64
summary_max_chars: 120               # PRD §5.5 摘要 ≤120
tags_count: 3                        # PRD §5.5 恰好 3 个
min_evidence: 1                      # 必读门: 至少 1 条证据
item_prompt_path: "src/prompts/interpret_item.md"
daily_prompt_path: "src/prompts/daily_take.md"
```

- [ ] **Step 3b: Add the loader**

In `src/core/config.py`: extend the top import and append the function.

Change the import line:
```python
from src.core.types import DedupConfig, ScoringConfig
```
to:
```python
from src.core.types import DedupConfig, ScoringConfig, InterpretConfig
```

Append at end of file:
```python
def load_interpret_config(path: str) -> InterpretConfig:
    """Load interpret model params/field limits from YAML; missing file -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return InterpretConfig()
    d = InterpretConfig()
    return InterpretConfig(
        model=data.get("model", d.model),
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
        item_prompt_path=data.get("item_prompt_path", d.item_prompt_path),
        daily_prompt_path=data.get("daily_prompt_path", d.daily_prompt_path),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_interpret_config.py -v`
Expected: PASS (3 tests). Then `uv run pytest -q` → expect 91 passed.

- [ ] **Step 5: Commit**

```bash
git add config/interpret.yaml src/core/config.py tests/contract/test_interpret_config.py
git commit -m "feat(interpret): config loader + config/interpret.yaml"
```

---

### Task 3: Prompt loader + prompt files

**Files:**
- Create: `src/core/prompts.py`
- Create: `src/prompts/interpret_item.md`
- Create: `src/prompts/daily_take.md`
- Test: `tests/contract/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_prompts.py`:

```python
import pytest
from src.core.prompts import load_prompt


def test_load_prompt_reads_file(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("hello {{title_en}}", encoding="utf-8")
    assert load_prompt(str(p)) == "hello {{title_en}}"


def test_load_prompt_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(str(tmp_path / "nope.md"))


def test_repo_prompts_exist_and_have_placeholders():
    item = load_prompt("src/prompts/interpret_item.md")
    assert "{{title_en}}" in item and "{{raw_summary}}" in item
    assert "{{link}}" in item and "{{related_links}}" in item
    daily = load_prompt("src/prompts/daily_take.md")
    assert "{{items}}" in daily
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.prompts'`.

- [ ] **Step 3a: Implement the loader**

Create `src/core/prompts.py`:

```python
from __future__ import annotations


def load_prompt(path: str) -> str:
    """Read a prompt template verbatim (runtime-loaded SOP, not hardcoded).
    Templates use {{name}} double-brace placeholders so JSON braces are untouched."""
    with open(path, encoding="utf-8") as f:
        return f.read()
```

- [ ] **Step 3b: Create `src/prompts/interpret_item.md`**

```markdown
你是中文 AI 资讯日报的资深编辑。基于给定的英文条目信息，产出**结构化 JSON**解读。

硬约束（必须遵守）：
- 先抽取事实、再成文；只依据下方提供的信息，**不得编造**任何事实或链接。
- `title`：中文标题，简洁、可扫读，≤64 字；术语（模型名/公司名/技术名）保留英文原文。
- `summary`：中文摘要，≤120 字，说清"是什么 + 为什么重要"。
- `takeaway`：对从业者"意味着什么 / 能怎么用"，落到可操作。
- `hot_take`：一句话锐评草稿，有判断有态度、无 AI 味（口语、不堆砌形容词）。
- `tags`：恰好 3 个，每个以 # 开头。
- `evidence`：关键事实 → 原文锚点；`anchor` 只能取自下方的 link 或 related_links 之一，**不得编造锚点**。无法给出有锚点的事实时返回空数组。

只输出 JSON，结构如下（不要额外解释）：
{"title": "...", "summary": "...", "takeaway": "...", "hot_take": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}]}

条目信息：
- 英文标题: {{title_en}}
- 来源: {{source}}（类型 {{source_type}}）
- 主链接: {{link}}
- 相关链接:
{{related_links}}
- 原文摘要: {{raw_summary}}
```

- [ ] **Step 3c: Create `src/prompts/daily_take.md`**

```markdown
你是中文 AI 资讯日报的主编。基于今天入选的条目标题与摘要，写"今日看点"：3-5 句宏观趋势判断，串起今天最重要的信号，有观点、无 AI 味。

只输出 JSON（不要额外解释）：
{"highlights": "..."}

今日条目：
{{items}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_prompts.py -v`
Expected: PASS (3 tests). Then `uv run pytest -q` → expect 94 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/prompts.py src/prompts/interpret_item.md src/prompts/daily_take.md tests/contract/test_prompts.py
git commit -m "feat(interpret): prompt loader + interpret_item/daily_take templates"
```

---

### Task 4: `LLMProvider` protocol + `OpenAICompatLLM` adapter + fakes

**Files:**
- Create: `src/adapters/llm/__init__.py` (empty)
- Create: `src/adapters/llm/base.py`
- Create: `src/adapters/llm/openai_compat.py`
- Modify: `tests/fakes.py` (append fakes)
- Test: `tests/contract/test_llm_adapter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_llm_adapter.py`:

```python
import httpx, respx, pytest
from src.adapters.llm.openai_compat import OpenAICompatLLM
from tests.fakes import FakeLLMProvider, FailingLLMProvider

URL = "https://api-inference.modelscope.cn/v1/chat/completions"


@respx.mock
def test_openai_compat_returns_message_content():
    respx.post(URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": '{"title": "ok"}'}}]}))
    llm = OpenAICompatLLM(api_key="k", model="m")
    out = llm.complete_json("hi", temperature=0.3, max_tokens=100)
    assert out == '{"title": "ok"}'


@respx.mock
def test_openai_compat_raises_on_http_error():
    respx.post(URL).mock(return_value=httpx.Response(500))
    llm = OpenAICompatLLM(api_key="k", model="m")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_llm_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.adapters.llm'`.

- [ ] **Step 3a: Create the package + protocol**

Create empty `src/adapters/llm/__init__.py`.

Create `src/adapters/llm/base.py`:
```python
from typing import Protocol


class LLMProvider(Protocol):
    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        """Return the model's raw text completion (expected to be JSON).
        Raise to signal a provider/network failure (caller falls back)."""
        ...
```

- [ ] **Step 3b: Create the real adapter**

Create `src/adapters/llm/openai_compat.py`:
```python
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
```

- [ ] **Step 3c: Append fakes to `tests/fakes.py`**

```python
class FakeLLMProvider:
    """Returns canned JSON strings keyed by a substring of the prompt (e.g. the
    item's link). No key match -> `default`, or KeyError if default is None.
    Records every prompt in `.calls` for assertions (e.g. not-called on silent)."""

    def __init__(self, by_substring: dict[str, str], default: str | None = None):
        self._map = by_substring
        self._default = default
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        self.calls.append(prompt)
        for key, resp in self._map.items():
            if key in prompt:
                return resp
        if self._default is not None:
            return self._default
        raise KeyError("FakeLLMProvider: no canned response for prompt")


class FailingLLMProvider:
    """Simulates total LLM failure -> every item falls back to extractive."""

    def __init__(self):
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        self.calls.append(prompt)
        raise RuntimeError("llm provider unavailable")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_llm_adapter.py -v`
Expected: PASS (4 tests). Then `uv run pytest -q` → expect 98 passed.

- [ ] **Step 5: Commit**

```bash
git add src/adapters/llm/__init__.py src/adapters/llm/base.py src/adapters/llm/openai_compat.py tests/fakes.py tests/contract/test_llm_adapter.py
git commit -m "feat(interpret): LLMProvider protocol + OpenAICompatLLM adapter + fakes"
```

---

### Task 5: Pure helpers (prompt build, parse, constraints, fallback)

**Files:**
- Create: `src/pipeline/interpret.py`
- Test: `tests/contract/test_interpret_unit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_interpret_unit.py`:

```python
import json
from datetime import datetime, timezone
import pytest
from src.core.types import ScoredItem, SourceType, InterpretConfig
from src.pipeline.interpret import (build_item_prompt, parse_and_validate,
                                     build_ok_item, extractive_fallback)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(title_en="GLM-5 released", link="https://hf.co/glm5",
                source="Hugging Face", source_type=SourceType.MODEL,
                published_at=NOW, raw_summary="MoE open weights model.",
                cluster_id="evt-1", related_links=["https://blog/glm5"],
                score=88, score_breakdown={"机构影响力": 88}, is_explore=False)
    base.update(over)
    return ScoredItem(**base)


def test_build_item_prompt_substitutes_double_brace_placeholders():
    tpl = "T={{title_en}} L={{link}} R={{related_links}} S={{raw_summary}} ST={{source_type}}"
    out = build_item_prompt(_scored(), tpl)
    assert "T=GLM-5 released" in out
    assert "L=https://hf.co/glm5" in out
    assert "https://blog/glm5" in out
    assert "S=MoE open weights model." in out
    assert "ST=model" in out


def test_build_item_prompt_handles_empty_summary_and_links():
    out = build_item_prompt(_scored(raw_summary=None, related_links=[]), "S={{raw_summary}}|R={{related_links}}")
    assert "S=|" in out


def test_parse_and_validate_ok():
    assert parse_and_validate('{"title": "x"}') == {"title": "x"}


def test_parse_and_validate_rejects_non_json():
    with pytest.raises(ValueError):
        parse_and_validate("not json")


def test_parse_and_validate_rejects_non_object():
    with pytest.raises(ValueError):
        parse_and_validate('[1, 2, 3]')


def test_build_ok_item_full_fields():
    it = _scored()
    parsed = {"title": "智谱发布 GLM-5", "summary": "开源 MoE。",
              "takeaway": "可自建推理。", "hot_take": "护城河变薄。",
              "tags": ["#开源", "#MoE", "#GLM"],
              "evidence": [{"claim": "MoE 开源", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.interpretation_status == "ok"
    assert out.title == "智谱发布 GLM-5" and len(out.tags) == 3
    assert out.evidence[0].anchor == "https://hf.co/glm5"
    assert out.eligible_for_must_read is True


def test_build_ok_item_clamps_title_and_summary():
    it = _scored()
    cfg = InterpretConfig(title_max_chars=5, summary_max_chars=4)
    parsed = {"title": "0123456789", "summary": "abcdefgh", "takeaway": "t",
              "hot_take": "", "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, cfg)
    assert out.title == "01234" and out.summary == "abcd"


def test_build_ok_item_drops_illegal_anchor():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "bad", "anchor": "https://evil/elsewhere"},
                           {"claim": "good", "anchor": "https://blog/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert [e.anchor for e in out.evidence] == ["https://blog/glm5"]


def test_build_ok_item_wrong_tag_count_raises():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#only", "#two"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    with pytest.raises(ValueError):
        build_ok_item(parsed, it, InterpretConfig())


def test_build_ok_item_empty_evidence_not_eligible():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
              "tags": ["#a", "#b", "#c"], "evidence": []}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.evidence == [] and out.eligible_for_must_read is False


def test_build_ok_item_empty_takeaway_not_eligible():
    it = _scored()
    parsed = {"title": "t", "summary": "s", "takeaway": "", "hot_take": "",
              "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}]}
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.eligible_for_must_read is False


def test_extractive_fallback_zero_fabrication():
    it = _scored()
    out = extractive_fallback(it, InterpretConfig())
    assert out.interpretation_status == "extractive_fallback"
    assert out.title == "GLM-5 released"
    assert out.summary == "MoE open weights model."
    assert out.takeaway == "" and out.hot_take == ""
    assert out.tags == [] and out.evidence == []
    assert out.eligible_for_must_read is False


def test_extractive_fallback_truncates_summary_and_handles_none():
    it = _scored(raw_summary="x" * 200)
    assert len(extractive_fallback(it, InterpretConfig()).summary) == 120
    it2 = _scored(raw_summary=None)
    assert extractive_fallback(it2, InterpretConfig()).summary == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.interpret'`.

- [ ] **Step 3: Implement the pure helpers**

Create `src/pipeline/interpret.py`:
```python
from __future__ import annotations
import json
from src.core.types import (ScoredItem, InterpretedItem, Evidence, InterpretConfig)


def build_item_prompt(item: ScoredItem, template: str) -> str:
    """Render the per-item prompt by substituting {{name}} placeholders.
    Double-brace placeholders avoid clashing with JSON braces in the template."""
    related = "\n".join(item.related_links)
    repl = {
        "{{title_en}}": item.title_en,
        "{{source}}": item.source,
        "{{source_type}}": item.source_type.value,
        "{{link}}": item.link,
        "{{related_links}}": related,
        "{{raw_summary}}": item.raw_summary or "",
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def parse_and_validate(raw: str) -> dict:
    """Parse a JSON object string. Raises ValueError on invalid/non-object JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON LLM output: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    return data


def _filter_evidence(raw_evidence, item: ScoredItem) -> list[Evidence]:
    allowed = {item.link, *item.related_links}
    out: list[Evidence] = []
    for e in raw_evidence or []:
        if not isinstance(e, dict):
            continue
        claim = str(e.get("claim", "")).strip()
        anchor = str(e.get("anchor", "")).strip()
        if claim and anchor in allowed:
            out.append(Evidence(claim=claim, anchor=anchor))
    return out


def build_ok_item(parsed: dict, item: ScoredItem,
                  config: InterpretConfig) -> InterpretedItem:
    """Enforce field constraints (spec §5.2) and build an 'ok' InterpretedItem.
    Raises ValueError if tags count != config.tags_count (caller falls back)."""
    tags = parsed.get("tags")
    if not isinstance(tags, list) or len(tags) != config.tags_count:
        raise ValueError("tags count not met")
    title = str(parsed.get("title", ""))[:config.title_max_chars]
    summary = str(parsed.get("summary", ""))[:config.summary_max_chars]
    takeaway = str(parsed.get("takeaway", ""))
    hot_take = str(parsed.get("hot_take", ""))
    evidence = _filter_evidence(parsed.get("evidence"), item)
    eligible = bool(takeaway) and len(evidence) >= config.min_evidence
    return InterpretedItem(
        **item.model_dump(), title=title, summary=summary, takeaway=takeaway,
        hot_take=hot_take, tags=[str(t) for t in tags], evidence=evidence,
        interpretation_status="ok", eligible_for_must_read=eligible)


def extractive_fallback(item: ScoredItem,
                        config: InterpretConfig) -> InterpretedItem:
    """No-fabrication fallback (spec §5.3): keep title_en, truncate raw_summary,
    leave generated fields empty, mark ineligible for must-read."""
    return InterpretedItem(
        **item.model_dump(), title=item.title_en,
        summary=(item.raw_summary or "")[:config.summary_max_chars],
        takeaway="", hot_take="", tags=[], evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: PASS (13 tests). Then `uv run pytest -q` → expect 111 passed.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py
git commit -m "feat(interpret): pure helpers (prompt build, parse, constraints, fallback)"
```

---

### Task 6: Per-item glue `interpret_item` + daily-take helpers

**Files:**
- Modify: `src/pipeline/interpret.py` (append)
- Test: `tests/contract/test_interpret_unit.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_interpret_unit.py`:

```python
from src.pipeline.interpret import (interpret_item, build_daily_prompt,
                                     generate_daily_take)
from src.core.prompts import load_prompt
from tests.fakes import FakeLLMProvider, FailingLLMProvider

_OK_JSON = json.dumps({"title": "智谱 GLM-5", "summary": "开源 MoE。",
                       "takeaway": "可自建推理。", "hot_take": "变薄了。",
                       "tags": ["#开源", "#MoE", "#GLM"],
                       "evidence": [{"claim": "MoE", "anchor": "https://hf.co/glm5"}]})


def test_interpret_item_ok_path():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    llm = FakeLLMProvider({"https://hf.co/glm5": _OK_JSON})
    out = interpret_item(it, tpl, InterpretConfig(), llm)
    assert out.interpretation_status == "ok" and out.eligible_for_must_read is True


def test_interpret_item_llm_failure_falls_back():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    out = interpret_item(it, tpl, InterpretConfig(), FailingLLMProvider())
    assert out.interpretation_status == "extractive_fallback"
    assert out.title == "GLM-5 released"


def test_interpret_item_bad_json_falls_back():
    it = _scored()
    tpl = load_prompt("src/prompts/interpret_item.md")
    llm = FakeLLMProvider({"https://hf.co/glm5": "not json"})
    out = interpret_item(it, tpl, InterpretConfig(), llm)
    assert out.interpretation_status == "extractive_fallback"


def test_build_daily_prompt_uses_titles():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = "Today:\n{{items}}"
    out = build_daily_prompt([it_ok], tpl)
    assert "智谱 GLM-5" in out


def test_generate_daily_take_ok():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = load_prompt("src/prompts/daily_take.md")
    llm = FakeLLMProvider({}, default=json.dumps({"highlights": "今天开源大爆发。"}))
    assert generate_daily_take([it_ok], tpl, InterpretConfig(), llm) == "今天开源大爆发。"


def test_generate_daily_take_failure_returns_none():
    it_ok = build_ok_item(json.loads(_OK_JSON), _scored(), InterpretConfig())
    tpl = load_prompt("src/prompts/daily_take.md")
    assert generate_daily_take([it_ok], tpl, InterpretConfig(), FailingLLMProvider()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: FAIL with `ImportError: cannot import name 'interpret_item'`.

- [ ] **Step 3: Implement the glue**

Append to `src/pipeline/interpret.py`:
```python
from src.core.types import RunContext  # noqa: E402  (grouped import; see note)


def interpret_item(item: ScoredItem, item_template: str, config: InterpretConfig,
                   llm) -> InterpretedItem:
    """One item: prompt -> LLM -> parse -> enforce. Any failure -> extractive
    fallback (spec §5.2/§5.3). Pure except for the injected llm call."""
    try:
        prompt = build_item_prompt(item, item_template)
        raw = llm.complete_json(prompt, temperature=config.temperature,
                                max_tokens=config.max_tokens)
        parsed = parse_and_validate(raw)
        return build_ok_item(parsed, item, config)
    except Exception:
        return extractive_fallback(item, config)


def build_daily_prompt(items: list[InterpretedItem], template: str) -> str:
    """Render the daily-take prompt from interpreted items' titles + summaries."""
    lines = []
    for it in items:
        title = it.title if it.interpretation_status == "ok" else it.title_en
        lines.append(f"- {title}: {it.summary}")
    return template.replace("{{items}}", "\n".join(lines))


def generate_daily_take(items: list[InterpretedItem], daily_template: str,
                        config: InterpretConfig, llm) -> str | None:
    """One LLM call for the macro '今日看点'. Any failure -> None (no fabrication)."""
    try:
        prompt = build_daily_prompt(items, daily_template)
        raw = llm.complete_json(prompt, temperature=config.temperature,
                                max_tokens=config.max_tokens)
        data = json.loads(raw)
        text = data.get("highlights", "") if isinstance(data, dict) else ""
        return text or None
    except Exception:
        return None
```

> Note: move `RunContext` into the top import group with the other `src.core.types` names instead of a separate line if the implementer prefers — it is used by the orchestrator in Task 7. Either placement is fine; keep one import only.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: PASS (19 tests in file). Then `uv run pytest -q` → expect 117 passed.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py
git commit -m "feat(interpret): per-item glue + daily-take helpers"
```

---

### Task 7: `interpret()` orchestrator + golden cases

**Files:**
- Modify: `src/pipeline/interpret.py` (append orchestrator; finalize imports)
- Create: `tests/golden/test_interpret.py`
- Test: `tests/golden/test_interpret.py`

- [ ] **Step 1: Write the failing golden test**

Create `tests/golden/test_interpret.py`:

```python
import json, logging
from datetime import datetime, timezone
from src.core.types import ScoredItem, SourceType, RunContext, InterpretConfig
from src.pipeline.interpret import interpret
from tests.fakes import FakeLLMProvider, FailingLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-interpret"))


def _scored(link, title_en="X released", score=80, related=None, raw="A summary."):
    return ScoredItem(title_en=title_en, link=link, source="src",
                      source_type=SourceType.MODEL, published_at=NOW,
                      raw_summary=raw, cluster_id="evt-1",
                      related_links=related or [], score=score,
                      score_breakdown={"机构影响力": float(score)}, is_explore=False)


def _ok_json(anchor):
    return json.dumps({"title": "中文标题", "summary": "中文摘要。",
                       "takeaway": "怎么用。", "hot_take": "锐评。",
                       "tags": ["#a", "#b", "#c"],
                       "evidence": [{"claim": "事实", "anchor": anchor}]})


# Case 1 (spec §9.1): happy full fields
def test_golden_happy_full_fields():
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": _ok_json("https://a/1")},
                          default=json.dumps({"highlights": "看点。"}))
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    one = res.interpreted_items[0]
    assert one.interpretation_status == "ok"
    assert len(one.tags) == 3 and one.eligible_for_must_read is True
    assert res.interpreted_count == 1 and res.fallback_count == 0
    assert res.daily_take == "看点。"


# Case 2 (spec §9.2): wrong tag count -> fallback
def test_golden_wrong_tags_falls_back():
    bad = json.dumps({"title": "t", "summary": "s", "takeaway": "x",
                      "hot_take": "", "tags": ["#one"], "evidence": []})
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": bad}, default=json.dumps({"highlights": "h"}))
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "extractive_fallback"
    assert res.interpreted_items[0].tags == []


# Case 3 (spec §9.3): total LLM failure -> all fallback, daily None
def test_golden_total_failure_all_fallback():
    items = [_scored("https://a/1", title_en="T1", raw="R1."),
             _scored("https://b/2", title_en="T2", raw="R2.")]
    res = interpret(items, InterpretConfig(), _ctx(), FailingLLMProvider())
    assert res.fallback_count == 2 and res.interpreted_count == 0
    assert all(i.interpretation_status == "extractive_fallback" for i in res.interpreted_items)
    assert res.interpreted_items[0].title == "T1"
    assert res.interpreted_items[0].summary == "R1."
    assert res.daily_take is None
    # zero fabrication
    assert all(i.takeaway == "" and i.tags == [] and i.evidence == [] for i in res.interpreted_items)


# Case 4 (spec §9.4): evidence empty -> not must-read
def test_golden_empty_evidence_not_must_read():
    j = json.dumps({"title": "t", "summary": "s", "takeaway": "x", "hot_take": "",
                    "tags": ["#a", "#b", "#c"], "evidence": []})
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "ok"
    assert res.interpreted_items[0].eligible_for_must_read is False


# Case 5 (spec §9.5): empty input -> silent, LLM not called
def test_golden_empty_input_silent_no_llm_call():
    llm = FailingLLMProvider()
    res = interpret([], InterpretConfig(), _ctx(), llm)
    assert res.is_silent is True and res.interpreted_items == []
    assert res.daily_take is None and res.input_count == 0
    assert llm.calls == []                       # never called on silent


# Case 6 (spec §9.6): illegal anchor dropped + determinism
def test_golden_illegal_anchor_dropped_and_deterministic():
    j = _ok_json("https://evil/x")               # anchor not in link∪related
    items = [_scored("https://a/1", related=["https://r/1"])]
    llm = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res1 = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res1.interpreted_items[0].evidence == []
    assert res1.interpreted_items[0].eligible_for_must_read is False
    llm2 = FakeLLMProvider({"https://a/1": j}, default=json.dumps({"highlights": "h"}))
    res2 = interpret(items, InterpretConfig(), _ctx(), llm2)
    assert [e.model_dump() for e in res2.interpreted_items[0].evidence] == []
    assert res1.interpreted_items[0].title == res2.interpreted_items[0].title
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_interpret.py -v`
Expected: FAIL with `ImportError: cannot import name 'interpret'`.

- [ ] **Step 3: Implement the orchestrator**

Append to `src/pipeline/interpret.py`:
```python
from src.core.prompts import load_prompt
from src.core.types import InterpretResult
from src.observability.events import emit


def interpret(items: list[ScoredItem], config: InterpretConfig, ctx: RunContext,
              llm) -> InterpretResult:
    """Orchestrate per-item interpretation + daily take (spec §3, §5, §11).
    Only side effect is the injected llm; everything else is pure/testable."""
    emit(ctx.logger, "interpret_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "interpret_done", input_count=0, interpreted_count=0,
             fallback_count=0, silent=True)
        return InterpretResult(interpreted_items=[], daily_take=None,
                               input_count=0, interpreted_count=0,
                               fallback_count=0, is_silent=True)

    item_tpl = load_prompt(config.item_prompt_path)
    out: list[InterpretedItem] = []
    for it in items:
        res = interpret_item(it, item_tpl, config, llm)
        emit(ctx.logger, "item_interpreted", link=res.link,
             status=res.interpretation_status, evidence_count=len(res.evidence))
        if res.interpretation_status == "extractive_fallback":
            emit(ctx.logger, "interpret_fallback", link=res.link)
        out.append(res)

    daily_tpl = load_prompt(config.daily_prompt_path)
    daily = generate_daily_take(out, daily_tpl, config, llm)
    emit(ctx.logger, "daily_take_done", ok=daily is not None)

    interpreted_count = sum(1 for r in out if r.interpretation_status == "ok")
    fallback_count = len(out) - interpreted_count
    emit(ctx.logger, "interpret_done", input_count=len(items),
         interpreted_count=interpreted_count, fallback_count=fallback_count,
         silent=False)
    return InterpretResult(interpreted_items=out, daily_take=daily,
                           input_count=len(items),
                           interpreted_count=interpreted_count,
                           fallback_count=fallback_count, is_silent=False)
```

> After this task, consolidate the `from src.core.types import ...` lines at the top of `interpret.py` into a single import (names: `ScoredItem, InterpretedItem, Evidence, InterpretConfig, RunContext, InterpretResult`) and keep `from src.core.prompts import load_prompt` / `from src.observability.events import emit` near the top. No duplicate imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_interpret.py -v`
Expected: PASS (6 tests). Then `uv run pytest -q` → expect 123 passed.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/interpret.py tests/golden/test_interpret.py
git commit -m "feat(interpret): interpret() orchestration + golden cases (spec §9)"
```

---

### Task 8: CLI `--interpret` chain

**Files:**
- Modify: `src/cli.py`
- Test: `tests/contract/test_cli.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_cli.py`:

```python
from src.cli import run_dry_interpret
from tests.fakes import FailingLLMProvider


def test_run_dry_interpret_returns_result_json():
    out = run_dry_interpret(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    assert "interpreted_count" in out and "interpreted_items" in out
    assert "daily_take" in out and "fallback_count" in out
    assert out["input_count"] >= 0
    json.dumps(out)                                  # must be JSON-serializable
```

> `FakeEmbeddingProvider`, `json`, `datetime`, `timezone` are already imported at the top of this file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli.py::test_run_dry_interpret_returns_result_json -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_interpret'`.

- [ ] **Step 3: Implement `run_dry_interpret` + `--interpret`**

In `src/cli.py`, add imports after the existing `from src.pipeline.score import score` line (added in Circle 3):
```python
from src.core.config import load_interpret_config
from src.pipeline.interpret import interpret
from src.adapters.llm.openai_compat import OpenAICompatLLM
```

Add the function after `run_dry_score`:
```python
def run_dry_interpret(registry_path: str, now: datetime | None = None,
                      embedder=None, llm=None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model, batch_size=dcfg.batch_size)
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)

    icfg = load_interpret_config("config/interpret.yaml")
    if llm is None:
        llm = OpenAICompatLLM(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""), model=icfg.model,
            timeout_s=icfg.timeout_s)
    ires = interpret(sres.selected_items, icfg, ctx, llm)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": ires.input_count,
        "interpreted_count": ires.interpreted_count,
        "fallback_count": ires.fallback_count,
        "is_silent": ires.is_silent,
        "daily_take": ires.daily_take,
        "interpreted_items": [it.model_dump(mode="json")
                              for it in ires.interpreted_items],
    }
```

In `main()`, add the flag after the `--score` add_argument:
```python
    p.add_argument("--interpret", action="store_true",
                   help="chain collect -> dedup -> score -> interpret, print InterpretResult JSON")
```

Add the dispatch branch BEFORE the `if args.dry_run and args.score:` branch:
```python
    if args.dry_run and args.interpret:
        out = run_dry_interpret(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli.py -v`
Expected: PASS (4 tests in file). Then `uv run pytest -q` → expect 124 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/contract/test_cli.py
git commit -m "feat(cli): --interpret chain (collect -> dedup -> score -> interpret)"
```

---

### Task 9: Full suite + ROADMAP update

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (~124 passed).

- [ ] **Step 2: Update `docs/ROADMAP.md`**

Change the §1 mermaid C4 class from `todo` to `done`:
```
    C4["④ 解读生成<br/>interpret()"]:::done
```

Change the §2 progress table row ④ from:
```
| ④ | 解读生成 interpret | — | — | — | — | ⬜ |
```
to:
```
| ④ | 解读生成 interpret | `specs/interpret.md` | ✅ `pipeline/interpret.py`（LLM 解读+抽取式回退） | ✅ golden | ✅ `--dry-run --interpret` 实跑 | **🟩 已合并 (master)** |
```

Update the "最后更新" line near the top to:
```
> 每完成一个 Circle 更新此文。最后更新：2026-06-01（Circle 4 interpret 已合并）。
```

In §4 文档地图, add under SPECS and PLANS:
```
    SPECS --> S4["interpret.md ✅"]
```
```
    PLANS --> P4["2026-06-01-interpret-layer.md ✅"]
```

Update §5 "下一步" to point at Circle 5 (review/审阅) and add a "已完成（Circle 4 · interpret）" note above the existing Circle 3 note, mirroring how prior circles were recorded:
```
### 已完成（Circle 4 · interpret）
- `interpret()` 逐条 LLM 解读（结构化 JSON + schema 校验），任一失败→抽取式回退、零编造；`LLMProvider` 协议 + `OpenAICompatLLM`(ModelScope) 适配器 + `FakeLLMProvider` 注入测试。
- 证据链锚点必须 ∈ link∪related_links，非法锚点丢弃；`eligible_for_must_read` 实现「无证据不进必读」；一次日报级「今日看点」。
- 验收门 PRD #5 解读零幻觉（golden 断言回退零编造、必读门）；`--dry-run --interpret` 链路实跑；偏离记于 `docs/adr/0001-llm-openai-compatible.md`。
```
Also change the §5 heading and steps from "下一步（Circle 4 · interpret）" to "下一步（Circle 5 · review）" with content:
```
## 5. 下一步（Circle 5 · review）

1. **你 review** 即将产出的 `docs/specs/review.md`（审阅层契约，验收门 = 留/删/改/排序 + 审阅动作回收为反馈信号）。
2. 确认后 → `superpowers:writing-plans` 产出 review 的逐任务 TDD 计划。
3. 按计划 TDD 实现：本地极简审阅产物 + review_action 记录（PRD §5.3），对外副作用支持 `--dry-run`。
4. 收尾合并，回来更新本表 ⑤→🟩。
```

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: ROADMAP — mark interpret layer (Circle 4) done"
```

---

## Self-Review

**1. Spec coverage:**
- §3 contract `interpret(items, config, ctx, llm)` → Task 7. ✅
- §3 LLMProvider injection + prompts runtime-loaded → Tasks 3,4,7. ✅
- §4 data types (Evidence/InterpretedItem/InterpretConfig/InterpretResult) → Task 1. ✅
- §5.1 empty short-circuit → Task 7 (golden case 5). ✅
- §5.2 per-item build/parse/enforce → Tasks 5,6. ✅
- §5.3 extractive fallback no-fabrication → Tasks 5,6 (asserted) + golden case 3. ✅
- §5.4 must-read gate → Task 5 (`build_ok_item` eligible) + golden case 4. ✅
- §5.5 deterministic order → upstream-sorted preserved; golden case 6 determinism. ✅
- §5.6 daily take + None on fail → Task 6 + golden cases 1,3. ✅
- §6.1 config + loader → Task 2. ✅
- §6.2 LLMProvider + OpenAICompatLLM + fakes → Task 4. ✅
- §6.3 prompts → Task 3. ✅
- §7 degrade table → Tasks 5,6,7 (empty/llm-fail/bad-json/tags/illegal-anchor/empty-summary). ✅
- §8 invariants 1-9 → asserted across Tasks 5-7 unit/golden. ✅
- §9 6 golden cases → Task 7. ✅
- §11 events (item_interpreted/interpret_fallback/daily_take_done/interpret_done) → Task 7. ✅
- §12 acceptance (#5 零幻觉, #1 end-to-end CLI, #8 silent, #9 observable) → Tasks 7,8. ✅
- ADR (deviation from "解读用 Claude") → Task 1. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". All steps carry full code. ✅

**3. Type consistency:** `Evidence`/`InterpretedItem`/`InterpretConfig`/`InterpretResult` names identical across Tasks 1→2→5→6→7→8. Function names `build_item_prompt`/`parse_and_validate`/`build_ok_item`/`extractive_fallback`/`interpret_item`/`build_daily_prompt`/`generate_daily_take`/`interpret` consistent. `complete_json(prompt, *, temperature, max_tokens)` signature identical across protocol (Task 4), fakes (Task 4), adapter (Task 4), and callers (Tasks 6,7). `interpretation_status` string values `"ok"`/`"extractive_fallback"` consistent. Field-limit names (`title_max_chars`/`summary_max_chars`/`tags_count`/`min_evidence`) consistent between config (Tasks 1,2) and usage (Task 5). ✅
