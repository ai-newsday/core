# GitHub Sources (releases + trending) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two GitHub source adapters — `github_releases` (curated marquee repos) and `github_trending` (Search API base + Trending HTML best-effort) — feeding repo star counts as a new popularity-signal axis.

**Architecture:** Each adapter implements the existing `SourceAdapter` Protocol (`async fetch(source, ctx, timeout_s) -> list[RawItem]`), registered in `ADAPTERS`. Items carry `signals={"github_stars": N}`, scored via the existing `popularity_weights` machinery. No new genre — releases & trending repos map to `announcement` (per ADR 0003). Recency guard (闸 ii) is provided for free by `collect.py`'s existing `published_at >= cutoff` window filter, so adapters only set `published_at` correctly (release date / repo `pushed_at`).

**Tech Stack:** Python 3.12, httpx (async), respx (test mock), pydantic types in `src/core/types.py`.

## Global Constraints

- Python 3.12; deps via uv. No new dependency (httpx/respx already present).
- Adapters are IO-isolated under `src/adapters/sources/`; pure scoring/config unchanged.
- LLM/config rule: weights live in `config/scoring.yaml`, not in code.
- `RawItem.published_at` MUST be tz-aware (pydantic validator enforces).
- Auth: read `GITHUB_TOKEN` from env if present → `Authorization: Bearer <token>` header (5000/hr); absent → anonymous (60/hr), still works.
- Signal key MUST be exactly `github_stars` (already an agreed key in `RawItem.signals` docstring; read by `popularity_weights` + `enrich`).
- No new genre / no quota change in this plan.

---

### Task 1: Wire `github_stars` signal + two adapter literals

**Files:**
- Modify: `src/core/types.py` (SourceSpec.adapter Literal)
- Modify: `src/pipeline/enrich.py` (`_POPULARITY_KEYS`)
- Modify: `config/scoring.yaml` (`popularity_weights`)
- Test: `tests/contract/test_github_signal_wiring.py`

**Interfaces:**
- Consumes: existing `SourceSpec`, `enrich._has_popularity`, `RawItem`.
- Produces: `SourceSpec.adapter` accepts `"github_releases"` / `"github_trending"`; `enrich` treats `github_stars` as popularity; `popularity_weights["github_stars"] == 0.3`.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_github_signal_wiring.py
from datetime import datetime, timezone

import yaml

from src.core.types import Genre, Publisher, RawItem, SourceSpec
from src.pipeline.enrich import _has_popularity


def test_sourcespec_accepts_github_adapters():
    for ad in ("github_releases", "github_trending"):
        s = SourceSpec(name="x", url="https://api.github.com/x", genre=Genre.announcement,
                       publisher=Publisher.company, adapter=ad)
        assert s.adapter == ad


def test_github_stars_counts_as_popularity():
    it = RawItem(title_en="t", link="l", source="s", genre=Genre.announcement,
                 publisher=Publisher.company,
                 published_at=datetime(2026, 6, 23, tzinfo=timezone.utc),
                 signals={"github_stars": 1200})
    assert _has_popularity(it) is True


def test_scoring_yaml_has_github_stars_weight():
    cfg = yaml.safe_load(open("config/scoring.yaml"))
    assert cfg["popularity_weights"]["github_stars"] == 0.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_github_signal_wiring.py -v`
Expected: FAIL — `SourceSpec` rejects the adapter values; `github_stars` not in `_POPULARITY_KEYS`; key missing in yaml.

- [ ] **Step 3: Apply the three edits**

In `src/core/types.py`, change the `SourceSpec.adapter` line:

```python
    adapter: Literal["rss", "hf_papers", "hf_models", "hn", "reddit", "github_releases", "github_trending"]
```

In `src/pipeline/enrich.py`, extend the key set:

```python
_POPULARITY_KEYS = {"upvotes", "likes", "hn_points", "downloads", "github_stars"}
```

In `config/scoring.yaml`, the `popularity_weights` line — add `github_stars`:

```yaml
popularity_weights: {upvotes: 0.6, hn_points: 0.5, likes: 0.3, github_stars: 0.3, num_comments: 0.4}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_github_signal_wiring.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py src/pipeline/enrich.py config/scoring.yaml tests/contract/test_github_signal_wiring.py
git commit -m "feat(sources): wire github_stars signal + github adapter literals"
```

---

### Task 2: `github_releases` adapter

**Files:**
- Create: `src/adapters/sources/github_releases.py`
- Modify: `src/adapters/sources/__init__.py` (register in `ADAPTERS`)
- Test: `tests/contract/test_github_releases_adapter.py`

**Interfaces:**
- Consumes: `SourceSpec` (`url` = `https://api.github.com/repos/{owner}/{repo}/releases`, `genre`, `publisher`, `name`), `RunContext`.
- Produces: `GithubReleasesAdapter().fetch(source, ctx, timeout_s) -> list[RawItem]`. One item per release: `title_en="{repo} {tag_name}"`, `link=release.html_url`, `raw_summary=release.body`, `published_at=release.published_at`, `signals={"github_stars": <repo stargazers_count>}`. Registered under key `"github_releases"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_github_releases_adapter.py
import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.github_releases import GithubReleasesAdapter
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_RELEASES = "https://api.github.com/repos/comfyanonymous/ComfyUI/releases"
_REPO = "https://api.github.com/repos/comfyanonymous/ComfyUI"


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.ghr"))


def _spec():
    return SourceSpec(name="comfyui", url=_RELEASES, genre=Genre.announcement,
                      publisher=Publisher.individual, adapter="github_releases")


def _release(tag="v0.3.40", body="adds new nodes", date="2026-06-22T10:00:00Z",
             url="https://github.com/comfyanonymous/ComfyUI/releases/tag/v0.3.40"):
    return {"tag_name": tag, "body": body, "published_at": date, "html_url": url}


@respx.mock
async def test_releases_maps_fields_and_star_signal():
    respx.get(_RELEASES).mock(return_value=httpx.Response(200, json=[_release()]))
    respx.get(_REPO).mock(return_value=httpx.Response(200, json={"stargazers_count": 65000}))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "comfyui v0.3.40"
    assert it.link == "https://github.com/comfyanonymous/ComfyUI/releases/tag/v0.3.40"
    assert it.raw_summary == "adds new nodes"
    assert it.genre == Genre.announcement and it.publisher == Publisher.individual
    assert it.signals == {"github_stars": 65000}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_releases_empty_returns_empty():
    respx.get(_RELEASES).mock(return_value=httpx.Response(200, json=[]))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_releases_skips_release_without_published_at():
    # draft / unpublished release has published_at: null
    respx.get(_RELEASES).mock(return_value=httpx.Response(
        200, json=[{"tag_name": "v9", "body": "", "published_at": None, "html_url": "u"}]))
    respx.get(_REPO).mock(return_value=httpx.Response(200, json={"stargazers_count": 1}))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


@respx.mock
async def test_releases_http_error_raises():
    respx.get(_RELEASES).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_github_releases_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: src.adapters.sources.github_releases`.

- [ ] **Step 3: Write the adapter**

```python
# src/adapters/sources/github_releases.py
from __future__ import annotations

import os
from datetime import datetime

import httpx

from src.core.types import RawItem, RunContext, SourceSpec


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _parse_dt(s: str) -> datetime:
    # GitHub ISO8601 ends in 'Z'; make it tz-aware
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class GithubReleasesAdapter:
    """Watch a curated repo's releases. `source.url` =
    https://api.github.com/repos/{owner}/{repo}/releases. Each published release →
    one (announcement) item carrying the repo's star count as `github_stars`.
    Recency is enforced downstream by collect's window filter via published_at."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        repo_url = source.url.rsplit("/releases", 1)[0]
        headers = _auth_headers()
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            releases = resp.json() or []
            if not releases:
                return []
            repo_resp = await client.get(repo_url)
            repo_resp.raise_for_status()
            stars = (repo_resp.json() or {}).get("stargazers_count")

        signals = {"github_stars": stars} if stars is not None else {}
        items: list[RawItem] = []
        for r in releases:
            published = r.get("published_at")
            tag = r.get("tag_name")
            html_url = r.get("html_url")
            if not published or not tag or not html_url:
                continue
            items.append(
                RawItem(
                    title_en=f"{source.name} {tag}",
                    link=html_url,
                    source=source.name,
                    genre=source.genre,
                    publisher=source.publisher,
                    published_at=_parse_dt(published),
                    raw_summary=r.get("body") or None,
                    signals=dict(signals),
                    fetched_via="native",
                )
            )
        return items
```

- [ ] **Step 4: Register the adapter**

In `src/adapters/sources/__init__.py` add the import and the `ADAPTERS` entry:

```python
from src.adapters.sources.github_releases import GithubReleasesAdapter
```
```python
    "github_releases": GithubReleasesAdapter(),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_github_releases_adapter.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/adapters/sources/github_releases.py src/adapters/sources/__init__.py tests/contract/test_github_releases_adapter.py
git commit -m "feat(sources): github_releases adapter (curated repo release watch)"
```

---

### Task 3: `github_trending` adapter (Search base + Trending HTML best-effort)

**Files:**
- Create: `src/adapters/sources/github_trending.py`
- Modify: `src/adapters/sources/__init__.py` (register)
- Test: `tests/contract/test_github_trending_adapter.py`

**Interfaces:**
- Consumes: `SourceSpec` (`url` = full Search API query, e.g. `https://api.github.com/search/repositories?q=...&sort=stars`), `RunContext`.
- Produces: `GithubTrendingAdapter().fetch(...) -> list[RawItem]`. One item per repo: `title_en=full_name`, `link=html_url`, `raw_summary=description`, `published_at=pushed_at`, `signals={"github_stars": stargazers_count}`. Also a module-level `_scrape_trending(html: str) -> list[str]` returning repo full names. Registered under `"github_trending"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_github_trending_adapter.py
import logging
from datetime import datetime, timezone

import httpx
import pytest
import respx

from src.adapters.sources.github_trending import GithubTrendingAdapter, _scrape_trending
from src.core.types import Genre, Publisher, RawItem, RunContext, SourceSpec

_SEARCH = "https://api.github.com/search/repositories?q=topic:llm+sort:stars&sort=stars&order=desc&per_page=30"
_TRENDING_HTML = "https://github.com/trending?since=daily"


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.ght"))


def _spec():
    return SourceSpec(name="gh-trending", url=_SEARCH, genre=Genre.announcement,
                      publisher=Publisher.company, adapter="github_trending")


def _repo(full="openai/whisper", url="https://github.com/openai/whisper",
          desc="ASR model", pushed="2026-06-23T01:00:00Z", stars=70000):
    return {"full_name": full, "html_url": url, "description": desc,
            "pushed_at": pushed, "stargazers_count": stars}


@respx.mock
async def test_trending_search_maps_fields_and_signal():
    respx.get(_SEARCH).mock(return_value=httpx.Response(200, json={"items": [_repo()]}))
    # Trending HTML best-effort returns nothing here (no extra repos)
    respx.get("https://github.com/trending").mock(return_value=httpx.Response(200, text="<html></html>"))
    items = await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, RawItem)
    assert it.title_en == "openai/whisper"
    assert it.link == "https://github.com/openai/whisper"
    assert it.raw_summary == "ASR model"
    assert it.genre == Genre.announcement and it.publisher == Publisher.company
    assert it.signals == {"github_stars": 70000}
    assert it.published_at.tzinfo is not None


@respx.mock
async def test_trending_scrape_failure_keeps_search_results():
    respx.get(_SEARCH).mock(return_value=httpx.Response(200, json={"items": [_repo()]}))
    respx.get("https://github.com/trending").mock(return_value=httpx.Response(403))
    items = await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    # Search base still yields, scrape 403 is swallowed
    assert len(items) == 1
    assert items[0].title_en == "openai/whisper"


@respx.mock
async def test_trending_search_http_error_raises():
    respx.get(_SEARCH).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await GithubTrendingAdapter().fetch(_spec(), _ctx(), timeout_s=15)


def test_scrape_trending_extracts_full_names():
    html = '''
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/comfyanonymous/ComfyUI">ComfyUI</a></h2>
    </article>
    <article class="Box-row">
      <h2 class="h3 lh-condensed"><a href="/ollama/ollama">ollama</a></h2>
    </article>
    '''
    assert _scrape_trending(html) == ["comfyanonymous/ComfyUI", "ollama/ollama"]


def test_scrape_trending_empty_on_garbage():
    assert _scrape_trending("<html>nope</html>") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_github_trending_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: src.adapters.sources.github_trending`.

- [ ] **Step 3: Write the adapter**

```python
# src/adapters/sources/github_trending.py
from __future__ import annotations

import os
import re
from datetime import datetime

import httpx

from src.core.types import RawItem, RunContext, SourceSpec

# ponytail: canonical Trending endpoint hardcoded (an endpoint, not a tuning knob)
_TRENDING_URL = "https://github.com/trending"
# /owner/repo inside the trending list heading anchors
_TRENDING_RE = re.compile(r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"')


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _scrape_trending(html: str) -> list[str]:
    """Extract owner/repo full names from a github.com/trending HTML page."""
    return _TRENDING_RE.findall(html)


def _item_from_repo(r: dict, source: SourceSpec) -> RawItem | None:
    full = r.get("full_name")
    pushed = r.get("pushed_at")
    html_url = r.get("html_url")
    if not full or not pushed or not html_url:
        return None
    stars = r.get("stargazers_count")
    return RawItem(
        title_en=full,
        link=html_url,
        source=source.name,
        genre=source.genre,
        publisher=source.publisher,
        published_at=_parse_dt(pushed),
        raw_summary=r.get("description") or None,
        signals={"github_stars": stars} if stars is not None else {},
        fetched_via="native",
    )


class GithubTrendingAdapter:
    """Discover trending AI repos. Base: Search API (source.url, reliable, token'd).
    Best-effort bonus: scrape github.com/trending for repos the search missed, resolved
    via the repo API. Scrape failures (403 from Actions IP, layout change) are swallowed —
    the Search base still produces. Recency (闸 ii) is enforced downstream by collect's
    window filter via published_at=pushed_at."""

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        headers = _auth_headers()
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            resp = await client.get(source.url)
            resp.raise_for_status()
            repos = (resp.json() or {}).get("items") or []

            items: list[RawItem] = []
            seen: set[str] = set()
            for r in repos:
                it = _item_from_repo(r, source)
                if it:
                    items.append(it)
                    seen.add(r.get("full_name"))

            # best-effort Trending HTML scrape
            try:
                t_resp = await client.get(_TRENDING_URL, params={"since": "daily"})
                t_resp.raise_for_status()
                for full in _scrape_trending(t_resp.text):
                    if full in seen:
                        continue
                    seen.add(full)
                    repo_resp = await client.get(f"https://api.github.com/repos/{full}")
                    repo_resp.raise_for_status()
                    it = _item_from_repo(repo_resp.json() or {}, source)
                    if it:
                        items.append(it)
            except (httpx.HTTPError, ValueError) as e:
                ctx.logger.info("trending scrape skipped: %s", e)

        return items
```

- [ ] **Step 4: Register the adapter**

In `src/adapters/sources/__init__.py` add:

```python
from src.adapters.sources.github_trending import GithubTrendingAdapter
```
```python
    "github_trending": GithubTrendingAdapter(),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_github_trending_adapter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/adapters/sources/github_trending.py src/adapters/sources/__init__.py tests/contract/test_github_trending_adapter.py
git commit -m "feat(sources): github_trending adapter (search base + trending scrape)"
```

---

### Task 4: Register marquee sources + dry-run verification

**Files:**
- Modify: `config/sources.yaml` (add GitHub rows)
- (No new test code — uses the live `--dry-run` path.)

**Interfaces:**
- Consumes: Tasks 2 & 3 adapters via `ADAPTERS`; `load_registry`.

- [ ] **Step 1: Add the source rows**

Append a `# --- github ---` block to `config/sources.yaml` (publishers per repo identity; genre=announcement; status=working). Use real owner/repo paths:

```yaml
# --- github (releases watch + trending discovery) ---
- {name: comfyui, url: "https://api.github.com/repos/comfyanonymous/ComfyUI/releases", genre: announcement, publisher: individual, adapter: github_releases, status: working, priority: 2}
- {name: ollama, url: "https://api.github.com/repos/ollama/ollama/releases", genre: announcement, publisher: company, adapter: github_releases, status: working, priority: 2}
- {name: vllm, url: "https://api.github.com/repos/vllm-project/vllm/releases", genre: announcement, publisher: company, adapter: github_releases, status: working, priority: 2}
- {name: gh-trending-ai, url: "https://api.github.com/search/repositories?q=topic:llm+topic:artificial-intelligence+sort:stars&sort=stars&order=desc&per_page=30", genre: announcement, publisher: company, adapter: github_trending, status: working, priority: 3}
```

> Add ComfyUI ecosystem / OpenClaw rows the same way once their exact owner/repo paths are confirmed. Keep new rows `status: working` only after the dry-run below confirms they yield.

- [ ] **Step 2: Run the full test suite (no regressions)**

Run: `uv run pytest -q`
Expected: all green (prior ~350 + the new GitHub tests).

- [ ] **Step 3: Real dry-run with scoring (network)**

Run: `MODELSCOPE_API_KEY=$MODELSCOPE_API_KEY uv run python -m src.cli --tick collect --dry-run --score`
Expected: collected funnel shows items from `comfyui`/`ollama`/`vllm`/`gh-trending-ai`; scored items carry `github_stars` in signals. Inspect the dry-run `0X_*.jsonl` to confirm.

- [ ] **Step 4: Record the Trending-scrape prod reality in KANBAN**

In the dry-run logs, check whether `trending scrape skipped: ...` appears (i.e. whether `github.com/trending` 403s from this environment, the reddit precedent). Note the outcome in `docs/KANBAN.md` §2/§3 — if the Actions IP 403s the scrape, the Search API base is what carries trending in prod (by design).

- [ ] **Step 5: Commit**

```bash
git add config/sources.yaml docs/KANBAN.md
git commit -m "feat(sources): register marquee GitHub repos + trending query"
```

---

## Self-Review

- **Spec coverage:** ① releases watch → Task 2. ① trending (Search base + Trending best-effort) → Task 3. ② no new genre / announcement mapping → source rows in Task 4 use `genre: announcement`. ② github_stars signal + popularity_weights + enrich skip → Task 1. ③ 闸 ii recency → free via collect window (documented; adapters set `published_at`). ③ 闸 i deferred → not in plan (correct). ④ tests → contract tests per adapter + scrape fixture (Tasks 2/3). ⑤ dry-run verification + KANBAN note → Task 4. All covered.
- **Placeholder scan:** no TBD/"handle errors" — error paths are concrete (raise on base HTTP error; swallow scrape error). The only open item is "add OpenClaw/ComfyUI-ecosystem rows once owner/repo confirmed" — a data-entry note, not a code placeholder.
- **Type consistency:** `_parse_dt`, `_auth_headers`, `_item_from_repo`, `_scrape_trending`, `GithubReleasesAdapter`, `GithubTrendingAdapter`, signal key `github_stars`, adapter keys `github_releases`/`github_trending` consistent across tasks and matched to `SourceAdapter` Protocol + `ADAPTERS`.
