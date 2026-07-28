# hf-models README Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `hf_models`-adapter items real description text (fetched from each model's HF README) before they can occupy a review-card slot, so `interpret` always has real material to write from instead of producing an empty `body`.

**Architecture:** New enrich-stage pipeline function `enrich_hf_models_readme()` in `src/pipeline/hf_readme.py`, mirroring the existing `judge_release_importance()` shape (adapter-filtered, hard-filters items it can't service) and the existing `enrich_with_hn()` shape (async, concurrency-limited, injected client protocol). Runs in the same `collect → enrich → dedup → score → interpret` slot as the other two enrich-stage functions, in `src/cli.py`, before `dedup`/`score` truncate the pool — so items without usable content are dropped before they can consume a `card_pool_limit` slot, not after.

**Tech Stack:** Python 3.12, httpx (async), pydantic/dataclasses, pytest.

## Global Constraints

- Only `hf_models`-adapter items are touched; every other adapter passes through untouched (mirrors `judge_release_importance`'s `item.adapter != "github_releases"` passthrough).
- No network/IO in `src/pipeline/*` beyond the injected client call — text cleaning and filtering logic must be pure functions, testable without a network client.
- All new knobs (timeout, concurrency, minimum usable-content length) live in `config/enrich.yaml` via a new `HFReadmeConfig` dataclass — nothing hardcoded in the pipeline module.
- A model with no fetchable README (404, network error, or content too short after cleaning) is **dropped from the candidate list entirely** — it must not reach `interpret`/the review card pool with an empty body. This is a hard filter, not an annotation.
- The exact `min_body_chars` threshold shipped in `config/enrich.yaml` is a placeholder until Task 5's real dry-run — per project discipline, thresholds ship with real before/after numbers in the PR, not a guessed constant.
- Follow existing file conventions exactly: `src/pipeline/release_importance.py` (adapter-filtered hard-filter pipeline function) and `src/pipeline/enrich.py` / `src/adapters/enrich/hn_algolia.py` (async injected-client enrichment) are the two patterns being combined here — don't invent a third shape.

---

## Task 0: Isolate this work on its own branch

This feature is separate from the already-written `fix/empty-body-model-cards` defensive patch (unmerged, unrelated PR). Do not develop it on that branch or in that worktree.

- [ ] **Step 1: Create a fresh worktree + branch off `origin/master`**

```bash
cd /Users/nev4rb14su/workspace
git -C ai-newsday fetch origin master
git -C ai-newsday worktree add ../ai-newsday-hf-readme -b fix/hf-models-readme-enrichment origin/master
```

All subsequent file paths in this plan (e.g. `src/core/types.py`) are relative to `/Users/nev4rb14su/workspace/ai-newsday-hf-readme`, not `ai-newsday-fix`.

---

## Task 1: `HFReadmeConfig` + `EnrichConfig` wiring + `config/enrich.yaml`

**Files:**
- Modify: `src/core/types.py` (add `HFReadmeConfig` dataclass near `ReleaseImportanceConfig` at line ~419; add `hf_readme` field to `EnrichConfig` at line ~442)
- Modify: `src/core/config.py` (extend `load_enrich_config` at line ~172)
- Modify: `config/enrich.yaml` (add `hf_readme:` block)
- Test: `tests/contract/test_enrich_config.py`

**Interfaces:**
- Produces: `HFReadmeConfig` dataclass with fields `enabled: bool`, `timeout_s: int`, `concurrency: int`, `min_body_chars: int`, consumed by this task's own `enrich_hf_models_readme()` (Task 2) and by Task 4's cli.py wiring.
- Produces: `EnrichConfig.hf_readme: HFReadmeConfig`, consumed by `src/cli.py` in Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/contract/test_enrich_config.py`:

```python
from src.core.types import HFReadmeConfig


def test_enrich_config_hf_readme_defaults():
    cfg = EnrichConfig()
    hr = cfg.hf_readme
    assert isinstance(hr, HFReadmeConfig)
    assert hr.enabled is True
    assert hr.timeout_s > 0
    assert hr.concurrency >= 1
    assert hr.min_body_chars > 0


def test_load_enrich_config_hf_readme_overrides(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text(
        """
hf_readme:
  enabled: false
  timeout_s: 5
  concurrency: 3
  min_body_chars: 100
""",
        encoding="utf-8",
    )
    cfg = load_enrich_config(str(p))
    hr = cfg.hf_readme
    assert hr.enabled is False
    assert hr.timeout_s == 5
    assert hr.concurrency == 3
    assert hr.min_body_chars == 100


def test_load_enrich_config_hf_readme_missing_block_uses_defaults(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text("enabled: true\n", encoding="utf-8")
    cfg = load_enrich_config(str(p))
    assert cfg.hf_readme == HFReadmeConfig()


def test_production_enrich_yaml_has_hf_readme_configured():
    cfg = load_enrich_config("config/enrich.yaml")
    assert cfg.hf_readme.enabled is True
    assert cfg.hf_readme.min_body_chars > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/contract/test_enrich_config.py -k hf_readme -v`
Expected: FAIL with `ImportError: cannot import name 'HFReadmeConfig'` (or `AttributeError: 'EnrichConfig' object has no attribute 'hf_readme'`).

- [ ] **Step 3: Add `HFReadmeConfig` and wire it into `EnrichConfig`**

In `src/core/types.py`, immediately after the `ReleaseImportanceConfig` class (ends at line 438, right before the blank line at 439-441), insert:

```python
@dataclass
class HFReadmeConfig:
    """hf-models 条目本身没有描述文本(adapter 只调用模型列表 API, raw_summary 恒为 None)。
    抓取模型的 HF README 作为 interpret 的素材来源; 抓不到/清洗后内容太短的条目直接从候选
    列表剔除(不带着空 body 进审阅卡池), 而不是放行后指望下游过滤器兜底。"""

    enabled: bool = True
    timeout_s: int = 8
    concurrency: int = 5
    min_body_chars: int = 80  # 清洗 frontmatter/图片/HTML 后剩余正文长度下限; 上线前用真实数据核实
```

Then modify the `EnrichConfig` dataclass (currently at line ~442-450):

```python
@dataclass
class EnrichConfig:
    """RSS 类源天然无 popularity, 用 HN Algolia by URL 反查补 signals.hn_*。"""

    enabled: bool = True
    concurrency: int = 5
    timeout_s: int = 8
    # 已经带原生 popularity 信号的 genre 不查 HN (省请求, 不覆盖)
    skip_genres: list[str] = field(default_factory=lambda: ["paper", "model"])
    release_importance: ReleaseImportanceConfig = field(default_factory=ReleaseImportanceConfig)
    hf_readme: HFReadmeConfig = field(default_factory=HFReadmeConfig)
```

(Only the added `hf_readme` line is new — everything else in that class is unchanged.)

- [ ] **Step 4: Extend `load_enrich_config` to parse the `hf_readme` block**

In `src/core/config.py`, modify `load_enrich_config` (currently lines 172-208). Add the import and parsing block, and pass it into the returned `EnrichConfig`:

```python
def load_enrich_config(path: str) -> EnrichConfig:
    """HN URL 反查 popularity 的开关 + 配额, release 重要性判定, hf-models README 抓取;
    缺文件 -> 默认。"""
    from src.core.types import (  # local import to avoid cycles
        HFReadmeConfig,
        ProviderSpec,
        ReleaseImportanceConfig,
    )

    data = _read_yaml(path)
    d = EnrichConfig()
    ri_data = data.get("release_importance", {})
    ri_d = ReleaseImportanceConfig()
    raw_providers = ri_data.get("providers")
    if raw_providers:
        ri_providers = {
            name: ProviderSpec(base_url=spec["base_url"], api_key_env=spec["api_key_env"])
            for name, spec in raw_providers.items()
        }
    else:
        ri_providers = ri_d.providers
    release_importance = ReleaseImportanceConfig(
        enabled=ri_data.get("enabled", ri_d.enabled),
        model=ri_data.get("model", ri_d.model),
        models=ri_data.get("models", ri_d.models),
        fallback_models=ri_data.get("fallback_models", ri_d.fallback_models),
        providers=ri_providers,
        temperature=ri_data.get("temperature", ri_d.temperature),
        max_tokens=ri_data.get("max_tokens", ri_d.max_tokens),
        timeout_s=ri_data.get("timeout_s", ri_d.timeout_s),
        empty_body_min_chars=ri_data.get("empty_body_min_chars", ri_d.empty_body_min_chars),
        hard_filter_max_tier=ri_data.get("hard_filter_max_tier", ri_d.hard_filter_max_tier),
        tier_score=ri_data.get("tier_score", ri_d.tier_score),
        prompt_path=ri_data.get("prompt_path", ri_d.prompt_path),
    )
    hr_data = data.get("hf_readme", {})
    hr_d = HFReadmeConfig()
    hf_readme = HFReadmeConfig(
        enabled=hr_data.get("enabled", hr_d.enabled),
        timeout_s=hr_data.get("timeout_s", hr_d.timeout_s),
        concurrency=hr_data.get("concurrency", hr_d.concurrency),
        min_body_chars=hr_data.get("min_body_chars", hr_d.min_body_chars),
    )
    return EnrichConfig(
        enabled=data.get("enabled", d.enabled),
        concurrency=data.get("concurrency", d.concurrency),
        timeout_s=data.get("timeout_s", d.timeout_s),
        skip_genres=data.get("skip_genres", d.skip_genres),
        release_importance=release_importance,
        hf_readme=hf_readme,
    )
```

- [ ] **Step 5: Add the `hf_readme` block to `config/enrich.yaml`**

Append to the end of `config/enrich.yaml`:

```yaml

# hf-models 条目补描述文本: adapter 本身只调模型列表 API, 没有正文可用 (2026-07-27,
# 实锤 microsoft/Mage-Flow 发布时 body 是空的, 但 HF 上其实有真实 README)。
# 抓每个候选模型的 README, 清洗 frontmatter/图片/HTML 后当 raw_summary 素材;
# 抓不到或清洗后太短的条目直接从候选列表剔除, 不让空 body 进审阅卡池。
hf_readme:
  enabled: true
  timeout_s: 8
  concurrency: 5
  min_body_chars: 80  # 占位值, Task 5 用真实数据核实后可能调整
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/contract/test_enrich_config.py -v`
Expected: all PASS (existing release_importance tests + new hf_readme tests).

- [ ] **Step 7: Commit**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme
git add src/core/types.py src/core/config.py config/enrich.yaml tests/contract/test_enrich_config.py
git commit -m "feat(enrich): add HFReadmeConfig for hf-models README fetch"
```

---

## Task 2: `_clean_readme` pure text-cleaning function

**Files:**
- Create: `src/pipeline/hf_readme.py`
- Test: `tests/contract/test_hf_readme_unit.py`

**Interfaces:**
- Produces: `_clean_readme(text: str) -> str`, consumed by Task 3's `enrich_hf_models_readme()`.
- Produces: `_model_id_from_link(link: str) -> str`, consumed by Task 3's `enrich_hf_models_readme()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/contract/test_hf_readme_unit.py`:

```python
from src.pipeline.hf_readme import _clean_readme, _model_id_from_link


def test_clean_readme_strips_yaml_frontmatter():
    text = "---\nlicense: mit\ntags:\n  - foo\n---\n\nReal prose starts here."
    assert _clean_readme(text) == "Real prose starts here."


def test_clean_readme_strips_images():
    text = "Intro line.\n\n![demo](https://example.com/demo.png)\n\nMore prose."
    out = _clean_readme(text)
    assert "![" not in out
    assert "Intro line." in out
    assert "More prose." in out


def test_clean_readme_strips_html_tags():
    text = '<h1 align="center">Title</h1>\n\n<p align="center"><a href="x">badge</a></p>\n\nBody text.'
    out = _clean_readme(text)
    assert "<h1" not in out and "<p" not in out and "<a" not in out
    assert "Body text." in out


def test_clean_readme_collapses_blank_lines():
    text = "Para one.\n\n\n\n\nPara two."
    out = _clean_readme(text)
    assert "\n\n\n" not in out


def test_clean_readme_empty_input_returns_empty():
    assert _clean_readme("") == ""
    assert _clean_readme("   \n\n  ") == ""


def test_clean_readme_frontmatter_only_returns_empty():
    text = "---\nlicense: mit\n---\n"
    assert _clean_readme(text) == ""


def test_model_id_from_link_strips_hf_prefix():
    assert _model_id_from_link("https://huggingface.co/microsoft/Mage-Flow") == "microsoft/Mage-Flow"
    assert _model_id_from_link("https://huggingface.co/unsloth/Laguna-S-2.1-GGUF") == "unsloth/Laguna-S-2.1-GGUF"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/contract/test_hf_readme_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.hf_readme'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/pipeline/hf_readme.py`:

```python
"""hf_readme: hf-models 条目没有描述文本(adapter 只调模型列表 API, raw_summary 恒为
None)。本文件的纯文本工具函数负责把抓回来的 README 原文清洗成可用素材;
抓取 + 硬过滤编排逻辑(`enrich_hf_models_readme`)在 Task 3 加上。"""

from __future__ import annotations

import re

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _clean_readme(text: str) -> str:
    """去 YAML frontmatter + markdown 图片 + HTML 标签 + 折叠多余空行(纯函数, 无网络)。"""
    out = _FRONTMATTER_RE.sub("", text)
    out = _IMAGE_RE.sub("", out)
    out = _HTML_TAG_RE.sub("", out)
    out = _BLANK_LINES_RE.sub("\n\n", out)
    return out.strip()


def _model_id_from_link(link: str) -> str:
    """hf_models adapter 固定用 f"https://huggingface.co/{{mid}}" 构造 link, 反查 model id。"""
    return link.removeprefix("https://huggingface.co/")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/contract/test_hf_readme_unit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme
git add src/pipeline/hf_readme.py tests/contract/test_hf_readme_unit.py
git commit -m "feat(enrich): add hf-models README cleaning helpers"
```

---

## Task 3: `enrich_hf_models_readme` golden tests (client injection, hard filter, error handling)

**Files:**
- Modify: `src/pipeline/hf_readme.py` (add `enrich_hf_models_readme`, on top of Task 2's `_clean_readme`/`_model_id_from_link`)
- Create: `src/adapters/enrich/hf_readme.py` (the injected client)
- Test: `tests/golden/test_hf_readme.py`

**Interfaces:**
- Consumes: `_clean_readme`, `_model_id_from_link` from Task 2 (already implemented in `src/pipeline/hf_readme.py`).
- Produces: `enrich_hf_models_readme(items, client, config, ctx)`, consumed by Task 4's `src/cli.py` wiring.
- Produces: `HFReadmeClient` class with `async def fetch_readme(model_id: str) -> str | None`, consumed by Task 4's `src/cli.py` wiring.

- [ ] **Step 1: Write the failing tests**

Create `tests/golden/test_hf_readme.py`:

```python
"""golden: enrich_hf_models_readme 用伪 README 客户端, 验证素材填充 + 硬过滤 + 容错。"""

import asyncio
import logging
from datetime import datetime, timezone

from src.core.types import Genre, HFReadmeConfig, Publisher, RawItem, RunContext
from src.pipeline.hf_readme import enrich_hf_models_readme

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


class FakeReadmeClient:
    """注入式: model_id → README 原文(或 None = 404/无文件)。"""

    def __init__(self, mapping: dict[str, str | None]):
        self._map = mapping
        self.calls: list[str] = []

    async def fetch_readme(self, model_id: str) -> str | None:
        self.calls.append(model_id)
        return self._map.get(model_id)


class BoomClient:
    async def fetch_readme(self, model_id: str) -> str | None:
        raise RuntimeError("network down")


def _model_item(mid, adapter="hf_models"):
    return RawItem(
        title_en=mid,
        link=f"https://huggingface.co/{mid}",
        source="hf-models",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=None,
        adapter=adapter,
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-hf-readme"))


_REAL_README = "---\nlicense: mit\n---\n\n" + "This model does X and Y in production. " * 5


def test_non_hf_models_adapter_passthrough_no_client_call():
    items = [_model_item("a/b", adapter="rss")]
    client = FakeReadmeClient({})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert out == items
    assert client.calls == []


def test_real_readme_fills_raw_summary_and_keeps_item():
    items = [_model_item("microsoft/Mage-Flow")]
    client = FakeReadmeClient({"microsoft/Mage-Flow": _REAL_README})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert len(out) == 1
    assert out[0].raw_summary is not None
    assert "This model does X and Y" in out[0].raw_summary
    assert "license: mit" not in out[0].raw_summary  # frontmatter stripped


def test_missing_readme_filters_item_out():
    items = [_model_item("nobody/no-readme")]
    client = FakeReadmeClient({"nobody/no-readme": None})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert out == []


def test_readme_too_short_after_cleaning_filters_item_out():
    items = [_model_item("x/tiny")]
    client = FakeReadmeClient({"x/tiny": "---\nlicense: mit\n---\n\nHi."})
    out = asyncio.run(
        enrich_hf_models_readme(items, client, HFReadmeConfig(min_body_chars=80), _ctx())
    )
    assert out == []


def test_client_failure_filters_item_out_does_not_crash_batch():
    items = [_model_item("a/ok"), _model_item("b/boom")]

    class MixedClient:
        async def fetch_readme(self, model_id: str) -> str | None:
            if model_id == "b/boom":
                raise RuntimeError("network down")
            return _REAL_README

    out = asyncio.run(enrich_hf_models_readme(items, MixedClient(), HFReadmeConfig(), _ctx()))
    assert [i.title_en for i in out] == ["a/ok"]


def test_disabled_passthrough_no_client_call():
    items = [_model_item("a/b")]
    client = FakeReadmeClient({"a/b": _REAL_README})
    out = asyncio.run(
        enrich_hf_models_readme(items, client, HFReadmeConfig(enabled=False), _ctx())
    )
    assert out == items
    assert client.calls == []


def test_mixed_list_preserves_order_for_kept_items():
    items = [
        _model_item("drop/me"),  # 无 README, 剔除
        _model_item("keep/me1"),
        RawItem(
            title_en="rss-item",
            link="https://blog.example.com/post",
            source="blog",
            genre=Genre.writeup,
            publisher=Publisher.individual,
            published_at=NOW,
            adapter="rss",
        ),  # 非 hf_models, 透传(不检查长度)
        _model_item("keep/me2"),
    ]
    client = FakeReadmeClient(
        {"drop/me": None, "keep/me1": _REAL_README, "keep/me2": _REAL_README}
    )
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert [i.title_en for i in out] == ["keep/me1", "rss-item", "keep/me2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/golden/test_hf_readme.py -v`
Expected: FAIL with `ImportError: cannot import name 'enrich_hf_models_readme' from 'src.pipeline.hf_readme'`.

- [ ] **Step 3: Implement `enrich_hf_models_readme`**

Append to `src/pipeline/hf_readme.py`. First, update the module docstring and imports at the top of the file to:

```python
"""hf_readme: hf-models 条目没有描述文本(adapter 只调模型列表 API, raw_summary 恒为
None)。抓每个候选模型的 HF README 当素材, 清洗掉 frontmatter/图片/HTML 噪声后填进
raw_summary。只处理 adapter == "hf_models" 的条目; 其余原样透传。抓不到 README、
或清洗后正文短于 min_body_chars 的条目直接从返回列表剔除(不带空 body 进审阅卡池),
不是放行后指望下游过滤器兜底。"""

from __future__ import annotations

import asyncio
import re

from src.core.types import HFReadmeConfig, RawItem, RunContext
from src.observability.events import emit
```

(This replaces Task 2's plainer docstring and adds the `asyncio`/`HFReadmeConfig`/`RawItem`/`RunContext`/`emit` imports Task 2 didn't need. The `_FRONTMATTER_RE`/`_IMAGE_RE`/`_HTML_TAG_RE`/`_BLANK_LINES_RE` constants and `_clean_readme`/`_model_id_from_link` functions from Task 2 stay unchanged below the imports.)

Then append this function at the end of the file:

```python
async def enrich_hf_models_readme(
    items: list[RawItem], client, config: HFReadmeConfig, ctx: RunContext
) -> list[RawItem]:
    """对 adapter == "hf_models" 的条目抓 README 填 raw_summary; 其余原样透传。
    返回硬过滤后的列表(抓不到内容或内容太短的条目被剔除)。"""
    emit(ctx.logger, "hf_readme_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "hf_readme_done", fetched=0, filtered=0)
        return items

    targets = [it for it in items if it.adapter == "hf_models"]
    if not targets:
        emit(ctx.logger, "hf_readme_done", fetched=0, filtered=0)
        return items

    sem = asyncio.Semaphore(max(1, config.concurrency))
    cleaned: dict[str, str | None] = {}

    async def _fetch_one(item: RawItem) -> None:
        async with sem:
            try:
                raw = await client.fetch_readme(_model_id_from_link(item.link))
            except Exception as e:
                emit(
                    ctx.logger,
                    "hf_readme_error",
                    link=item.link,
                    error_type=type(e).__name__,
                    error=str(e)[:200],
                )
                cleaned[item.link] = None
                return
        cleaned[item.link] = _clean_readme(raw) if raw else None

    await asyncio.gather(*(_fetch_one(it) for it in targets))

    out: list[RawItem] = []
    fetched = filtered = 0
    for item in items:
        if item.adapter != "hf_models":
            out.append(item)
            continue
        text = cleaned.get(item.link)
        if not text or len(text) < config.min_body_chars:
            filtered += 1
            continue
        item.raw_summary = text
        fetched += 1
        out.append(item)

    emit(ctx.logger, "hf_readme_done", fetched=fetched, filtered=filtered)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest tests/golden/test_hf_readme.py -v`
Expected: all PASS.

- [ ] **Step 5: Implement the real `HFReadmeClient` (production adapter, mirrors `HNAlgoliaClient`)**

Create `src/adapters/enrich/hf_readme.py`:

```python
"""HF README 抓取客户端。协议: async def fetch_readme(model_id) -> str | None。
404(模型没有 README 文件)一律返回 None, 交给调用方硬过滤; 其它 HTTP 错误向上抛出,
由调用方(enrich_hf_models_readme)按单条失败处理, 不挂整批。"""

from __future__ import annotations

import httpx


class HFReadmeClient:
    def __init__(self, timeout_s: int = 8):
        self._timeout = timeout_s

    async def fetch_readme(self, model_id: str) -> str | None:
        url = f"https://huggingface.co/{model_id}/raw/main/README.md"
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
```

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest -q`
Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme
git add src/pipeline/hf_readme.py src/adapters/enrich/hf_readme.py tests/golden/test_hf_readme.py
git commit -m "feat(enrich): add enrich_hf_models_readme + HFReadmeClient + golden tests"
```

---

## Task 4: Wire `enrich_hf_models_readme` into `src/cli.py`

**Files:**
- Modify: `src/cli.py` (two call sites: `_dry_run_prefix`'s `_collect_then_enrich()` at lines ~150-157, and `run_tick`'s `_collect_and_interpret()` at lines ~433-441)

**Interfaces:**
- Consumes: `enrich_hf_models_readme` from `src.pipeline.hf_readme`, `HFReadmeClient` from `src.adapters.enrich.hf_readme` (both from Task 3).

- [ ] **Step 1: Add imports to `src/cli.py`**

Near the existing imports of `enrich_with_hn` / `judge_release_importance` / `HNAlgoliaClient`, add:

```python
from src.adapters.enrich.hf_readme import HFReadmeClient
from src.pipeline.hf_readme import enrich_hf_models_readme
```

- [ ] **Step 2: Wire the call into `_dry_run_prefix`'s `_collect_then_enrich()`**

In `src/cli.py`, modify the inner function (currently lines 150-157):

```python
        async def _collect_then_enrich():
            c = await collect(coll_cfg, ctx)
            if ecfg.enabled and c.items:
                await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
            if ecfg.release_importance.enabled and c.items:
                ri_llm = release_llm or _make_release_importance_llm(ecfg.release_importance)
                c.items = judge_release_importance(c.items, ri_llm, ecfg.release_importance, ctx)
            if ecfg.hf_readme.enabled and c.items:
                c.items = await enrich_hf_models_readme(
                    c.items, HFReadmeClient(ecfg.hf_readme.timeout_s), ecfg.hf_readme, ctx
                )
            return c
```

(Only the `if ecfg.hf_readme.enabled...` block is new.)

- [ ] **Step 3: Wire the call into `run_tick`'s `_collect_and_interpret()`**

In `src/cli.py`, modify the inner function (currently lines ~433-441):

```python
    async def _collect_and_interpret():
        c = await collect(coll_cfg, ctx)
        if ecfg.enabled and c.items:
            await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
        if ecfg.release_importance.enabled and c.items:
            ri_llm = _make_release_importance_llm(ecfg.release_importance)
            c.items = judge_release_importance(c.items, ri_llm, ecfg.release_importance, ctx)
        if ecfg.hf_readme.enabled and c.items:
            c.items = await enrich_hf_models_readme(
                c.items, HFReadmeClient(ecfg.hf_readme.timeout_s), ecfg.hf_readme, ctx
            )
        dcfg2 = load_dedup_config("config/dedup.yaml")
        dcfg2.sources_registry_path = registry_path
        _embedder = _make_embedder(dcfg2, embedder)
        dres = dedup(c.items, dcfg2, ctx, embedder=_embedder, store=InMemoryVectorStore())
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        quality_of = await db.get_quality_weights()
        sres = score(dres.deduped_items, scfg, ctx, quality_of=quality_of)
        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(sres.selected_items, icfg, ctx, _llm)
        return ires
```

(Only the `if ecfg.hf_readme.enabled...` block is new — everything else in that function is unchanged.)

- [ ] **Step 4: Run the full test suite**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run pytest -q`
Expected: all PASS, no regressions.

- [ ] **Step 5: Run lint**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && uv run ruff check . && uv run ruff format --check .`
Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme
git add src/cli.py
git commit -m "feat(enrich): wire hf-models README fetch into collect->enrich pipeline"
```

---

## Task 5: Real dry-run verification + threshold check + KANBAN update + PR

**Files:**
- Modify: `config/enrich.yaml` (adjust `min_body_chars` if real data says the placeholder is wrong)
- Modify: `docs/KANBAN.md` (move this item from 待决策 to ✅ Done, following [[finish-branch-update-kanban-same-pr]])

This task has no new code — it's the verification gate required before this can merge (per [[verify-scoring-changes-against-real-dry-run]]: don't ship a new threshold on unit tests alone).

- [ ] **Step 1: Run a real dry-run against live hf-models candidates**

Run: `cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme && MODELSCOPE_API_KEY=<real key> uv run python -m src.cli --dry-run --score --enrich 2>&1 | tee /tmp/hf_readme_dryrun.log`

(Use whatever the project's actual dry-run invocation is — check `src/cli.py`'s `__main__`/argparse block for the exact flags if the above doesn't match; the goal is to run through `collect → enrich(with hf_readme) → dedup → score` and inspect the `hf-models` items in the output.)

- [ ] **Step 2: Inspect real numbers**

From the run's `hf_readme_done` log line(s) and the dumped `03_scored.jsonl` (or equivalent), record:
- How many hf-models candidates were collected today.
- How many were filtered out (no README / too short) vs. kept with real content.
- For 3-5 kept items, confirm `raw_summary` actually looks like usable prose (not just leftover badge/link noise the cleaner missed).
- For 2-3 filtered-out items, spot-check on huggingface.co that they genuinely have no usable README (not a cleaning bug false-positive).

- [ ] **Step 3: Adjust `min_body_chars` if the real numbers say the placeholder (80) is wrong**

If real kept items commonly have <80 chars of substantive content that's still clearly usable, lower it. If filtered-out items commonly have >80 chars of pure boilerplate (e.g. a long badge/link block the cleaner didn't catch), consider tightening the cleaner regex in `src/pipeline/hf_readme.py` first (root cause) rather than just raising the number.

- [ ] **Step 4: Put the before/after numbers in the PR description**

Not "should reduce empty-body cards" — the actual counts from Step 2.

- [ ] **Step 5: Update `docs/KANBAN.md`**

Move "模型条目正文完全空仍照发" from the 待决策 row in §3 to a new ✅ Done row in §5, with the real numbers from Step 2 and a note that this supersedes the `bool(body)` safety-net patch in `fix/empty-body-model-cards` (that branch's fix stays as a defensive last resort in `build_report`, not the primary fix — no need to revert it).

- [ ] **Step 6: Push and open PR**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday-hf-readme
git push -u origin fix/hf-models-readme-enrichment
```

(Confirm with the user before pushing/opening the PR — this is a shared-state action.)
