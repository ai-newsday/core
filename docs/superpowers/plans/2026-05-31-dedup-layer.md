# Dedup / Clustering Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cluster upstream `RawItem`s by event so each event appears once, keeping the most first-hand source as the cluster primary and folding other sources' links into `related_links`.

**Architecture:** Network (embedding) and persistence (vector store) live behind injected adapters (`EmbeddingProvider`, `VectorStore`); the clustering core `cluster()` is a pure deterministic function (items + vectors + priority map → clusters). `dedup()` orchestrates: embed → cluster → store → assemble `DedupResult`. Primary tie-break priority comes from a `source→priority` map built from the registry and passed into `cluster()` as data (RawItem stays unchanged). Tests inject `FakeEmbeddingProvider` with frozen vectors for fully offline, deterministic golden cases.

**Tech Stack:** Python 3.12, pydantic v2 (NewsItem), numpy (cosine), httpx (ModelScope sync client), pytest + respx. Embeddings = ModelScope API-Inference (OpenAI-compatible `/v1/embeddings`, model `Qwen/Qwen3-Embedding-8B`). Qdrant deferred behind `VectorStore` (this circle = in-memory).

**Spec:** `docs/specs/dedup.md`. Acceptance gate: 去重覆盖率 100% (PRD §2.1 #3).

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | add `numpy` dependency |
| `config/dedup.yaml` (create) | thresholds/weights (spec §6) |
| `src/core/types.py` (modify) | add `NewsItem`, `DedupConfig`, `Cluster`, `DedupResult` |
| `src/core/config.py` (create) | `load_dedup_config(path) -> DedupConfig` |
| `src/core/registry.py` (modify) | add `load_source_priorities(path) -> dict[str,int]` |
| `src/adapters/embedding/base.py` (create) | `EmbeddingProvider` Protocol |
| `src/adapters/embedding/modelscope.py` (create) | `ModelScopeEmbedder` (real, sync httpx) |
| `src/adapters/vectorstore/base.py` (create) | `VectorStore` Protocol |
| `src/adapters/vectorstore/memory.py` (create) | `InMemoryVectorStore` |
| `src/pipeline/dedup.py` (create) | `build_embed_text`, `embedding_id`, pure `cluster()`, `dedup()` |
| `tests/fakes.py` (create) | `FakeEmbeddingProvider`, `FailingEmbeddingProvider` |
| `tests/contract/test_dedup_types.py` (create) | NewsItem schema invariants |
| `tests/contract/test_dedup_config.py` (create) | config + priority-map loaders |
| `tests/contract/test_embedding_adapter.py` (create) | ModelScope adapter under respx |
| `tests/contract/test_vectorstore.py` (create) | InMemoryVectorStore behavior |
| `tests/contract/test_cluster_unit.py` (create) | pure `cluster()` unit cases |
| `tests/golden/test_dedup.py` (create) | spec §9 1-6 → §8 invariants |
| `src/cli.py` (modify) | add `--dedup` chain producing `DedupResult` JSON |

Run all tests with `uv run pytest` (the repo venv has the deps; bare `pytest` picks the wrong interpreter).

---

## Task 1: Dedup core types

**Files:**
- Modify: `src/core/types.py`
- Test: `tests/contract/test_dedup_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_dedup_types.py
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (RawItem, NewsItem, SourceType,
                            DedupConfig, Cluster, DedupResult)


def _raw(title="GPT-X released", link="https://e.com/a", src="openai",
         st=SourceType.OFFICIAL):
    return RawItem(title_en=title, link=link, source=src, source_type=st,
                   published_at=datetime(2026, 5, 30, 12, tzinfo=timezone.utc))


def test_newsitem_inherits_rawitem_and_adds_fields():
    raw = _raw()
    ni = NewsItem(**raw.model_dump(), cluster_id="evt-2026-05-30-001",
                  related_links=["https://e.com/b"], embedding_id="abc123")
    assert ni.title_en == "GPT-X released"          # inherited
    assert ni.cluster_id == "evt-2026-05-30-001"
    assert ni.related_links == ["https://e.com/b"]
    assert ni.embedding_id == "abc123"


def test_newsitem_rejects_empty_cluster_id():
    raw = _raw()
    with pytest.raises(ValidationError):
        NewsItem(**raw.model_dump(), cluster_id="")


def test_newsitem_defaults():
    raw = _raw()
    ni = NewsItem(**raw.model_dump(), cluster_id="evt-2026-05-30-001")
    assert ni.related_links == [] and ni.embedding_id is None


def test_dedupconfig_defaults():
    c = DedupConfig()
    assert c.similarity_threshold == 0.83
    assert c.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert c.batch_size == 32
    assert c.source_type_rank[0] == "official"
    assert c.sources_registry_path == "config/sources.yaml"


def test_cluster_and_result_construct():
    raw = _raw()
    primary = NewsItem(**raw.model_dump(), cluster_id="evt-2026-05-30-001")
    cl = Cluster(cluster_id="evt-2026-05-30-001", primary=primary,
                 members=[raw], related_links=[], size=1)
    res = DedupResult(clusters=[cl], deduped_items=[primary],
                      input_count=1, cluster_count=1, duplicate_count=0)
    assert res.cluster_count == 1 and res.duplicate_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_dedup_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'NewsItem'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/core/types.py` (after the existing `RawItem`; reuse the imports already at the top — `dataclass`, `field` from dataclasses, `Field` from pydantic):

```python
# --- dedup layer (Circle 2) ---
class NewsItem(RawItem):
    cluster_id: str = Field(min_length=1)
    related_links: list[str] = Field(default_factory=list)
    embedding_id: str | None = None


@dataclass
class DedupConfig:
    similarity_threshold: float = 0.83
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    batch_size: int = 32
    source_type_rank: list[str] = field(default_factory=lambda: [
        "official", "paper", "model", "tool", "news", "community", "blog"])
    sources_registry_path: str = "config/sources.yaml"


@dataclass
class Cluster:
    cluster_id: str
    primary: NewsItem
    members: list[RawItem]
    related_links: list[str]
    size: int


@dataclass
class DedupResult:
    clusters: list[Cluster]
    deduped_items: list[NewsItem]
    input_count: int
    cluster_count: int
    duplicate_count: int
```

Add `field` to the dataclasses import at the top of the file: change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_dedup_types.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_dedup_types.py
git commit -m "feat(types): NewsItem + DedupConfig/Cluster/DedupResult (dedup layer)"
```

---

## Task 2: Config loaders (dedup.yaml + priority map)

**Files:**
- Create: `config/dedup.yaml`
- Create: `src/core/config.py`
- Modify: `src/core/registry.py`
- Test: `tests/contract/test_dedup_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_dedup_config.py
import logging
from src.core.config import load_dedup_config
from src.core.registry import load_source_priorities
from src.core.types import RunContext
from datetime import datetime, timezone


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, tzinfo=timezone.utc),
                      logger=logging.getLogger("t"))


def test_load_dedup_config_reads_yaml():
    c = load_dedup_config("config/dedup.yaml")
    assert c.similarity_threshold == 0.83
    assert c.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert c.source_type_rank == [
        "official", "paper", "model", "tool", "news", "community", "blog"]


def test_load_dedup_config_missing_file_returns_defaults():
    c = load_dedup_config("does/not/exist.yaml")
    assert c.similarity_threshold == 0.83          # falls back to dataclass defaults


def test_load_source_priorities_maps_name_to_priority():
    m = load_source_priorities("tests/golden/data/registry_min.yaml")
    assert m == {"hf-papers": 1, "openai": 2, "some-blog": 3}


def test_load_source_priorities_missing_file_is_empty():
    assert load_source_priorities("does/not/exist.yaml") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_dedup_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.config'`

- [ ] **Step 3: Write minimal implementation**

Create `src/core/config.py`:

```python
from __future__ import annotations
import yaml
from src.core.types import DedupConfig


def load_dedup_config(path: str) -> DedupConfig:
    """Load dedup thresholds from YAML; missing/empty file -> dataclass defaults."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return DedupConfig()
    defaults = DedupConfig()
    return DedupConfig(
        similarity_threshold=data.get("similarity_threshold", defaults.similarity_threshold),
        embedding_model=data.get("embedding_model", defaults.embedding_model),
        batch_size=data.get("batch_size", defaults.batch_size),
        source_type_rank=data.get("source_type_rank", defaults.source_type_rank),
        sources_registry_path=data.get("sources_registry_path", defaults.sources_registry_path),
    )
```

Append to `src/core/registry.py` (it already imports `yaml`):

```python
def load_source_priorities(path: str) -> dict[str, int]:
    """name -> priority for ALL registry entries (any status). Missing file -> {}."""
    try:
        with open(path) as f:
            rows = yaml.safe_load(f) or []
    except FileNotFoundError:
        return {}
    return {r["name"]: r.get("priority", 3) for r in rows}
```

Create `config/dedup.yaml`:

```yaml
similarity_threshold: 0.83        # cosine 阈值; 调高=更少合并(更保守), 调低=更多合并
embedding_model: "Qwen/Qwen3-Embedding-8B"
batch_size: 32
# 主条目优先级: 越靠前=越一手/权威; dedup 选主与排序据此
source_type_rank: [official, paper, model, tool, news, community, blog]
sources_registry_path: "config/sources.yaml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_dedup_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add config/dedup.yaml src/core/config.py src/core/registry.py tests/contract/test_dedup_config.py
git commit -m "feat(config): dedup.yaml loader + source priority map"
```

---

## Task 3: EmbeddingProvider adapter (ModelScope)

**Files:**
- Create: `src/adapters/embedding/__init__.py`
- Create: `src/adapters/embedding/base.py`
- Create: `src/adapters/embedding/modelscope.py`
- Test: `tests/contract/test_embedding_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_embedding_adapter.py
import httpx, respx, pytest
from src.adapters.embedding.modelscope import ModelScopeEmbedder

URL = "https://api-inference.modelscope.cn/v1/embeddings"


def _resp(vectors):
    return httpx.Response(200, json={"data": [{"embedding": v} for v in vectors]})


@respx.mock
def test_embed_returns_vectors_in_order():
    respx.post(URL).mock(return_value=_resp([[1.0, 0.0], [0.0, 1.0]]))
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    out = emb.embed(["a", "b"])
    assert out == [[1.0, 0.0], [0.0, 1.0]]


@respx.mock
def test_embed_batches_by_batch_size():
    route = respx.post(URL).mock(side_effect=[
        _resp([[1.0]]), _resp([[2.0]])])
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=1)
    out = emb.embed(["a", "b"])
    assert out == [[1.0], [2.0]]
    assert route.call_count == 2          # one request per batch


@respx.mock
def test_embed_raises_on_http_error():
    respx.post(URL).mock(return_value=httpx.Response(500))
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    with pytest.raises(Exception):
        emb.embed(["a"])


def test_embed_empty_returns_empty():
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    assert emb.embed([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_embedding_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.adapters.embedding'`

- [ ] **Step 3: Write minimal implementation**

Create `src/adapters/embedding/__init__.py` (empty file).

Create `src/adapters/embedding/base.py`:

```python
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float] | None]:
        """Return one vector per input text (aligned). None at an index means
        that single text failed; raise to signal a whole-batch/provider failure."""
        ...
```

Create `src/adapters/embedding/modelscope.py`:

```python
from __future__ import annotations
import httpx

_BASE_URL = "https://api-inference.modelscope.cn/v1/embeddings"


class ModelScopeEmbedder:
    """OpenAI-compatible embeddings via ModelScope API-Inference."""

    def __init__(self, api_key: str, model: str, batch_size: int = 32,
                 timeout_s: int = 30):
        self._api_key = api_key
        self._model = model
        self._batch = max(1, batch_size)
        self._timeout = timeout_s

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            for i in range(0, len(texts), self._batch):
                chunk = texts[i:i + self._batch]
                r = client.post(_BASE_URL, headers=headers,
                                json={"model": self._model, "input": chunk})
                r.raise_for_status()
                data = r.json()["data"]
                out.extend(d["embedding"] for d in data)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_embedding_adapter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/adapters/embedding tests/contract/test_embedding_adapter.py
git commit -m "feat(adapter): ModelScope EmbeddingProvider (OpenAI-compatible, batched)"
```

---

## Task 4: VectorStore adapter (in-memory)

**Files:**
- Create: `src/adapters/vectorstore/__init__.py`
- Create: `src/adapters/vectorstore/base.py`
- Create: `src/adapters/vectorstore/memory.py`
- Test: `tests/contract/test_vectorstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_vectorstore.py
from src.adapters.vectorstore.memory import InMemoryVectorStore


def test_upsert_records_points():
    store = InMemoryVectorStore()
    store.upsert([("id1", [1.0, 0.0], {"cluster_id": "evt-x-001"})])
    assert store.points["id1"] == ([1.0, 0.0], {"cluster_id": "evt-x-001"})


def test_upsert_is_idempotent_by_id():
    store = InMemoryVectorStore()
    store.upsert([("id1", [1.0], {"k": "a"})])
    store.upsert([("id1", [2.0], {"k": "b"})])
    assert store.points["id1"] == ([2.0], {"k": "b"})
    assert len(store.points) == 1


def test_upsert_empty_is_noop():
    store = InMemoryVectorStore()
    store.upsert([])
    assert store.points == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_vectorstore.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.adapters.vectorstore'`

- [ ] **Step 3: Write minimal implementation**

Create `src/adapters/vectorstore/__init__.py` (empty file).

Create `src/adapters/vectorstore/base.py`:

```python
from typing import Protocol, Any


class VectorStore(Protocol):
    def upsert(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        """Persist (id, vector, payload) triples. Real impl = Qdrant (deferred)."""
        ...
```

Create `src/adapters/vectorstore/memory.py`:

```python
from __future__ import annotations
from typing import Any


class InMemoryVectorStore:
    """This-circle stand-in for Qdrant; also acts as the dry-run no-op store
    (nothing persists beyond the process)."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}

    def upsert(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        for pid, vector, payload in points:
            self.points[pid] = (vector, payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_vectorstore.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/adapters/vectorstore tests/contract/test_vectorstore.py
git commit -m "feat(adapter): InMemoryVectorStore + VectorStore protocol (Qdrant deferred)"
```

---

## Task 5: Add numpy + dedup helpers + test fakes

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/pipeline/dedup.py` (helpers only this task)
- Create: `tests/fakes.py`
- Test: `tests/contract/test_cluster_unit.py` (helper portion only this task)

- [ ] **Step 1: Add numpy dependency**

Run: `uv add numpy`
Expected: numpy added to `pyproject.toml` `[project].dependencies`, lockfile updated.

- [ ] **Step 2: Write the failing test**

```python
# tests/contract/test_cluster_unit.py
from datetime import datetime, timezone
from src.core.types import RawItem, SourceType
from src.pipeline.dedup import build_embed_text, embedding_id, _cosine


def _raw(title, summary=None, link="https://e.com/a"):
    return RawItem(title_en=title, link=link, source="s",
                   source_type=SourceType.OFFICIAL,
                   published_at=datetime(2026, 5, 30, tzinfo=timezone.utc),
                   raw_summary=summary)


def test_build_embed_text_with_summary():
    assert build_embed_text(_raw("T", "S")) == "T\nS"


def test_build_embed_text_without_summary():
    assert build_embed_text(_raw("T", None)) == "T"


def test_embedding_id_is_stable_16_hex():
    a = embedding_id("https://e.com/a")
    b = embedding_id("https://e.com/a")
    assert a == b and len(a) == 16


def test_cosine_orthogonal_is_zero_and_parallel_is_one():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert abs(_cosine([1.0, 1.0], [2.0, 2.0]) - 1.0) < 1e-9


def test_cosine_zero_vector_is_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cluster_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.dedup'`

- [ ] **Step 4: Write minimal implementation**

Create `src/pipeline/dedup.py` (helpers only — `cluster()`/`dedup()` added in later tasks):

```python
from __future__ import annotations
import hashlib
import numpy as np
from src.core.types import RawItem


def build_embed_text(item: RawItem) -> str:
    """title_en + summary; degrade to title only when summary missing (spec §5.1)."""
    return f"{item.title_en}\n{item.raw_summary}" if item.raw_summary else item.title_en


def embedding_id(link: str) -> str:
    """Stable 16-hex id derived from the item link (spec §5.2)."""
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
```

Create `tests/fakes.py`:

```python
from __future__ import annotations


class FakeEmbeddingProvider:
    """Returns frozen vectors keyed by exact input text. Missing key -> KeyError.
    A mapped value of None marks that single text as failed (spec §7 single-fail)."""

    def __init__(self, vectors_by_text: dict[str, list[float] | None]):
        self._map = vectors_by_text

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        return [self._map[t] for t in texts]


class FailingEmbeddingProvider:
    """Simulates total provider failure (spec §7 degrade-to-singletons)."""

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        raise RuntimeError("embedding provider unavailable")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cluster_unit.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/pipeline/dedup.py tests/fakes.py tests/contract/test_cluster_unit.py
git commit -m "feat(dedup): embed-text/embedding-id/cosine helpers + numpy + test fakes"
```

---

## Task 6: Pure `cluster()` function

**Files:**
- Modify: `src/pipeline/dedup.py`
- Test: `tests/contract/test_cluster_unit.py` (append clustering cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_cluster_unit.py`:

```python
import logging
from src.core.types import DedupConfig, RunContext
from src.pipeline.dedup import cluster


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, tzinfo=timezone.utc),
                      logger=logging.getLogger("t"))


def _item(title, link, source, st, when=None):
    return RawItem(title_en=title, link=link, source=source, source_type=st,
                   published_at=when or datetime(2026, 5, 30, 12, tzinfo=timezone.utc))


def test_cluster_merges_similar_above_threshold():
    items = [
        _item("A", "https://e/1", "openai", SourceType.OFFICIAL),
        _item("B", "https://e/2", "blogx", SourceType.BLOG),
    ]
    vectors = [[1.0, 0.0], [0.99, 0.14]]          # cosine ~0.99 > 0.83
    cfg = DedupConfig()
    clusters = cluster(items, vectors, {"openai": 2, "blogx": 3}, cfg, _ctx())
    assert len(clusters) == 1
    c = clusters[0]
    assert c.size == 2
    assert c.primary.source == "openai"            # official outranks blog
    assert c.related_links == ["https://e/2"]
    assert c.cluster_id == "evt-2026-05-30-001"


def test_cluster_keeps_dissimilar_separate():
    items = [
        _item("A", "https://e/1", "openai", SourceType.OFFICIAL),
        _item("B", "https://e/2", "openai", SourceType.OFFICIAL),
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]            # orthogonal -> below threshold
    clusters = cluster(items, vectors, {"openai": 2}, DedupConfig(), _ctx())
    assert len(clusters) == 2
    assert [c.cluster_id for c in clusters] == [
        "evt-2026-05-30-001", "evt-2026-05-30-002"]
    assert all(c.size == 1 and c.related_links == [] for c in clusters)


def test_cluster_primary_priority_then_published():
    early = datetime(2026, 5, 30, 8, tzinfo=timezone.utc)
    late = datetime(2026, 5, 30, 20, tzinfo=timezone.utc)
    items = [
        _item("low-prio", "https://e/1", "src-a", SourceType.PAPER, late),
        _item("hi-prio", "https://e/2", "src-b", SourceType.PAPER, late),
        _item("earliest", "https://e/3", "src-b", SourceType.PAPER, early),
    ]
    vectors = [[1.0, 0.0], [1.0, 0.01], [1.0, 0.02]]   # all similar -> one cluster
    # same source_type (paper); src-b priority 1 < src-a priority 2;
    # within src-b, earliest published wins.
    clusters = cluster(items, vectors, {"src-a": 2, "src-b": 1}, DedupConfig(), _ctx())
    assert len(clusters) == 1
    assert clusters[0].primary.title_en == "earliest"


def test_cluster_none_vector_is_forced_singleton():
    items = [
        _item("A", "https://e/1", "openai", SourceType.OFFICIAL),
        _item("B", "https://e/2", "openai", SourceType.OFFICIAL),
    ]
    vectors = [None, None]                         # embeddings missing
    clusters = cluster(items, vectors, {"openai": 2}, DedupConfig(), _ctx())
    assert len(clusters) == 2                      # never merged without vectors


def test_cluster_sets_embedding_id_on_primary():
    items = [_item("A", "https://e/1", "openai", SourceType.OFFICIAL)]
    clusters = cluster(items, [[1.0]], {"openai": 2}, DedupConfig(), _ctx())
    assert clusters[0].primary.embedding_id == embedding_id("https://e/1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cluster_unit.py -v`
Expected: FAIL with `ImportError: cannot import name 'cluster'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pipeline/dedup.py` (add imports `from src.core.types import NewsItem, Cluster, DedupConfig, RunContext, SourceType` to the existing import line):

```python
def _rank_index(source_type: SourceType, order: list[str]) -> int:
    try:
        return order.index(source_type.value)
    except ValueError:
        return len(order)            # unknown types sort last


def cluster(items: list[RawItem],
            vectors: list[list[float] | None],
            priority_of: dict[str, int],
            config: DedupConfig,
            ctx: RunContext) -> list[Cluster]:
    """Pure greedy-threshold clustering. `vectors` aligns to `items` by index;
    None means that item has no embedding and is forced into its own singleton.
    Seeds are primaries by construction because of the priority sort (spec §5)."""
    # pair items with vectors + stable embedding ids, then sort by primary priority
    indexed = [
        (it, vectors[i], embedding_id(it.link))
        for i, it in enumerate(items)
    ]
    indexed.sort(key=lambda t: (
        _rank_index(t[0].source_type, config.source_type_rank),
        priority_of.get(t[0].source, 3),
        t[0].published_at,
    ))

    # each open cluster: {seed_vec, members:[(item, emb_id)], seed_item}
    open_clusters: list[dict] = []
    for it, vec, emb_id in indexed:
        joined = False
        if vec is not None:
            best_sim, best = -1.0, None
            for oc in open_clusters:
                if oc["seed_vec"] is None:
                    continue
                sim = _cosine(vec, oc["seed_vec"])
                if sim > best_sim:
                    best_sim, best = sim, oc
            if best is not None and best_sim > config.similarity_threshold:
                best["members"].append((it, emb_id))
                joined = True
        if not joined:
            open_clusters.append(
                {"seed_vec": vec, "seed_item": it, "seed_emb": emb_id,
                 "members": [(it, emb_id)]})

    clusters: list[Cluster] = []
    for n, oc in enumerate(open_clusters, start=1):
        cid = f"evt-{ctx.now:%Y-%m-%d}-{n:03d}"
        seed_item = oc["seed_item"]
        members = [m for m, _ in oc["members"]]
        related = [m.link for m in members if m is not seed_item]
        primary = NewsItem(**seed_item.model_dump(), cluster_id=cid,
                           related_links=related, embedding_id=oc["seed_emb"])
        clusters.append(Cluster(cluster_id=cid, primary=primary,
                                members=members, related_links=related,
                                size=len(members)))
    return clusters
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cluster_unit.py -v`
Expected: PASS (all helper + clustering cases)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/dedup.py tests/contract/test_cluster_unit.py
git commit -m "feat(dedup): pure greedy-threshold cluster() with priority-sorted seeds"
```

---

## Task 7: `dedup()` orchestration + golden cases

**Files:**
- Modify: `src/pipeline/dedup.py`
- Test: `tests/golden/test_dedup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/golden/test_dedup.py
import logging
from datetime import datetime, timezone
from src.core.types import RawItem, SourceType, DedupConfig, RunContext
from src.pipeline.dedup import dedup, build_embed_text
from src.adapters.vectorstore.memory import InMemoryVectorStore
from tests.fakes import FakeEmbeddingProvider, FailingEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-dedup"))


def _item(title, link, source, st):
    # raw_summary=None so embed_text == title_en (keys the FakeEmbeddingProvider)
    return RawItem(title_en=title, link=link, source=source, source_type=st,
                   published_at=NOW)


# config with no registry file -> priority_of falls back to default 3 for all
def _cfg():
    return DedupConfig(sources_registry_path="tests/golden/data/registry_min.yaml")


# Case 1 (spec §9.1): 3 cross-source near-duplicates -> 1 cluster, duplicate_count==2
def test_golden_cross_source_merge():
    items = [
        _item("Event X", "https://a/1", "openai", SourceType.OFFICIAL),
        _item("Event X take", "https://b/2", "some-blog", SourceType.BLOG),
        _item("Event X recap", "https://c/3", "some-blog", SourceType.BLOG),
    ]
    vecs = {build_embed_text(items[0]): [1.0, 0.0],
            build_embed_text(items[1]): [0.99, 0.10],
            build_embed_text(items[2]): [0.98, 0.12]}
    store = InMemoryVectorStore()
    res = dedup(items, _cfg(), _ctx(),
                embedder=FakeEmbeddingProvider(vecs), store=store)
    assert res.cluster_count == 1
    assert res.duplicate_count == 2
    assert res.deduped_items[0].source == "openai"          # most first-hand
    assert sorted(res.deduped_items[0].related_links) == ["https://b/2", "https://c/3"]
    # §8.2: every input belongs to exactly one cluster
    assert sum(c.size for c in res.clusters) == res.input_count == 3


# Case 2 (spec §9.2): N dissimilar -> N singletons, duplicate_count==0
def test_golden_no_duplicates():
    items = [
        _item("Alpha", "https://a/1", "openai", SourceType.OFFICIAL),
        _item("Beta", "https://b/2", "openai", SourceType.OFFICIAL),
        _item("Gamma", "https://c/3", "openai", SourceType.OFFICIAL),
    ]
    vecs = {build_embed_text(items[0]): [1.0, 0.0, 0.0],
            build_embed_text(items[1]): [0.0, 1.0, 0.0],
            build_embed_text(items[2]): [0.0, 0.0, 1.0]}
    res = dedup(items, _cfg(), _ctx(),
                embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore())
    assert res.cluster_count == 3 and res.duplicate_count == 0
    assert len(res.deduped_items) == res.cluster_count


# Case 3 (spec §9.3): official beats blog as primary regardless of order
def test_golden_primary_selection_official_over_blog():
    items = [
        _item("E blog", "https://b/1", "some-blog", SourceType.BLOG),
        _item("E official", "https://o/2", "openai", SourceType.OFFICIAL),
    ]
    vecs = {build_embed_text(items[0]): [1.0, 0.02],
            build_embed_text(items[1]): [1.0, 0.0]}
    res = dedup(items, _cfg(), _ctx(),
                embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore())
    assert res.cluster_count == 1
    assert res.deduped_items[0].source == "openai"
    assert res.deduped_items[0].related_links == ["https://b/1"]


# Case 4 (spec §9.4): threshold boundary — just-above merges, just-below splits
def test_golden_threshold_boundary():
    # cfg threshold 0.83; build one pair ~0.95 (merge) and an item ~0.0 (split)
    items = [
        _item("P", "https://a/1", "openai", SourceType.OFFICIAL),
        _item("P near", "https://b/2", "openai", SourceType.OFFICIAL),
        _item("Q far", "https://c/3", "openai", SourceType.OFFICIAL),
    ]
    vecs = {build_embed_text(items[0]): [1.0, 0.0],
            build_embed_text(items[1]): [0.95, 0.31],   # cosine ~0.95 > 0.83
            build_embed_text(items[2]): [0.0, 1.0]}     # cosine 0.0 < 0.83
    res = dedup(items, _cfg(), _ctx(),
                embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore())
    assert res.cluster_count == 2          # {P,P near} + {Q far}
    sizes = sorted(c.size for c in res.clusters)
    assert sizes == [1, 2]


# Case 5 (spec §9.5): empty input -> empty result, no raise
def test_golden_empty_input():
    res = dedup([], _cfg(), _ctx(),
                embedder=FakeEmbeddingProvider({}), store=InMemoryVectorStore())
    assert res.clusters == [] and res.deduped_items == []
    assert res.input_count == res.cluster_count == res.duplicate_count == 0


# Case 6 (spec §9.6): provider failure -> all singletons, no raise
def test_golden_embedding_degraded_all_singletons():
    items = [
        _item("A", "https://a/1", "openai", SourceType.OFFICIAL),
        _item("B", "https://b/2", "openai", SourceType.OFFICIAL),
    ]
    res = dedup(items, _cfg(), _ctx(),
                embedder=FailingEmbeddingProvider(), store=InMemoryVectorStore())
    assert res.cluster_count == res.input_count == 2     # spec §8.6
    assert res.duplicate_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_dedup.py -v`
Expected: FAIL with `ImportError: cannot import name 'dedup'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pipeline/dedup.py` (add imports: `from src.core.types import DedupResult` and `from src.core.registry import load_source_priorities`, plus `from src.observability.events import emit`):

```python
def dedup(items: list[RawItem], config: DedupConfig, ctx: RunContext, *,
          embedder, store) -> DedupResult:
    emit(ctx.logger, "dedup_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "dedup_done", input_count=0, cluster_count=0,
             duplicate_count=0, silent=True)
        return DedupResult(clusters=[], deduped_items=[], input_count=0,
                           cluster_count=0, duplicate_count=0)

    priority_of = load_source_priorities(config.sources_registry_path)
    texts = [build_embed_text(it) for it in items]
    try:
        vectors = embedder.embed(texts)
    except Exception as e:  # noqa: BLE001 - degrade is non-fatal (spec §7)
        emit(ctx.logger, "dedup_embedding_degraded", reason=str(e))
        vectors = [None] * len(items)

    clusters = cluster(items, vectors, priority_of, config, ctx)

    points: list[tuple] = []
    for c in clusters:
        emit(ctx.logger, "dedup_cluster_created", cluster_id=c.cluster_id, size=c.size)
        if c.primary.embedding_id is not None:
            idx = items.index(c.primary_source_item) if False else None  # placeholder
    # build store points from primaries that carry a vector
    by_emb = {embedding_id(it.link): v for it, v in zip(items, vectors)}
    for c in clusters:
        eid = c.primary.embedding_id
        vec = by_emb.get(eid) if eid else None
        if vec is not None:
            points.append((eid, vec, {"cluster_id": c.cluster_id}))
    store.upsert(points)

    deduped = [c.primary for c in clusters]
    result = DedupResult(clusters=clusters, deduped_items=deduped,
                         input_count=len(items), cluster_count=len(clusters),
                         duplicate_count=len(items) - len(clusters))
    emit(ctx.logger, "dedup_done", input_count=result.input_count,
         cluster_count=result.cluster_count,
         duplicate_count=result.duplicate_count, silent=False)
    return result
```

NOTE during implementation: delete the dead `points`-loop placeholder above — keep only the `by_emb`-based store-points construction. (It is shown crossed-out here to flag the trap; the correct loop is the second one.) Final `dedup()` should have exactly one loop emitting `dedup_cluster_created` and one building `points` from `by_emb`. Clean version:

```python
    by_emb = {embedding_id(it.link): v for it, v in zip(items, vectors)}
    points: list[tuple] = []
    for c in clusters:
        emit(ctx.logger, "dedup_cluster_created", cluster_id=c.cluster_id, size=c.size)
        vec = by_emb.get(c.primary.embedding_id)
        if vec is not None:
            points.append((c.primary.embedding_id, vec, {"cluster_id": c.cluster_id}))
    store.upsert(points)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_dedup.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (Circle 1 + Circle 2).

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/dedup.py tests/golden/test_dedup.py
git commit -m "feat(dedup): dedup() orchestration + golden cases (spec §9 → §8 invariants)"
```

---

## Task 8: CLI `--dedup` chain

**Files:**
- Modify: `src/cli.py`
- Test: `tests/contract/test_cli.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_cli.py`:

```python
from datetime import datetime, timezone
from src.cli import run_dry_dedup
from tests.fakes import FakeEmbeddingProvider


def test_run_dry_dedup_returns_dedupresult_json(monkeypatch):
    # collect side is mocked at the source level by existing fixtures in this file;
    # here we drive dedup directly with an injected fake embedder via run_dry_dedup.
    out = run_dry_dedup(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),     # no items collected in unit -> empty
    )
    assert "cluster_count" in out and "deduped_items" in out
    assert out["input_count"] == out["cluster_count"] + out["duplicate_count"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli.py::test_run_dry_dedup_returns_dedupresult_json -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_dedup'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/cli.py` (new imports at top: `import os`; `from src.core.config import load_dedup_config`; `from src.pipeline.dedup import dedup`; `from src.adapters.embedding.modelscope import ModelScopeEmbedder`; `from src.adapters.vectorstore.memory import InMemoryVectorStore`):

```python
def run_dry_dedup(registry_path: str, now: datetime | None = None,
                  embedder=None) -> dict:
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
    res = dedup(coll.items, dcfg, ctx,
                embedder=embedder, store=InMemoryVectorStore())
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": res.input_count,
        "cluster_count": res.cluster_count,
        "duplicate_count": res.duplicate_count,
        "deduped_items": [ni.model_dump(mode="json") for ni in res.deduped_items],
    }
```

Extend `main()` to support `--dedup` (add `p.add_argument("--dedup", action="store_true", help="chain collect -> dedup, print DedupResult JSON")`), and branch before the existing collect-only path:

```python
    if args.dry_run and args.dedup:
        out = run_dry_dedup(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

(Place this branch right after `if not args.dry_run: ... return 2`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli.py -v`
Expected: PASS (existing CLI tests + new one)

- [ ] **Step 5: Live dry-run smoke (manual, optional)**

Run: `MODELSCOPE_API_KEY=$MODELSCOPE_API_KEY uv run python -m src.cli --dry-run --dedup --registry config/sources.yaml | head -40`
Expected: valid `DedupResult` JSON. With no key, embedding degrades → all singletons (chain still completes, no crash — validates acceptance #1).

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/contract/test_cli.py
git commit -m "feat(cli): --dedup chain (collect -> dedup) emitting DedupResult JSON"
```

---

## Task 9: Full suite + ROADMAP update

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 2: Update ROADMAP §2 progress table**

In `docs/ROADMAP.md`, flip row ② to done: status `🟩 已合并`, fill 实现 (`pipeline/dedup.py` + embedding/vectorstore adapters), 测试 (golden + contract green), dry-run (`--dry-run --dedup`). Update the §1 mermaid: change `C2` class from `spec` to `done`. Update "最后更新" date.

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: ROADMAP — mark dedup layer (Circle 2) done"
```

---

## Self-Review (against spec `docs/specs/dedup.md`)

- **§3 contract** (`dedup(items, config, ctx)` + injected `embedder`/`store`, pure `cluster()`): Tasks 6–7. ✅
- **§4 data contracts** (`NewsItem`/`Cluster`/`DedupResult`): Task 1. ✅
- **§5 algorithm** (embed_text, embedding_id, priority sort, greedy threshold, cluster_id `evt-%Y-%m-%d-NNN`): Tasks 5–6. ✅
- **§6 config** (`config/dedup.yaml`): Task 2. ✅
- **§7 errors** (empty input, total degrade, single-fail via None, dry-run no-op store): empty+degrade Task 7; single-fail supported by `FakeEmbeddingProvider` None + `cluster()` None-singleton (Task 6); dry-run no-op = InMemoryVectorStore injection. ✅
- **§8 invariants 1–7**: covered across golden (Task 7) + cluster unit (Task 6): coverage-100%/one-cluster-per-item (§8.1–2 golden case 1), primary rule (§8.3 case 3 + unit), counts (§8.4 cases 1–2), NewsItem invariants + non-empty cluster_id (§8.5 Task 1), degrade all-singleton (§8.6 case 6), determinism (§8.7 — cluster_id sequence asserted in unit `test_cluster_keeps_dissimilar_separate`). ✅
- **§9 golden 1–6**: Task 7 cases 1–6. ✅
- **§10 testing** (contract+golden, injected `ctx.now`, offline pure fn): all tests use `NOW`/`_ctx()`, FakeEmbeddingProvider → offline. ✅
- **§11 observability** (`dedup_cluster_created`, `dedup_embedding_degraded`, `dedup_done`): Task 7. ✅
- **§12 acceptance** (#3 dedup-100%, #1 end-to-end `--dry-run`, #8 silent): golden case 1 (#3), Task 8 CLI (#1), golden case 5 (#8). ✅

**Placeholder scan:** Task 7 Step 3 intentionally shows a dead `points` placeholder then the clean replacement, with an explicit instruction to keep only the clean loop — flagged, not a silent gap.

**Type consistency:** `EmbeddingProvider.embed -> list[list[float] | None]` (base.py, modelscope.py, fakes.py) consistent; `cluster(items, vectors, priority_of, config, ctx)` signature identical in Task 6 impl and Task 7 caller; `DedupConfig.sources_registry_path` used by both `dedup()` and CLI; `InMemoryVectorStore.upsert(list[tuple[str, list[float], dict]])` consistent with the points built in `dedup()`.
