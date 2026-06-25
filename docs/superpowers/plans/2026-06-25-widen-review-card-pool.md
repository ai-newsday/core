# 放宽发卡池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"可审发卡候选池"与"最终刊物组成"解耦——发卡阶段放宽到按 score 取 top-N，per-genre 配额与总量上限移到人工 keep 之后的 publish 阶段。

**Architecture:** 三处改动，三个独立可测任务。Task 1 让 `score()` 输出 `selected_items = all_scored[:card_pool_limit]`（不再 quota→11），发卡池变宽。Task 2 让 publish 在人 keep 后施加 per-genre 配额 + total_limit，并把 keep 质量地板从 60 降到 40。Task 3 清理：把 `quota`/`total_limit` 从 `ScoringConfig` 迁出（配置边界方案 A）。

**Tech Stack:** Python 3.12, dataclass config, pytest（contract/golden 两层），ruff。

## Global Constraints

- 阈值/配额/权重全读 `config/`，不写死代码（CLAUDE.md）。
- 纯函数 + IO 隔离：打分/配额是纯函数，无网络/LLM 副作用。
- TDD：先写失败测试再实现。CI 跑 `ruff check` + `ruff format --check`（pytest 不含），**每次 commit 前本地跑 ruff**。
- `apply_quota` 是按 `(score desc, published_at asc, link asc)` 排序后每 genre 取 top-N、再总量截的纯函数。
- 设计文档: [docs/superpowers/specs/2026-06-25-widen-review-card-pool-design.md](../specs/2026-06-25-widen-review-card-pool-design.md)
- **设计微调（已确认更省 diff）**: 设计文档说"`apply_quota` 移到 publish.py"。实现改为**保留在 `src/pipeline/score.py` 作为纯排序辅助函数，由 publish 导入复用**——避免搬代码+搬测试，只加一行 import（`score` 不 import `publish`，无环）。行为与设计完全一致。

---

### Task 1: 发卡池放宽（闸 1）

把 `score()` 的 `selected_items` 重定义为"按 score 取的发卡候选池"`all_scored[:card_pool_limit]`，不再 per-genre 配额截。`apply_quota` 改成接受裸 `quota`/`total_limit` 参数的纯辅助函数（供 Task 2 publish 复用），不再被 `score()` 调用。

**Files:**
- Modify: `src/core/types.py`（`ScoringConfig` 加 `card_pool_limit`）
- Modify: `src/core/config.py`（`load_scoring_config` 读 `card_pool_limit`）
- Modify: `config/scoring.yaml`（加 `card_pool_limit: 25`）
- Modify: `src/pipeline/score.py`（`score()` 用 top-N；`apply_quota` 改签名）
- Test: `tests/contract/test_scoring_config.py`、`tests/contract/test_score_unit.py`、`tests/golden/test_score.py`

**Interfaces:**
- Produces:
  - `ScoringConfig.card_pool_limit: int = 25`
  - `apply_quota(scored: list, quota: dict[str, int], total_limit: int) -> tuple[list, dict[str, QuotaLine]]` — 纯函数，Task 2 的 publish 复用。
  - `score(...)` 返回的 `ScoreResult.selected_items` 语义＝发卡候选池（top-N by score），`quota_report` 置 `{}`。

- [ ] **Step 1: 写失败测试 — config 读 card_pool_limit**

在 `tests/contract/test_scoring_config.py` 末尾追加：

```python
def test_card_pool_limit_default_and_override(tmp_path):
    from src.core.config import load_scoring_config
    from src.core.types import ScoringConfig

    assert ScoringConfig().card_pool_limit == 25
    p = tmp_path / "s.yaml"
    p.write_text("card_pool_limit: 40\n", encoding="utf-8")
    assert load_scoring_config(str(p)).card_pool_limit == 40
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/contract/test_scoring_config.py::test_card_pool_limit_default_and_override -v`
Expected: FAIL — `AttributeError: 'ScoringConfig' object has no attribute 'card_pool_limit'`

- [ ] **Step 3: 实现 — ScoringConfig 加字段 + loader 读**

`src/core/types.py`，在 `ScoringConfig` 的 `total_limit: int = 8` 下一行加：

```python
    card_pool_limit: int = 25  # 发卡候选池: 按 score 取 top-N 进 interpret(成本上界)
```

`src/core/config.py` 的 `load_scoring_config` 返回 `ScoringConfig(...)` 中，在 `total_limit=...` 那行后加：

```python
        card_pool_limit=data.get("card_pool_limit", d.card_pool_limit),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/contract/test_scoring_config.py::test_card_pool_limit_default_and_override -v`
Expected: PASS

- [ ] **Step 5: 写失败测试 — apply_quota 新签名（裸参数）**

`tests/contract/test_score_unit.py` 里现有 4 个 `test_apply_quota_*` 用 `cfg = ScoringConfig(); cfg.quota = {...}` 再 `apply_quota(scored, cfg)`。改成传裸参数。逐个替换为：

```python
def test_apply_quota_trims_to_quota_keeping_top_scored():
    ctx = _ctx()
    fresh = NOW
    mid = NOW - timedelta(hours=36)
    stale = NOW - timedelta(hours=100)
    scored = _scored_list(
        ctx,
        ("p-fresh", "https://p/1", "p1", Genre.paper, fresh),
        ("p-mid", "https://p/2", "p2", Genre.paper, mid),
        ("p-stale", "https://p/3", "p3", Genre.paper, stale),
    )
    selected, report = apply_quota(scored, {"paper": 2}, total_limit=99)
    assert report["paper"].available == 3
    assert report["paper"].quota == 2
    assert report["paper"].selected == 2
    links = {s.link for s in selected}
    assert links == {"https://p/1", "https://p/2"}


def test_apply_quota_keeps_all_when_under_quota():
    ctx = _ctx()
    scored = _scored_list(ctx, ("t", "https://t/1", "t1", Genre.writeup, NOW))
    selected, report = apply_quota(scored, {"writeup": 2}, total_limit=99)
    assert report["writeup"].available == 1
    assert report["writeup"].selected == 1
    assert len(selected) == 1


def test_apply_quota_zero_for_unlisted_type():
    ctx = _ctx()
    scored = _scored_list(ctx, ("n", "https://n/1", "n1", Genre.news, NOW))
    selected, report = apply_quota(scored, {"paper": 2}, total_limit=99)
    assert report["news"].quota == 0 and report["news"].selected == 0
    assert selected == []


def test_apply_quota_respects_total_limit():
    ctx = _ctx()
    scored = _scored_list(
        ctx,
        ("a", "https://a/1", "s1", Genre.paper, NOW),
        ("b", "https://b/2", "s2", Genre.model, NOW),
        ("c", "https://c/3", "s3", Genre.writeup, NOW),
    )
    selected, _ = apply_quota(scored, {"paper": 1, "model": 1, "writeup": 1}, total_limit=2)
    assert len(selected) == 2
    assert selected[0].score >= selected[1].score


def test_apply_quota_does_not_dedupe_same_source_within_genre():
    ctx = _ctx()
    scored = _scored_list(
        ctx,
        ("post-a", "https://langchain.com/a", "langchain", Genre.writeup, NOW),
        ("post-b", "https://langchain.com/b", "langchain", Genre.writeup, NOW),
    )
    selected, report = apply_quota(scored, {"writeup": 2}, total_limit=99)
    assert report["writeup"].selected == 2
    assert {s.source for s in selected} == {"langchain"}
```

- [ ] **Step 6: 跑测试确认失败**

Run: `pytest tests/contract/test_score_unit.py -k apply_quota -v`
Expected: FAIL — `apply_quota()` 收到多余位置参数 / 缺 `total_limit`（旧签名是 `(scored, config)`）。

- [ ] **Step 7: 实现 — apply_quota 改签名为裸参数**

`src/pipeline/score.py`，替换 `apply_quota` 整个函数：

```python
def apply_quota(
    scored: list[ScoredItem],
    quota: dict[str, int],
    total_limit: int,
) -> tuple[list[ScoredItem], dict[str, QuotaLine]]:
    """Strict per-type quota selection (spec §5.4). No cross-type fill.
    纯函数: 按 genre 分组, 每组按 (score desc, published_at, link) 取 top-N(quota[genre]),
    再总量截到 total_limit。score 阶段与 publish 阶段共用。"""
    by_genre: dict[str, list[ScoredItem]] = defaultdict(list)
    for s in scored:
        by_genre[s.genre.value].append(s)

    selected: list[ScoredItem] = []
    report: dict[str, QuotaLine] = {}
    for g, group in by_genre.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = quota.get(g, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[g] = QuotaLine(genre=g, available=len(group), quota=q, selected=len(take))

    selected.sort(key=lambda s: (-s.score, s.published_at, s.link))
    if len(selected) > total_limit:
        selected = selected[:total_limit]
    return selected, report
```

- [ ] **Step 8: 跑测试确认通过**

Run: `pytest tests/contract/test_score_unit.py -k apply_quota -v`
Expected: PASS（5 个）

- [ ] **Step 9: 写失败测试 — score() 输出发卡池 top-N**

`tests/golden/test_score.py` 里删除现有 `test_golden_quota_trims_top_scored` 与 `test_golden_under_quota_keeps_all` 两个用例（它们断言 score 阶段配额，已不存在），替换为：

```python
# score() selected_items = 发卡候选池(按 score top-N), 不再 per-genre 配额
def test_score_selected_is_card_pool_top_n():
    cfg = _cfg()
    cfg.card_pool_limit = 2
    items = [
        _ni("p1", "https://p/1", "s1", Genre.paper, NOW),
        _ni("p2", "https://p/2", "s2", Genre.paper, NOW - timedelta(hours=36)),
        _ni("p3", "https://p/3", "s3", Genre.paper, NOW - timedelta(hours=100)),
    ]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 2
    assert len(res.all_scored) == 3
    # all_scored 已按 score 降序; selected = 前 2
    assert res.selected_items == res.all_scored[:2]
    assert res.quota_report == {}


def test_score_card_pool_keeps_all_when_under_limit():
    cfg = _cfg()
    cfg.card_pool_limit = 25
    items = [_ni("t", "https://t/1", "t1", Genre.writeup, NOW)]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 1
    assert res.selected_items == res.all_scored
```

确认文件顶部已 `from datetime import ... timedelta`（已有）。

- [ ] **Step 10: 跑测试确认失败**

Run: `pytest tests/golden/test_score.py::test_score_selected_is_card_pool_top_n -v`
Expected: FAIL — `score()` 内部仍调旧 `apply_quota(scored, config)`，签名已变 → TypeError；且 `selected_items` 仍是配额结果。

- [ ] **Step 11: 实现 — score() 改用 card_pool_limit**

`src/pipeline/score.py` 的 `score()` 函数，替换从 `selected, report = apply_quota(scored, config)` 到 `for s in selected: emit(... "item_selected" ...)` 之间的块为：

```python
    selected = scored[: config.card_pool_limit]
    for s in selected:
        emit(ctx.logger, "item_selected", link=s.link, genre=s.genre.value, score=s.score)

    result = ScoreResult(
        selected_items=selected,
        all_scored=scored,
        quota_report={},
        input_count=len(items),
        selected_count=len(selected),
        is_silent=False,
    )
```

（删掉原 `apply_quota` 调用与 `quota_applied` 的 emit 循环；`apply_quota` 函数本身保留供 publish 用。）

- [ ] **Step 12: 跑测试确认通过 + 全 score 套件 + ruff**

Run: `pytest tests/golden/test_score.py tests/contract/test_score_unit.py tests/golden/test_score_popularity.py -v`
Expected: PASS
Run: `ruff check src tests && ruff format --check src tests`
Expected: 无报错（若 format 不过，跑 `ruff format src tests` 再 commit）

- [ ] **Step 13: Commit**

```bash
git add src/core/types.py src/core/config.py config/scoring.yaml src/pipeline/score.py \
        tests/contract/test_scoring_config.py tests/contract/test_score_unit.py tests/golden/test_score.py
git commit -m "feat(score): widen card pool to top-N, decouple from per-genre quota

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: publish 重施配额 + 降 keep 地板（闸 2）

人工 keep 后，对 kept 集合施加 per-genre 配额 + total_limit（复用 Task 1 的 `apply_quota`），并把质量地板 `min_display_score` 从 60 降到 40。

**Files:**
- Modify: `src/core/types.py`（`PublishConfig` 加 `quota` / `total_limit`）
- Modify: `src/core/config.py`（`load_publish_config` 读两者）
- Modify: `config/publish.yaml`（加 `quota` / `total_limit`，`min_display_score` 60→40）
- Modify: `src/pipeline/publish.py`（`build_report` 施加配额）
- Test: `tests/contract/test_publish_config.py`、`tests/golden/test_publish.py`

**Interfaces:**
- Consumes: `apply_quota(items, quota, total_limit)`（Task 1，from `src.pipeline.score`）
- Produces:
  - `PublishConfig.quota: dict[str, int]`、`PublishConfig.total_limit: int`、`PublishConfig.min_display_score: int = 40`
  - `build_report` 在地板过滤后追加 `apply_quota` 截断。

- [ ] **Step 1: 写失败测试 — PublishConfig 读 quota/total_limit + 地板默认 40**

`tests/contract/test_publish_config.py` 追加：

```python
def test_publish_quota_total_limit_and_floor(tmp_path):
    from src.core.config import load_publish_config
    from src.core.types import PublishConfig

    d = PublishConfig()
    assert d.min_display_score == 40
    assert d.total_limit == 11
    assert d.quota["paper"] == 3

    p = tmp_path / "p.yaml"
    p.write_text(
        "min_display_score: 40\ntotal_limit: 5\nquota: {paper: 1, model: 1}\n",
        encoding="utf-8",
    )
    c = load_publish_config(str(p))
    assert c.total_limit == 5
    assert c.quota == {"paper": 1, "model": 1}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/contract/test_publish_config.py::test_publish_quota_total_limit_and_floor -v`
Expected: FAIL — `min_display_score` 默认 60、无 `quota`/`total_limit` 属性。

- [ ] **Step 3: 实现 — PublishConfig 加字段 + loader**

`src/core/types.py` 的 `PublishConfig`：把 `min_display_score: int = 60` 改为 `= 40`，并在其后加：

```python
    quota: dict[str, int] = field(
        default_factory=lambda: {
            "paper": 3,
            "model": 3,
            "announcement": 3,
            "writeup": 2,
            "news": 1,
        }
    )
    total_limit: int = 11
```

`src/core/config.py` 的 `load_publish_config` 返回里加：

```python
        quota=data.get("quota", d.quota),
        total_limit=data.get("total_limit", d.total_limit),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/contract/test_publish_config.py::test_publish_quota_total_limit_and_floor -v`
Expected: PASS

- [ ] **Step 5: 写失败测试 — build_report 施加配额 + 新地板**

`tests/golden/test_publish.py`，现有 `test_build_report_drops_below_min_display_score`（断言默认地板 60，59 被砍）已因默认改 40 而过期。替换为：

```python
def test_build_report_floor_is_40():
    """human-keep 条目质量底降到 40: <40 砍, >=40 留。"""
    items = [
        _ri("https://a/1", score=80),
        _ri("https://a/2", score=39),  # 地板下 — 砍
        _ri("https://a/3", score=40),  # 触地板 — 留
    ]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 2


def test_build_report_applies_per_genre_quota():
    """kept 集合某 genre 超配额 → 只留该类 top-N(按 score)。"""
    cfg = PublishConfig()
    cfg.quota = {"paper": 1}
    cfg.total_limit = 99
    items = [
        _ri("https://a/1", score=80, genre=Genre.paper, title="高分论文"),
        _ri("https://a/2", score=70, genre=Genre.paper, title="低分论文"),
    ]
    rep = build_report(_rr(items), "2026-05-30", cfg)
    assert rep.item_count == 1
    titles = {it.title for c in rep.categories for it in c.items}
    assert titles == {"高分论文"}


def test_build_report_respects_total_limit():
    cfg = PublishConfig()
    cfg.quota = {"paper": 5, "model": 5}
    cfg.total_limit = 2
    items = [
        _ri("https://a/1", score=90, genre=Genre.paper),
        _ri("https://a/2", score=80, genre=Genre.model),
        _ri("https://a/3", score=70, genre=Genre.paper),
    ]
    rep = build_report(_rr(items), "2026-05-30", cfg)
    assert rep.item_count == 2
```

确认 `test_publish.py` 顶部已 import `PublishConfig`、`Genre`（已有）。`_rr(...)` 为现有构造 `ReviewResult` 的 helper——若文件里 helper 名不同，用文件内已有的同义 helper（搜 `ReviewResult(` 的构造处）。

- [ ] **Step 6: 跑测试确认失败**

Run: `pytest tests/golden/test_publish.py -k "floor_is_40 or per_genre_quota or total_limit" -v`
Expected: FAIL — `build_report` 未施配额（quota/total_limit 用例返回全部条目）。

- [ ] **Step 7: 实现 — build_report 追加 apply_quota**

`src/pipeline/publish.py` 顶部 import 区加：

```python
from src.pipeline.score import apply_quota
```

`build_report` 里，地板过滤之后、构造 `DailyReport` 之前，插入配额截断：

```python
    items = [
        it
        for it in review_result.reviewed_items
        if it.score >= config.min_display_score and it.relevant
    ]
    items, _ = apply_quota(items, config.quota, config.total_limit)
```

（`apply_quota` 对 `ReviewedItem` 透明可用：它只读 `.genre`/`.score`/`.published_at`/`.link`，`ReviewedItem` 均有。返回的 report 丢弃。）

- [ ] **Step 8: 跑测试确认通过 + 全 publish 套件 + ruff**

Run: `pytest tests/golden/test_publish.py tests/contract/test_publish_config.py tests/contract/test_publish_types.py tests/contract/test_cli_publish.py -v`
Expected: PASS
Run: `ruff check src tests && ruff format --check src tests`
Expected: 通过

- [ ] **Step 9: 实现 — 更新 config/publish.yaml**

`config/publish.yaml`：`min_display_score: 60` 改 `40`，文件末尾追加：

```yaml
quota: {paper: 3, model: 3, announcement: 3, writeup: 2, news: 1}  # per-genre 上限(人 keep 后施加)
total_limit: 11                                                    # 刊物总条目硬上限
```

- [ ] **Step 10: Commit**

```bash
git add src/core/types.py src/core/config.py config/publish.yaml src/pipeline/publish.py \
        tests/contract/test_publish_config.py tests/golden/test_publish.py
git commit -m "feat(publish): apply per-genre quota + total_limit after human keep, lower floor to 40

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 配置边界清理 — quota/total_limit 迁出 ScoringConfig

方案 A：`quota`/`total_limit` 现只在 publish 阶段起作用，从 `ScoringConfig` 与 `scoring.yaml` 移除，避免两处同名配置混淆。

**Files:**
- Modify: `src/core/types.py`（`ScoringConfig` 删 `quota` / `total_limit`）
- Modify: `src/core/config.py`（`load_scoring_config` 停读两者）
- Modify: `config/scoring.yaml`（删 `quota` / `total_limit`）
- Modify: `tests/golden/data/scoring_golden.yaml`（若含 quota/total_limit 则删）
- Test: `tests/contract/test_scoring_config.py`

**Interfaces:**
- Produces: `ScoringConfig` 不再有 `quota` / `total_limit` 字段（线上 quota 唯一来源＝`PublishConfig`）。

- [ ] **Step 1: 改测试 — 删 ScoringConfig 的 quota/total_limit 断言**

`tests/contract/test_scoring_config.py`：删除引用 `c.total_limit` / `c.quota` 的断言与用例。具体：
- 删 `assert c.total_limit == 8`、`assert c.quota["paper"] == 2`（约 8-9 行）
- 删含 `"quota: {paper: 1, model: 1}\n" "total_limit: 5\n"` 的 yaml override 用例里对 `c.quota`/`c.total_limit` 的两条断言（约 33-34 行）；该用例其余断言若只剩这两条则整例删。
- 删第 43 行 yaml 字符串里的 `quota: {paper: 2}` 与第 49 行 `assert c.quota == {"paper": 2}`。
- 删第 54-57 的 quota/total_limit 不变量用例（已移至 publish 语义）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/contract/test_scoring_config.py -v`
Expected: 若先跑实现前——此步是改测试，先确认改后的测试在"字段仍存在"时仍 PASS（无新断言失败）。直接进 Step 3 删字段，再用 Step 4 验证全绿。

- [ ] **Step 3: 实现 — 删字段 + loader 停读 + yaml 清理**

`src/core/types.py` 的 `ScoringConfig`：删除 `quota: dict[str, int] = field(...)` 整块与 `total_limit: int = 8` 一行。

`src/core/config.py` 的 `load_scoring_config`：删除返回里的 `quota=data.get("quota", d.quota),` 与 `total_limit=data.get("total_limit", d.total_limit),` 两行。

`config/scoring.yaml`：删除 `quota: {...}` 行与 `total_limit: 11` 行（连同上方解释注释中已失效部分，保留 `card_pool_limit`）。

`tests/golden/data/scoring_golden.yaml`：若含 `quota`/`total_limit` 键则删（先 `grep -n "quota\|total_limit" tests/golden/data/scoring_golden.yaml`）。

- [ ] **Step 4: 跑全套件确认通过**

Run: `pytest -q`
Expected: 全绿。重点看 `tests/golden/test_score.py`、`tests/contract/test_score_unit.py`、`tests/golden/test_tick.py` 无 `AttributeError: quota/total_limit`。若任何 golden 仍引用 `cfg.quota`/`cfg.total_limit`（ScoringConfig）则就地删除该引用。

- [ ] **Step 5: ruff**

Run: `ruff check src tests && ruff format --check src tests`
Expected: 通过（不过则 `ruff format src tests`）

- [ ] **Step 6: Commit**

```bash
git add src/core/types.py src/core/config.py config/scoring.yaml \
        tests/contract/test_scoring_config.py tests/golden/data/scoring_golden.yaml
git commit -m "refactor(config): move quota/total_limit from scoring to publish (boundary A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 目标1（发卡池放宽）→ Task 1 ✓
- 目标2（keep 低分进刊物）→ Task 2 地板降 40 ✓
- 目标3（最终有界+分类均衡）→ Task 2 publish quota+total_limit ✓
- 目标4（interpret 成本硬上界）→ Task 1 card_pool_limit ✓
- 目标5（纯函数/config/dry-run 不受损）→ apply_quota 仍纯函数；`selected_items` 仍是 dry-run `03_scored.jsonl` 源（现为发卡池）✓
- 配置边界 A → Task 3 ✓
- 已知天花板（genre 饿死）→ 设计文档记录，本计划不实现（YAGNI）✓

**Placeholder scan:** 无 TBD/TODO；每个 code step 含完整代码。Step 5(Task2) 的 `_rr` helper 名做了回退说明（搜 `ReviewResult(` 定位）——非 placeholder，是适配现有测试文件的指引。

**Type consistency:** `apply_quota(items, quota, total_limit)` 三处一致（定义 Task1-Step7、score 不再调用、publish Task2-Step7 调用）。`PublishConfig.quota`/`total_limit`/`min_display_score=40` 与 `ScoringConfig.card_pool_limit=25` 命名贯穿一致。`quota_report={}` 与 `ScoreResult` 字段保留一致。
