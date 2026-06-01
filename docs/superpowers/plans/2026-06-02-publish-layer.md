# 发布层 (Publish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把审阅层定稿的 `ReviewResult` 组装成统一内容模型 `DailyReport`（今日看点/必读Top3/分类速览/数据概览），再渲染成可发布的 Markdown 字符串。

**Architecture:** 纯核心，两步解耦 = PRD §5.1 一稿多渲染骨架。组装（`build_report`）与渲染（`render_markdown`）分开，皆纯函数；本圈只落地 Markdown 渲染器，P1 复用同一 `DailyReport` 加 JSON/Notion 渲染器。无网络/LLM/渠道副作用，唯一产物是内存字符串，天然 `--dry-run`。

**Tech Stack:** Python 3.12（uv）、pydantic v2、PyYAML、pytest。沿用 `src/observability/events.emit`、`src/core/config` 加载器风格、`src/cli.py` 链式 dry-run 模式。

> **重要：全程用 `uv run pytest`**。裸 `pytest` 会选到 miniconda 3.13 解释器导致 import 失败。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/core/types.py` | 追加 `Overview`/`CategorySection`/`DailyReport`/`PublishConfig`/`PublishResult` | Modify |
| `src/core/config.py` | 追加 `load_publish_config(path)` | Modify |
| `config/publish.yaml` | 展示常量（必读条数/类型标签/水印/关键词数） | Create |
| `src/pipeline/publish.py` | 6 个纯函数 + `publish()` 编排 | Create |
| `src/cli.py` | 追加 `run_dry_publish` + `--publish` flag | Modify |
| `tests/contract/test_publish_types.py` | 类型 schema / 默认值 | Create |
| `tests/contract/test_publish_config.py` | 配置加载（缺文件回默认 / 覆盖） | Create |
| `tests/golden/test_publish.py` | §9 用例 + 纯函数单测 | Create |
| `tests/golden/data/publish_report.md` | snapshot 期望 Markdown | Create（Task 5 内生成） |
| `tests/contract/test_cli_publish.py` | `--publish` 链形状 | Create |
| `docs/ROADMAP.md` | Circle 6 → 🟩 | Modify |

**上游契约回顾（只读，勿改）：**
- `ReviewResult`（`src/core/types.py`）：`reviewed_items: list[ReviewedItem]`、`daily_take: str | None`、`input_count`、`kept_count`、`dropped_count`、`edited_count`、`is_reviewed`、`is_pending`、`is_silent`。
- `ReviewedItem(InterpretedItem)`：继承 `title_en`/`link`/`source`/`source_type:SourceType`/`published_at`/`raw_summary`/`cluster_id`/`related_links`/`score:int`/`score_breakdown`/`is_explore:bool`/`title`/`summary`/`takeaway`/`hot_take`/`tags:list[str]`/`evidence:list[Evidence]`/`interpretation_status`/`eligible_for_must_read:bool`，加 `review_action`/`was_edited`/`edited_fields`。
- `Evidence`：`claim: str`、`anchor: str`（皆 `Field(min_length=1)`）。
- `SourceType` 枚举值：`paper`/`model`/`tool`/`community`/`official`/`news`/`blog`。
- `emit(logger, event, **params)`：写一行 JSON 日志。

---

## Task 1: 核心类型 (DailyReport / PublishConfig / PublishResult)

**Files:**
- Modify: `src/core/types.py`（在文件末尾、`ReviewResult` 之后追加）
- Test: `tests/contract/test_publish_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_publish_types.py`:

```python
from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, ReviewedItem,
                            Overview, CategorySection, DailyReport,
                            PublishConfig, PublishResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ri(link="https://a/1", source_type=SourceType.MODEL, score=80,
        eligible=True, is_explore=False):
    return ReviewedItem(
        title_en="X released", link=link, source="src",
        source_type=source_type, published_at=NOW, raw_summary="A.",
        cluster_id="evt-1", related_links=[], score=score,
        score_breakdown={"机构影响力": float(score)}, is_explore=is_explore,
        title="中文标题", summary="中文摘要。", takeaway="怎么用。",
        hot_take="锐评。", tags=["#a", "#b", "#c"],
        evidence=[Evidence(claim="事实", anchor=link)],
        interpretation_status="ok", eligible_for_must_read=eligible,
        review_action="keep", was_edited=False, edited_fields=[])


def test_overview_shape():
    o = Overview(type_distribution={"model": 2}, keywords=["MoE", "Agent"])
    assert o.type_distribution == {"model": 2}
    assert o.keywords == ["MoE", "Agent"]


def test_category_section_shape():
    c = CategorySection(source_type="model", label="模型", items=[_ri()])
    assert c.source_type == "model" and c.label == "模型"
    assert len(c.items) == 1 and c.items[0].score == 80


def test_daily_report_shape():
    rep = DailyReport(
        date_label="2026-05-30（周六）", daily_take="看点。",
        must_read=[_ri()],
        categories=[CategorySection(source_type="model", label="模型",
                                    items=[_ri()])],
        overview=Overview(type_distribution={"model": 1}, keywords=["a"]),
        is_pending=False, item_count=1, explore_count=0)
    assert rep.date_label == "2026-05-30（周六）"
    assert rep.daily_take == "看点。" and rep.is_pending is False
    assert rep.item_count == 1 and rep.explore_count == 0
    assert rep.must_read[0].title == "中文标题"


def test_daily_report_daily_take_optional():
    rep = DailyReport(date_label="d", daily_take=None, must_read=[],
                      categories=[],
                      overview=Overview(type_distribution={}, keywords=[]),
                      is_pending=True, item_count=0, explore_count=0)
    assert rep.daily_take is None and rep.is_pending is True


def test_publish_config_defaults():
    c = PublishConfig()
    assert c.must_read_count == 3 and c.top_keywords == 4
    assert "未审" in c.pending_watermark
    assert c.type_labels["model"] == "模型"
    # type_labels 键顺序即组间顺序
    assert list(c.type_labels)[0] == "official"


def test_publish_result_shape():
    res = PublishResult(
        report=DailyReport(date_label="d", daily_take=None, must_read=[],
                           categories=[],
                           overview=Overview(type_distribution={}, keywords=[]),
                           is_pending=True, item_count=0, explore_count=0),
        markdown="", is_pending=True, is_silent=True)
    assert res.markdown == "" and res.is_silent is True
    assert res.is_pending is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_publish_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'Overview'`

- [ ] **Step 3: Append types to `src/core/types.py`**

在文件末尾（`ReviewResult` 之后）追加。注意 `BaseModel`/`Field`/`dataclass`/`field` 已在文件顶部导入：

```python
# --- publish layer (Circle 6) ---
class Overview(BaseModel):
    type_distribution: dict[str, int] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)


class CategorySection(BaseModel):
    source_type: str
    label: str
    items: list[ReviewedItem] = Field(default_factory=list)


class DailyReport(BaseModel):
    date_label: str
    daily_take: str | None
    must_read: list[ReviewedItem] = Field(default_factory=list)
    categories: list[CategorySection] = Field(default_factory=list)
    overview: Overview
    is_pending: bool
    item_count: int
    explore_count: int


@dataclass
class PublishConfig:
    must_read_count: int = 3
    top_keywords: int = 4
    pending_watermark: str = "⚠ 未审草稿（待人工定稿，勿直接发布）"
    type_labels: dict[str, str] = field(default_factory=lambda: {
        "official": "官方",
        "paper": "论文",
        "model": "模型",
        "tool": "工具 / 开源",
        "news": "新闻",
        "community": "社区",
        "blog": "博客",
    })


@dataclass
class PublishResult:
    report: DailyReport
    markdown: str
    is_pending: bool
    is_silent: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_publish_types.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_publish_types.py
git commit -m "feat(publish): add Circle 6 core types (DailyReport/PublishConfig/PublishResult)"
```

---

## Task 2: 配置加载器 + config/publish.yaml

**Files:**
- Modify: `src/core/config.py`（追加 `load_publish_config`；import 行加 `PublishConfig`）
- Create: `config/publish.yaml`
- Test: `tests/contract/test_publish_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_publish_config.py`:

```python
from src.core.config import load_publish_config
from src.core.types import PublishConfig


def test_load_publish_config_missing_returns_defaults(tmp_path):
    cfg = load_publish_config(str(tmp_path / "nope.yaml"))
    assert cfg == PublishConfig()


def test_load_publish_config_overrides_fields(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text("must_read_count: 5\ntop_keywords: 2\n"
                 'pending_watermark: "待审"\n', encoding="utf-8")
    cfg = load_publish_config(str(p))
    assert cfg.must_read_count == 5 and cfg.top_keywords == 2
    assert cfg.pending_watermark == "待审"
    # 未覆盖字段保持默认
    assert cfg.type_labels["model"] == "模型"


def test_load_publish_config_overrides_type_labels(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text("type_labels:\n  model: \"大模型\"\n  paper: \"论文\"\n",
                 encoding="utf-8")
    cfg = load_publish_config(str(p))
    assert cfg.type_labels == {"model": "大模型", "paper": "论文"}
    # 未覆盖标量字段保持默认
    assert cfg.must_read_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_publish_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_publish_config'`

- [ ] **Step 3: Create `config/publish.yaml`**

```yaml
must_read_count: 3                       # 今日必读取前几条
top_keywords: 4                          # 数据概览高频关键词个数
pending_watermark: "⚠ 未审草稿（待人工定稿，勿直接发布）"
type_labels:                             # source_type → 中文展示名(键顺序=组间顺序)
  official: "官方"
  paper: "论文"
  model: "模型"
  tool: "工具 / 开源"
  news: "新闻"
  community: "社区"
  blog: "博客"
```

- [ ] **Step 4: Add loader to `src/core/config.py`**

把顶部 import 行改为包含 `PublishConfig`：

```python
from src.core.types import (DedupConfig, ScoringConfig, InterpretConfig,
                            ReviewConfig, ReviewDecision, PublishConfig)
```

在文件末尾追加：

```python
def load_publish_config(path: str) -> PublishConfig:
    """Load publish display constants from YAML; missing/empty file -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return PublishConfig()
    d = PublishConfig()
    return PublishConfig(
        must_read_count=data.get("must_read_count", d.must_read_count),
        top_keywords=data.get("top_keywords", d.top_keywords),
        pending_watermark=data.get("pending_watermark", d.pending_watermark),
        type_labels=data.get("type_labels", d.type_labels),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_publish_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/core/config.py config/publish.yaml tests/contract/test_publish_config.py
git commit -m "feat(publish): add load_publish_config + config/publish.yaml"
```

---

## Task 3: 纯函数 — select_must_read / group_by_category / build_overview

**Files:**
- Create: `src/pipeline/publish.py`
- Test: `tests/golden/test_publish.py`（本任务先建文件 + 这三组测试）

- [ ] **Step 1: Write the failing test**

Create `tests/golden/test_publish.py`:

```python
from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, ReviewedItem, PublishConfig)
from src.pipeline.publish import (select_must_read, group_by_category,
                                  build_overview)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
CFG = PublishConfig()


def _ri(link="https://a/1", source_type=SourceType.MODEL, score=80,
        title="中文标题", summary="中文摘要。", takeaway="怎么用。",
        hot_take="锐评。", tags=None, evidence=None, related=None,
        eligible=True, is_explore=False, status="ok"):
    return ReviewedItem(
        title_en="X released", link=link, source="src",
        source_type=source_type, published_at=NOW, raw_summary="A.",
        cluster_id="evt-1", related_links=related or [], score=score,
        score_breakdown={"机构影响力": float(score)}, is_explore=is_explore,
        title=title, summary=summary, takeaway=takeaway, hot_take=hot_take,
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [
            Evidence(claim="事实", anchor=link)],
        interpretation_status=status, eligible_for_must_read=eligible,
        review_action="keep", was_edited=False, edited_fields=[])


def test_select_must_read_only_eligible_top_n():
    items = [_ri("https://a/1", eligible=True),
             _ri("https://a/2", eligible=False),
             _ri("https://a/3", eligible=True),
             _ri("https://a/4", eligible=True),
             _ri("https://a/5", eligible=True)]
    mr = select_must_read(items, CFG)
    # 仅 eligible, 保上游序, 取前 3
    assert [i.link for i in mr] == ["https://a/1", "https://a/3", "https://a/4"]


def test_select_must_read_fewer_than_n():
    items = [_ri("https://a/1", eligible=True),
             _ri("https://a/2", eligible=False)]
    mr = select_must_read(items, CFG)
    assert [i.link for i in mr] == ["https://a/1"]


def test_group_by_category_order_and_grouping():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.PAPER),
             _ri("https://a/3", source_type=SourceType.MODEL)]
    cats = group_by_category(items, CFG)
    # type_labels 键序: official, paper, model... → paper 组在 model 组前
    assert [c.source_type for c in cats] == ["paper", "model"]
    assert cats[0].label == "论文" and cats[1].label == "模型"
    # 空类目不产 section
    assert all(len(c.items) > 0 for c in cats)
    # 组内保上游序
    assert [i.link for i in cats[1].items] == ["https://a/1", "https://a/3"]
    # 全量目录: 不漏
    assert sum(len(c.items) for c in cats) == 3


def test_group_by_category_unknown_type_last():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.BLOG)]
    # blog 在 type_labels 末尾, 仍按声明序; 构造一个不在表里的类型测兜底
    cfg = PublishConfig(type_labels={"model": "模型"})
    cats = group_by_category(items, cfg)
    # model 在表里排前, blog 不在表里排末尾且 label 回退英文
    assert [c.source_type for c in cats] == ["model", "blog"]
    assert cats[1].label == "blog"


def test_build_overview_distribution_and_keywords():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 tags=["#MoE", "#Agent"]),
             _ri("https://a/2", source_type=SourceType.MODEL,
                 tags=["#MoE", "#推理"]),
             _ri("https://a/3", source_type=SourceType.PAPER,
                 tags=["#MoE"])]
    ov = build_overview(items, CFG)
    assert ov.type_distribution == {"paper": 1, "model": 2}
    # MoE 频次最高在前; 去 # 前缀; 按频次降序、同频按首现序
    assert ov.keywords[0] == "MoE"
    assert set(ov.keywords) == {"MoE", "Agent", "推理"}


def test_build_overview_keywords_top_n_and_empty_tags():
    items = [_ri("https://a/1", tags=[]),
             _ri("https://a/2", tags=["#x", "#y", "#z", "#w", "#v"])]
    cfg = PublishConfig(top_keywords=2)
    ov = build_overview(items, cfg)
    assert len(ov.keywords) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.publish'`

- [ ] **Step 3: Create `src/pipeline/publish.py` with the three pure functions**

```python
from __future__ import annotations
from collections import Counter
from src.core.types import (ReviewedItem, PublishConfig, Overview,
                            CategorySection, DailyReport, PublishResult,
                            ReviewResult, RunContext)
from src.observability.events import emit


def select_must_read(items: list[ReviewedItem],
                     config: PublishConfig) -> list[ReviewedItem]:
    """合格(eligible)条目里按上游序取前 must_read_count 条。"""
    eligible = [it for it in items if it.eligible_for_must_read]
    return eligible[:config.must_read_count]


def group_by_category(items: list[ReviewedItem],
                      config: PublishConfig) -> list[CategorySection]:
    """按 source_type 分组; 组间按 type_labels 键序(不在表里的排末尾);
    组内保上游序; 空类目不产 section。"""
    order = list(config.type_labels)
    seen: list[str] = []
    buckets: dict[str, list[ReviewedItem]] = {}
    for it in items:
        st = it.source_type.value
        if st not in buckets:
            buckets[st] = []
            seen.append(st)
        buckets[st].append(it)

    def rank(st: str) -> tuple[int, int]:
        return (order.index(st), 0) if st in order else (len(order), seen.index(st))

    out: list[CategorySection] = []
    for st in sorted(seen, key=rank):
        out.append(CategorySection(
            source_type=st, label=config.type_labels.get(st, st),
            items=buckets[st]))
    return out


def build_overview(items: list[ReviewedItem],
                   config: PublishConfig) -> Overview:
    """类型分布计数(按 type_labels 键序) + 高频关键词(聚合 tags 去 # 取 Top N)。"""
    order = list(config.type_labels)
    counts = Counter(it.source_type.value for it in items)
    dist = {st: counts[st] for st in order if counts.get(st)}
    for st in counts:                       # 不在表里的类型补在后面
        if st not in dist:
            dist[st] = counts[st]

    freq: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    seq = 0
    for it in items:
        for tag in it.tags:
            kw = tag.lstrip("#")
            if not kw:
                continue
            if kw not in first_seen:
                first_seen[kw] = seq
                seq += 1
            freq[kw] += 1
    ranked = sorted(freq, key=lambda k: (-freq[k], first_seen[k]))
    return Overview(type_distribution=dist,
                    keywords=ranked[:config.top_keywords])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py
git commit -m "feat(publish): pure select_must_read/group_by_category/build_overview"
```

---

## Task 4: build_report + render_markdown

**Files:**
- Modify: `src/pipeline/publish.py`（追加 `build_report` + `render_markdown`）
- Test: `tests/golden/test_publish.py`（追加测试）

**渲染格式（确定性，无 now()）：** 报头 `# AI Daily · {date_label}`；pending 时下一行 `> {watermark}`；`daily_take` 非空时 `> **今日看点**：{daily_take}`；必读块每条 `### {i}. [{label}] {title}（{title_en}）` + 一句话/对你/锐评/评分+来源/依据；速览块每组 `**{label}**` + 行 `` - `[{score}]`[ 🧭探索] {title} — {summary} ｜ [{source}]({link})``；概览块分类分布 + 高频关键词；页脚固定。块之间空行分隔。

- [ ] **Step 1: Write the failing test**

在 `tests/golden/test_publish.py` 末尾追加（顶部 import 改为同时引入 `build_report` / `render_markdown` / `DailyReport` / `ReviewResult`）：

把文件顶部的 import 行改成：

```python
from src.core.types import (SourceType, Evidence, ReviewedItem, PublishConfig,
                            DailyReport, ReviewResult)
from src.pipeline.publish import (select_must_read, group_by_category,
                                  build_overview, build_report, render_markdown)
```

追加测试：

```python
def _rr(items, daily_take="看点。", is_pending=False, is_silent=False):
    n = len(items)
    return ReviewResult(
        reviewed_items=items, daily_take=daily_take, input_count=n,
        kept_count=n, dropped_count=0, edited_count=0,
        is_reviewed=not is_pending, is_pending=is_pending, is_silent=is_silent)


def test_build_report_assembles_blocks():
    items = [_ri("https://a/1", source_type=SourceType.MODEL, eligible=True),
             _ri("https://a/2", source_type=SourceType.PAPER, eligible=False,
                 is_explore=True)]
    rep = build_report(_rr(items), "2026-05-30（周六）", CFG)
    assert rep.date_label == "2026-05-30（周六）"
    assert rep.item_count == 2 and rep.explore_count == 1
    assert [i.link for i in rep.must_read] == ["https://a/1"]
    assert [c.source_type for c in rep.categories] == ["paper", "model"]
    assert rep.is_pending is False
    # 必读子集: must_read 出现在其类型分组里
    model_cat = [c for c in rep.categories if c.source_type == "model"][0]
    assert "https://a/1" in [i.link for i in model_cat.items]
    # 全量目录守恒
    assert sum(len(c.items) for c in rep.categories) == rep.item_count


def test_render_markdown_full():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 title="GLM-5 发布", summary="开源 MoE。", tags=["#MoE"])]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert md.startswith("# AI Daily · 2026-05-30")
    assert "> **今日看点**：看点。" in md
    assert "## 🏆 今日必读" in md
    assert "### 1. [模型] GLM-5 发布（X released）" in md
    assert "**一句话**：开源 MoE。" in md
    assert "**对你**：怎么用。" in md
    assert "**锐评**：锐评。" in md
    assert "[src](https://a/1)" in md
    assert "## 📚 分类速览" in md
    assert "## 📊 数据概览" in md
    assert "MoE" in md


def test_render_markdown_pending_watermark():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, is_pending=True), "d", CFG), CFG)
    assert CFG.pending_watermark in md


def test_render_markdown_no_watermark_when_reviewed():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, is_pending=False), "d", CFG), CFG)
    assert CFG.pending_watermark not in md


def test_render_markdown_omits_empty_daily_take():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, daily_take=None), "d", CFG), CFG)
    assert "今日看点" not in md


def test_render_markdown_omits_must_read_when_none_eligible():
    items = [_ri("https://a/1", eligible=False)]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "今日必读" not in md
    assert "## 📚 分类速览" in md      # 速览仍在


def test_render_markdown_explore_marker():
    items = [_ri("https://a/1", is_explore=True, eligible=False)]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "🧭探索" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_report'`

- [ ] **Step 3: Append `build_report` + `render_markdown` to `src/pipeline/publish.py`**

```python
def build_report(review_result: ReviewResult, date_label: str,
                 config: PublishConfig) -> DailyReport:
    """组装内容模型: 必读 + 分类速览 + 数据概览 + 元信息。"""
    items = review_result.reviewed_items
    return DailyReport(
        date_label=date_label,
        daily_take=review_result.daily_take,
        must_read=select_must_read(items, config),
        categories=group_by_category(items, config),
        overview=build_overview(items, config),
        is_pending=review_result.is_pending,
        item_count=len(items),
        explore_count=sum(1 for it in items if it.is_explore),
    )


def _render_must_read(report: DailyReport, label_of: dict[str, str]) -> list[str]:
    lines = ["## 🏆 今日必读", ""]
    for i, it in enumerate(report.must_read, 1):
        label = label_of.get(it.source_type.value, it.source_type.value)
        lines.append(f"### {i}. [{label}] {it.title}（{it.title_en}）")
        lines.append(f"- **一句话**：{it.summary}")
        lines.append(f"- **对你**：{it.takeaway}")
        lines.append(f"- **锐评**：{it.hot_take}")
        lines.append(f"- **评分**：{it.score} ｜ **来源**：[{it.source}]({it.link})")
        if it.evidence:
            ev = "；".join(f"[{e.claim}]({e.anchor})" for e in it.evidence)
            lines.append(f"- **依据**：{ev}")
        lines.append("")
    return lines


def _render_categories(report: DailyReport) -> list[str]:
    lines = ["## 📚 分类速览", ""]
    for cat in report.categories:
        lines.append(f"**{cat.label}**")
        for it in cat.items:
            mark = " 🧭探索" if it.is_explore else ""
            lines.append(
                f"- `[{it.score}]`{mark} {it.title} — {it.summary} "
                f"｜ [{it.source}]({it.link})")
        lines.append("")
    return lines


def _render_overview(report: DailyReport, label_of: dict[str, str]) -> list[str]:
    lines = ["## 📊 数据概览"]
    dist = "｜".join(f"{label_of.get(st, st)} {n}"
                     for st, n in report.overview.type_distribution.items())
    lines.append(f"- 分类分布：{dist}")
    if report.overview.keywords:
        lines.append("- 高频关键词：" + "、".join(report.overview.keywords))
    lines.append("")
    return lines


def render_markdown(report: DailyReport, config: PublishConfig) -> str:
    """把 DailyReport 渲染成 Markdown(确定性, 无 now)。"""
    label_of = config.type_labels
    lines: list[str] = [f"# AI Daily · {report.date_label}", ""]
    if report.is_pending:
        lines.append(f"> {config.pending_watermark}")
        lines.append("")
    if report.daily_take:
        lines.append(f"> **今日看点**：{report.daily_take}")
        lines.append("")
    if report.must_read:
        lines += _render_must_read(report, label_of)
    if report.categories:
        lines += _render_categories(report)
    lines += _render_overview(report, label_of)
    lines.append("---")
    lines.append("📬 RSS ｜ 🗂 历史归档 ｜ 🏠 主站")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py
git commit -m "feat(publish): build_report + render_markdown (Markdown renderer)"
```

---

## Task 5: publish() 编排 + golden 用例 + snapshot

**Files:**
- Modify: `src/pipeline/publish.py`（追加 `publish()`）
- Test: `tests/golden/test_publish.py`（追加编排 + snapshot 测试）
- Create: `tests/golden/data/publish_report.md`（snapshot，Step 3 内由测试首次生成后固化）

- [ ] **Step 1: Write the failing test**

在 `tests/golden/test_publish.py` 顶部 import 追加 `publish`、`RunContext`、`logging`、`Path`：

```python
import logging
from pathlib import Path
from src.core.types import RunContext
from src.pipeline.publish import publish
```

追加：

```python
def _ctx():
    return RunContext(run_id="g", now=NOW,
                      logger=logging.getLogger("golden-publish"))


def test_publish_empty_input_silent():
    res = publish(_rr([], daily_take=None, is_silent=True), "d", CFG, _ctx())
    assert res.is_silent is True and res.markdown == ""
    assert res.report.item_count == 0


def test_publish_pending_propagates():
    items = [_ri("https://a/1")]
    res = publish(_rr(items, is_pending=True), "d", CFG, _ctx())
    assert res.is_pending is True
    assert CFG.pending_watermark in res.markdown


def test_publish_deterministic():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.PAPER)]
    r1 = publish(_rr(items), "2026-05-30", CFG, _ctx())
    r2 = publish(_rr(items), "2026-05-30", CFG, _ctx())
    assert r1.markdown == r2.markdown
    assert r1.report.model_dump() == r2.report.model_dump()


SNAPSHOT = Path(__file__).parent / "data" / "publish_report.md"


def _snapshot_items():
    return [
        _ri("https://a/1", source_type=SourceType.MODEL, title="GLM-5 发布",
            summary="开源 MoE 旗舰。", takeaway="可自建推理。",
            hot_take="护城河变薄。", score=88, tags=["#MoE", "#开源"],
            eligible=True),
        _ri("https://a/2", source_type=SourceType.PAPER, title="新论文",
            summary="一句话摘要。", score=82, tags=["#MoE", "#推理"],
            eligible=True),
        _ri("https://a/3", source_type=SourceType.COMMUNITY, title="社区热帖",
            summary="探索选题。", score=71, tags=["#Agent"],
            eligible=False, is_explore=True),
    ]


def test_publish_markdown_snapshot():
    res = publish(_rr(_snapshot_items(), daily_take="看点一句话。"),
                  "2026-05-30（周六）", CFG, _ctx())
    if not SNAPSHOT.exists():               # 首次运行固化快照
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(res.markdown, encoding="utf-8")
    assert res.markdown == SNAPSHOT.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_publish.py -k "publish_empty or publish_pending or publish_deter or snapshot" -v`
Expected: FAIL with `ImportError: cannot import name 'publish'`

- [ ] **Step 3: Append `publish()` to `src/pipeline/publish.py`**

```python
def publish(review_result: ReviewResult, date_label: str,
            config: PublishConfig, ctx: RunContext) -> PublishResult:
    """编排: 空→静默; 否则组装内容模型并渲染 Markdown。无网络/LLM/渠道副作用。"""
    items = review_result.reviewed_items
    emit(ctx.logger, "publish_start", run_id=ctx.run_id, input_count=len(items))
    report = build_report(review_result, date_label, config)
    if not items:
        emit(ctx.logger, "publish_done", item_count=0, must_read_count=0,
             is_pending=report.is_pending, silent=True)
        return PublishResult(report=report, markdown="",
                             is_pending=report.is_pending, is_silent=True)
    emit(ctx.logger, "report_built", must_read_count=len(report.must_read),
         category_count=len(report.categories), item_count=report.item_count,
         is_pending=report.is_pending)
    markdown = render_markdown(report, config)
    emit(ctx.logger, "publish_done", item_count=report.item_count,
         must_read_count=len(report.must_read), is_pending=report.is_pending,
         silent=False)
    return PublishResult(report=report, markdown=markdown,
                         is_pending=report.is_pending, is_silent=False)
```

- [ ] **Step 4: Run tests to verify they pass (and generate snapshot)**

Run: `uv run pytest tests/golden/test_publish.py -v`
Expected: PASS（首次运行会写出 `tests/golden/data/publish_report.md`，再断言相等）。再跑一次确认稳定通过。

- [ ] **Step 5: Eyeball the snapshot**

Run: `cat tests/golden/data/publish_report.md`
确认四块齐全、必读 2 条（a/1,a/2）、社区组含 `🧭探索`、概览有分类分布 + 关键词。若格式有误，改 `render_markdown` 后删快照重生成。

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py tests/golden/data/publish_report.md
git commit -m "feat(publish): publish() orchestration + golden cases + markdown snapshot"
```

---

## Task 6: CLI --publish 链 + contract 测试

**Files:**
- Modify: `src/cli.py`
- Test: `tests/contract/test_cli_publish.py`

参考既有 `run_dry_review`（`src/cli.py:136-181`）与 `tests/contract/test_cli_review.py` 的模式。

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_cli_publish.py`（镜像 review 的 CLI 测试：用最小 registry + fake providers + 缺失 decisions 文件，断言输出 dict 形状 + JSON 可序列化，不过度断言 `is_silent`）:

```python
import json
from datetime import datetime, timezone
from src.cli import run_dry_publish
from tests.fakes import FakeEmbeddingProvider, FailingLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_publish_shape():
    out = run_dry_publish(
        registry_path="tests/golden/data/registry_min.yaml", now=NOW,
        embedder=FakeEmbeddingProvider({}), llm=FailingLLMProvider(),
        decisions_path="tests/golden/data/__no_such_decisions__.json")
    # 形状
    for k in ("run_id", "now", "input_count", "must_read_count",
              "item_count", "is_pending", "is_silent", "markdown"):
        assert k in out
    assert isinstance(out["markdown"], str)
    assert isinstance(out["is_pending"], bool)
    # JSON 可序列化
    json.dumps(out, ensure_ascii=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli_publish.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_publish'`

- [ ] **Step 3: Add `run_dry_publish` + `--publish` to `src/cli.py`**

顶部 import 区追加（紧接现有 review 两行之后）：

```python
from src.core.config import load_publish_config
from src.pipeline.publish import publish
```

在 `run_dry_review`（结束于 `:181`）之后、`def main` 之前追加：

```python
def run_dry_publish(registry_path: str, now: datetime | None = None,
                    embedder=None, llm=None, decisions_path=None) -> dict:
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

    rcfg = load_review_config("config/review.yaml")
    decisions = load_review_decisions(decisions_path or rcfg.decisions_path)
    rres = review(ires.interpreted_items, ires.daily_take, decisions, rcfg, ctx)

    pcfg = load_publish_config("config/publish.yaml")
    date_label = now.date().isoformat()
    pres = publish(rres, date_label, pcfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": pres.report.item_count,
        "item_count": pres.report.item_count,
        "must_read_count": len(pres.report.must_read),
        "is_pending": pres.is_pending,
        "is_silent": pres.is_silent,
        "markdown": pres.markdown,
    }
```

在 `main()` 的 argparse 区，`--review` 那行之后追加：

```python
    p.add_argument("--publish", action="store_true",
                   help="chain collect -> ... -> publish, print daily-report Markdown")
```

在 dispatch 区，**放在 `--review` 分支之前**（更下游优先）：

```python
    if args.dry_run and args.publish:
        out = run_dry_publish(registry_path=args.registry)
        print(out["markdown"])
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli_publish.py -v`
Expected: PASS（约 15s，因真实抓取 registry_min 源，与 review 的 CLI 测试同）

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/contract/test_cli_publish.py
git commit -m "feat(publish): CLI --publish chain (collect->...->publish, print Markdown)"
```

---

## Task 7: 全套绿 + ROADMAP 更新

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: 全绿（既有 159 + 本圈新增约 19 个用例 ≈ 178 passed）。任何红灯先修，不得删测试或绕 schema。

- [ ] **Step 2: Update `docs/ROADMAP.md`**

按既有 Circle 5 的写法更新（保持表格/mermaid 风格一致）：
- 顶部日期 → 2026-06-02。
- mermaid 图：`C6:::done`（仿 `C5:::done`）。
- 进度表第 ⑥ 行状态 → `🟩 已合并 (master)`，产物列出 `specs/publish.md` + `pipeline/publish.py` + `--dry-run --publish`。
- 文档地图：`S6: publish.md ✅`、`P6: 2026-06-02-publish-layer.md ✅`。
- §下一步 重新指向 **Circle 7 · feedback**（反馈闭环：回收 review 动作 + outcome 调权重/源信誉）。
- 追加"已完成（Circle 6 · publish）"小结。

> 具体行文照抄 Circle 5 段落的措辞替换即可；先 `grep -n "Circle 5\|C5:::done\|review.md\|⑤" docs/ROADMAP.md` 定位要改的行。

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): mark Circle 6 publish done, point next to Circle 7 feedback"
```

- [ ] **Step 4: Finish the branch**

REQUIRED SUB-SKILL: 用 `superpowers:finishing-a-development-branch` 收尾（验证测试 → 选项 → 合并到 master）。

---

## Self-Review (已执行)

**Spec coverage：** §1 目的→Task 3-5；§2 范围（组装/必读/分组/概览/渲染/水印/静默/留痕）→Task 1-6；§3 接口→Task 5；§4 数据契约→Task 1（含不变式由 Task 4-5 golden 断言）；§5.1 空输入→Task 5；§5.2 必读→Task 3；§5.3 分组→Task 3；§5.4 概览→Task 3；§5.5 组装→Task 4；§5.6 渲染→Task 4；§5.7 水印→Task 4；§6 配置→Task 2；§7 错误回退→Task 4-5 测试覆盖；§8 不变量→golden 断言（1,4 Task4；2,3 Task4；7,8,9 Task4；10,11 Task5；12 Task3）；§9 用例 1-9→Task 3-5；§10 测试→各任务；§11 事件→Task 5；§12 验收→端到端 Task 6 + 全套 Task 7。

**偏差说明（对齐 CLAUDE.md「宁可少写不可编造」）：** spec §5.6 提到必读条目的"解读"行，但上游无独立"解读"字段（只有 `summary`/`takeaway`/`hot_take`）；渲染器只渲染真实存在的字段（一句话=summary、对你=takeaway、锐评=hot_take），不虚构"解读"内容。相对时间"X 小时前"依赖 now()、违反层内无 now 纪律，故省略，只渲染注入的 `date_label`。

**Placeholder scan：** 无 TBD/TODO；每个 code step 均含完整代码。

**Type consistency：** `PublishConfig`/`DailyReport`/`CategorySection`/`Overview`/`PublishResult` 字段在 Task 1 定义，Task 3-6 一致使用；`source_type.value` 取枚举字符串贯穿；`select_must_read`/`group_by_category`/`build_overview`/`build_report`/`render_markdown`/`publish` 命名前后一致。
