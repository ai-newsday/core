# HN + Reddit 信号源 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Hacker News 和 Reddit 两个 source adapter,把人群高赞内容作为自带信号(points/upvotes)的候选 item 引入,均归 `(writeup, individual)`。

**Architecture:** 沿用现有 adapter 模式(实现 `SourceAdapter.fetch(source, ctx, timeout) -> list[RawItem]`,在 `ADAPTERS` dict 注册,collect 层统一做窗口过滤/`max_items`/source_report)。HN 走 Algolia `front_page` API,Reddit 走公开 `.json` + 描述性 UA。过滤(points/upvotes 阈值 + HN 的 AI 关键词)在 adapter 内,阈值/关键词读 `SourceSpec` 字段(config 驱动)。dedup/enrich/score 不改。

**Tech Stack:** Python 3.12、httpx(async)、respx(测试 mock)、pydantic、pytest、uv。

**基线:** 分支 `feat/hn-reddit-sources`,基于真实 master(不含 #18 refactor)。spec:`docs/superpowers/specs/2026-06-18-hn-reddit-sources-design.md`。

---

## File Structure

- **Modify** `src/core/types.py` — `SourceSpec.adapter` Literal 加 `hn`/`reddit`;新增可选 `min_score: int | None`、`keywords: list[str] | None`。
- **Create** `src/adapters/sources/hn.py` — `HNAdapter`(Algolia front_page)。
- **Create** `src/adapters/sources/reddit.py` — `RedditAdapter`(`.json` top)。
- **Modify** `src/adapters/sources/__init__.py` — 注册 `hn`/`reddit`。
- **Modify** `config/sources.yaml` — 新增 1 行 HN + 8 行 Reddit。
- **Create** `tests/contract/test_hn_adapter.py`、`tests/contract/test_reddit_adapter.py`。
- **Modify** `tests/contract/test_adapter_registry.py`、`tests/contract/test_types.py`。

`pytest.ini`/配置已支持 async 测试(现有 `test_rss_adapter.py` 用裸 `async def` + `@respx.mock`,照抄即可)。

---

## Task 1: SourceSpec 加 adapter 值 + min_score/keywords

**Files:**
- Modify: `src/core/types.py`
- Test: `tests/contract/test_types.py`

- [ ] **Step 1: 加失败测试**

在 `tests/contract/test_types.py` 末尾追加:

```python
def test_sourcespec_accepts_hn_reddit_and_filter_fields():
    s = SourceSpec(
        name="hackernews",
        url="https://hn.algolia.com/api/v1/search?tags=front_page",
        genre="writeup",
        publisher="individual",
        adapter="hn",
        min_score=100,
        keywords=["AI", "LLM"],
    )
    assert s.adapter == "hn"
    assert s.min_score == 100 and s.keywords == ["AI", "LLM"]
    r = SourceSpec(
        name="reddit-localllama",
        url="https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=25",
        genre="writeup",
        publisher="individual",
        adapter="reddit",
    )
    assert r.adapter == "reddit"
    assert r.min_score is None and r.keywords is None  # optional, default None
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_types.py::test_sourcespec_accepts_hn_reddit_and_filter_fields -q`
Expected: FAIL（`adapter` Literal 不接受 `"hn"` → ValidationError）

- [ ] **Step 3: 改 types.py**

`SourceSpec`(找到 `adapter: Literal[...]` 那行)改为:

```python
class SourceSpec(BaseModel):
    name: str
    url: str
    genre: Genre
    publisher: Publisher
    adapter: Literal["rss", "hf_papers", "hf_models", "hn", "reddit"]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False
    max_items: int | None = None  # truncate fetched items to this cap (e.g. arXiv firehose)
    min_score: int | None = None  # HN points / Reddit ups 下限; None = 不过滤
    keywords: list[str] | None = None  # HN AI 关键词(标题/URL 命中); Reddit 不填
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_types.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_types.py
git commit -m "feat(types): SourceSpec supports hn/reddit adapters + min_score/keywords"
```

---

## Task 2: HN adapter

**Files:**
- Create: `src/adapters/sources/hn.py`
- Test: `tests/contract/test_hn_adapter.py`

- [ ] **Step 1: 写失败测试**

`tests/contract/test_hn_adapter.py`:

```python
import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.hn import HNAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50"


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.hn"),
    )


def _spec(min_score=100, keywords=("AI", "LLM", "model")):
    return SourceSpec(
        name="hackernews",
        url=_URL,
        genre=Genre.writeup,
        publisher=Publisher.individual,
        adapter="hn",
        min_score=min_score,
        keywords=list(keywords),
    )


def _hits(*hits):
    return {"hits": list(hits)}


def _hit(title, points, url="https://ex.com/a", oid="111", comments=5, created=1_750_000_000):
    return {
        "title": title,
        "url": url,
        "points": points,
        "num_comments": comments,
        "objectID": oid,
        "created_at_i": created,
    }


@respx.mock
async def test_hn_maps_fields_and_signals():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("New LLM model breaks records", 250)
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "New LLM model breaks records"
    assert it.link == "https://ex.com/a"
    assert it.genre == Genre.writeup and it.publisher == Publisher.individual
    assert it.signals == {"points": 250, "num_comments": 5}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_hn_filters_by_points_threshold():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("AI breakthrough", 50)  # below min_score=100
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_hn_filters_by_keyword():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        _hit("New Rust web framework released", 300)  # no AI keyword
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_hn_self_post_falls_back_to_discussion_link():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_hits(
        {"title": "Ask HN: best LLM tooling?", "url": None, "points": 200,
         "num_comments": 9, "objectID": "999", "created_at_i": 1_750_000_000}
    )))
    items = await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items[0].link == "https://news.ycombinator.com/item?id=999"


@respx.mock
async def test_hn_http_error_raises():
    respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await HNAdapter().fetch(_spec(), _ctx(), timeout_s=15)
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_hn_adapter.py -q`
Expected: FAIL（`ModuleNotFoundError: src.adapters.sources.hn`）

- [ ] **Step 3: 写实现**

`src/adapters/sources/hn.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec


class HNAdapter:
    """Hacker News front-page via Algolia. Keeps AI-relevant, high-point stories
    as (writeup, individual) items carrying `points` as signal."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            hits = (resp.json() or {}).get("hits") or []

        kws = [k.lower() for k in (source.keywords or [])]
        items: list[RawItem] = []
        for h in hits:
            title = h.get("title")
            created = h.get("created_at_i")
            if not title or created is None:
                continue
            points = h.get("points") or 0
            if source.min_score is not None and points < source.min_score:
                continue
            url = h.get("url")
            haystack = f"{title} {url or ''}".lower()
            if kws and not any(k in haystack for k in kws):
                continue
            link = url or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            signals = {"points": points, "num_comments": h.get("num_comments")}
            signals = {k: v for k, v in signals.items() if v not in (None, "")}
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=datetime.fromtimestamp(int(created), tz=timezone.utc),
                    signals=signals,
                    fetched_via="native",
                )
            )
        return items
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_hn_adapter.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add src/adapters/sources/hn.py tests/contract/test_hn_adapter.py
git commit -m "feat(sources): HN adapter (Algolia front_page, points signal + AI filter)"
```

---

## Task 3: Reddit adapter

**Files:**
- Create: `src/adapters/sources/reddit.py`
- Test: `tests/contract/test_reddit_adapter.py`

- [ ] **Step 1: 写失败测试**

`tests/contract/test_reddit_adapter.py`:

```python
import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.reddit import RedditAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_URL = "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=25"


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.reddit"),
    )


def _spec(min_score=50):
    return SourceSpec(
        name="reddit-localllama",
        url=_URL,
        genre=Genre.writeup,
        publisher=Publisher.individual,
        adapter="reddit",
        min_score=min_score,
    )


def _listing(*posts):
    return {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


def _post(title, ups, url="https://ex.com/a", is_self=False, permalink="/r/x/comments/1/p/",
          selftext="", comments=3, created=1_750_000_000.0):
    return {
        "title": title, "url": url, "ups": ups, "is_self": is_self,
        "permalink": permalink, "selftext": selftext, "num_comments": comments,
        "created_utc": created,
    }


@respx.mock
async def test_reddit_maps_fields_and_signals():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("New 70B model dropped", 420)
    )))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "New 70B model dropped"
    assert it.link == "https://ex.com/a"
    assert it.genre == Genre.writeup and it.publisher == Publisher.individual
    assert it.signals == {"upvotes": 420, "num_comments": 3}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_reddit_filters_by_upvotes_threshold():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("minor question", 10)  # below min_score=50
    )))
    items = await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_reddit_self_post_uses_permalink_and_selftext():
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing(
        _post("Guide: running LLMs locally", 300, is_self=True,
              permalink="/r/LocalLLaMA/comments/abc/guide/", selftext="Step 1 ...")
    )))
    it = (await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15))[0]
    assert it.link == "https://www.reddit.com/r/LocalLLaMA/comments/abc/guide/"
    assert it.raw_summary == "Step 1 ..."


@respx.mock
async def test_reddit_sends_user_agent():
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=_listing()))
    await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert route.called
    ua = route.calls.last.request.headers.get("user-agent", "")
    assert "ai-newsday" in ua  # not a generic UA (Reddit 429s those)


@respx.mock
async def test_reddit_http_error_raises():
    respx.get(_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        await RedditAdapter().fetch(_spec(), _ctx(), timeout_s=15)
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_reddit_adapter.py -q`
Expected: FAIL（`ModuleNotFoundError: src.adapters.sources.reddit`）

- [ ] **Step 3: 写实现**

`src/adapters/sources/reddit.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.core.types import RawItem, RunContext, SourceSpec

# Reddit 429s generic UAs; a descriptive one is required even for public .json.
_USER_AGENT = "ai-newsday/1.0 (https://github.com/ai-newsday/core)"


class RedditAdapter:
    """Subreddit top.json → (writeup, individual) items carrying `upvotes` as signal."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            children = ((resp.json() or {}).get("data") or {}).get("children") or []

        items: list[RawItem] = []
        for c in children:
            d = c.get("data") or {}
            title = d.get("title")
            created = d.get("created_utc")
            if not title or created is None:
                continue
            ups = d.get("ups") or 0
            if source.min_score is not None and ups < source.min_score:
                continue
            if d.get("is_self"):
                link = f"https://www.reddit.com{d.get('permalink', '')}"
                raw_summary = (d.get("selftext") or "")[:500] or None
            else:
                link = d.get("url") or f"https://www.reddit.com{d.get('permalink', '')}"
                raw_summary = None
            signals = {"upvotes": ups, "num_comments": d.get("num_comments")}
            signals = {k: v for k, v in signals.items() if v not in (None, "")}
            items.append(
                RawItem(
                    title_en=title,
                    link=link,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=datetime.fromtimestamp(float(created), tz=timezone.utc),
                    raw_summary=raw_summary,
                    signals=signals,
                    fetched_via="native",
                )
            )
        return items
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_reddit_adapter.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add src/adapters/sources/reddit.py tests/contract/test_reddit_adapter.py
git commit -m "feat(sources): Reddit adapter (.json top, upvotes signal + UA)"
```

---

## Task 4: 注册 adapters

**Files:**
- Modify: `src/adapters/sources/__init__.py`
- Test: `tests/contract/test_adapter_registry.py`

- [ ] **Step 1: 改测试(先红)**

`tests/contract/test_adapter_registry.py` 整体替换为:

```python
from src.adapters.sources import ADAPTERS
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.reddit import RedditAdapter
from src.adapters.sources.rss import RSSAdapter


def test_adapters_map_covers_all_adapter_keys():
    assert set(ADAPTERS) == {"rss", "hf_papers", "hf_models", "hn", "reddit"}
    assert isinstance(ADAPTERS["rss"], RSSAdapter)
    assert isinstance(ADAPTERS["hf_papers"], HFPapersAdapter)
    assert isinstance(ADAPTERS["hf_models"], HFModelsAdapter)
    assert isinstance(ADAPTERS["hn"], HNAdapter)
    assert isinstance(ADAPTERS["reddit"], RedditAdapter)
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_adapter_registry.py -q`
Expected: FAIL（ImportError / KeyError）

- [ ] **Step 3: 改 `__init__.py`**

`src/adapters/sources/__init__.py` 整体:

```python
from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.reddit import RedditAdapter
from src.adapters.sources.rss import RSSAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
    "hn": HNAdapter(),
    "reddit": RedditAdapter(),
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_adapter_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/adapters/sources/__init__.py tests/contract/test_adapter_registry.py
git commit -m "feat(sources): register hn + reddit adapters"
```

---

## Task 5: sources.yaml 新增 HN + Reddit 行

**Files:**
- Modify: `config/sources.yaml`
- Test: `tests/contract/test_sources_yaml.py`(应保持绿)

- [ ] **Step 1: 加行**

在 `config/sources.yaml` 末尾(HN-popularity 博客块之前或之后均可,建议新起一节)追加:

```yaml
# --- aggregators (人群信号源: HN points / Reddit upvotes) ---
- {name: hackernews, url: "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50", genre: writeup, publisher: individual, adapter: hn, status: working, priority: 2, min_score: 100, max_items: 15, keywords: [AI, LLM, GPT, model, agent, diffusion, neural, transformer, "machine learning", inference, RAG, multimodal, "open source", openai, anthropic, gemini, llama]}
- {name: reddit-localllama, url: "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-machinelearning, url: "https://www.reddit.com/r/MachineLearning/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-openai, url: "https://www.reddit.com/r/OpenAI/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-claudeai, url: "https://www.reddit.com/r/ClaudeAI/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-stablediffusion, url: "https://www.reddit.com/r/StableDiffusion/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-geminiai, url: "https://www.reddit.com/r/GeminiAI/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-midjourney, url: "https://www.reddit.com/r/midjourney/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 50, max_items: 10}
- {name: reddit-comfyui, url: "https://www.reddit.com/r/comfyui/top.json?t=day&limit=25", genre: writeup, publisher: individual, adapter: reddit, status: working, priority: 3, min_score: 30, max_items: 10}
```

(阈值为保守起步值,待子项目 4 看板校准。)

- [ ] **Step 2: 跑校验**

Run: `uv run pytest tests/contract/test_sources_yaml.py -q`
Expected: PASS（全部行过 `SourceSpec` 校验,无重复 URL）

- [ ] **Step 3: Commit**

```bash
git add config/sources.yaml
git commit -m "config(sources): add Hacker News + 8 Reddit aggregator sources"
```

---

## Task 6: 全量回归 + 真实联网抽查

**Files:** 无(验证)

- [ ] **Step 1: 全量测试 + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 全绿、ruff 干净。

- [ ] **Step 2: dry-run 集成冒烟(联网)**

Run:
```bash
uv run python -m src.cli --dry-run > /tmp/collect_hn_reddit.json 2>/tmp/collect.err
```
检查 `source_reports` 里 `hackernews` 和 `reddit-*` 的 `status`/`item_count`;用 `uv run python` 读 JSON,确认这些源产出的 item 带 `signals.points`/`signals.upvotes`、`genre=writeup`、`publisher=individual`、`link` 合理(外链 or permalink)。

- [ ] **Step 3: 判定 + 记录**

- HN/Reddit 有 item 进来且带信号 → 通过。
- 若 Reddit 某源 `status=failed`(429)→ 记录;若多数 429,标该批 `manual` 并在 PR 注明"需 OAuth(子项目后续)"。
- 阈值若明显过滤过狠/过松,记进 PR 描述,留子项目 4 看板校准(本期不调)。

- [ ] **Step 4: 无 commit**(纯验证;如调了 sources.yaml 阈值并入 Task 5 后续 commit)

---

## Self-Review

- **Spec 覆盖:** Schema(T1)、HN adapter+过滤+link 回退+signals(T2)、Reddit adapter+UA+self-post+signals(T3)、注册(T4)、sources.yaml 9 行(T5)、dry-run 冒烟+429 应对(T6)。spec 的"dedup/enrich/score 不改"= 计划不含这些文件 ✓。
- **Placeholder 扫描:** 无 TBD;阈值为具体起步值;每个 code step 给完整代码。
- **类型一致:** `HNAdapter`/`RedditAdapter` 类名、`min_score`/`keywords` 字段名、`signals` 键(`points`/`upvotes`/`num_comments`)在 spec、实现、测试间一致;`adapter` Literal 五值一致。
- **风险:** Reddit 真实 429 是运行期风险(T6 Step 3 有应对),非代码缺陷。
