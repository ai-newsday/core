# Collection Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the AI News Daily collection layer (`docs/specs/collection.md`): a `collect()` function that fetches recent items from multiple sources in parallel, normalizes them to `RawItem`, and never lets a single source failure break the chain.

**Architecture:** Provider/adapter decoupling. `collect()` (pure orchestration) loads a source registry, dispatches each source to its `SourceAdapter` concurrently via `asyncio`, applies a fixed 24h time-window filter, and returns `CollectionResult`. Network/IO lives only in adapters; types and contracts live in `src/core/`. No dedup/scoring/LLM here (those are downstream layers).

**Tech Stack:** Python 3.12 (uv), `httpx` (async HTTP), `feedparser` (RSS/Atom), `pydantic` v2 (validated data contracts), `pyyaml` (registry), `pytest` + `pytest-asyncio` + `respx` (httpx mocking) for tests.

**Locked decisions (from brainstorm):**
- Source strategy = Option 3: ~30 AI-relevant sources `status: working`; ~80 general blogs from the HN-popularity gist written into yaml as `status: manual` (not run this circle).
- `source_type` enum = `paper｜model｜tool｜community｜official｜news｜blog` (adds `blog`; requires spec §4 edit).
- GitHub deferred entirely this circle.
- Fixed 24h window — no 36h fallback in `collect()` this circle.
- Firecrawl off: `needs_firecrawl` sources marked `failed` and skipped.
- No retries; single attempt; `httpx` async + `asyncio.gather`; concurrency 10.
- Missing-timezone datetimes coerced to UTC; items with no date dropped.
- HF Models uses `createdAt`.
- `runs` events go to a lightweight structured logger (no SQLite this circle).

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | uv project, pin Python 3.12, deps |
| `CLAUDE.md` | moved from `docs/CLAUDE.md` to repo root (auto-loaded) |
| `docs/specs/collection.md` | moved from `docs/collection.md`; §4 edited to add `blog` |
| `config/sources.yaml` | single source of truth registry (~110 entries) |
| `references/hn-popular-blogs-2025.opml` | raw gist OPML, kept for provenance |
| `scripts/opml_to_sources.py` | one-off: append gist blogs as `manual` entries |
| `src/core/types.py` | `SourceType`, `RawItem`, `SourceReport`, `SourceSpec`, `CollectionConfig`, `RunContext`, `CollectionResult` |
| `src/core/registry.py` | `load_registry()` + hardcoded `FALLBACK_SOURCES` |
| `src/observability/events.py` | `emit()` structured event helper |
| `src/adapters/sources/base.py` | `SourceAdapter` Protocol |
| `src/adapters/sources/rss.py` | `RSSAdapter` (feedparser) |
| `src/adapters/sources/hf_papers.py` | `HFPapersAdapter` |
| `src/adapters/sources/hf_models.py` | `HFModelsAdapter` |
| `src/adapters/sources/__init__.py` | `ADAPTERS` registry mapping name→adapter |
| `src/pipeline/collect.py` | `collect()` orchestration |
| `src/cli.py` | `--dry-run` entry point |
| `tests/contract/test_*.py` | per-adapter schema contract tests |
| `tests/golden/test_collect.py` | the 5 golden cases (spec §8) + invariants (spec §7) |
| `fixtures/sources/*` | frozen response samples |

---

## Provider Interface Signatures (locked — every later task must match)

```python
# src/core/types.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol
import logging
from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    PAPER = "paper"
    MODEL = "model"
    TOOL = "tool"
    COMMUNITY = "community"
    OFFICIAL = "official"
    NEWS = "news"
    BLOG = "blog"


class RawItem(BaseModel):
    title_en: str = Field(min_length=1)
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_type: SourceType
    published_at: datetime              # MUST be tz-aware
    raw_summary: str | None = None
    image_url: str | None = None
    fetched_via: Literal["native", "firecrawl"] = "native"

    @field_validator("published_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return v


class SourceReport(BaseModel):
    name: str
    status: Literal["working", "failed", "empty"]
    item_count: int
    error: str | None = None
    elapsed_ms: int


class SourceSpec(BaseModel):
    name: str
    url: str
    type: SourceType
    adapter: Literal["rss", "hf_papers", "hf_models"]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False


@dataclass
class CollectionConfig:
    sources_registry_path: str
    window_hours: int = 24
    max_window_hours: int = 36
    concurrency: int = 10
    timeout_s: int = 15
    firecrawl_enabled: bool = False


@dataclass
class RunContext:
    run_id: str
    now: datetime                       # injected for determinism; MUST be tz-aware
    logger: logging.Logger


@dataclass
class CollectionResult:
    items: list[RawItem]
    source_reports: list[SourceReport]
    is_silent: bool


# src/adapters/sources/base.py
class SourceAdapter(Protocol):
    async def fetch(
        self, source: SourceSpec, ctx: RunContext, timeout_s: int
    ) -> list[RawItem]:
        """Fetch + normalize. Raises on transport/parse error (collect() isolates)."""
        ...


# src/pipeline/collect.py
async def collect(config: CollectionConfig, run_ctx: RunContext) -> CollectionResult: ...
```

---

## Task 0: Scaffold repo, pin Python, move docs

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Move: `docs/CLAUDE.md` → `CLAUDE.md`
- Move: `docs/collection.md` → `docs/specs/collection.md`
- Create: empty package dirs with `__init__.py`

- [ ] **Step 1: Init uv project pinned to 3.12**

```bash
cd /Users/nev4rb14su/workspace/ai-newsday
uv init --python 3.12 --no-readme -q
uv add httpx feedparser pydantic pyyaml
uv add --dev pytest pytest-asyncio respx
```

- [ ] **Step 2: Configure pytest asyncio mode in `pyproject.toml`**

Append:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Move docs to canonical locations**

```bash
git mv docs/CLAUDE.md CLAUDE.md
mkdir -p docs/specs
git mv docs/collection.md docs/specs/collection.md
```

- [ ] **Step 4: Create package skeleton**

```bash
mkdir -p src/core src/adapters/sources src/pipeline src/observability \
  tests/contract tests/golden fixtures/sources config references scripts
touch src/__init__.py src/core/__init__.py src/adapters/__init__.py \
  src/adapters/sources/__init__.py src/pipeline/__init__.py \
  src/observability/__init__.py tests/__init__.py
```

- [ ] **Step 5: Verify toolchain**

Run: `uv run python -c "import httpx, feedparser, pydantic, yaml; print('ok')"`
Expected: prints `ok`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "scaffold: uv project, package skeleton, move CLAUDE.md/spec to canonical paths"
```

---

## Task 1: Spec edit — add `blog` to `source_type`

**Files:**
- Modify: `docs/specs/collection.md` (the `RawItem` block in §4, `source_type` line)

- [ ] **Step 1: Edit the enum comment in §4**

Change the `source_type` line inside the `RawItem` class block from:

```python
    source_type: str     # paper|model|tool|community|official|news
```

to:

```python
    source_type: str     # paper|model|tool|community|official|news|blog
```

- [ ] **Step 2: Verify**

Run: `grep -n "paper|model|tool|community|official|news|blog" docs/specs/collection.md`
Expected: one match line.

- [ ] **Step 3: Commit**

```bash
git add docs/specs/collection.md
git commit -m "spec: add blog to source_type enum (collection §4)"
```

---

## Task 2: Core types + validation

**Files:**
- Create: `src/core/types.py`
- Test: `tests/contract/test_types.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/contract/test_types.py
from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
from src.core.types import RawItem, SourceType, SourceReport, SourceSpec


def _utc(h_ago=0):
    return datetime.now(timezone.utc) - timedelta(hours=h_ago)


def test_rawitem_minimal_valid():
    it = RawItem(
        title_en="GPT-X released",
        link="https://example.com/a",
        source="openai",
        source_type=SourceType.OFFICIAL,
        published_at=_utc(),
    )
    assert it.fetched_via == "native"
    assert it.raw_summary is None


def test_rawitem_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="x", link="https://e.com", source="s",
            source_type=SourceType.PAPER,
            published_at=datetime(2026, 5, 30, 12, 0, 0),  # naive
        )


def test_rawitem_rejects_empty_required():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="", link="https://e.com", source="s",
            source_type=SourceType.PAPER, published_at=_utc(),
        )


def test_blog_is_valid_source_type():
    assert SourceType("blog") == SourceType.BLOG


def test_sourcereport_status_literal():
    r = SourceReport(name="openai", status="working", item_count=3, elapsed_ms=120)
    assert r.error is None


def test_sourcespec_defaults():
    s = SourceSpec(name="hf-papers", url="https://huggingface.co/api/papers",
                   type=SourceType.PAPER, adapter="hf_papers")
    assert s.status == "working" and s.priority == 3 and s.needs_firecrawl is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.core.types'`

- [ ] **Step 3: Implement `src/core/types.py`**

Paste the full contents from the **Provider Interface Signatures** section above (the `src/core/types.py` block, including all imports, `SourceType`, `RawItem`, `SourceReport`, `SourceSpec`, `CollectionConfig`, `RunContext`, `CollectionResult`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_types.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_types.py
git commit -m "feat(core): RawItem/SourceReport/SourceSpec contracts with tz + non-empty validation"
```

---

## Task 3: Observability `emit()`

**Files:**
- Create: `src/observability/events.py`
- Test: `tests/contract/test_events.py`

- [ ] **Step 1: Write failing test**

```python
# tests/contract/test_events.py
import json, logging
from src.observability.events import emit


def test_emit_logs_structured_json(caplog):
    logger = logging.getLogger("test.events")
    with caplog.at_level(logging.INFO, logger="test.events"):
        emit(logger, "source_fetch_success", name="openai", item_count=3)
    rec = caplog.records[-1]
    payload = json.loads(rec.message)
    assert payload["event"] == "source_fetch_success"
    assert payload["name"] == "openai"
    assert payload["item_count"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/observability/events.py`**

```python
# src/observability/events.py
import json
import logging
from typing import Any


def emit(logger: logging.Logger, event: str, **params: Any) -> None:
    """Write one structured event as a JSON log line (runs-record stand-in)."""
    payload: dict[str, Any] = {"event": event, **params}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/observability/events.py tests/contract/test_events.py
git commit -m "feat(obs): structured emit() for runs events"
```

---

## Task 4: Registry loader + fallback

**Files:**
- Create: `src/core/registry.py`
- Test: `tests/contract/test_registry.py`
- Create (test fixture): `tests/golden/data/registry_min.yaml`

- [ ] **Step 1: Create a tiny valid registry fixture**

```yaml
# tests/golden/data/registry_min.yaml
- name: hf-papers
  url: https://huggingface.co/api/papers
  type: paper
  adapter: hf_papers
  status: working
  priority: 1
- name: openai
  url: https://openai.com/news/rss.xml
  type: official
  adapter: rss
  status: working
  priority: 2
- name: some-blog
  url: https://example.com/feed.xml
  type: blog
  adapter: rss
  status: manual
  priority: 3
```

- [ ] **Step 2: Write failing tests**

```python
# tests/contract/test_registry.py
import logging
from datetime import datetime, timezone
from src.core.registry import load_registry, FALLBACK_SOURCES
from src.core.types import RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime.now(timezone.utc),
                      logger=logging.getLogger("test.registry"))


def test_load_returns_only_working_sources():
    specs = load_registry("tests/golden/data/registry_min.yaml", _ctx())
    names = {s.name for s in specs}
    assert names == {"hf-papers", "openai"}        # 'some-blog' is manual -> excluded


def test_missing_file_falls_back_and_warns(caplog):
    with caplog.at_level(logging.INFO, logger="test.registry"):
        specs = load_registry("does/not/exist.yaml", _ctx())
    assert specs == FALLBACK_SOURCES
    assert any("registry_load_failed" in r.message for r in caplog.records)


def test_fallback_sources_are_all_working():
    assert FALLBACK_SOURCES
    assert all(s.status == "working" for s in FALLBACK_SOURCES)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `src/core/registry.py`**

```python
# src/core/registry.py
from __future__ import annotations
import yaml
from src.core.types import RunContext, SourceSpec, SourceType
from src.observability.events import emit

FALLBACK_SOURCES: list[SourceSpec] = [
    SourceSpec(name="hf-papers", url="https://huggingface.co/api/papers",
               type=SourceType.PAPER, adapter="hf_papers", status="working", priority=1),
    SourceSpec(name="openai", url="https://openai.com/news/rss.xml",
               type=SourceType.OFFICIAL, adapter="rss", status="working", priority=2),
    SourceSpec(name="deepmind", url="https://deepmind.google/blog/rss.xml",
               type=SourceType.OFFICIAL, adapter="rss", status="working", priority=2),
]


def load_registry(path: str, ctx: RunContext) -> list[SourceSpec]:
    """Load enabled (status=working) sources. On any load/parse error, fall back."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or []
        specs = [SourceSpec(**entry) for entry in raw]
    except Exception as e:  # noqa: BLE001 - load must never be fatal
        emit(ctx.logger, "registry_load_failed", path=path, error=str(e))
        return FALLBACK_SOURCES
    return [s for s in specs if s.status == "working"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/core/registry.py tests/contract/test_registry.py tests/golden/data/registry_min.yaml
git commit -m "feat(core): registry loader filters to working sources + hardcoded fallback"
```

---

## Task 5: RSS adapter

**Files:**
- Create: `src/adapters/sources/base.py`
- Create: `src/adapters/sources/rss.py`
- Create (fixture): `fixtures/sources/rss_sample.xml`
- Test: `tests/contract/test_rss_adapter.py`

- [ ] **Step 1: Create RSS fixture (two items; one missing date)**

```xml
<!-- fixtures/sources/rss_sample.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>OpenAI</title>
  <item>
    <title>Introducing GPT-X</title>
    <link>https://openai.com/blog/gpt-x</link>
    <description>A new frontier model.</description>
    <pubDate>Sat, 30 May 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Undated announcement</title>
    <link>https://openai.com/blog/undated</link>
    <description>No date here.</description>
  </item>
</channel></rss>
```

- [ ] **Step 2: Write failing contract test**

```python
# tests/contract/test_rss_adapter.py
import logging
from datetime import datetime, timezone
import httpx, respx, pytest
from src.adapters.sources.rss import RSSAdapter
from src.core.types import SourceSpec, SourceType, RunContext, RawItem


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.rss"))


def _spec():
    return SourceSpec(name="openai", url="https://openai.com/news/rss.xml",
                      type=SourceType.OFFICIAL, adapter="rss")


@respx.mock
async def test_rss_parses_and_drops_undated():
    xml = open("fixtures/sources/rss_sample.xml", "rb").read()
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=xml))
    items = await RSSAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1                       # undated item dropped
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "Introducing GPT-X"
    assert it.source == "openai"
    assert it.source_type == SourceType.OFFICIAL
    assert it.published_at.tzinfo is not None     # tz-aware (UTC)
    assert it.fetched_via == "native"


@respx.mock
async def test_rss_http_error_raises():
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await RSSAdapter().fetch(_spec(), _ctx(), timeout_s=15)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_rss_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: src.adapters.sources.rss`

- [ ] **Step 4: Implement base Protocol + RSS adapter**

```python
# src/adapters/sources/base.py
from typing import Protocol
from src.core.types import SourceSpec, RunContext, RawItem


class SourceAdapter(Protocol):
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        ...
```

```python
# src/adapters/sources/rss.py
from __future__ import annotations
from calendar import timegm
from datetime import datetime, timezone
import feedparser
import httpx
from src.core.types import SourceSpec, RunContext, RawItem


def _published_utc(entry) -> datetime | None:
    tm = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if tm is None:
        return None
    return datetime.fromtimestamp(timegm(tm), tz=timezone.utc)


def _image_url(entry) -> str | None:
    for m in getattr(entry, "media_content", []) or []:
        if m.get("url"):
            return m["url"]
    for enc in getattr(entry, "enclosures", []) or []:
        if enc.get("href"):
            return enc["href"]
    return None


class RSSAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        items: list[RawItem] = []
        for entry in feed.entries:
            published = _published_utc(entry)
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not published or not title or not link:
                continue                        # drop undated/incomplete
            items.append(RawItem(
                title_en=title, link=link, source=source.name,
                source_type=source.type, published_at=published,
                raw_summary=getattr(entry, "summary", None),
                image_url=_image_url(entry), fetched_via="native",
            ))
        return items
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_rss_adapter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/adapters/sources/base.py src/adapters/sources/rss.py \
  fixtures/sources/rss_sample.xml tests/contract/test_rss_adapter.py
git commit -m "feat(adapters): RSS adapter (feedparser, UTC dates, drops undated)"
```

---

## Task 6: HF Papers adapter

**Files:**
- Create: `src/adapters/sources/hf_papers.py`
- Create (fixture): `fixtures/sources/hf_papers_sample.json`
- Test: `tests/contract/test_hf_papers_adapter.py`

- [ ] **Step 1: Create HF Papers fixture**

```json
[
  {"paper": {"id": "2605.00001", "title": "Diffusion Editing at Scale",
             "publishedAt": "2026-05-30T08:00:00.000Z",
             "summary": "A method for image editing."},
   "publishedAt": "2026-05-30T08:00:00.000Z", "upvotes": 42},
  {"paper": {"id": "2605.00002", "title": "Video Generation Survey",
             "publishedAt": "2026-05-29T08:00:00.000Z", "summary": "Survey."},
   "publishedAt": "2026-05-29T08:00:00.000Z", "upvotes": 7}
]
```

- [ ] **Step 2: Write failing contract test**

```python
# tests/contract/test_hf_papers_adapter.py
import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.core.types import SourceSpec, SourceType, RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.hfp"))


def _spec():
    return SourceSpec(name="hf-papers", url="https://huggingface.co/api/papers",
                      type=SourceType.PAPER, adapter="hf_papers")


@respx.mock
async def test_hf_papers_maps_fields():
    data = json.load(open("fixtures/sources/hf_papers_sample.json"))
    respx.get("https://huggingface.co/api/papers").mock(
        return_value=httpx.Response(200, json=data))
    items = await HFPapersAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "Diffusion Editing at Scale"
    assert it.link == "https://huggingface.co/papers/2605.00001"
    assert it.source_type == SourceType.PAPER
    assert it.published_at == datetime(2026, 5, 30, 8, 0, tzinfo=timezone.utc)
    assert it.raw_summary == "A method for image editing."
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_hf_papers_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `src/adapters/sources/hf_papers.py`**

```python
# src/adapters/sources/hf_papers.py
from __future__ import annotations
from datetime import datetime, timezone
import httpx
from src.core.types import SourceSpec, RunContext, RawItem


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFPapersAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            paper = row.get("paper", {})
            pid, title = paper.get("id"), paper.get("title")
            published = _parse_dt(paper.get("publishedAt") or row.get("publishedAt"))
            if not pid or not title or not published:
                continue
            items.append(RawItem(
                title_en=title, link=f"https://huggingface.co/papers/{pid}",
                source=source.name, source_type=source.type,
                published_at=published, raw_summary=paper.get("summary"),
                fetched_via="native",
            ))
        return items
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_hf_papers_adapter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/adapters/sources/hf_papers.py fixtures/sources/hf_papers_sample.json \
  tests/contract/test_hf_papers_adapter.py
git commit -m "feat(adapters): HF Papers adapter"
```

---

## Task 7: HF Models adapter

**Files:**
- Create: `src/adapters/sources/hf_models.py`
- Create (fixture): `fixtures/sources/hf_models_sample.json`
- Test: `tests/contract/test_hf_models_adapter.py`

- [ ] **Step 1: Create HF Models fixture**

```json
[
  {"id": "acme/diffusion-xl", "createdAt": "2026-05-30T09:30:00.000Z",
   "lastModified": "2026-05-31T01:00:00.000Z", "likes": 120},
  {"id": "lab/text-encoder", "createdAt": "2026-05-28T09:30:00.000Z",
   "lastModified": "2026-05-30T01:00:00.000Z", "likes": 5}
]
```

- [ ] **Step 2: Write failing contract test**

```python
# tests/contract/test_hf_models_adapter.py
import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.adapters.sources.hf_models import HFModelsAdapter
from src.core.types import SourceSpec, SourceType, RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.hfm"))


def _spec():
    return SourceSpec(name="hf-models",
                      url="https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50",
                      type=SourceType.MODEL, adapter="hf_models")


@respx.mock
async def test_hf_models_uses_createdat():
    data = json.load(open("fixtures/sources/hf_models_sample.json"))
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    items = await HFModelsAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "acme/diffusion-xl"
    assert it.link == "https://huggingface.co/acme/diffusion-xl"
    assert it.source_type == SourceType.MODEL
    assert it.published_at == datetime(2026, 5, 30, 9, 30, tzinfo=timezone.utc)  # createdAt, not lastModified
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_hf_models_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `src/adapters/sources/hf_models.py`**

```python
# src/adapters/sources/hf_models.py
from __future__ import annotations
from datetime import datetime, timezone
import httpx
from src.core.types import SourceSpec, RunContext, RawItem


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class HFModelsAdapter:
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            data = resp.json()
        items: list[RawItem] = []
        for row in data:
            mid = row.get("id")
            published = _parse_dt(row.get("createdAt"))   # createdAt per decision #11
            if not mid or not published:
                continue
            items.append(RawItem(
                title_en=mid, link=f"https://huggingface.co/{mid}",
                source=source.name, source_type=source.type,
                published_at=published, raw_summary=None, fetched_via="native",
            ))
        return items
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_hf_models_adapter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/adapters/sources/hf_models.py fixtures/sources/hf_models_sample.json \
  tests/contract/test_hf_models_adapter.py
git commit -m "feat(adapters): HF Models adapter (createdAt)"
```

---

## Task 8: Adapter registry (`ADAPTERS`)

**Files:**
- Modify: `src/adapters/sources/__init__.py`
- Test: `tests/contract/test_adapter_registry.py`

- [ ] **Step 1: Write failing test**

```python
# tests/contract/test_adapter_registry.py
from src.adapters.sources import ADAPTERS
from src.adapters.sources.rss import RSSAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hf_models import HFModelsAdapter


def test_adapters_map_covers_all_adapter_keys():
    assert set(ADAPTERS) == {"rss", "hf_papers", "hf_models"}
    assert isinstance(ADAPTERS["rss"], RSSAdapter)
    assert isinstance(ADAPTERS["hf_papers"], HFPapersAdapter)
    assert isinstance(ADAPTERS["hf_models"], HFModelsAdapter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_adapter_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'ADAPTERS'`

- [ ] **Step 3: Implement `src/adapters/sources/__init__.py`**

```python
# src/adapters/sources/__init__.py
from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.rss import RSSAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hf_models import HFModelsAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_adapter_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/adapters/sources/__init__.py tests/contract/test_adapter_registry.py
git commit -m "feat(adapters): ADAPTERS name->instance registry"
```

---

## Task 9: `collect()` orchestration

**Files:**
- Create: `src/pipeline/collect.py`
- Test: `tests/contract/test_collect_unit.py`

- [ ] **Step 1: Write failing unit test (window filter + per-source isolation, no network via injected adapters)**

```python
# tests/contract/test_collect_unit.py
import logging
from datetime import datetime, timezone, timedelta
import pytest
from src.core.types import (CollectionConfig, RunContext, SourceSpec, SourceType,
                            RawItem)
from src.pipeline import collect as collect_mod


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="t", now=NOW, logger=logging.getLogger("test.collect"))


def _item(name, hours_ago):
    return RawItem(title_en=f"t-{name}", link=f"https://e.com/{name}-{hours_ago}",
                   source=name, source_type=SourceType.OFFICIAL,
                   published_at=NOW - timedelta(hours=hours_ago))


class FakeOK:
    def __init__(self, items): self._items = items
    async def fetch(self, source, ctx, timeout_s): return self._items

class FakeBoom:
    async def fetch(self, source, ctx, timeout_s): raise RuntimeError("403 Forbidden")


@pytest.fixture
def cfg(tmp_path):
    return CollectionConfig(sources_registry_path=str(tmp_path / "x.yaml"))


async def test_window_filter_drops_old_items(monkeypatch, cfg):
    specs = [SourceSpec(name="a", url="u", type=SourceType.OFFICIAL, adapter="rss")]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS",
                        {"rss": FakeOK([_item("a", 2), _item("a", 40)])})
    res = await collect_mod.collect(cfg, _ctx())
    assert len(res.items) == 1                       # 40h-old dropped (24h window)
    assert res.is_silent is False
    rep = res.source_reports[0]
    assert rep.status == "working" and rep.item_count == 1


async def test_one_source_failure_does_not_break_chain(monkeypatch, cfg):
    specs = [SourceSpec(name="a", url="u", type=SourceType.OFFICIAL, adapter="rss"),
             SourceSpec(name="b", url="u", type=SourceType.OFFICIAL, adapter="hf_papers")]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS",
                        {"rss": FakeOK([_item("a", 1)]), "hf_papers": FakeBoom()})
    res = await collect_mod.collect(cfg, _ctx())
    assert len(res.items) == 1
    reps = {r.name: r for r in res.source_reports}
    assert reps["a"].status == "working"
    assert reps["b"].status == "failed" and "403" in reps["b"].error
    assert len(res.source_reports) == 2              # invariant: every enabled source reported


async def test_empty_source_marked_empty_not_failed(monkeypatch, cfg):
    specs = [SourceSpec(name="a", url="u", type=SourceType.OFFICIAL, adapter="rss")]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS", {"rss": FakeOK([])})
    res = await collect_mod.collect(cfg, _ctx())
    assert res.is_silent is True and res.items == []
    assert res.source_reports[0].status == "empty"


async def test_needs_firecrawl_skipped_when_disabled(monkeypatch, cfg):
    specs = [SourceSpec(name="hard", url="u", type=SourceType.BLOG, adapter="rss",
                        needs_firecrawl=True)]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS", {"rss": FakeOK([_item("hard", 1)])})
    res = await collect_mod.collect(cfg, _ctx())   # firecrawl_enabled defaults False
    assert res.source_reports[0].status == "failed"
    assert "firecrawl" in res.source_reports[0].error
    assert res.items == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_collect_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: src.pipeline.collect`

- [ ] **Step 3: Implement `src/pipeline/collect.py`**

```python
# src/pipeline/collect.py
from __future__ import annotations
import asyncio
import time
from datetime import timedelta
from src.core.types import (CollectionConfig, RunContext, CollectionResult,
                            SourceReport, SourceSpec, RawItem)
from src.core.registry import load_registry
from src.adapters.sources import ADAPTERS
from src.observability.events import emit


async def _run_one(source: SourceSpec, config: CollectionConfig,
                   ctx: RunContext, sem: asyncio.Semaphore
                   ) -> tuple[SourceReport, list[RawItem]]:
    start = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    if source.needs_firecrawl and not config.firecrawl_enabled:
        emit(ctx.logger, "source_fetch_fail", name=source.name,
             error_code="firecrawl_disabled")
        return SourceReport(name=source.name, status="failed", item_count=0,
                            error="needs_firecrawl but firecrawl_enabled=false",
                            elapsed_ms=elapsed()), []

    adapter = ADAPTERS[source.adapter]
    try:
        async with sem:
            items = await asyncio.wait_for(
                adapter.fetch(source, ctx, config.timeout_s),
                timeout=config.timeout_s)
    except Exception as e:  # noqa: BLE001 - single source failure is non-fatal
        emit(ctx.logger, "source_fetch_fail", name=source.name, error_code=str(e))
        return SourceReport(name=source.name, status="failed", item_count=0,
                            error=str(e), elapsed_ms=elapsed()), []

    cutoff = ctx.now - timedelta(hours=config.window_hours)
    kept = [it for it in items if it.published_at >= cutoff]
    status = "working" if kept else "empty"
    emit(ctx.logger, "source_fetch_success", name=source.name, item_count=len(kept))
    return SourceReport(name=source.name, status=status, item_count=len(kept),
                        elapsed_ms=elapsed()), kept


async def collect(config: CollectionConfig, run_ctx: RunContext) -> CollectionResult:
    emit(run_ctx.logger, "pipeline_start", run_id=run_ctx.run_id,
         now=run_ctx.now, window_hours=config.window_hours)
    sources = load_registry(config.sources_registry_path, run_ctx)
    sem = asyncio.Semaphore(config.concurrency)
    results = await asyncio.gather(
        *[_run_one(s, config, run_ctx, sem) for s in sources])
    items: list[RawItem] = [it for _, kept in results for it in kept]
    reports = [rep for rep, _ in results]
    is_silent = len(items) == 0
    emit(run_ctx.logger, "collection_done",
         total_items=len(items), silent=is_silent)
    return CollectionResult(items=items, source_reports=reports, is_silent=is_silent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_collect_unit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/collect.py tests/contract/test_collect_unit.py
git commit -m "feat(pipeline): collect() concurrent fetch + window filter + failure isolation"
```

---

## Task 10: Golden tests (spec §8 cases + §7 invariants) end-to-end via respx

**Files:**
- Create (fixtures): `fixtures/sources/golden_403.xml` is N/A (403 has no body); reuse `rss_sample.xml`, `hf_papers_sample.json`
- Create: `tests/golden/data/registry_golden.yaml`
- Create: `tests/golden/data/registry_allold.yaml`
- Test: `tests/golden/test_collect.py`

- [ ] **Step 1: Create golden registries**

```yaml
# tests/golden/data/registry_golden.yaml
- {name: hf-papers, url: "https://huggingface.co/api/papers", type: paper, adapter: hf_papers, status: working, priority: 1}
- {name: openai, url: "https://openai.com/news/rss.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: deepmind, url: "https://deepmind.google/blog/rss.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: broken, url: "https://broken.example/feed.xml", type: blog, adapter: rss, status: working, priority: 3}
- {name: disabled, url: "https://x.example/feed.xml", type: blog, adapter: rss, status: manual, priority: 3}
```

```yaml
# tests/golden/data/registry_allold.yaml
- {name: openai, url: "https://openai.com/news/rss.xml", type: official, adapter: rss, status: working, priority: 2}
```

- [ ] **Step 2: Write the golden tests**

```python
# tests/golden/test_collect.py
import json, logging
from datetime import datetime, timezone
import httpx, respx, pytest
from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect

RSS_XML = open("fixtures/sources/rss_sample.xml", "rb").read()
HFP = json.load(open("fixtures/sources/hf_papers_sample.json"))


def _ctx(now):
    return RunContext(run_id="g", now=now, logger=logging.getLogger("golden"))


def _mount_ok():
    respx.get("https://huggingface.co/api/papers").mock(
        return_value=httpx.Response(200, json=HFP))
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))
    respx.get("https://deepmind.google/blog/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))


# Case 1 (spec §8.1): mixed sources incl. a 403 -> others succeed, no raise
@respx.mock
async def test_golden_mixed_with_403():
    _mount_ok()
    respx.get("https://broken.example/feed.xml").mock(return_value=httpx.Response(403))
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_golden.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    reps = {r.name: r for r in res.source_reports}
    assert set(reps) == {"hf-papers", "openai", "deepmind", "broken"}  # §7.4 enabled-only
    assert reps["broken"].status == "failed"
    assert any(r.status == "working" for r in res.source_reports)
    # §7.1 invariant: nothing older than max_window_hours
    cutoff = _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)).now
    from datetime import timedelta
    assert all(it.published_at >= cutoff - timedelta(hours=cfg.max_window_hours)
               for it in res.items)
    # §7.2 invariant: required fields present (pydantic guarantees; spot check)
    assert all(it.title_en and it.link and it.source for it in res.items)
    # §7.6 invariant
    assert all(it.fetched_via in ("native", "firecrawl") for it in res.items)


# Case 2 (spec §8.2): everything outside window -> silent
@respx.mock
async def test_golden_all_outside_window_is_silent():
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_allold.yaml")
    # now far in the future so the 2026-05-30 fixture item is outside 24h
    res = await collect(cfg, _ctx(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)))
    assert res.items == [] and res.is_silent is True       # §7.5
    assert res.source_reports[0].status == "empty"


# Case 3 (spec §8.3): cross-source duplicates kept (no dedup in this layer)
@respx.mock
async def test_golden_cross_source_duplicates_kept():
    _mount_ok()
    respx.get("https://broken.example/feed.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))  # same content as openai/deepmind
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_golden.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    # openai + deepmind + broken each yield the same 1 dated RSS item -> 3 RSS items kept
    rss_items = [it for it in res.items if it.title_en == "Introducing GPT-X"]
    assert len(rss_items) == 3            # duplicates NOT removed here


# Case 5 (spec §8.5): registry missing -> fallback used, still produces items
@respx.mock
async def test_golden_registry_missing_uses_fallback():
    respx.get("https://huggingface.co/api/papers").mock(
        return_value=httpx.Response(200, json=HFP))
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))
    respx.get("https://deepmind.google/blog/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML))
    cfg = CollectionConfig(sources_registry_path="does/not/exist.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    assert {r.name for r in res.source_reports} == {"hf-papers", "openai", "deepmind"}
    assert len(res.items) >= 1
```

> Note: spec §8.4 (Firecrawl source behavior) is fully covered by the unit test `test_needs_firecrawl_skipped_when_disabled` in Task 9 (firecrawl disabled = the only path this circle). No live-Firecrawl golden case until the Firecrawl circle.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/golden/test_collect.py -v`
Expected: FAIL only if something regressed; if all prior tasks done they should already PASS. If a test references unimplemented behavior, it FAILs first — fix forward.

- [ ] **Step 4: Run full suite green**

Run: `uv run pytest -v`
Expected: PASS (all contract + golden)

- [ ] **Step 5: Commit**

```bash
git add tests/golden/
git commit -m "test(golden): spec §8 cases 1/2/3/5 + §7 invariants for collect()"
```

---

## Task 11: Generate `config/sources.yaml` (Option 3) + gist conversion

**Files:**
- Create: `config/sources.yaml` (working set, ~30 entries, written by hand)
- Create: `references/hn-popular-blogs-2025.opml` (paste raw gist)
- Create: `scripts/opml_to_sources.py`
- Test: `tests/contract/test_sources_yaml.py`

- [ ] **Step 1: Write the working set `config/sources.yaml`**

```yaml
# config/sources.yaml — single source of truth (SSOT). Edit here only.
# status: working = run this circle; manual = registered but not run.
# --- primary APIs ---
- {name: hf-papers, url: "https://huggingface.co/api/papers", type: paper, adapter: hf_papers, status: working, priority: 1}
- {name: hf-models, url: "https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50", type: model, adapter: hf_models, status: working, priority: 1}
# --- official / lab blogs (RSS) ---
- {name: openai, url: "https://openai.com/news/rss.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: deepmind, url: "https://deepmind.google/blog/rss.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: google-research, url: "https://research.google/blog/rss", type: official, adapter: rss, status: working, priority: 2}
- {name: huggingface-blog, url: "https://huggingface.co/blog/feed.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: nvidia, url: "https://blogs.nvidia.com/feed", type: official, adapter: rss, status: working, priority: 2}
- {name: apple-ml, url: "https://machinelearning.apple.com/rss.xml", type: official, adapter: rss, status: working, priority: 2}
- {name: microsoft-ai, url: "https://news.microsoft.com/source/topics/ai/feed", type: official, adapter: rss, status: working, priority: 2}
- {name: aws-ml, url: "https://aws.amazon.com/blogs/machine-learning/feed", type: official, adapter: rss, status: working, priority: 2}
- {name: meta-ai, url: "https://ai.meta.com/blog/rss/", type: official, adapter: rss, status: working, priority: 2}
# --- tools / infra ---
- {name: pytorch, url: "https://pytorch.org/blog/feed.xml", type: tool, adapter: rss, status: working, priority: 2}
- {name: comfy, url: "https://blog.comfy.org/feed.xml", type: tool, adapter: rss, status: working, priority: 3}
- {name: replicate, url: "https://replicate.com/blog/rss", type: tool, adapter: rss, status: working, priority: 3}
- {name: ollama, url: "https://ollama.com/blog/rss.xml", type: tool, adapter: rss, status: working, priority: 3}
- {name: roboflow, url: "https://blog.roboflow.com/feed", type: tool, adapter: rss, status: working, priority: 3}
- {name: civitai-education, url: "https://education.civitai.com/feed", type: tool, adapter: rss, status: working, priority: 3}
# --- academic ---
- {name: bair, url: "https://bair.berkeley.edu/blog/feed.xml", type: paper, adapter: rss, status: working, priority: 2}
- {name: stanford-ai, url: "http://ai.stanford.edu/blog/feed.xml", type: paper, adapter: rss, status: working, priority: 3}
- {name: nature-machine-intelligence, url: "https://www.nature.com/natmachintell.rss", type: paper, adapter: rss, status: working, priority: 2}
# --- community / newsletters (AI-focused subset of gist) ---
- {name: latent-space, url: "https://www.latent.space/feed.xml", type: community, adapter: rss, status: working, priority: 2}
- {name: simonwillison, url: "https://simonwillison.net/atom/everything/", type: blog, adapter: rss, status: working, priority: 2}
- {name: dwarkesh, url: "https://www.dwarkeshpatel.com/feed", type: community, adapter: rss, status: working, priority: 3}
- {name: minimaxir, url: "https://minimaxir.com/index.xml", type: blog, adapter: rss, status: working, priority: 3}
- {name: gwern, url: "https://gwern.substack.com/feed", type: blog, adapter: rss, status: working, priority: 3}
- {name: garymarcus, url: "https://garymarcus.substack.com/feed", type: blog, adapter: rss, status: working, priority: 3}
- {name: geohot, url: "https://geohot.github.io/blog/feed.xml", type: blog, adapter: rss, status: working, priority: 3}
- {name: lcamtuf, url: "https://lcamtuf.substack.com/feed", type: blog, adapter: rss, status: working, priority: 3}
# (general HN-popularity blogs appended below as status: manual by scripts/opml_to_sources.py)
```

- [ ] **Step 2: Save the gist OPML to `references/`**

Paste the full OPML fetched from `https://gist.github.com/emschwartz/e6d2bf860ccc367fe37ff953ba6de66b` (file `hn-popular-blogs-2025.opml`) verbatim into `references/hn-popular-blogs-2025.opml`.

- [ ] **Step 3: Write the OPML→sources converter**

```python
# scripts/opml_to_sources.py
"""Append HN-popularity OPML feeds to config/sources.yaml as status: manual.
Idempotent: skips any feed whose url is already present. Run: uv run python scripts/opml_to_sources.py"""
from __future__ import annotations
import re, sys, xml.etree.ElementTree as ET
from pathlib import Path
import yaml

OPML = Path("references/hn-popular-blogs-2025.opml")
OUT = Path("config/sources.yaml")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "blog"


def main() -> int:
    existing = yaml.safe_load(OUT.read_text()) or []
    have = {e["url"] for e in existing}
    tree = ET.parse(OPML)
    appended = 0
    lines = []
    for o in tree.iter("outline"):
        url = o.get("xmlUrl")
        if not url or url in have:
            continue
        name = slug(o.get("title") or o.get("text") or url)
        lines.append(
            f'- {{name: hn-{name}, url: "{url}", type: blog, adapter: rss, '
            f"status: manual, priority: 5}}")
        have.add(url)
        appended += 1
    if lines:
        with OUT.open("a", encoding="utf-8") as f:
            f.write("\n# --- HN-popularity general blogs (manual, not run) ---\n")
            f.write("\n".join(lines) + "\n")
    print(f"appended {appended} manual sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the converter**

Run: `uv run python scripts/opml_to_sources.py`
Expected: prints `appended N manual sources` (N ≈ 80)

- [ ] **Step 5: Write a validation test for the SSOT registry**

```python
# tests/contract/test_sources_yaml.py
import yaml
from src.core.types import SourceSpec


def test_sources_yaml_all_valid_specs():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]          # raises if any entry invalid
    assert len(specs) >= 30


def test_working_set_has_primaries_and_no_duplicates_urls():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]
    working = [s for s in specs if s.status == "working"]
    names = {s.name for s in working}
    assert {"hf-papers", "hf-models"} <= names       # primary sources enabled
    urls = [s.url for s in specs]
    assert len(urls) == len(set(urls))               # no dup URLs across registry
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_sources_yaml.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add config/sources.yaml references/hn-popular-blogs-2025.opml \
  scripts/opml_to_sources.py tests/contract/test_sources_yaml.py
git commit -m "feat(config): SSOT sources.yaml (Option 3) + OPML->manual converter"
```

---

## Task 12: `--dry-run` CLI

**Files:**
- Create: `src/cli.py`
- Test: `tests/contract/test_cli.py`

- [ ] **Step 1: Write failing test (dry-run prints CollectionResult summary as JSON, makes no writes)**

```python
# tests/contract/test_cli.py
import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.cli import run_dry


@respx.mock
def test_run_dry_returns_summary_dict(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text(
        '- {name: openai, url: "https://openai.com/news/rss.xml", '
        'type: official, adapter: rss, status: working, priority: 2}\n')
    xml = open("fixtures/sources/rss_sample.xml", "rb").read()
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=xml))
    out = run_dry(registry_path=str(reg),
                  now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc))
    assert out["is_silent"] is False
    assert out["total_items"] == 1
    assert out["source_reports"][0]["name"] == "openai"
    json.dumps(out)                                  # must be JSON-serializable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: src.cli`

- [ ] **Step 3: Implement `src/cli.py`**

```python
# src/cli.py
from __future__ import annotations
import argparse, asyncio, json, logging, sys, uuid
from datetime import datetime, timezone
from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect


def run_dry(registry_path: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    cfg = CollectionConfig(sources_registry_path=registry_path)
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)
    res = asyncio.run(collect(cfg, ctx))
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "is_silent": res.is_silent,
        "total_items": len(res.items),
        "items": [it.model_dump(mode="json") for it in res.items],
        "source_reports": [r.model_dump() for r in res.source_reports],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ai-newsday-collect")
    p.add_argument("--registry", default="config/sources.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="collect + print result JSON; no side effects (only mode this circle)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if not args.dry_run:
        print("This circle supports --dry-run only (publishing is a later layer).",
              file=sys.stderr)
        return 2
    out = run_dry(registry_path=args.registry)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Full suite + a real dry-run (this is the §9 "inject now" + step③ fixture-freeze point)**

Run: `uv run pytest -v`
Expected: ALL PASS

Run (live — produces the dry-run artifact to paste back for review; the ONE place real network happens; capture responses here to freeze as fixtures if any adapter mapping needs adjusting):
`uv run python -m src.cli --dry-run --registry config/sources.yaml > /tmp/collection_dryrun.json; head -c 400 /tmp/collection_dryrun.json`
Expected: JSON with `total_items`, per-source `source_reports`. Failures appear as `status: failed` rows, not a crash.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/contract/test_cli.py
git commit -m "feat(cli): --dry-run entry point emitting CollectionResult JSON"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
| --- | --- |
| §3 `collect(config, run_ctx) -> CollectionResult` | Task 9, 12 |
| §3 CollectionConfig fields | Task 2 |
| §3 RunContext (run_id/now/logger) | Task 2 |
| §3 CollectionResult (items/source_reports/is_silent) | Task 2, 9 |
| §4 RawItem (+ `blog` enum) | Task 1, 2 |
| §4 SourceReport | Task 2 |
| §5 single 403/404/timeout → failed, continue | Task 9 (`test_one_source_failure...`), Task 10 (case 1) |
| §5 empty source → empty (not error) | Task 9 (`test_empty_source...`) |
| §5 needs_firecrawl + disabled → failed/skip | Task 9 (`test_needs_firecrawl...`) |
| §5 registry load failure → fallback + warn | Task 4, Task 10 (case 5) |
| §5 all fail/empty → items=[], is_silent | Task 9, Task 10 (case 2) |
| §6 registry yaml shape | Task 4, Task 11 |
| §7 invariants 1-6 | Task 10 (asserted), 2 (pydantic for #2/#6) |
| §8 golden cases 1/2/3/5 | Task 10 |
| §8 golden case 4 (firecrawl) | Task 9 unit (firecrawl-off is the only path this circle; noted) |
| §9 contract + schema validation | Tasks 2,5,6,7,8,11 |
| §9 inject now, deterministic | every test uses fixed `RunContext.now` |
| §10 events (source_fetch_*, collection_done) | Task 3 + emitted in Task 9 |
| §11 acceptance #1/#2/#8 | Task 12 (#1 end-to-end dry-run), Task 9/10 (#2 robust), Task 9/10 (#8 silent) |

**2. Placeholder scan:** none — every code step has complete code; the only deliberate prose-instruction steps (paste OPML in Task 11 §2, paste types in Task 2 §3) point to fully-specified content present in this document.

**3. Type consistency:** verified `collect()`, `SourceAdapter.fetch(source, ctx, timeout_s)`, `RawItem`/`SourceReport`/`SourceSpec` field names, `ADAPTERS` keys (`rss`/`hf_papers`/`hf_models`), and `SourceReport.status` literals (`working`/`failed`/`empty`) are identical across Tasks 2–12.

**Out of scope (correctly deferred):** dedup/scoring/translation/publishing (later layers), GitHub adapter, 36h fallback, Firecrawl live path, SQLite `runs` table.
