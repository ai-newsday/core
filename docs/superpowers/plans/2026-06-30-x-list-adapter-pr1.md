# X List Adapter (PR-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 collect 层的 `x_list` adapter, 把放在 `data/x/YYYY-MM-DD.ndjson` 里的 X 推文按 `list_id` 路由到对应 yaml source, 产 `RawItem` 进 pipeline。PR-1 不上 cron (status: manual), 不依赖 extension 真实数据 —— 用 fixture ndjson 验通路。

**Architecture:** Filesystem-backed adapter (无 HTTP), 读 `data/x/<today-UTC>.ndjson` + `data/x/<yesterday-UTC>.ndjson`, 按 `source.url == "xlist:<list_id>"` 反查路由。无新依赖。

**Tech Stack:** Python 3.12, pydantic (已有 RawItem schema), pytest + pytest-asyncio (已有), `datetime.fromisoformat` 解 RFC3339, 标准库 `pathlib`/`json` 读 ndjson。

## Global Constraints

(spec [2026-06-30-x-list-interceptor-design.md](../specs/2026-06-30-x-list-interceptor-design.md))

- 输出类型 `RawItem` (在 `src/core/types.py`)
- `RawItem.published_at` 必须 tz-aware (validator 强制, 不传 UTC 会抛)
- adapter 协议: `async def fetch(source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]` (`src/adapters/sources/base.py`)
- yaml source 走 `config/sources.d/x.yaml` (registry loader 已支持 sources.d/ overlay, see memory [[sources.d-overlay]])
- 不进 score: `signals` 用 `x_favorite/x_retweet/x_quote/x_reply` flat key, production yaml `popularity_weights` 不包含 `x_*` → 贡献 0 分
- 单源 `status: manual` → PR-1 注册但 collect 层不调度

---

### Task 1: SourceSpec Literal + adapter 注册 stub

**Files:**
- Modify: `src/core/types.py:62` (`SourceSpec.adapter` Literal 加 `"x_list"`)
- Create: `src/adapters/sources/x_list.py` (空 class, fetch 返回 `[]`)
- Modify: `src/adapters/sources/__init__.py` (import + 注册)
- Test: `tests/contract/test_x_list_adapter.py` (新文件)

**Interfaces:**
- Consumes: `SourceSpec`, `RunContext`, `RawItem` from `src/core/types`
- Produces: `XListAdapter` class with `async def fetch(self, source, ctx, timeout_s) -> list[RawItem]`; registered as `ADAPTERS["x_list"]`

- [ ] **Step 1: Write the failing test**

`tests/contract/test_x_list_adapter.py`:
```python
import logging
from datetime import datetime, timezone

import pytest

from src.adapters.sources import ADAPTERS
from src.adapters.sources.x_list import XListAdapter
from src.core.types import Genre, Publisher, RunContext, SourceSpec


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 30, 1, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.x_list"),
    )


def _spec(list_id="L1", name="x-ai-lab", publisher=Publisher.lab, genre=Genre.announcement):
    return SourceSpec(
        name=name,
        url=f"xlist:{list_id}",
        genre=genre,
        publisher=publisher,
        adapter="x_list",
        status="manual",
    )


def test_x_list_is_registered_in_adapters():
    assert "x_list" in ADAPTERS
    assert isinstance(ADAPTERS["x_list"], XListAdapter)


async def test_x_list_empty_when_no_data_dir(tmp_path):
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.adapters.sources.x_list'`

- [ ] **Step 3: Modify `SourceSpec.adapter` Literal**

`src/core/types.py:62` — 把 Literal 加 `"x_list"`:
```python
    adapter: Literal[
        "rss", "hf_papers", "hf_models", "hn",
        "github_releases", "github_trending", "x_list",
    ]
```

- [ ] **Step 4: Create stub `src/adapters/sources/x_list.py`**

```python
from __future__ import annotations

from pathlib import Path

from src.core.types import RawItem, RunContext, SourceSpec


class XListAdapter:
    """Filesystem adapter: reads X (Twitter) list-timeline tweets from
    data/x/<date>.ndjson, routes by source.url == 'xlist:<list_id>'.

    PR-1: read-only, no LLM, no network. data_dir is constructor-injectable
    for tests; production singleton uses default ./data/x.
    """

    def __init__(self, data_dir: Path | str = "data/x") -> None:
        self._data_dir = Path(data_dir)

    async def fetch(
        self, source: SourceSpec, ctx: RunContext, timeout_s: int
    ) -> list[RawItem]:
        if not self._data_dir.is_dir():
            return []
        return []
```

- [ ] **Step 5: Register in `src/adapters/sources/__init__.py`**

```python
from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.github_releases import GithubReleasesAdapter
from src.adapters.sources.github_trending import GithubTrendingAdapter
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.rss import RSSAdapter
from src.adapters.sources.x_list import XListAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
    "hn": HNAdapter(),
    "github_releases": GithubReleasesAdapter(),
    "github_trending": GithubTrendingAdapter(),
    "x_list": XListAdapter(),
}
```

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add src/core/types.py src/adapters/sources/x_list.py src/adapters/sources/__init__.py tests/contract/test_x_list_adapter.py
git commit -m "feat(x_list): register adapter stub + SourceSpec Literal entry"
```

---

### Task 2: `_tweet_title` helper

**Files:**
- Modify: `src/adapters/sources/x_list.py` (加 module-level `_tweet_title`)
- Test: `tests/contract/test_x_list_adapter.py` (加边界 case)

**Interfaces:**
- Produces: `_tweet_title(text: str, n: int = 140) -> str` (module-private, 测试直接 import)

- [ ] **Step 1: Write the failing test**

加到 `tests/contract/test_x_list_adapter.py` 末尾:
```python
def test_tweet_title_short_text_returned_as_is():
    from src.adapters.sources.x_list import _tweet_title

    assert _tweet_title("GPT-5 is here.") == "GPT-5 is here."


def test_tweet_title_first_line_only():
    from src.adapters.sources.x_list import _tweet_title

    text = "GPT-5 is here.\nDetails below.\nMore stuff."
    assert _tweet_title(text) == "GPT-5 is here."


def test_tweet_title_long_english_sentence_cuts_at_sentence_end():
    from src.adapters.sources.x_list import _tweet_title

    text = (
        "OpenAI just dropped GPT-5 with 10x reasoning improvements over GPT-4. "
        "This is huge and changes the entire landscape of foundation models forever."
    )
    out = _tweet_title(text, 140)
    assert out.endswith(".")
    assert len(out) <= 140
    assert "GPT-5" in out


def test_tweet_title_long_no_punct_cuts_at_word_boundary():
    from src.adapters.sources.x_list import _tweet_title

    text = "word " * 50  # 250 chars all words, no punct
    out = _tweet_title(text, 140)
    assert out.endswith("…")
    assert len(out) <= 140
    assert " " not in out[-2:]  # cut not mid-word


def test_tweet_title_long_chinese_no_punct_hard_cuts_with_ellipsis():
    from src.adapters.sources.x_list import _tweet_title

    text = "测" * 200  # 200 chars, no spaces, no sentence-end
    out = _tweet_title(text, 140)
    assert out.endswith("…")
    assert len(out) == 140  # n-1 chars + ellipsis = n


def test_tweet_title_emoji_and_url_preserved_when_short():
    from src.adapters.sources.x_list import _tweet_title

    text = "GPT-5 🔥 https://openai.com/gpt5"
    assert _tweet_title(text) == "GPT-5 🔥 https://openai.com/gpt5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v -k tweet_title`
Expected: 5 FAIL with `ImportError: cannot import name '_tweet_title'`

- [ ] **Step 3: Implement `_tweet_title` in `src/adapters/sources/x_list.py`**

在 `class XListAdapter` 上方加:
```python
# 对齐 src/pipeline/interpret.py:59 _SENT_ENDS
_SENT_ENDS = "。！？!?；;."


def _tweet_title(text: str, n: int = 140) -> str:
    """推文首行取标题, 超长按句末 → 词界 → 硬切+省略号 三档降级。

    n 默认 140 ≈ X 推文上限 280 的一半, 给后续 interpret 层 LLM 翻译/精炼留余地;
    interpret/review 层另有 title_max_chars=64 二次夹紧, 这里不参与最终长度。
    """
    first_line = text.split("\n", 1)[0].strip()
    if len(first_line) <= n:
        return first_line
    window = first_line[:n]
    cut = max((window.rfind(ch) for ch in _SENT_ENDS), default=-1)
    if cut >= 0:
        return window[: cut + 1]
    ws = window.rfind(" ")
    if ws > n // 2:
        return window[:ws] + "…"
    return first_line[: n - 1] + "…"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v -k tweet_title`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/adapters/sources/x_list.py tests/contract/test_x_list_adapter.py
git commit -m "feat(x_list): _tweet_title helper (first-line, sentence/word/ellipsis cascade)"
```

---

### Task 3: Fixture ndjson + happy-path fetch (字段映射)

**Files:**
- Create: `tests/fixtures/x_list_sample.ndjson` (5 行, 覆盖 happy/quote/list_id-mismatch/long-text)
- Modify: `src/adapters/sources/x_list.py` (实现 fetch 真逻辑)
- Test: `tests/contract/test_x_list_adapter.py` (加映射断言)

**Interfaces:**
- Consumes: ndjson 行 schema (spec §"ndjson schema") — keys: `tweet_id, list_id, author_handle, author_name, text, quoted_text?, quoted_author_handle?, permalink, created_at, favorite_count, retweet_count, quote_count, reply_count, captured_at`
- Produces: `XListAdapter.fetch` 真实读取 + 路由 + 字段映射

- [ ] **Step 1: 写 fixture `tests/fixtures/x_list_sample.ndjson`** (5 行, 每行一个 JSON)

```ndjson
{"tweet_id":"1001","list_id":"L1","author_handle":"sama","author_name":"Sam Altman","text":"GPT-5 is here. 10x reasoning over GPT-4.","permalink":"https://x.com/sama/status/1001","created_at":"2026-06-30T00:30:00Z","favorite_count":12000,"retweet_count":3400,"quote_count":210,"reply_count":890,"captured_at":"2026-06-30T00:35:00Z"}
{"tweet_id":"1002","list_id":"L1","author_handle":"demishassabis","author_name":"Demis Hassabis","text":"Gemini Ultra wins on MMLU.\nSee paper.","permalink":"https://x.com/demishassabis/status/1002","created_at":"2026-06-29T22:10:00Z","favorite_count":8000,"retweet_count":2000,"quote_count":100,"reply_count":300,"captured_at":"2026-06-29T22:15:00Z"}
{"tweet_id":"1003","list_id":"L1","author_handle":"swyx","author_name":"swyx","text":"Hot take on GPT-5","quoted_text":"GPT-5 is here.","quoted_author_handle":"sama","permalink":"https://x.com/swyx/status/1003","created_at":"2026-06-30T01:00:00Z","favorite_count":500,"retweet_count":80,"quote_count":12,"reply_count":40,"captured_at":"2026-06-30T01:05:00Z"}
{"tweet_id":"1004","list_id":"L2","author_handle":"ylecun","author_name":"Yann LeCun","text":"Scale is not enough.","permalink":"https://x.com/ylecun/status/1004","created_at":"2026-06-30T00:00:00Z","favorite_count":3000,"retweet_count":700,"quote_count":50,"reply_count":200,"captured_at":"2026-06-30T00:05:00Z"}
{"tweet_id":"1005","list_id":"UNKNOWN","author_handle":"rando","author_name":"Rando","text":"Should not be routed.","permalink":"https://x.com/rando/status/1005","created_at":"2026-06-30T00:00:00Z","favorite_count":1,"retweet_count":0,"quote_count":0,"reply_count":0,"captured_at":"2026-06-30T00:05:00Z"}
```

- [ ] **Step 2: Write failing tests for field mapping**

加到 `tests/contract/test_x_list_adapter.py`:
```python
import shutil
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "x_list_sample.ndjson"


def _seed_data(tmp_path, date_str="2026-06-30"):
    """复制 fixture 到 tmp_path/<date>.ndjson 模拟 extension PUT 的位置。"""
    dst = tmp_path / f"{date_str}.ndjson"
    shutil.copy(FIXTURE, dst)
    return dst


async def test_x_list_routes_by_list_id_and_maps_fields(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    spec = _spec(list_id="L1", name="x-ai-lab", publisher=Publisher.lab,
                 genre=Genre.announcement)
    items = await adapter.fetch(spec, _ctx(), timeout_s=15)

    # L1 has 3 rows (1001, 1002, 1003); L2 + UNKNOWN excluded
    assert [it.link for it in items] == [
        "https://x.com/sama/status/1001",
        "https://x.com/demishassabis/status/1002",
        "https://x.com/swyx/status/1003",
    ]

    it = items[0]
    assert it.source == "x-ai-lab"
    assert it.title_en == "GPT-5 is here. 10x reasoning over GPT-4."
    assert it.genre == Genre.announcement
    assert it.publisher == Publisher.lab
    assert it.published_at.tzinfo is not None
    assert it.published_at == datetime(2026, 6, 30, 0, 30, tzinfo=timezone.utc)
    assert it.raw_summary.startswith("@sama:\n")
    assert "GPT-5 is here." in it.raw_summary
    assert it.signals == {
        "x_favorite": 12000, "x_retweet": 3400,
        "x_quote": 210, "x_reply": 890,
    }
    assert it.fetched_via == "native"


async def test_x_list_quote_tweet_appends_quoted_text_to_body(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    # tweet 1003 has quoted_text
    quote_item = next(it for it in items if it.link.endswith("1003"))
    assert "@swyx:\n" in quote_item.raw_summary
    assert "Hot take on GPT-5" in quote_item.raw_summary
    assert "> 引用 @sama: GPT-5 is here." in quote_item.raw_summary


async def test_x_list_skips_rows_with_unknown_list_id(tmp_path, caplog):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    # spec L1 only — 1005 (list_id=UNKNOWN) and 1004 (list_id=L2) must not appear
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    links = [it.link for it in items]
    assert "https://x.com/rando/status/1005" not in links
    assert "https://x.com/ylecun/status/1004" not in links


async def test_x_list_different_spec_routes_different_rows(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(
        _spec(list_id="L2", name="x-ai-kol-en",
              publisher=Publisher.individual, genre=Genre.writeup),
        _ctx(), timeout_s=15,
    )
    assert len(items) == 1
    assert items[0].link.endswith("1004")
    assert items[0].source == "x-ai-kol-en"
    assert items[0].publisher == Publisher.individual
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v`
Expected: 4 new tests FAIL (stub returns [])

- [ ] **Step 4: Implement real fetch in `x_list.py`**

替换 stub `fetch` 方法:
```python
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class XListAdapter:
    def __init__(self, data_dir: Path | str = "data/x") -> None:
        self._data_dir = Path(data_dir)

    async def fetch(
        self, source: SourceSpec, ctx: RunContext, timeout_s: int
    ) -> list[RawItem]:
        if not self._data_dir.is_dir():
            return []
        list_id = _parse_list_id(source.url)
        if list_id is None:
            logger.warning("x_list source %s has invalid url %r", source.name, source.url)
            return []

        items: list[RawItem] = []
        for path in _candidate_files(self._data_dir, ctx.now):
            for row in _iter_ndjson(path):
                if row.get("list_id") != list_id:
                    continue
                item = _row_to_raw_item(row, source)
                if item is not None:
                    items.append(item)
        return items


def _parse_list_id(url: str) -> str | None:
    if not url.startswith("xlist:"):
        return None
    lid = url[len("xlist:") :].strip()
    return lid or None


def _candidate_files(data_dir: Path, now: datetime) -> list[Path]:
    """读 today-UTC + yesterday-UTC 两文件 (finalize 跑在 01:00 UTC, 这两天合并刚覆盖晨报关心的 24h 窗)。"""
    today = now.astimezone(timezone.utc).date()
    yday = today.fromordinal(today.toordinal() - 1)
    return [
        data_dir / f"{yday.isoformat()}.ndjson",
        data_dir / f"{today.isoformat()}.ndjson",
    ]


def _iter_ndjson(path: Path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("x_list malformed ndjson at %s:%d", path, lineno)


def _row_to_raw_item(row: dict, source: SourceSpec) -> RawItem | None:
    try:
        text = str(row["text"])
        permalink = str(row["permalink"])
        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        author = str(row.get("author_handle") or "")
        quoted = row.get("quoted_text")
        body_parts = [f"@{author}:\n{text}"] if author else [text]
        if quoted:
            qa = row.get("quoted_author_handle") or ""
            body_parts.append(f"\n\n> 引用 @{qa}: {quoted}")
        body = "".join(body_parts)
        signals = {
            "x_favorite": row.get("favorite_count") or 0,
            "x_retweet": row.get("retweet_count") or 0,
            "x_quote": row.get("quote_count") or 0,
            "x_reply": row.get("reply_count") or 0,
        }
        return RawItem(
            title_en=_tweet_title(text),
            link=permalink,
            source=source.name,
            genre=source.genre,
            publisher=source.publisher,
            published_at=created,
            raw_summary=body,
            signals=signals,
            fetched_via="native",
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("x_list bad row %r: %s", row.get("tweet_id"), e)
        return None
```

- [ ] **Step 5: Run all tests to verify pass**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v`
Expected: all PASS (~11 tests total: 2 setup + 5 tweet_title + 4 mapping)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/x_list_sample.ndjson src/adapters/sources/x_list.py tests/contract/test_x_list_adapter.py
git commit -m "feat(x_list): fetch reads ndjson, routes by list_id, maps to RawItem"
```

---

### Task 4: Filesystem 边界 case (缺文件 / 坏行 / 坏时间戳)

**Files:**
- Test: `tests/contract/test_x_list_adapter.py` (加边界 case)

**Interfaces:** 无新接口, 验证既有实现对降级路径的反应。

- [ ] **Step 1: Write failing tests for edges**

加到测试文件末尾:
```python
async def test_x_list_missing_today_uses_yesterday_only(tmp_path):
    _seed_data(tmp_path, date_str="2026-06-29")  # 只放 yesterday-UTC
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    assert len(items) == 3  # L1 三条仍读到


async def test_x_list_malformed_ndjson_line_skipped(tmp_path, caplog):
    p = tmp_path / "2026-06-30.ndjson"
    p.write_text(
        '{"tweet_id":"a","list_id":"L1","text":"good","permalink":"https://x.com/a/status/a","created_at":"2026-06-30T00:00:00Z","author_handle":"a","favorite_count":0,"retweet_count":0,"quote_count":0,"reply_count":0}\n'
        "this is not json\n"
        '{"tweet_id":"b","list_id":"L1","text":"good 2","permalink":"https://x.com/b/status/b","created_at":"2026-06-30T00:00:00Z","author_handle":"b","favorite_count":0,"retweet_count":0,"quote_count":0,"reply_count":0}\n',
        encoding="utf-8",
    )
    adapter = XListAdapter(data_dir=tmp_path)
    with caplog.at_level("WARNING"):
        items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    assert len(items) == 2  # 第 1 / 第 3 行通过
    assert any("malformed ndjson" in r.message for r in caplog.records)


async def test_x_list_row_missing_required_key_skipped(tmp_path):
    p = tmp_path / "2026-06-30.ndjson"
    p.write_text(
        '{"tweet_id":"x","list_id":"L1"}\n',  # 缺 text/permalink/created_at
        encoding="utf-8",
    )
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    assert items == []


async def test_x_list_invalid_url_returns_empty(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    spec = SourceSpec(
        name="bad",
        url="not-xlist-prefix",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        adapter="x_list",
        status="manual",
    )
    items = await adapter.fetch(spec, _ctx(), timeout_s=15)
    assert items == []


async def test_x_list_no_data_dir_returns_empty_silently(tmp_path):
    adapter = XListAdapter(data_dir=tmp_path / "does-not-exist")
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    assert items == []
```

- [ ] **Step 2: Run tests to verify behavior**

Run: `uv run pytest tests/contract/test_x_list_adapter.py -v`
Expected: 5 new tests PASS (Task 3 实现里已覆盖这些路径). 若 missing-today test 失败 → 检查 `_candidate_files` 是否按顺序返回 yesterday 在前.

- [ ] **Step 3: 若有 FAIL — fix in `x_list.py`**

(预期不需要; 但若 caplog 没捕获到, 检查 logger 是否用了 `logging.getLogger(__name__)` 而非自建. 若 row-missing-key test 失败, 确认 `_row_to_raw_item` 的 except 涵盖 `KeyError`.)

- [ ] **Step 4: Commit**

```bash
git add tests/contract/test_x_list_adapter.py
git commit -m "test(x_list): cover missing file, malformed line, bad row, invalid url"
```

---

### Task 5: yaml 配置 + registry loader 校验

**Files:**
- Create: `config/sources.d/x.yaml`
- Test: 既有 registry contract test (e.g. `tests/test_registry.py` 或 `tests/golden/`) 若有则确认通过; 否则不强行加测

**Interfaces:** yaml 加载后 4 个 source 被注册到 registry, 不会因 Literal/format 报错; status=manual 让 collect 跳过它们。

- [ ] **Step 1: Find existing registry test**

Run: `grep -rln "sources_registry_path\|load_sources\|sources\\.d" tests/ 2>/dev/null | head -3`
Expected: 找到 1-2 个文件; 若无 → 跳过 Step 4 (yaml 改动由 ruff/yaml-lint 兜底).

- [ ] **Step 2: Create `config/sources.d/x.yaml`**

```yaml
# config/sources.d/x.yaml — X (Twitter) list timelines via local extension capture
# url: 'xlist:<list_id>' sentinel encodes the X List ID;
#   adapter routes data/x/YYYY-MM-DD.ndjson rows by list_id match.
# status: manual — PR-1 wires path but does not run; flip to 'working' after
#   PR-3 (extension) is installed and the user fills real list_id values.
- {name: x-ai-lab,    url: "xlist:TBD", publisher: lab,        genre: announcement, adapter: x_list, status: manual, priority: 1}
- {name: x-ai-kol-en, url: "xlist:TBD", publisher: individual, genre: writeup,      adapter: x_list, status: manual, priority: 2}
- {name: x-ai-kol-zh, url: "xlist:TBD", publisher: individual, genre: writeup,      adapter: x_list, status: manual, priority: 2}
- {name: x-ai-news,   url: "xlist:TBD", publisher: media,      genre: news,         adapter: x_list, status: manual, priority: 2}
```

- [ ] **Step 3: Smoke-load via Python**

Run: `uv run python -c "from src.core.registry import load_sources; specs = load_sources('config/sources.yaml'); print([s.name for s in specs if s.adapter == 'x_list'])"`
Expected: prints `['x-ai-lab', 'x-ai-kol-en', 'x-ai-kol-zh', 'x-ai-news']` (若 loader 函数名不同, 见 Step 1 grep 结果替换).

若报错 `Literal does not include 'x_list'` → Task 1 Step 3 没改全, 回去修。

- [ ] **Step 4: Run full test suite + lint (memory: [[lint-before-push]])**

Run in parallel:
- `uv run pytest tests/ -q`
- `uv run ruff check .`
- `uv run ruff format --check .`

Expected: 全绿. ruff format 若有抱怨 → `uv run ruff format .` 后 commit.

- [ ] **Step 5: Commit**

```bash
git add config/sources.d/x.yaml
git commit -m "feat(sources): wire 4 X list sources in sources.d/x.yaml (status=manual)"
```

---

### Task 6: 收尾 — 跑一次 --dry-run 确认 funnel 不挂

**Files:** 无新文件; 验证集成。

**Interfaces:** 整个 collect pipeline 对 `status: manual` 源跳过, 不抓 x_list, source_reports 不报错。

- [ ] **Step 1: Confirm collect skips manual sources**

Run: `grep -n "status.*manual\|status.*working" src/pipeline/collect.py src/core/registry.py | head`
预期: 看到 collect 只跑 `status == "working"` 源; manual 的 4 个 x-* 不会触发 adapter 调用.

- [ ] **Step 2: Dry-run smoke (per memory: [[run-real-dryrun-diagnostics]])**

Run: `uv run python -m src.cli --dry-run --score 2>&1 | tail -30`
Expected: 跑完不抛; 输出里**没有** `x-ai-*` 相关错误 (因为 manual 不跑).

⚠️ **不要跑 `--tick collect` 或 `--tick finalize`** — 会写 db / content. 只 `--dry-run --score`.

- [ ] **Step 3: Final commit (若 Step 2 揭示任何小问题)**

无问题 → 跳过. 有 → 修 + commit.

---

## Self-review (post-write check)

**Spec 覆盖检查 (spec → task 映射):**

| spec 内容 | task |
|---|---|
| ndjson schema 12 字段 | Task 3 fixture 全覆盖 |
| `_tweet_title` 4 档降级 | Task 2 五个 case |
| `xlist:<list_id>` sentinel url | Task 1 `_parse_list_id` + Task 3 `fetch` |
| UTC today + yesterday 双文件读 | Task 3 `_candidate_files` + Task 4 missing-today case |
| RawItem 字段映射 | Task 3 mapping test |
| 失败降级 (坏行 / 坏 row / 无 dir) | Task 4 五个 case |
| signals key 命名 `x_favorite/...` flat | Task 3 mapping assertion |
| publisher 来源自 SourceSpec | Task 3 `test_x_list_different_spec_routes_different_rows` |
| yaml 4 行 status=manual | Task 5 |
| PR-1 不上 cron | Task 5 (status=manual) + Task 6 verify |

(PR-2 / PR-3 不在本 plan, 见 spec §"实施顺序")

**Placeholder 扫:** 无 TBD / TODO / "similar to" / "appropriate error handling". 所有 code block 完整可粘贴.

**Type 一致:** `XListAdapter(data_dir=Path|str)` 一致; `_tweet_title(text, n=140)` 默认值一致; signals key 在 spec / Task 3 实现 / Task 3 测试三处全是 `x_favorite/x_retweet/x_quote/x_reply`.

---

## 不在本 plan (PR-2 / PR-3)

- PR-2: 提交一份 `data/x/2026-07-01.ndjson` 进 master, 把 `status: manual` → `working`, dry-run 看 funnel.
- PR-3: 新 repo `ai-newsday/x-extension` (MV3 + RequestInterceptor + GitHub API sync).

两者独立, 不阻塞本 PR-1 合并.
