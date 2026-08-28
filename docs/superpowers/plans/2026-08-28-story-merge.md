# Story Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a company ships a model and third-party platforms each post their own "we now support it" announcement, merge those into a single published card instead of letting each one eat a separate genre-quota slot — while keeping every item visible independently during human review.

**Architecture:** A new pipeline step `src/pipeline/storylink.py::link_stories()` runs between `score()` and `interpret()` on the card-pool-sized candidate list. It's a two-phase pure-plus-one-LLM-call process per candidate pair (mirroring the existing `src/pipeline/release_importance.py` shape): (1) a regex extracts "entity tokens" (model/version-like substrings) from each item's title/summary; items published on the same calendar day whose token sets overlap become candidate pairs; (2) each candidate pair gets one cheap yes/no LLM call asking "same story?" — never asked to name or invent entities, only to confirm/deny, reusing the fabrication-avoidance pattern from today's `x-ai-company` fix. Confirmed pairs are merged via union-find into `story_id` groups, written onto a new `NewsItem.story_id` field that all downstream types (`ScoredItem`, `InterpretedItem`, `ReviewedItem`) inherit automatically. Grouping is invisible everywhere except one place: `publish.py::build_report()`, where a new `merge_story_groups()` runs *after* `apply_adapter_quota()` and *before* `apply_quota()` — collapsing each group down to its earliest-published "primary" item, with up to N other group members' links folded into the primary's `evidence` list (reusing the *existing* evidence→references rendering path, `_ref()`, for free — no renderer changes needed) and a fixed sentence appended to the primary's body.

**Tech Stack:** Python 3.12, pydantic (`src/core/types.py`), the existing `OpenAICompatLLM` adapter, pytest.

## Global Constraints

- `dedup.py::cluster()`'s semantic-similarity clustering is untouched — story-linking is a parallel, independent second signal, not a replacement.
- Review cards stay one-card-per-item — merging only happens at `publish.py::build_report()` render time, never before.
- No general entity-linking/NER/knowledge-graph machinery — only same-day token-overlap candidates get an LLM look, and the LLM only answers yes/no, never generates or repeats an entity name.
- LLM failure/parse failure on a candidate pair → fail-closed (`same_story = False`) — a missed merge opportunity is acceptable; an incorrectly merged pair is not.
- The display name used for a "supporting platform" mention must never be `item.source` (the internal source slug) — this plan reuses the link-domain convention already established in `src/notifiers/telegram_polling.py::_make_card_message` (`urlparse(link).netloc`), which is exactly the kind of non-slug, always-derivable-from-public-data display name the story-merge spec requires.
- All thresholds/config (regex pattern, model chain, `max_support`) live in `config/storylink.yaml` / `StoryLinkConfig`, never hardcoded.

---

## File Structure

- Modify `src/core/types.py` — `NewsItem.story_id: str | None = None`; new `StoryLinkConfig` dataclass; `PublishConfig.story_merge_max_support: int = 3`.
- Modify `src/core/config.py` — `load_storylink_config`; `load_publish_config` reads `story_merge_max_support`.
- Create `config/storylink.yaml` — new config file.
- Create `src/prompts/story_link_confirm.md` — new prompt.
- Create `src/pipeline/storylink.py` — `extract_entity_tokens`, `find_candidate_pairs`, `confirm_pair`, `link_stories`.
- Modify `src/pipeline/publish.py` — `merge_story_groups()`; wire into `build_report()`.
- Modify `src/cli.py` — `_make_storylink_llm`; call `link_stories()` between `score()` and `interpret()` in `_dry_run_prefix` and `run_tick`.
- Create `tests/contract/test_storylink_config.py`, `tests/contract/test_storylink_unit.py`, `tests/contract/test_storylink_prompt.py`.
- Modify `tests/contract/test_types.py`, `tests/contract/test_publish_config.py`, `tests/golden/test_publish.py`.

---

### Task 1: `NewsItem.story_id` field

**Files:**
- Modify: `src/core/types.py:110-113` (`class NewsItem`)
- Test: `tests/contract/test_types.py`

**Interfaces:**
- Produces: `NewsItem.story_id: str | None = None`, inherited by `ScoredItem` → `InterpretedItem` → `ReviewedItem` (pydantic subclass field inheritance — no per-layer plumbing needed, same pattern as the existing `cluster_id`/`related_links` fields).

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_types.py`:

```python
def test_news_item_story_id_defaults_none():
    from datetime import datetime, timezone

    from src.core.types import Genre, NewsItem, Publisher

    it = NewsItem(
        title_en="x",
        link="https://x/1",
        source="s",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        cluster_id="evt-1",
    )
    assert it.story_id is None


def test_news_item_story_id_settable():
    from datetime import datetime, timezone

    from src.core.types import Genre, NewsItem, Publisher

    it = NewsItem(
        title_en="x",
        link="https://x/1",
        source="s",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        cluster_id="evt-1",
        story_id="story-2026-06-26-001",
    )
    assert it.story_id == "story-2026-06-26-001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_types.py -v -k story_id`
Expected: FAIL — `TypeError` / pydantic "extra fields not permitted" or `AttributeError` (field doesn't exist yet)

- [ ] **Step 3: Add the field**

In `src/core/types.py`, change:

```python
# --- dedup layer (Circle 2) ---
class NewsItem(RawItem):
    cluster_id: str = Field(min_length=1)
    related_links: list[str] = Field(default_factory=list)
    embedding_id: str | None = None
```

to:

```python
# --- dedup layer (Circle 2) ---
class NewsItem(RawItem):
    cluster_id: str = Field(min_length=1)
    related_links: list[str] = Field(default_factory=list)
    embedding_id: str | None = None
    # 故事线合并 id(同一模型/产品同一轮动态的多条独立发布); None=不属于任何故事组
    # (绝大多数条目)。由 src/pipeline/storylink.py::link_stories() 写入(score 之后,
    # interpret 之前); publish.py::merge_story_groups() 在渲染层按此分组合并。
    story_id: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_types.py -v -k story_id`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_types.py
git commit -m "feat(types): add NewsItem.story_id for story-line merging"
```

---

### Task 2: `StoryLinkConfig` + loader + production YAML + prompt file

**Files:**
- Modify: `src/core/types.py` (new `StoryLinkConfig` dataclass, next to `ReleaseImportanceConfig`)
- Modify: `src/core/config.py` (new `load_storylink_config`)
- Create: `config/storylink.yaml`
- Create: `src/prompts/story_link_confirm.md`
- Test: `tests/contract/test_storylink_config.py`, `tests/contract/test_storylink_prompt.py`

**Interfaces:**
- Produces:
  - `StoryLinkConfig` dataclass (fields listed below), consumed by Task 5 (`link_stories`) and Task 6 (cli wiring).
  - `load_storylink_config(path: str) -> StoryLinkConfig`.
  - `src/prompts/story_link_confirm.md` with placeholders `{{title_a}}`, `{{summary_a}}`, `{{title_b}}`, `{{summary_b}}`, output schema `{"same_story": bool, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/contract/test_storylink_config.py`:

```python
from src.core.config import load_storylink_config
from src.core.types import StoryLinkConfig


def test_missing_file_returns_defaults():
    c = load_storylink_config("does/not/exist.yaml")
    assert isinstance(c, StoryLinkConfig)
    assert c.enabled is True
    assert c.prompt_path == "src/prompts/story_link_confirm.md"
    assert c.temperature == 0.0


def test_loads_overrides(tmp_path):
    p = tmp_path / "storylink.yaml"
    p.write_text(
        "enabled: false\n"
        "entity_token_pattern: 'X\\\\d+'\n"
        "max_tokens: 100\n"
        "timeout_s: 10\n",
        encoding="utf-8",
    )
    c = load_storylink_config(str(p))
    assert c.enabled is False
    assert c.entity_token_pattern == "X\\d+"
    assert c.max_tokens == 100
    assert c.timeout_s == 10


def test_loads_providers(tmp_path):
    p = tmp_path / "storylink.yaml"
    p.write_text(
        "providers:\n"
        "  modelscope:\n"
        "    base_url: 'https://x/v1'\n"
        "    api_key_env: 'X_KEY'\n"
        "models: ['modelscope:foo']\n",
        encoding="utf-8",
    )
    c = load_storylink_config(str(p))
    assert c.providers["modelscope"].base_url == "https://x/v1"
    assert c.models == ["modelscope:foo"]


def test_production_config_exists_and_enabled():
    c = load_storylink_config("config/storylink.yaml")
    assert c.enabled is True
    assert c.entity_token_pattern
```

Create `tests/contract/test_storylink_prompt.py`:

```python
from src.core.prompts import load_prompt


def test_story_link_confirm_prompt_has_placeholders():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert "{{title_a}}" in t and "{{summary_a}}" in t
    assert "{{title_b}}" in t and "{{summary_b}}" in t


def test_story_link_confirm_prompt_has_output_schema():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert '"same_story"' in t
    assert '"reason"' in t


def test_story_link_confirm_prompt_forbids_naming_entities():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert "编造" in t or "不要" in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_storylink_config.py tests/contract/test_storylink_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'StoryLinkConfig'` and `FileNotFoundError` for the prompt

- [ ] **Step 3: Add `StoryLinkConfig` to `src/core/types.py`**

Add directly after `class ReleaseImportanceConfig` (which already establishes the multi-provider dataclass shape this reuses):

```python
@dataclass
class StoryLinkConfig:
    """故事线合并(spec 2026-08-28): 同一模型/产品当天的"原始发布"+"第三方支持公告"
    在发布渲染层合并成一条。两阶段: 正则抓 entity token 找候选对, 候选对过一次
    轻量 LLM 是非确认(不产出新文字)。跟 release_importance 同款多 provider 结构。"""

    enabled: bool = True
    # 默认: 字母前缀 + 可选连字符/空格 + 数字(可含小数点), 覆盖 "GLM-5.3" / "v0.28.0" / "Llama 4"
    # 这类"名称+版本号"模式。真实数据上需要反复调(2026-08-28 brainstorm 已知非最终值)。
    entity_token_pattern: str = r"\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b"
    prompt_path: str = "src/prompts/story_link_confirm.md"
    model: str = "modelscope:deepseek-ai/DeepSeek-V4-Flash"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.0
    max_tokens: int = 200
    timeout_s: int = 30
    summary_max_chars: int = 500  # 喂给确认 prompt 的摘要截断长度(防超长撑爆)
```

- [ ] **Step 4: Add `load_storylink_config` to `src/core/config.py`**

Add the import to the existing `from src.core.types import (...)` block:

```python
    ScoringConfig,
    SelfCheckConfig,
    StoryLinkConfig,
    TelegramConfig,
```

Add a new loader function, placed after `load_scoring_config` (mirrors `load_interpret_config`'s provider-loading shape):

```python
def load_storylink_config(path: str) -> StoryLinkConfig:
    """Load story-merge candidate-linking params from YAML; missing file -> defaults."""
    from src.core.types import ProviderSpec  # local import to avoid cycles

    data = _read_yaml(path)
    d = StoryLinkConfig()
    raw_providers = data.get("providers")
    if raw_providers:
        providers = {
            name: ProviderSpec(base_url=spec["base_url"], api_key_env=spec["api_key_env"])
            for name, spec in raw_providers.items()
        }
    else:
        providers = d.providers
    return StoryLinkConfig(
        enabled=data.get("enabled", d.enabled),
        entity_token_pattern=data.get("entity_token_pattern", d.entity_token_pattern),
        prompt_path=data.get("prompt_path", d.prompt_path),
        model=data.get("model", d.model),
        models=data.get("models", d.models),
        fallback_models=data.get("fallback_models", d.fallback_models),
        providers=providers,
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
    )
```

- [ ] **Step 5: Create `config/storylink.yaml`**

```yaml
# 故事线合并层配置(spec 2026-08-28)。同一模型/产品当天的多条独立发布(原始发布 +
# 第三方支持公告)在发布渲染层合并成一条, 不再各占一个 genre 配额位。
enabled: true

# entity token 正则: 字母前缀 + 可选连字符/空格 + 数字(可含小数点)。
# 覆盖 "GLM-5.3" / "v0.28.0" / "Llama 4" 这类"名称+版本号"模式。
# 真实数据上需要反复调(2026-08-28 brainstorm 给的是示例, 不是最终值)。
entity_token_pattern: '\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b'

providers:
  modelscope:
    base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
    api_key_env: "MODELSCOPE_API_KEY"

models:
  - "modelscope:deepseek-ai/DeepSeek-V4-Flash"

fallback_models:
  - "modelscope:Qwen/Qwen3.5-397B-A17B"

temperature: 0.0
max_tokens: 200
timeout_s: 30
summary_max_chars: 500
prompt_path: "src/prompts/story_link_confirm.md"
```

- [ ] **Step 6: Create `src/prompts/story_link_confirm.md`**

```markdown
你是判断两条 AI 资讯是否在讲**同一模型/产品的同一轮动态**的助手（例如"A 公司发布模型 X"与"B 平台已支持模型 X"，或"A 发布 X"与"C 也发了一篇讲 X 的文章"）。

只做是非判断，**不要编造、复述或推测任何公司名/模型名**——你的输出里不需要出现具体名字，只需要判断这两条是否在讲同一件事。

条目 A：
- 标题: {{title_a}}
- 摘要: {{summary_a}}

条目 B：
- 标题: {{title_b}}
- 摘要: {{summary_b}}

只输出 JSON，结构如下（不要额外解释）：
{"same_story": true, "reason": "..."}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_storylink_config.py tests/contract/test_storylink_prompt.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add src/core/types.py src/core/config.py config/storylink.yaml src/prompts/story_link_confirm.md tests/contract/test_storylink_config.py tests/contract/test_storylink_prompt.py
git commit -m "feat(storylink): add StoryLinkConfig, loader, production config, confirm prompt"
```

---

### Task 3: Entity token extraction (pure function)

**Files:**
- Create: `src/pipeline/storylink.py`
- Test: `tests/contract/test_storylink_unit.py`

**Interfaces:**
- Produces: `extract_entity_tokens(text: str, pattern: str) -> set[str]` — returns a set of normalized (uppercased, whitespace/hyphen-collapsed) token strings.

- [ ] **Step 1: Write the failing tests**

Create `tests/contract/test_storylink_unit.py`:

```python
from src.pipeline.storylink import extract_entity_tokens

DEFAULT_PATTERN = r"\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b"


def test_extract_entity_tokens_hyphenated_version():
    assert extract_entity_tokens("GLM-5.3 released today", DEFAULT_PATTERN) == {"GLM-5.3"}


def test_extract_entity_tokens_attached_version():
    assert extract_entity_tokens("Upgrade to v0.28.0 now", DEFAULT_PATTERN) == {"V0.28.0"}


def test_extract_entity_tokens_spaced_version():
    assert extract_entity_tokens("Llama 4 is here", DEFAULT_PATTERN) == {"LLAMA-4"}


def test_extract_entity_tokens_normalizes_case_and_separator():
    a = extract_entity_tokens("glm 5.3 dropped", DEFAULT_PATTERN)
    b = extract_entity_tokens("GLM-5.3 dropped", DEFAULT_PATTERN)
    assert a == b == {"GLM-5.3"}


def test_extract_entity_tokens_multiple_hits():
    text = "GLM-5.3 now supported alongside Llama 4 in v0.28.0 of the toolkit"
    tokens = extract_entity_tokens(text, DEFAULT_PATTERN)
    assert tokens == {"GLM-5.3", "LLAMA-4", "V0.28.0"}


def test_extract_entity_tokens_no_hits_returns_empty_set():
    assert extract_entity_tokens("A generic announcement with no version numbers", DEFAULT_PATTERN) == set()


def test_extract_entity_tokens_empty_text():
    assert extract_entity_tokens("", DEFAULT_PATTERN) == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.pipeline.storylink'`

- [ ] **Step 3: Create `src/pipeline/storylink.py`**

```python
"""storylink: 故事线合并候选识别(spec 2026-08-28)。两阶段:
1. 正则抓 entity token(型号名+版本号模式), 同一天内 token 重叠的两两配对成候选。
2. 候选对过一次轻量 LLM 确认(是非题, 不产出新文字), 确认为真才连边。
连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
LLM 调用失败/解析失败 -> fail-closed(不误合并)。"""

from __future__ import annotations

import re

_SEP_RE = re.compile(r"[-\s]+")


def extract_entity_tokens(text: str, pattern: str) -> set[str]:
    """正则抓"字母前缀+数字版本号"模式的 token, 规范化(大写 + 连字符/空格归一)去重。
    纯函数, 无副作用。空文本/无命中 -> 空集合。"""
    if not text:
        return set()
    hits = re.findall(pattern, text)
    return {_SEP_RE.sub("-", h.upper()) for h in hits}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/storylink.py tests/contract/test_storylink_unit.py
git commit -m "feat(storylink): add entity token extraction"
```

---

### Task 4: Candidate pairing (same-day + token overlap)

**Files:**
- Modify: `src/pipeline/storylink.py`
- Test: `tests/contract/test_storylink_unit.py`

**Interfaces:**
- Consumes: `extract_entity_tokens` (Task 3).
- Produces: `find_candidate_pairs(items: list[ScoredItem], pattern: str) -> list[tuple[int, int]]` — index pairs `(i, j)` with `i < j` into the input list, one entry per pair that (a) falls on the same UTC calendar day and (b) has overlapping entity tokens (computed from `title_en` + `raw_summary`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_storylink_unit.py`:

```python
from datetime import datetime, timezone

from src.core.types import Genre, Publisher, ScoredItem
from src.pipeline.storylink import find_candidate_pairs

DAY1 = datetime(2026, 8, 27, 9, tzinfo=timezone.utc)
DAY1_LATE = datetime(2026, 8, 27, 20, tzinfo=timezone.utc)
DAY2 = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)


def _item(link, title_en, published_at, raw_summary=None, score=80):
    return ScoredItem(
        title_en=title_en,
        link=link,
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=published_at,
        raw_summary=raw_summary,
        cluster_id="evt-1",
        score=score,
        score_breakdown={"机构影响力": float(score)},
    )


def test_find_candidate_pairs_same_day_overlapping_tokens():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
    ]
    pairs = find_candidate_pairs(items, DEFAULT_PATTERN)
    assert pairs == [(0, 1)]


def test_find_candidate_pairs_different_days_excluded():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY2),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_no_token_overlap_excluded():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Unrelated announcement with Llama 4", DAY1_LATE),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_no_tokens_at_all_excluded():
    items = [
        _item("https://a/1", "Generic news with no version numbers", DAY1),
        _item("https://a/2", "Another generic announcement here", DAY1_LATE),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_uses_raw_summary_too():
    items = [
        _item("https://a/1", "Company A ships a new model", DAY1, raw_summary="It's called GLM-5.3."),
        _item("https://a/2", "Platform B blog post", DAY1_LATE, raw_summary="Now supporting GLM-5.3."),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == [(0, 1)]


def test_find_candidate_pairs_three_items_multiple_pairs():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "Platform C also supports GLM-5.3", DAY1_LATE),
    ]
    pairs = find_candidate_pairs(items, DEFAULT_PATTERN)
    assert set(pairs) == {(0, 1), (0, 2), (1, 2)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v -k find_candidate_pairs`
Expected: FAIL — `ImportError: cannot import name 'find_candidate_pairs'`

- [ ] **Step 3: Implement in `src/pipeline/storylink.py`**

Add to `src/pipeline/storylink.py` (after `extract_entity_tokens`):

```python
from datetime import timezone

from src.core.types import ScoredItem


def _item_text(item: ScoredItem) -> str:
    return f"{item.title_en} {item.raw_summary or ''}"


def _same_utc_day(a, b) -> bool:
    """判定两个 tz-aware datetime 是否落在同一 UTC 日历日(确定性比较, 不依赖 ctx.now)。"""
    return a.astimezone(timezone.utc).date() == b.astimezone(timezone.utc).date()


def find_candidate_pairs(items: list[ScoredItem], pattern: str) -> list[tuple[int, int]]:
    """同一 UTC 日历日 + entity token 集合有交集 -> 候选对(纯函数)。
    O(n²) 但 n 是发卡池量级(几十条), 可接受。返回 (i, j), i < j, 按原列表下标序。"""
    tokens = [extract_entity_tokens(_item_text(it), pattern) for it in items]
    pairs: list[tuple[int, int]] = []
    for i in range(len(items)):
        if not tokens[i]:
            continue
        for j in range(i + 1, len(items)):
            if not tokens[j]:
                continue
            if not _same_utc_day(items[i].published_at, items[j].published_at):
                continue
            if tokens[i] & tokens[j]:
                pairs.append((i, j))
    return pairs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v`
Expected: PASS (all, including Task 3's tests)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/storylink.py tests/contract/test_storylink_unit.py
git commit -m "feat(storylink): add same-day token-overlap candidate pairing"
```

---

### Task 5: LLM confirmation + union-find + `link_stories()` orchestrator

**Files:**
- Modify: `src/pipeline/storylink.py`
- Test: `tests/contract/test_storylink_unit.py`

**Interfaces:**
- Consumes: `find_candidate_pairs` (Task 4), `StoryLinkConfig` (Task 2), `tests.fakes.FakeLLMProvider`/`FailingLLMProvider` (existing).
- Produces:
  - `build_confirm_prompt(item_a: ScoredItem, item_b: ScoredItem, template: str, config: StoryLinkConfig) -> str`
  - `confirm_pair(item_a: ScoredItem, item_b: ScoredItem, template: str, llm, config: StoryLinkConfig) -> bool`
  - `link_stories(items: list[ScoredItem], llm, config: StoryLinkConfig, ctx: RunContext) -> list[ScoredItem]` — the public orchestrator, returns items in the same order with `story_id` set on multi-item groups.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_storylink_unit.py`:

```python
import json
import logging
import uuid

from src.core.types import RunContext, StoryLinkConfig
from src.pipeline.storylink import build_confirm_prompt, confirm_pair, link_stories
from tests.fakes import FailingLLMProvider, FakeLLMProvider

CFG = StoryLinkConfig()
TEMPLATE = "A: {{title_a}} / {{summary_a}}\nB: {{title_b}} / {{summary_b}}"


def _ctx(now=DAY1):
    return RunContext(run_id=str(uuid.uuid4()), now=now, logger=logging.getLogger("test"))


def test_build_confirm_prompt_substitutes_both_items():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1, raw_summary="Sum A")
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE, raw_summary="Sum B")
    out = build_confirm_prompt(a, b, TEMPLATE, CFG)
    assert "Company A releases GLM-5.3" in out and "Sum A" in out
    assert "Platform B supports GLM-5.3" in out and "Sum B" in out


def test_build_confirm_prompt_truncates_long_summary():
    a = _item("https://a/1", "T", DAY1, raw_summary="X" * 1000)
    b = _item("https://a/2", "T2", DAY1_LATE, raw_summary="short")
    cfg = StoryLinkConfig(summary_max_chars=50)
    out = build_confirm_prompt(a, b, TEMPLATE, cfg)
    assert "X" * 51 not in out


def test_confirm_pair_true():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": json.dumps({"same_story": True, "reason": "same model"})})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is True


def test_confirm_pair_false():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Unrelated GLM-5.3 retrospective", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": json.dumps({"same_story": False, "reason": "different"})})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is False


def test_confirm_pair_fail_closed_on_llm_error():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    assert confirm_pair(a, b, TEMPLATE, FailingLLMProvider(), CFG) is False


def test_confirm_pair_fail_closed_on_bad_json():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": "not json"})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is False


def test_link_stories_disabled_returns_items_unchanged():
    items = [_item("https://a/1", "GLM-5.3", DAY1), _item("https://a/2", "GLM-5.3 support", DAY1_LATE)]
    cfg = StoryLinkConfig(enabled=False)
    out = link_stories(items, FailingLLMProvider(), cfg, _ctx())
    assert all(it.story_id is None for it in out)
    assert len(out) == 2


def test_link_stories_empty_input():
    assert link_stories([], FailingLLMProvider(), CFG, _ctx()) == []


def test_link_stories_merges_confirmed_pair():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "Unrelated news, no tokens here", DAY1),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    by_link = {it.link: it for it in out}
    assert by_link["https://a/1"].story_id is not None
    assert by_link["https://a/1"].story_id == by_link["https://a/2"].story_id
    assert by_link["https://a/3"].story_id is None
    assert len(out) == 3


def test_link_stories_story_id_format():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    assert out[0].story_id == "story-2026-08-27-001"


def test_link_stories_transitive_grouping():
    """A-B confirmed, B-C confirmed (but A-C never checked / no direct token overlap)
    -> all three in one connected component via union-find."""
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1, raw_summary="GLM-5.3 only"),
        _item("https://a/2", "Platform B supports GLM-5.3 and Llama 4", DAY1_LATE),
        _item("https://a/3", "Platform C supports Llama 4", DAY1_LATE, raw_summary="Llama 4 only"),
    ]
    llm = FakeLLMProvider(
        {
            "https://a/1": "",  # placeholder, overwritten below by substring match on titles
        }
    )
    # FakeLLMProvider matches by substring of the *prompt*; use title text instead
    llm = FakeLLMProvider(
        {
            "Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"}),
            "Platform C supports Llama 4": json.dumps({"same_story": True, "reason": "x"}),
        }
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    ids = {it.link: it.story_id for it in out}
    assert ids["https://a/1"] == ids["https://a/2"] == ids["https://a/3"]
    assert ids["https://a/1"] is not None


def test_link_stories_no_candidates_all_none():
    items = [
        _item("https://a/1", "Generic news one", DAY1),
        _item("https://a/2", "Generic news two", DAY1_LATE),
    ]
    out = link_stories(items, FailingLLMProvider(), CFG, _ctx(now=DAY1))
    assert all(it.story_id is None for it in out)


def test_link_stories_preserves_order_and_count():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "z-topic unrelated", DAY2),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    assert [it.link for it in out] == ["https://a/1", "https://a/2", "https://a/3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_confirm_prompt'` (and others)

- [ ] **Step 3: Implement in `src/pipeline/storylink.py`**

Add the remaining imports at the top:

```python
import json

from src.core.types import RunContext, ScoredItem, StoryLinkConfig
from src.observability.events import emit
```

(This replaces the earlier `from src.core.types import ScoredItem` single-name import from Task 4 — consolidate into one import block.)

Add after `find_candidate_pairs`:

```python
def build_confirm_prompt(
    item_a: ScoredItem, item_b: ScoredItem, template: str, config: StoryLinkConfig
) -> str:
    """Render the pairwise confirm prompt. Summaries truncated to config.summary_max_chars
    (hard cut, no ellipsis needed -- this prompt only needs enough context to judge, not
    to quote back to the reader)."""
    n = config.summary_max_chars
    repl = {
        "{{title_a}}": item_a.title_en,
        "{{summary_a}}": (item_a.raw_summary or "")[:n],
        "{{title_b}}": item_b.title_en,
        "{{summary_b}}": (item_b.raw_summary or "")[:n],
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def _parse_same_story(raw: str) -> bool:
    data = json.loads(raw)
    if not isinstance(data, dict) or "same_story" not in data:
        raise ValueError("missing same_story key")
    return bool(data["same_story"])


def confirm_pair(
    item_a: ScoredItem, item_b: ScoredItem, template: str, llm, config: StoryLinkConfig
) -> bool:
    """一次 LLM 是非确认。任何失败(网络/解析) -> False(fail-closed, spec: 宁可错过
    一次合并机会, 不要把两个不相关条目错误拼一起给读者看)。"""
    try:
        prompt = build_confirm_prompt(item_a, item_b, template, config)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        return _parse_same_story(raw)
    except Exception:
        return False


class _UnionFind:
    """按下标合并; find() 路径压缩。纯粹的实现细节, 不导出。"""

    def __init__(self, n: int):
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def link_stories(
    items: list[ScoredItem], llm, config: StoryLinkConfig, ctx: RunContext
) -> list[ScoredItem]:
    """纯函数(除 llm 调用外无副作用)。两阶段:
    1. 正则抓 entity token, 同一天内 token 重叠的两两配对成候选(find_candidate_pairs)。
    2. 候选对过一次轻量 LLM 确认(confirm_pair), 确认为真才连边。
    连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
    返回顺序/数量与输入一致(spec: 合并只在 publish 层生效, 这里只打标)。"""
    emit(ctx.logger, "storylink_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "storylink_done", grouped_count=0)
        return items

    from src.core.prompts import load_prompt

    template = load_prompt(config.prompt_path)
    pairs = find_candidate_pairs(items, config.entity_token_pattern)
    uf = _UnionFind(len(items))
    confirmed = 0
    for i, j in pairs:
        if confirm_pair(items[i], items[j], template, llm, config):
            uf.union(i, j)
            confirmed += 1

    # group indices by root
    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)

    out = list(items)
    n = 0
    for idx_list in groups.values():
        if len(idx_list) < 2:
            continue
        n += 1
        sid = f"story-{ctx.now:%Y-%m-%d}-{n:03d}"
        for idx in idx_list:
            out[idx] = out[idx].model_copy(update={"story_id": sid})

    emit(ctx.logger, "storylink_done", grouped_count=n, confirmed_pairs=confirmed)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_storylink_unit.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/storylink.py tests/contract/test_storylink_unit.py
git commit -m "feat(storylink): add LLM confirmation + union-find + link_stories orchestrator"
```

---

### Task 6: Wire `link_stories()` into `src/cli.py` (score → interpret transition)

**Files:**
- Modify: `src/cli.py:1-40` (imports), `:84-96` (new `_make_storylink_llm`), `:176-185` (`_dry_run_prefix`), `:454-465` (`run_tick`'s `_collect_and_interpret`)
- Test: `tests/contract/test_cli.py` (regression only — `link_stories` unit behavior already covered in Task 5)

**Interfaces:**
- Consumes: `src.pipeline.storylink.link_stories`, `src.core.config.load_storylink_config`, `src.core.types.StoryLinkConfig` (Tasks 2, 5).

- [ ] **Step 1: Add imports to `src/cli.py`**

Add to the `from src.core.config import (...)` block:

```python
    load_scoring_config,
    load_selfcheck_config,
    load_storylink_config,
```

Add to the `from src.core.types import (...)` block:

```python
    CollectionConfig,
    InterpretConfig,
    ProviderSpec,
    ReleaseImportanceConfig,
    RunContext,
    StoryLinkConfig,
```

Add a new pipeline import next to `from src.pipeline.score import score`:

```python
from src.pipeline.storylink import link_stories
```

- [ ] **Step 2: Add `_make_storylink_llm` next to `_make_release_importance_llm`**

```python
def _make_storylink_llm(cfg: StoryLinkConfig) -> OpenAICompatLLM:
    if cfg.models:
        primary = cfg.models[0]
        fallbacks = cfg.models[1:] + cfg.fallback_models
    else:
        primary = cfg.model
        fallbacks = cfg.fallback_models
    return OpenAICompatLLM(
        providers=cfg.providers,
        model=primary,
        timeout_s=cfg.timeout_s,
        fallback_models=fallbacks,
    )
```

- [ ] **Step 3: Wire into `_dry_run_prefix`**

Change:

```python
    if want >= _STAGES.index("score"):
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        sres = score(dres.deduped_items, scfg, ctx)

    if want >= _STAGES.index("interpret"):
        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        ires = interpret(
            sres.selected_items, icfg, ctx, llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
```

to:

```python
    if want >= _STAGES.index("score"):
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        sres = score(dres.deduped_items, scfg, ctx)

    if want >= _STAGES.index("interpret"):
        slcfg = load_storylink_config("config/storylink.yaml")
        sl_llm = _make_storylink_llm(slcfg)
        linked_items = link_stories(sres.selected_items, sl_llm, slcfg, ctx)

        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        ires = interpret(
            linked_items, icfg, ctx, llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
```

(If the content-certainty plan has not landed yet, drop the `uncertain_content_penalty=...` kwarg and just pass `linked_items, icfg, ctx, llm` — the `sres.selected_items` → `linked_items` substitution is the only change relevant to this plan.)

- [ ] **Step 4: Wire into `run_tick`'s `_collect_and_interpret`**

Change:

```python
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        quality_of = await db.get_quality_weights()
        sres = score(dres.deduped_items, scfg, ctx, quality_of=quality_of)
        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(
            sres.selected_items, icfg, ctx, _llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
        return ires
```

to:

```python
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        quality_of = await db.get_quality_weights()
        sres = score(dres.deduped_items, scfg, ctx, quality_of=quality_of)

        slcfg = load_storylink_config("config/storylink.yaml")
        sl_llm = _make_storylink_llm(slcfg)
        linked_items = link_stories(sres.selected_items, sl_llm, slcfg, ctx)

        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(
            linked_items, icfg, ctx, _llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
        return ires
```

(Same note as Step 3 if the content-certainty plan hasn't landed: drop the `uncertain_content_penalty` kwarg, keep the `linked_items` substitution.)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run pytest tests/ -x -q`
Expected: PASS — `run_dry_interpret`/`run_tick` tests use `FailingLLMProvider`/`FakeEmbeddingProvider` fixtures with no story-linkable content, so `link_stories` naturally finds zero candidates and passes items through unchanged. `StoryLinkConfig` defaults to `enabled=True`, so this exercises the real code path, not a disabled short-circuit — if any existing dry-run/tick test fails here, check whether its fixture data accidentally contains two same-day items with overlapping entity tokens and a `FailingLLMProvider`/`FakeLLMProvider` that doesn't have a matching canned response for the storylink confirm prompt; `confirm_pair`'s fail-closed behavior means this should still resolve to `story_id=None` for everyone, never an exception bubbling up.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): wire link_stories() between score() and interpret()"
```

---

### Task 7: `PublishConfig.story_merge_max_support` + loader + production YAML

**Files:**
- Modify: `src/core/types.py:367-398` (`class PublishConfig`)
- Modify: `src/core/config.py:156-170` (`load_publish_config`)
- Modify: `config/publish.yaml`
- Test: `tests/contract/test_publish_config.py`

**Interfaces:**
- Produces: `PublishConfig.story_merge_max_support: int = 3`, consumed by Task 8's `merge_story_groups`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_publish_config.py`:

```python
def test_story_merge_max_support_default():
    assert PublishConfig().story_merge_max_support == 3


def test_load_publish_config_story_merge_max_support_override(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text("story_merge_max_support: 5\n", encoding="utf-8")
    cfg = load_publish_config(str(p))
    assert cfg.story_merge_max_support == 5


def test_production_config_has_story_merge_max_support():
    c = load_publish_config("config/publish.yaml")
    assert c.story_merge_max_support == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_publish_config.py -v -k story_merge`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add the field**

In `src/core/types.py`, inside `class PublishConfig`, add directly after `adapter_quota`:

```python
    adapter_quota: dict[str, int] = field(
        default_factory=dict
    )  # 按采集渠道封顶(spec §5), 不占用 genre 配额名额
    story_merge_max_support: int = 3  # 故事线合并: 每组最多附带几个"已支持"平台提及(spec 2026-08-28)
```

- [ ] **Step 4: Wire the loader**

In `src/core/config.py::load_publish_config`, add:

```python
        adapter_quota=data.get("adapter_quota", d.adapter_quota),
        timezone=data.get("timezone", d.timezone),
        story_merge_max_support=data.get(
            "story_merge_max_support", d.story_merge_max_support
        ),
```

- [ ] **Step 5: Add to production YAML**

In `config/publish.yaml`, add:

```yaml
story_merge_max_support: 3               # 故事线合并: 每组最多附带几个"已支持"平台提及(spec 2026-08-28)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_publish_config.py -v`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add src/core/types.py src/core/config.py config/publish.yaml tests/contract/test_publish_config.py
git commit -m "feat(publish): add story_merge_max_support config field"
```

---

### Task 8: `merge_story_groups()` + wire into `build_report()`

**Files:**
- Modify: `src/pipeline/publish.py`
- Test: `tests/golden/test_publish.py`

**Interfaces:**
- Consumes: `ReviewedItem.story_id` (Task 1, inherited), `PublishConfig.story_merge_max_support` (Task 7).
- Produces: `merge_story_groups(items: list[ReviewedItem], max_support: int = 3) -> list[ReviewedItem]`, called inside `build_report()` between `apply_adapter_quota()` and `apply_quota()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/golden/test_publish.py`:

```python
from src.core.types import Evidence as _Evidence
from src.pipeline.publish import merge_story_groups


def test_merge_story_groups_passthrough_when_no_story_id():
    items = [_ri("https://a/1"), _ri("https://a/2")]
    out = merge_story_groups(items, max_support=3)
    assert len(out) == 2
    assert {i.link for i in out} == {"https://a/1", "https://a/2"}


def test_merge_story_groups_merges_group_keeps_earliest_as_primary():
    from datetime import timedelta

    early = _ri("https://a/1", score=70)
    early = early.model_copy(update={"story_id": "story-1"})
    late = _ri("https://a/2", score=90)
    late = late.model_copy(update={"story_id": "story-1", "published_at": NOW + timedelta(hours=2)})
    out = merge_story_groups([early, late], max_support=3)
    assert len(out) == 1
    assert out[0].link == "https://a/1"  # earliest = primary, regardless of score


def test_merge_story_groups_appends_support_sentence_to_primary_body():
    primary = _ri("https://a/1", body="原始正文。").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2", score=90).model_copy(
        update={"story_id": "story-1", "link": "https://support.example.com/post"}
    )
    out = merge_story_groups([primary, support], max_support=3)
    assert "原始正文。" in out[0].body
    assert "跟进支持" in out[0].body
    assert "support.example.com" in out[0].body  # domain, not internal source slug


def test_merge_story_groups_support_link_registered_as_evidence():
    primary = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2").model_copy(
        update={"story_id": "story-1", "link": "https://support.example.com/post"}
    )
    out = merge_story_groups([primary, support], max_support=3)
    anchors = [e.anchor for e in out[0].evidence]
    assert "https://support.example.com/post" in anchors


def test_merge_story_groups_never_uses_internal_source_as_display_name():
    primary = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2").model_copy(
        update={"story_id": "story-1", "link": "https://support.example.com/post", "source": "x-ai-company"}
    )
    out = merge_story_groups([primary, support], max_support=3)
    assert "x-ai-company" not in out[0].body


def test_merge_story_groups_caps_support_at_max_support_by_score():
    primary = _ri("https://a/1", score=100).model_copy(update={"story_id": "story-1"})
    supports = [
        _ri(f"https://s{i}/x", score=s).model_copy(
            update={"story_id": "story-1", "link": f"https://s{i}.example.com/post"}
        )
        for i, s in enumerate([50, 90, 70, 60], start=1)
    ]
    out = merge_story_groups([primary, *supports], max_support=2)
    assert len(out) == 1
    anchors = [e.anchor for e in out[0].evidence]
    # top 2 by score among supports: s2 (90), s3 (70)
    assert "https://s2.example.com/post" in anchors
    assert "https://s3.example.com/post" in anchors
    assert "https://s1.example.com/post" not in anchors
    assert "https://s4.example.com/post" not in anchors


def test_merge_story_groups_dropped_members_removed_from_result():
    primary = _ri("https://a/1", score=100).model_copy(update={"story_id": "story-1"})
    supports = [
        _ri(f"https://s{i}/x", score=s).model_copy(
            update={"story_id": "story-1", "link": f"https://s{i}.example.com/post"}
        )
        for i, s in enumerate([50, 90, 70, 60], start=1)
    ]
    out = merge_story_groups([primary, *supports], max_support=2)
    # 5 items in -> 1 out (primary absorbs top-2 support, drops the rest)
    assert len(out) == 1


def test_merge_story_groups_mixed_grouped_and_ungrouped():
    grouped_a = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    grouped_b = _ri("https://a/2").model_copy(update={"story_id": "story-1"})
    solo = _ri("https://a/3")
    out = merge_story_groups([grouped_a, grouped_b, solo], max_support=3)
    assert len(out) == 2
    assert {i.link for i in out} == {"https://a/1", "https://a/3"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/golden/test_publish.py -v -k merge_story_groups`
Expected: FAIL — `ImportError: cannot import name 'merge_story_groups'`

- [ ] **Step 3: Implement `merge_story_groups` in `src/pipeline/publish.py`**

Add the import:

```python
from urllib.parse import urlparse
```

Add the function, placed before `build_report`:

```python
def _support_display_name(link: str) -> str:
    """支持平台的展示名: 用链接域名, 不用 item.source(内部配置 slug)——今天刚修过
    source 泄漏喂给 LLM 当事实这个坑(#102), 渲染层不能重新踩一遍。跟
    telegram_polling.py::_make_card_message 的 link_domain 是同一约定。"""
    return urlparse(link).netloc or link


def merge_story_groups(items: list[ReviewedItem], max_support: int = 3) -> list[ReviewedItem]:
    """按 story_id 分组; 组内按 published_at 升序, 最早=原始发布(留作 primary);
    其余按 -score 排序取前 max_support 个当"已支持平台", 拼进 primary.body 末尾一句,
    并把它们的 link 登记进 primary.evidence(复用 _ref() 的既有"anchor -> 参考表"渲染
    路径, 渲染层不用改)。组内其余条目(含超出 max_support 的部分)从结果中剔除,
    不再单独占位。story_id 为 None 的条目原样透传。"""
    groups: dict[str, list[ReviewedItem]] = {}
    passthrough: list[ReviewedItem] = []
    for it in items:
        if it.story_id is None:
            passthrough.append(it)
        else:
            groups.setdefault(it.story_id, []).append(it)

    out: list[ReviewedItem] = list(passthrough)
    for group in groups.values():
        ordered = sorted(group, key=lambda it: it.published_at)
        primary, rest = ordered[0], ordered[1:]
        support = sorted(rest, key=lambda it: -it.score)[:max_support]
        if not support:
            out.append(primary)
            continue
        names = [_support_display_name(it.link) for it in support]
        suffix = f"\n\n目前已知 {'、'.join(names)} 等平台跟进支持。"
        new_evidence = list(primary.evidence) + [
            Evidence(claim=name, anchor=it.link) for name, it in zip(names, support)
        ]
        out.append(
            primary.model_copy(
                update={"body": primary.body + suffix, "evidence": new_evidence}
            )
        )
    return out
```

Add the `Evidence` import to the existing `from src.core.types import (...)` block in `publish.py`:

```python
from src.core.types import (
    CategorySection,
    DailyReport,
    Evidence,
    Overview,
    PublishConfig,
    PublishResult,
    ReviewedItem,
    ReviewResult,
    RunContext,
)
```

- [ ] **Step 4: Wire into `build_report`**

Change:

```python
    items = [
        it
        for it in review_result.reviewed_items
        if it.score >= config.min_display_score and it.relevant
    ]
    # 采集渠道封顶(spec §5): 先砍 GitHub 超额, 让 genre 配额的剩余名额优先给非 GitHub 条目
    items, _ = apply_adapter_quota(items, config.adapter_quota)
    # per-genre 配额 + total_limit: 人 keep 之后对 kept 集合施加(组成控制, 复用 score 纯函数)
    items, _ = apply_quota(items, config.quota, config.total_limit)
```

to:

```python
    items = [
        it
        for it in review_result.reviewed_items
        if it.score >= config.min_display_score and it.relevant
    ]
    # 采集渠道封顶(spec §5): 先砍 GitHub 超额, 让 genre 配额的剩余名额优先给非 GitHub 条目
    items, _ = apply_adapter_quota(items, config.adapter_quota)
    # 故事线合并(spec 2026-08-28): 先把同故事的条目收成一条再占配额, 不然故事组
    # 可能因为占了多个配额位反而把其它公司当天的公告挤掉 —— 合并要先于配额生效
    # 才能真正省位置。
    items = merge_story_groups(items, config.story_merge_max_support)
    # per-genre 配额 + total_limit: 人 keep 之后对 kept 集合施加(组成控制, 复用 score 纯函数)
    items, _ = apply_quota(items, config.quota, config.total_limit)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: PASS (all, including every pre-existing test in the file — `build_report` tests are unaffected since none of their fixtures set `story_id`, so `merge_story_groups` is a no-op passthrough for them)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 7: Real dry-run verification**

Per [[verify-scoring-changes-against-real-dry-run]] (this codebase's established practice): before treating this plan as done, run a real dry-run against a recent day's actual collected data (not just fixtures) to confirm `link_stories` finds at least one genuine "release + third-party support" candidate pair in practice, and that `merge_story_groups` collapses it correctly in the rendered markdown. This step has no fixed pass/fail assertion — it's a manual sanity check the implementer reports back on, e.g.:

```bash
MODELSCOPE_API_KEY=... uv run python -m src.cli dry-interpret --registry config/sources.yaml
```

then inspect the `story_id` values in the output JSON, and separately render a `dry-publish`-equivalent (or a short ad-hoc script calling `merge_story_groups` on real `ReviewedItem` data) to eyeball the merged body text before considering this plan shippable.

- [ ] **Step 8: Commit**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py
git commit -m "feat(publish): merge story groups into one card before per-genre quota"
```

---

## Self-Review

**Spec coverage:**
- ✅ §1 `NewsItem.story_id`: Task 1.
- ✅ §2 `link_stories()` — token extraction, candidate pairing, LLM confirm, union-find, fail-closed: Tasks 3, 4, 5.
- ✅ §2 位置(score 之后, interpret 之前): Task 6.
- ✅ §3 `config/storylink.yaml` + `StoryLinkConfig`: Task 2.
- ✅ §4 `merge_story_groups()`, 位置在 `apply_quota()` 之前: Task 8.
- ✅ §4 显示名不用 `item.source`: Task 8 (`_support_display_name` uses link domain; explicit test `test_merge_story_groups_never_uses_internal_source_as_display_name`).
- ✅ §4 `PublishConfig.story_merge_max_support`: Task 7.
- ✅ 不改 `dedup.cluster()`: confirmed — no changes to `src/pipeline/dedup.py` anywhere in this plan.
- ✅ 不合并到审阅卡片层: confirmed — `tick.py::_build_card` and `telegram_polling.py` are untouched by this plan; merging only happens in `publish.py::build_report()`, which runs at finalize time, after review.
- ✅ 不做通用实体链接: confirmed — regex + same-day + yes/no LLM confirm only, no NER/KG.
- ✅ 测试要点: token 抽取纯函数单测(Task 3), 候选配对(Task 4), LLM 确认 mock 三态(Task 5: true/false/exception + bad-json), 并查集分组正确性(Task 5: transitive grouping test), 跨天不配对(Task 4), merge_story_groups 全部 6 项(Task 8), 端到端真实 dry-run 对比(Task 8 Step 7).

**Placeholder scan:** Task 4's Step 3 intentionally shows a drafted-then-corrected `_same_utc_day` to demonstrate the reasoning and explicitly tells the implementer which version ships — this is not a placeholder, it's worked-through logic; the final code block is complete and correct. No other placeholders found.

**Type consistency:** `link_stories(items: list[ScoredItem], llm, config: StoryLinkConfig, ctx: RunContext) -> list[ScoredItem]` matches the spec's signature exactly. `merge_story_groups(items: list[ReviewedItem], max_support: int = 3) -> list[ReviewedItem]` matches. `story_id` field name is identical across `NewsItem`, `find_candidate_pairs`'s consumers, `link_stories`, and `merge_story_groups`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-28-story-merge.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
