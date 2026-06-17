# source_type → genre + publisher 拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单一 `source_type` 字段拆成正交的 `genre`(发的是什么,定 4 维内容价值 + 配额)和 `publisher`(谁发的,定机构影响力),作为源质量重构的地基,行为 output-stable。

**Architecture:** 纯 schema/配置重构,无新算法。打分公式从"按 type 查一张 5 维表"改成 b1:机构影响力 = `publisher_authority[publisher] + priority_bonus`,其余 4 维 = `genre_value[genre]`。配额、去重保留排序、报告分组、HN 富集跳过表全部从 type 改挂 genre。signal/时效/topic/penalty 逻辑不动。

**Tech Stack:** Python 3.12、pydantic v2、dataclass、PyYAML、pytest、uv。

**验收:** 全部测试转绿;重构前后真实 `--dry-run --score` 的 `selected_items` 一致或仅可解释的小位移。

**关键参照:** 设计 spec `docs/superpowers/specs/2026-06-17-source-taxonomy-genre-publisher-split-design.md`(含完整逐源映射表与种子值)。

**枚举值(全程一致,勿改名):**
- `Genre` = `paper, model, announcement, writeup, news`
- `Publisher` = `lab, company, individual, media`

**命名重构对照(全程一致):**
- `RawItem.source_type` / `SourceSpec.type` → `.genre` + `.publisher`
- `ScoringConfig.dimension_scores` → `genre_value` + `publisher_authority`
- `DedupConfig.source_type_rank` → `genre_rank`
- `EnrichConfig.skip_source_types` → `skip_genres`
- `PublishConfig.type_labels` → `genre_labels`
- `QuotaLine.source_type` → `genre`
- `CategorySection.source_type` → `genre`
- `Overview.type_distribution` → `genre_distribution`

---

## File Structure

**Modify (src):** `src/core/types.py`(枚举 + 全部 dataclass/BaseModel 字段)、`src/core/config.py`(4 个 loader)、`src/core/registry.py`(FALLBACK + import)、`src/pipeline/score.py`(b1 公式 + 配额)、`src/pipeline/dedup.py`(genre_rank)、`src/pipeline/enrich.py`(skip_genres)、`src/pipeline/publish.py`(genre 分组/标签/分布)、`src/pipeline/interpret.py`(占位符)、`src/pipeline/tick.py`(标签)、`src/adapters/sources/{rss,hf_papers,hf_models}.py`(产出 genre+publisher)。
**Modify (config):** `config/scoring.yaml`、`config/sources.yaml`、`config/dedup.yaml`、`config/enrich.yaml`、`config/publish.yaml`、`src/prompts/interpret_item.md`。
**Modify (tests):** `tests/contract/test_types.py`、`test_scoring_config.py`、`test_dedup_config.py`、`test_enrich_config.py`、`test_publish_config.py`、`test_sources_yaml.py`、`test_score_types.py`、`test_score_unit.py`、`test_collect_unit.py`、`test_dedup_*.py`、`test_publish_types.py`、`test_registry.py`、各 `tests/golden/*`、`tests/fakes.py`、`tests/golden/data/scoring_golden.yaml`。
**Create:** `docs/adr/0003-genre-publisher-split.md`。

**实施顺序原则:** 先改 `types.py`(根),再改产出端(registry/adapters),再改消费端(config/score/dedup/enrich/publish/interpret/tick),最后改配置文件 + golden + 文档。每个 Task 结束 `uv run pytest -q` 必须绿(或只剩后续 Task 覆盖的已知红),再 commit。

---

## Task 1: types.py — 枚举与核心字段

**Files:**
- Modify: `src/core/types.py`
- Test: `tests/contract/test_types.py`

- [ ] **Step 1: 改测试到新 schema(先红)**

把 `tests/contract/test_types.py` 中所有 `SourceType` / `source_type` 引用替换为新字段。新增断言:

```python
from src.core.types import Genre, Publisher, RawItem, SourceSpec

def test_genre_publisher_enums_have_expected_values():
    assert {g.value for g in Genre} == {"paper", "model", "announcement", "writeup", "news"}
    assert {p.value for p in Publisher} == {"lab", "company", "individual", "media"}

def test_rawitem_requires_genre_and_publisher():
    from datetime import datetime, timezone
    it = RawItem(
        title_en="t", link="l", source="s",
        genre=Genre.paper, publisher=Publisher.company,
        published_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
    )
    assert it.genre is Genre.paper and it.publisher is Publisher.company

def test_sourcespec_requires_genre_and_publisher():
    spec = SourceSpec(name="x", url="http://x", genre="writeup", publisher="individual", adapter="rss")
    assert spec.genre is Genre.writeup and spec.publisher is Publisher.individual
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_types.py -q`
Expected: FAIL(`ImportError: cannot import name 'Genre'`)

- [ ] **Step 3: 改 types.py**

删除 `SourceType` 枚举(types.py:12-19),替换为:

```python
class Genre(str, Enum):
    paper = "paper"
    model = "model"
    announcement = "announcement"
    writeup = "writeup"
    news = "news"


class Publisher(str, Enum):
    lab = "lab"
    company = "company"
    individual = "individual"
    media = "media"
```

`RawItem`(types.py:22-40):把 `source_type: SourceType` 替换为:

```python
    genre: Genre
    publisher: Publisher
```

`SourceSpec`(types.py:51-59):把 `type: SourceType` 替换为:

```python
    genre: Genre
    publisher: Publisher
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_types.py -q`
Expected: PASS(其余文件此时会红,下一 Task 修;本步只确认 test_types 绿)

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_types.py
git commit -m "refactor(types): replace SourceType with Genre + Publisher enums"
```

---

## Task 2: ScoringConfig / DedupConfig / EnrichConfig / PublishConfig / QuotaLine / CategorySection / Overview 字段

**Files:**
- Modify: `src/core/types.py`
- Test: `tests/contract/test_scoring_config.py`, `test_dedup_config.py`, `test_enrich_config.py`, `test_publish_config.py`, `test_score_types.py`, `test_publish_types.py`

- [ ] **Step 1: 改测试(先红)**

在上述测试文件中把旧 key 改新 key。要点:
- `test_scoring_config.py`:断言 `ScoringConfig().genre_value["paper"]["一手性"] == 20`、`ScoringConfig().publisher_authority["lab"] == 18`、`ScoringConfig().quota == {"paper":2,"announcement":2,"writeup":2,"model":1,"news":1}`;删除 `dimension_scores` 断言。
- `test_dedup_config.py`:`DedupConfig().genre_rank == ["paper","model","announcement","writeup","news"]`。
- `test_enrich_config.py`:`EnrichConfig().skip_genres == ["paper","model"]`。
- `test_publish_config.py`:`PublishConfig().genre_labels` 含 `{"paper":"论文","model":"模型","announcement":"官方","writeup":"博客 / 工具","news":"新闻"}`。
- `test_score_types.py`:`QuotaLine(genre="paper", available=1, quota=1, selected=1).genre == "paper"`。
- `test_publish_types.py`:`CategorySection(genre="paper", label="论文", items=[]).genre == "paper"`;`Overview(genre_distribution={"paper":1}).genre_distribution`.

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_scoring_config.py tests/contract/test_dedup_config.py tests/contract/test_enrich_config.py tests/contract/test_publish_config.py tests/contract/test_score_types.py tests/contract/test_publish_types.py -q`
Expected: FAIL(AttributeError/KeyError)

- [ ] **Step 3: 改 types.py 的 dataclass/BaseModel**

`ScoringConfig`(types.py:129-194):删除 `dimension_scores` 字段,替换为两个新字段(种子值取自 spec):

```python
    genre_value: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "paper":        {"一手性": 20, "技术价值": 16, "产业影响": 8,  "扩散潜力": 7},
            "model":        {"一手性": 18, "技术价值": 14, "产业影响": 10, "扩散潜力": 9},
            "announcement": {"一手性": 20, "技术价值": 10, "产业影响": 12, "扩散潜力": 9},
            "writeup":      {"一手性": 12, "技术价值": 12, "产业影响": 8,  "扩散潜力": 9},
            "news":         {"一手性": 8,  "技术价值": 6,  "产业影响": 12, "扩散潜力": 11},
        }
    )
    publisher_authority: dict[str, float] = field(
        default_factory=lambda: {"lab": 18, "company": 14, "individual": 8, "media": 12}
    )
```

同 `ScoringConfig` 的 `quota` 默认值改为:

```python
    quota: dict[str, int] = field(
        default_factory=lambda: {"paper": 2, "announcement": 2, "writeup": 2, "model": 1, "news": 1}
    )
```

`DedupConfig`(types.py:93-101):`source_type_rank` 字段改名 `genre_rank`,默认值:

```python
    genre_rank: list[str] = field(
        default_factory=lambda: ["paper", "model", "announcement", "writeup", "news"]
    )
```

`QuotaLine`(types.py:196-201):`source_type: str` → `genre: str`。

`Overview`(types.py:293-295):`type_distribution` → `genre_distribution`。

`CategorySection`(types.py:298-301):`source_type: str` → `genre: str`。

`PublishConfig`(types.py:315-330):`type_labels` 改名 `genre_labels`,默认值:

```python
    genre_labels: dict[str, str] = field(
        default_factory=lambda: {
            "paper": "论文",
            "model": "模型",
            "announcement": "官方",
            "writeup": "博客 / 工具",
            "news": "新闻",
        }
    )
```

`EnrichConfig`(types.py:358-366):`skip_source_types` 改名 `skip_genres`,默认 `["paper", "model"]`,并更新 docstring 中的字样。

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_scoring_config.py tests/contract/test_dedup_config.py tests/contract/test_enrich_config.py tests/contract/test_publish_config.py tests/contract/test_score_types.py tests/contract/test_publish_types.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_scoring_config.py tests/contract/test_dedup_config.py tests/contract/test_enrich_config.py tests/contract/test_publish_config.py tests/contract/test_score_types.py tests/contract/test_publish_types.py
git commit -m "refactor(types): split config schema into genre_value + publisher_authority"
```

---

## Task 3: registry.py + 三个 source adapter(产出 genre + publisher)

**Files:**
- Modify: `src/core/registry.py`, `src/adapters/sources/rss.py:47`, `src/adapters/sources/hf_papers.py:52`, `src/adapters/sources/hf_models.py:49`
- Test: `tests/contract/test_registry.py`, `test_rss_adapter.py`, `test_hf_papers_adapter.py`, `test_hf_models_adapter.py`, `test_collect_unit.py`

- [ ] **Step 1: 改测试(先红)**

测试里构造 `SourceSpec` 的地方,把 `type=SourceType.X` 改 `genre=..., publisher=...`;断言 adapter 产出的 `RawItem.genre`/`.publisher` 等于 spec 的对应字段。例如 `test_rss_adapter.py`:

```python
spec = SourceSpec(name="s", url="http://x", genre="news", publisher="media", adapter="rss")
# ... fetch ...
assert items[0].genre is Genre.news
assert items[0].publisher is Publisher.media
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_registry.py tests/contract/test_rss_adapter.py tests/contract/test_hf_papers_adapter.py tests/contract/test_hf_models_adapter.py tests/contract/test_collect_unit.py -q`
Expected: FAIL

- [ ] **Step 3: 改实现**

`src/core/registry.py`:import 改 `from src.core.types import RunContext, SourceSpec, Genre, Publisher`;`FALLBACK_SOURCES` 三个 spec 把 `type=SourceType.PAPER`→`genre=Genre.paper, publisher=Publisher.company`(hf-papers)、`type=SourceType.OFFICIAL`→`genre=Genre.announcement, publisher=Publisher.lab`(openai、deepmind)。

三个 adapter(rss.py:47、hf_papers.py:52、hf_models.py:49):把
```python
                    source_type=source.type,
```
改为
```python
                    genre=source.genre,
                    publisher=source.publisher,
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_registry.py tests/contract/test_rss_adapter.py tests/contract/test_hf_papers_adapter.py tests/contract/test_hf_models_adapter.py tests/contract/test_collect_unit.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/registry.py src/adapters/sources/ tests/contract/test_registry.py tests/contract/test_rss_adapter.py tests/contract/test_hf_papers_adapter.py tests/contract/test_hf_models_adapter.py tests/contract/test_collect_unit.py
git commit -m "refactor(collect): adapters + registry emit genre + publisher"
```

---

## Task 4: score.py — b1 打分公式 + 配额按 genre

**Files:**
- Modify: `src/pipeline/score.py`
- Test: `tests/contract/test_score_unit.py`, `tests/golden/test_score.py`, `tests/golden/test_score_popularity.py`

- [ ] **Step 1: 改/加单元测试(先红)**

在 `test_score_unit.py` 加断言验证 b1 拆分(机构影响力只来自 publisher + priority,4 维来自 genre):

```python
def test_b1_authority_from_publisher_matrix_from_genre():
    cfg = ScoringConfig()  # 默认种子值
    item = _news_item(genre=Genre.paper, publisher=Publisher.company, source="hf-papers")
    scored = compute_scores([item], priority_of={"hf-papers": 1}, config=cfg, ctx=ctx)
    bd = scored[0].score_breakdown
    # publisher=company(14) + priority1(+6) = 20
    assert bd["机构影响力"] == 20.0
    # genre=paper 的 4 维
    assert bd["一手性"] == 20.0 and bd["技术价值"] == 16.0
    assert bd["产业影响"] == 8.0 and bd["扩散潜力"] == 7.0
```

(构造 helper `_news_item` 用 `genre=`/`publisher=` 关键字;`fakes.py` 里若有共享构造器,同步改 —— 见 Task 9。)

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_score_unit.py::test_b1_authority_from_publisher_matrix_from_genre -q`
Expected: FAIL(AttributeError `source_type` 或值不符)

- [ ] **Step 3: 改 score.py**

`compute_scores`(score.py:116-133)循环体改:

```python
    for it in items:
        gdims = config.genre_value.get(it.genre.value, {})
        authority = config.publisher_authority.get(it.publisher.value, 0.0)
        prio = priority_of.get(it.source)
        prio_bonus = (
            config.priority_bonus.get(prio, config.priority_bonus_default)
            if prio is not None
            else config.priority_bonus_default
        )
        qw = (quality_of or {}).get(it.source, 1.0)
        breakdown = {
            "机构影响力": round((float(authority) + float(prio_bonus)) * qw, 4),
            "可见指标": round(_visibility(it, config), 4),
            "时效": recency_band(it.published_at, ctx.now, config),
            "惩罚": penalty_of[it.link],
            "读者相关度": _topic_relevance(it, config),
        }
        for k in _MATRIX_DIMS:
            breakdown[k] = float(gdims.get(k, 0))
```

`apply_quota`(score.py:150-163)按 genre 分组:

```python
    by_genre: dict[str, list[ScoredItem]] = defaultdict(list)
    for s in scored:
        by_genre[s.genre.value].append(s)

    selected: list[ScoredItem] = []
    report: dict[str, QuotaLine] = {}
    for g, group in by_genre.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = config.quota.get(g, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[g] = QuotaLine(genre=g, available=len(group), quota=q, selected=len(take))
```

`score()` 里三处 `emit(... source_type=s.source_type.value ...)`(score.py:194、198-205、206-209)把 key 改 `genre=s.genre.value`;`quota_applied` 的 `source_type=stype` 改 `genre=g`(对应循环变量改名)。

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_score_unit.py tests/golden/test_score.py tests/golden/test_score_popularity.py -q`
Expected: PASS(golden 的期望值需在 Task 9 统一更新;若此处 golden 红属预期,记录后继续)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/score.py tests/contract/test_score_unit.py
git commit -m "refactor(score): b1 scoring (publisher authority + genre value) + genre quota"
```

---

## Task 5: dedup.py — genre_rank 保留排序

**Files:**
- Modify: `src/pipeline/dedup.py:40-60`, `src/core/config.py:21-35`
- Test: `tests/contract/test_dedup_types.py`, `tests/golden/test_dedup.py`

- [ ] **Step 1: 改测试(先红)**

`test_dedup_types.py` 中构造 item 的 `source_type=` 改 `genre=`/`publisher=`;若有断言保留更高 rank 的源,改用 genre_rank 语义(paper 胜 news)。

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_dedup_types.py -q`
Expected: FAIL

- [ ] **Step 3: 改实现**

`src/pipeline/dedup.py`:`_rank_index` 签名与调用改用 genre。第 40 行起:

```python
def _rank_index(genre: Genre, order: list[str]) -> int:
    try:
        return order.index(genre.value)
    except ValueError:
        return len(order)
```
第 60 行 `_rank_index(t[0].source_type, config.source_type_rank)` → `_rank_index(t[0].genre, config.genre_rank)`。import 加 `Genre`。

`src/core/config.py` `load_dedup_config`(config.py:29-35):`source_type_rank=data.get("source_type_rank", ...)` 改 `genre_rank=data.get("genre_rank", defaults.genre_rank)`。

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_dedup_types.py tests/golden/test_dedup.py -q`
Expected: PASS(golden 期望值 Task 9 统一对账)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/dedup.py src/core/config.py tests/contract/test_dedup_types.py
git commit -m "refactor(dedup): rank survivors by genre_rank"
```

---

## Task 6: enrich.py — skip_genres

**Files:**
- Modify: `src/pipeline/enrich.py:53-55`, `src/core/config.py:148-153`
- Test: `tests/contract/test_enrich_config.py`(已在 Task 2 改), `tests/golden/test_enrich.py`

- [ ] **Step 1: 改测试(先红)**

`tests/golden/test_enrich.py` 构造 item 的 `source_type=` 改 `genre=`/`publisher=`;断言 paper/model genre 被跳过。

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/golden/test_enrich.py -q`
Expected: FAIL

- [ ] **Step 3: 改实现**

`src/pipeline/enrich.py:53-55`:
```python
    skip = set(config.skip_genres or [])
    targets = [
        it for it in items if it.genre.value not in skip and not _has_popularity(it)
    ]
```
`src/core/config.py` `load_enrich_config`(config.py:152):`skip_source_types=...` 改 `skip_genres=data.get("skip_genres", d.skip_genres)`。

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/golden/test_enrich.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/enrich.py src/core/config.py tests/golden/test_enrich.py
git commit -m "refactor(enrich): skip HN lookup by genre"
```

---

## Task 7: publish.py + interpret.py + tick.py — 按 genre 分组/标签/占位符

**Files:**
- Modify: `src/pipeline/publish.py`(26-52、94、125、172)、`src/core/config.py:132-137`、`src/pipeline/interpret.py:24`、`src/pipeline/tick.py:21-38`、`src/prompts/interpret_item.md:17`
- Test: `tests/golden/test_publish.py`, `tests/golden/test_interpret.py`, `tests/golden/test_tick.py`

- [ ] **Step 1: 改测试(先红)**

`test_publish.py`:期望 `CategorySection.genre`、`Overview.genre_distribution`、节序按 `genre_labels` 键序。`test_interpret.py`/`test_tick.py`:item 用 `genre=`/`publisher=`;tick 的 source_label 改由 genre 推。

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/golden/test_publish.py tests/golden/test_interpret.py tests/golden/test_tick.py -q`
Expected: FAIL

- [ ] **Step 3: 改实现**

`src/pipeline/publish.py`:全部 `config.type_labels` → `config.genre_labels`;`it.source_type.value` → `it.genre.value`;`CategorySection(source_type=st, ...)` → `CategorySection(genre=st, ...)`;`Overview(type_distribution=dist, ...)` → `Overview(genre_distribution=dist, ...)`。(对应行:28、32、44、51、52、71、94、125、172。)

`src/core/config.py` `load_publish_config`(config.py:136):`type_labels=data.get("type_labels", ...)` → `genre_labels=data.get("genre_labels", d.genre_labels)`。

`src/pipeline/interpret.py:24`:`"{{source_type}}": item.source_type.value,` → `"{{genre}}": item.genre.value,`。
`src/prompts/interpret_item.md:17`:`类型 {{source_type}}` → `类型 {{genre}}`。

`src/pipeline/tick.py:21-38`:`_source_type_label`/`source_type_value` 形参改 genre 语义,调用处 `item.source_type.value` → `item.genre.value`;若内部读 `type_labels` 改 `genre_labels`。

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/golden/test_publish.py tests/golden/test_interpret.py tests/golden/test_tick.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/publish.py src/pipeline/interpret.py src/pipeline/tick.py src/core/config.py src/prompts/interpret_item.md tests/golden/test_publish.py tests/golden/test_interpret.py tests/golden/test_tick.py
git commit -m "refactor(publish/interpret/tick): group + label by genre"
```

---

## Task 8: config.py — load_scoring_config 读双表

**Files:**
- Modify: `src/core/config.py:49-67`
- Test: `tests/contract/test_scoring_config.py`(扩展)

- [ ] **Step 1: 加测试(先红)**

写一个临时 yaml 验证 loader 读 `genre_value`/`publisher_authority`:

```python
def test_load_scoring_config_reads_genre_and_publisher(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "genre_value: {paper: {一手性: 20}}\n"
        "publisher_authority: {lab: 18}\n"
        "quota: {paper: 2}\n", encoding="utf-8")
    cfg = load_scoring_config(str(p))
    assert cfg.genre_value["paper"]["一手性"] == 20
    assert cfg.publisher_authority["lab"] == 18
    assert cfg.quota == {"paper": 2}
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_scoring_config.py::test_load_scoring_config_reads_genre_and_publisher -q`
Expected: FAIL

- [ ] **Step 3: 改 config.py**

`load_scoring_config`(config.py:49-67)把 `dimension_scores=data.get("dimension_scores", d.dimension_scores),` 替换为:

```python
        genre_value=data.get("genre_value", d.genre_value),
        publisher_authority=data.get("publisher_authority", d.publisher_authority),
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/contract/test_scoring_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/config.py tests/contract/test_scoring_config.py
git commit -m "refactor(config): load genre_value + publisher_authority from scoring.yaml"
```

---

## Task 9: 共享 test 构造器 + 全量测试转绿

**Files:**
- Modify: `tests/fakes.py`、`tests/golden/data/scoring_golden.yaml`、剩余所有引用旧字段的测试
- Test: 整个 suite

- [ ] **Step 1: 改 tests/fakes.py 的共享构造器**

把 `tests/fakes.py` 中构造 `RawItem`/`NewsItem`/`SourceSpec` 的工厂改用 `genre=`/`publisher=` 关键字(默认值给 `Genre.writeup`/`Publisher.individual`,除非调用方覆盖)。

- [ ] **Step 2: 跑全量,收集仍红的**

Run: `uv run pytest -q 2>&1 | tail -40`
Expected: 一批 golden/contract 红,记录文件清单。

- [ ] **Step 3: 逐文件修红**

对每个红测试:把 `source_type`/`SourceType`/`type=` 改新字段;golden 快照里 `source_type` 键改 `genre`、`type_distribution` 改 `genre_distribution`;`tests/golden/data/scoring_golden.yaml` 内 `dimension_scores` 改 `genre_value` + `publisher_authority`、`quota` 改 genre key。期望分值按 b1 重算(机构影响力 = publisher_authority + priority_bonus)。

- [ ] **Step 4: 跑全量确认绿**

Run: `uv run pytest -q`
Expected: PASS(全绿)

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: migrate all suites to genre + publisher schema"
```

---

## Task 10: 配置文件 — scoring.yaml / sources.yaml / dedup.yaml / enrich.yaml / publish.yaml

**Files:**
- Modify: `config/scoring.yaml`、`config/sources.yaml`、`config/dedup.yaml`、`config/enrich.yaml`、`config/publish.yaml`
- Test: `tests/contract/test_sources_yaml.py`

- [ ] **Step 1: 改 test_sources_yaml.py(先红)**

该测试用 `SourceSpec(**r)` 校验每行,现会因缺 `genre`/`publisher` 失败 —— 这正是我们要的验证。保留其结构断言不变。

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/contract/test_sources_yaml.py -q`
Expected: FAIL(`SourceSpec` 校验:缺 genre/publisher,多 type)

- [ ] **Step 3: 改配置文件**

`config/scoring.yaml`:把 `dimension_scores:` 整块替换为 `genre_value:`(5 个 genre 的 4 维,见 spec 种子值)+ `publisher_authority: {lab: 18, company: 14, individual: 8, media: 12}`;`quota:` 改 `{paper: 2, announcement: 2, writeup: 2, model: 1, news: 1}`。`popularity_weights`/`recency`/`topic_boost`/`penalty` 不动。

`config/sources.yaml`:每行 `type: X` 替换为 `genre: Y, publisher: Z`,按 spec **逐源映射表**(Section B)赋值。注意两处有意重分类:`pytorch` → `genre: announcement, publisher: company`;`bair`/`stanford-ai` → `genre: writeup, publisher: lab`。`papers-cool-*`/`arxiv-cs-*` → `genre: paper, publisher: company`(仍 manual)。全部 `hn-*` → `genre: writeup, publisher: individual`。

`config/dedup.yaml:5`:`source_type_rank: [official, paper, model, tool, news, community, blog]` → `genre_rank: [paper, model, announcement, writeup, news]`。

`config/enrich.yaml`:`skip_source_types: [paper, model]` → `skip_genres: [paper, model]`(注释同步)。

`config/publish.yaml`:`type_labels:` → `genre_labels:`,键改 `{paper: 论文, model: 模型, announcement: 官方, writeup: 博客 / 工具, news: 新闻}`。

- [ ] **Step 4: 跑测试确认绿 + 全量**

Run: `uv run pytest -q`
Expected: PASS(全绿)

- [ ] **Step 5: Commit**

```bash
git add config/ tests/contract/test_sources_yaml.py
git commit -m "config: migrate sources/scoring/dedup/enrich/publish to genre + publisher"
```

---

## Task 11: 真实重跑验证 output-stable

**Files:** 无代码改动(纯验证;如发现选择明显劣化则回到对应 Task 调种子值)

- [ ] **Step 1: 跑真实 score(需 key)**

Run:
```bash
eval "$(grep -E '^export (MODELSCOPE_API_KEY|OPENAI_API_KEY|OPENAI_BASE_URL)=' ~/.zshrc)"
uv run python -m src.cli --dry-run --score > /tmp/score_after.json 2>/tmp/score_after.err
```
Expected: exit 0;`score_done` 的 `selected_count` = 8(firehose 若仍 working 则 paper available 高,不影响验证;若已合入下线则 51)。

- [ ] **Step 2: 对账**

用 `uv run python` 读 `/tmp/score_after.json`,打印每条 `selected_items` 的 `genre/publisher/source/score/score_breakdown`;与本 session 记录的重构前 8 条(hf-papers×2、nvidia、hf-models、langchain、techcrunch、latent-space、simonwillison)对比。预期同一批或仅可解释位移(nvidia 因 company<lab 略降)。

- [ ] **Step 3: 判定**

若选择集稳定 → 通过。若某条好内容被挤掉且不可解释 → 记录,回 Task 2/10 微调 `genre_value`/`publisher_authority` 种子值(属 #1 允许范围内的对齐,不做大调参 —— 大调参是 #4)。

- [ ] **Step 4: 无 commit**(纯验证步;如调了种子值则并入 Task 10 的后续 commit)

---

## Task 12: 文档对齐 + ADR

**Files:**
- Modify: `docs/specs/`(打分 + 源契约相关 spec)
- Create: `docs/adr/0003-genre-publisher-split.md`

- [ ] **Step 1: 写 ADR**

`docs/adr/0003-genre-publisher-split.md`:Context(source_type 混轴诊断)、Decision(拆 genre+publisher,b1 单标量权威,配额挂 genre)、Consequences(release/changelog/aggregator 待 #2/#3;company<lab 行为变化)、Alternatives(二维查表 a / 多维 b2,为何否决)。

- [ ] **Step 2: 更新 docs/specs**

找到描述 `source_type`/`dimension_scores`/配额的 spec 段落(grep `source_type` docs/specs/),改写为 genre+publisher 双轴 + b1 公式(按 [[align-spec-and-code-no-exceptions]],对齐措辞而非加例外脚注)。

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs(adr): 0003 genre+publisher split; align specs"
```

---

## Self-Review 结论(写计划时已核)

- **Spec 覆盖:** schema(T1-2)、产出端(T3)、打分 b1(T4)、配额按 genre(T4)、dedup(T5)、enrich(T6)、publish/interpret/tick(T7)、config loader(T8、T2 内 dedup/enrich/publish loader 在 T5/6/7)、配置文件(T10)、output-stable 验证(T11)、文档+ADR(T12)。spec 提到的"下游 rekey"三项(dedup/报告/QuotaLine)分别在 T5/T7/T2。
- **超出 spec 的发现已纳入:** `EnrichConfig.skip_source_types`、`interpret_item.md` 占位符、`tick.py` 标签、`Overview.type_distribution` —— spec 未逐一列出,计划已覆盖(T2/T6/T7)。
- **类型一致性:** 全程 `genre`/`publisher`/`genre_value`/`publisher_authority`/`genre_rank`/`skip_genres`/`genre_labels`/`genre_distribution` 命名统一(见顶部对照表)。
- **无占位符:** 每个 code step 给出实际代码或精确 edit pattern + spec 表引用(sources.yaml 80 行走映射表,不逐行复制属合理判断)。
