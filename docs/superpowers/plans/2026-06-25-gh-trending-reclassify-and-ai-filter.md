# gh-trending 重分类 + 非 AI 过滤 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 gh-trending repo 按真实 owner 类型/工具属性打分(不再每条 ~92),并在抓取阶段丢掉非 AI repo。

**Architecture:** 只改 [src/adapters/sources/github_trending.py](../../../src/adapters/sources/github_trending.py) + registry 一行。Task 1(A): publisher 由 `owner.type` 推 + registry genre→writeup/publisher→individual。Task 2(C): HTML 抓取路径加 AI 关键词过滤(复用 hn 的 `_kw_match`) + registry 加 `keywords` 白名单。打分公式不动。

**Tech Stack:** Python 3.12, httpx + respx(adapter 测试), pytest, ruff。

## Global Constraints

- config 驱动: genre/publisher/keywords 都在 registry,不写死代码。
- 外科手术式: 只动 `github_trending.py` + `config/sources.yaml` 的 gh-trending-ai 行;不改打分公式、不改 Search API 路径、不动其他源。
- TDD: 先写失败测试。CI 跑 `ruff check` + `ruff format --check`;**commit 前本地 `uv run ruff` 一遍**。
- 测试用 `uv run python -m pytest`(依赖经 uv 装)。
- AI 过滤只加在**抓取路径**(scrape→repo API);Search API 路径信任(服务端已 `topic:llm AND topic:artificial-intelligence`)。`source.keywords` 为 None/空 → 不过滤(向后兼容)。
- 设计文档: [docs/superpowers/specs/2026-06-25-gh-trending-reclassify-and-ai-filter-design.md](../specs/2026-06-25-gh-trending-reclassify-and-ai-filter-design.md)

---

### Task 1: A — publisher 按 owner.type + registry 重分类

`_item_from_repo` 的 publisher 不再死用 `source.publisher`,改由 repo JSON 的 `owner.type` 推。registry 把 genre 降到 writeup、publisher fallback 降到 individual。

**Files:**
- Modify: `src/adapters/sources/github_trending.py`(import Publisher;加 `_publisher_for_owner`;`_item_from_repo` 用它)
- Modify: `config/sources.yaml`(gh-trending-ai 行:genre/publisher)
- Test: `tests/contract/test_github_trending_adapter.py`

**Interfaces:**
- Produces: `_publisher_for_owner(repo: dict, source: SourceSpec) -> Publisher`(Organization→company / User→individual / 缺→source.publisher)。Task 2 不依赖它。

- [ ] **Step 1: 写失败测试 — owner.type → publisher 三分支**

`tests/contract/test_github_trending_adapter.py` 末尾追加(顶部已 import `Genre, Publisher, RawItem, RunContext, SourceSpec`):

```python
from src.adapters.sources.github_trending import _item_from_repo, _publisher_for_owner


def _repo_with_owner(otype):
    r = _repo()
    r["owner"] = {"type": otype} if otype else {}
    return r


def test_publisher_from_owner_type():
    src = _spec()  # source.publisher == Publisher.company (fallback)
    assert _publisher_for_owner(_repo_with_owner("Organization"), src) == Publisher.company
    assert _publisher_for_owner(_repo_with_owner("User"), src) == Publisher.individual
    # 缺 owner / 未知 type → 回退 source.publisher
    assert _publisher_for_owner(_repo_with_owner(None), src) == src.publisher
    assert _publisher_for_owner({}, src) == src.publisher


def test_item_from_repo_uses_owner_publisher():
    src = _spec()
    user_repo = _repo_with_owner("User")
    it = _item_from_repo(user_repo, src)
    assert it.publisher == Publisher.individual  # 个人 repo, 不随 source.publisher=company
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py -k "owner_type or owner_publisher" -q`
Expected: FAIL — `cannot import name '_publisher_for_owner'`。

- [ ] **Step 3: 实现 `_publisher_for_owner` + 接进 `_item_from_repo`**

`src/adapters/sources/github_trending.py`:把 import 行
`from src.core.types import RawItem, RunContext, SourceSpec`
改为
`from src.core.types import Publisher, RawItem, RunContext, SourceSpec`

在 `_item_from_repo` 上方加:

```python
def _publisher_for_owner(repo: dict, source: SourceSpec) -> Publisher:
    """Repo owner.type → publisher: Organization=company, User=individual.
    缺 owner / 未知 type → source.publisher(registry fallback)。"""
    otype = (repo.get("owner") or {}).get("type")
    if otype == "Organization":
        return Publisher.company
    if otype == "User":
        return Publisher.individual
    return source.publisher
```

`_item_from_repo` 里把 `publisher=source.publisher,` 改为 `publisher=_publisher_for_owner(r, source),`。

- [ ] **Step 4: 跑测试确认通过 + 不回归既有 adapter 测试**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py -q`
Expected: PASS(含既有 `test_trending_search_maps_fields_and_signal` —— 其 `_repo()` 无 owner → 回退 source.publisher=company,旧断言 `publisher == company` 仍成立)。

- [ ] **Step 5: registry — genre→writeup, publisher→individual**

`config/sources.yaml` 把 gh-trending-ai 行的 `genre: announcement` 改 `genre: writeup`,
`publisher: company` 改 `publisher: individual`。改后该行:

```yaml
- {name: gh-trending-ai, url: "https://api.github.com/search/repositories?q=topic:llm+topic:artificial-intelligence+sort:stars&sort=stars&order=desc&per_page=30", genre: writeup, publisher: individual, adapter: github_trending, status: working, priority: 3}
```

- [ ] **Step 6: 跑 registry 相关测试 + ruff**

Run: `uv run python -m pytest tests/contract/test_sources_yaml.py tests/contract/test_adapter_registry.py -q`
Expected: PASS(schema 校验,不钉 gh-trending 具体 genre)。
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 通过

- [ ] **Step 7: Commit**

```bash
git add src/adapters/sources/github_trending.py config/sources.yaml tests/contract/test_github_trending_adapter.py
git commit -m "feat(gh-trending): per-repo publisher from owner.type, reclassify genre to writeup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: C — 抓取路径 AI 过滤 + keywords 白名单

HTML 抓取路径(`github.com/trending`,无 topic 过滤)是非 AI repo 的漏点。对抓取到的 repo 加 AI 关键词过滤;Search API 路径不动。

**Files:**
- Modify: `src/adapters/sources/github_trending.py`(加 `_is_ai_repo`;scrape loop 调用;import `_kw_match`)
- Modify: `config/sources.yaml`(gh-trending-ai 行加 `keywords`)
- Test: `tests/contract/test_github_trending_adapter.py`

**Interfaces:**
- Consumes: `_kw_match(haystack: str, keywords: list[str]) -> bool`(现有于 [src/adapters/sources/hn.py](../../../src/adapters/sources/hn.py),词边界匹配,空→True)。
- Produces: `_is_ai_repo(repo: dict, keywords: list[str] | None) -> bool`(topics∩keywords 或 desc 词边界命中 → True;keywords 空/None → True)。

- [ ] **Step 1: 写失败测试 — _is_ai_repo 判定**

`tests/contract/test_github_trending_adapter.py` 末尾追加:

```python
from src.adapters.sources.github_trending import _is_ai_repo

_KWS = ["llm", "ai", "agent", "machine-learning"]


def test_is_ai_repo_by_topic():
    assert _is_ai_repo({"topics": ["llm", "python"], "description": "x"}, _KWS) is True


def test_is_ai_repo_non_ai_dropped():
    # apple/container 类: topics 无 AI, desc 无 AI 词
    assert _is_ai_repo({"topics": ["swift", "macos"], "description": "Linux containers on macOS"}, _KWS) is False


def test_is_ai_repo_by_description_word_boundary():
    # 无 AI topic 但 desc 词边界命中 "agent"
    assert _is_ai_repo({"topics": [], "description": "An LLM agent toolkit"}, _KWS) is True
    # 子串不算: "chair" 不该被 "ai" 命中
    assert _is_ai_repo({"topics": [], "description": "ergonomic chair design"}, _KWS) is False


def test_is_ai_repo_empty_keywords_keeps_all():
    assert _is_ai_repo({"topics": ["swift"], "description": "x"}, None) is True
    assert _is_ai_repo({"topics": ["swift"], "description": "x"}, []) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py -k is_ai_repo -q`
Expected: FAIL — `cannot import name '_is_ai_repo'`。

- [ ] **Step 3: 实现 `_is_ai_repo`**

`src/adapters/sources/github_trending.py` 顶部 import 区加:

```python
from src.adapters.sources.hn import _kw_match
```

在 `_publisher_for_owner` 附近加:

```python
def _is_ai_repo(repo: dict, keywords: list[str] | None) -> bool:
    """抓取路径 AI 闸: repo 有 topic ∈ keywords, 或 description 词边界命中 keyword → 保留。
    keywords 空/None → 全保留(向后兼容, 不影响无 keywords 的源)。"""
    if not keywords:
        return True
    kws = {k.lower() for k in keywords}
    topics = {str(t).lower() for t in (repo.get("topics") or [])}
    if topics & kws:
        return True
    return _kw_match(repo.get("description") or "", keywords)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py -k is_ai_repo -q`
Expected: PASS(5)

- [ ] **Step 5: 写失败测试 — scrape 路径丢非 AI repo**

`tests/contract/test_github_trending_adapter.py` 末尾追加(用 respx 模拟 search 空 + trending HTML 出两个 repo + repos API 各自详情):

```python
@respx.mock
async def test_scrape_filters_non_ai_repos():
    # search 返回空; 全靠抓取路径
    respx.get(url__regex=r"https://api\.github\.com/search/repositories.*").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    html = (
        '<h2 class="h3 lh-condensed"><a href="/openai/whisper">w</a></h2>'
        '<h2 class="h3 lh-condensed"><a href="/apple/container">c</a></h2>'
    )
    respx.get("https://github.com/trending").mock(return_value=httpx.Response(200, text=html))
    respx.get("https://api.github.com/repos/openai/whisper").mock(
        return_value=httpx.Response(200, json={
            "full_name": "openai/whisper", "html_url": "https://github.com/openai/whisper",
            "description": "ASR", "pushed_at": "2026-06-23T01:00:00Z", "stargazers_count": 9,
            "topics": ["llm", "speech"], "owner": {"type": "Organization"},
        })
    )
    respx.get("https://api.github.com/repos/apple/container").mock(
        return_value=httpx.Response(200, json={
            "full_name": "apple/container", "html_url": "https://github.com/apple/container",
            "description": "Linux containers on macOS", "pushed_at": "2026-06-23T01:00:00Z",
            "stargazers_count": 9, "topics": ["swift", "macos"], "owner": {"type": "Organization"},
        })
    )
    spec = SourceSpec(
        name="gh-trending-ai",
        url="https://api.github.com/search/repositories?q=topic:llm&per_page=30",
        genre=Genre.writeup, publisher=Publisher.individual, adapter="github_trending",
        keywords=["llm", "ai", "agent"],
    )
    items = await GithubTrendingAdapter().fetch(spec, _ctx(), timeout_s=15)
    links = [it.link for it in items]
    assert "https://github.com/openai/whisper" in links  # AI topic → 留
    assert "https://github.com/apple/container" not in links  # 非 AI → 丢
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py::test_scrape_filters_non_ai_repos -q`
Expected: FAIL — apple/container 仍在 links(scrape loop 未过滤)。

- [ ] **Step 7: 实现 — scrape loop 调用 `_is_ai_repo`**

`src/adapters/sources/github_trending.py` `fetch` 的抓取循环里,把:

```python
                    repo_resp = await client.get(f"https://api.github.com/repos/{full}")
                    repo_resp.raise_for_status()
                    it = _item_from_repo(repo_resp.json() or {}, source)
                    if it:
                        items.append(it)
```

改为:

```python
                    repo_resp = await client.get(f"https://api.github.com/repos/{full}")
                    repo_resp.raise_for_status()
                    repo_json = repo_resp.json() or {}
                    if not _is_ai_repo(repo_json, source.keywords):
                        continue
                    it = _item_from_repo(repo_json, source)
                    if it:
                        items.append(it)
```

- [ ] **Step 8: 跑测试确认通过 + 全 adapter 套件**

Run: `uv run python -m pytest tests/contract/test_github_trending_adapter.py -q`
Expected: PASS(全部,含既有 scrape 测试 —— 既有 `test_trending_search_maps_fields_and_signal` 的 spec 无 keywords → `_is_ai_repo` 不过滤)

- [ ] **Step 9: registry — 加 keywords 白名单 + ruff**

`config/sources.yaml` gh-trending-ai 行(Task 1 已改 genre/publisher)在 `priority: 3` 后加 `keywords`:

```yaml
- {name: gh-trending-ai, url: "https://api.github.com/search/repositories?q=topic:llm+topic:artificial-intelligence+sort:stars&sort=stars&order=desc&per_page=30", genre: writeup, publisher: individual, adapter: github_trending, status: working, priority: 3, keywords: [llm, llms, ai, artificial-intelligence, machine-learning, deep-learning, agent, agents, agentic, rag, generative-ai, transformer, diffusion, multimodal, nlp, computer-vision]}
```

Run: `uv run python -m pytest tests/contract/test_sources_yaml.py -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 通过

- [ ] **Step 10: Commit**

```bash
git add src/adapters/sources/github_trending.py config/sources.yaml tests/contract/test_github_trending_adapter.py
git commit -m "feat(gh-trending): drop non-AI repos from scrape path via keyword gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 目标1(不再每条 92: owner.type publisher + genre writeup)→ Task 1 ✓
- 目标2(非 AI 抓取阶段丢)→ Task 2 `_is_ai_repo` + scrape loop ✓
- 目标3(config 驱动)→ registry genre/publisher/keywords,adapter 读 source.keywords ✓
- 目标4(外科手术,不改打分/Search 路径/他源)→ 只动 github_trending.py + registry 行;Search 路径未碰;keywords 空→不过滤 ✓
- 测试要点 A/C/registry → Task1 Step1、Task2 Step1/Step5 ✓

**Placeholder scan:** 无 TBD/TODO;每个 code step 含完整代码。

**Type consistency:** `_publisher_for_owner(repo, source)->Publisher`、`_is_ai_repo(repo, keywords)->bool`、`_kw_match(haystack, keywords)->bool`、scrape loop 变量 `repo_json` 全程一致。`source.keywords`(SourceSpec 既有字段 `list[str] | None`)与 `_is_ai_repo` 第二参一致。
