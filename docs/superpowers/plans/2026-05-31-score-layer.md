# Score / Ranking Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the 3rd pipeline layer — rule-based multi-dimensional scoring + type-quota selection of deduped news items, producing an explainable `ScoreResult`.

**Architecture:** Pure functions (`recency_band`, `compute_scores`, `apply_quota`) isolated from all IO; a thin `score()` orchestrator reads the registry priority map (reusing dedup's `load_source_priorities`) and emits `runs` events. All weights/quotas read from `config/scoring.yaml`. No LLM, no network. Mirrors the dedup layer's structure exactly.

**Tech Stack:** Python 3.12, pydantic v2, pyyaml, pytest. Run tests with `uv run pytest` (bare `pytest` picks the wrong interpreter and fails imports).

**Spec:** `docs/specs/score.md`. **Upstream contract:** `DedupResult.deduped_items: list[NewsItem]` (see `src/pipeline/dedup.py`).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/core/types.py` | Add `ScoringConfig`, `ScoredItem`, `QuotaLine`, `ScoreResult` | Modify (append after dedup types) |
| `src/core/config.py` | Add `load_scoring_config(path)` | Modify (append) |
| `config/scoring.yaml` | Default weights/quota (tunable, not hardcoded) | Create |
| `src/pipeline/score.py` | Pure `recency_band` / `compute_scores` / `apply_quota` + `score()` orchestrator | Create |
| `src/cli.py` | `run_dry_score()` + `--score` flag | Modify |
| `docs/ROADMAP.md` | Flip row ③ to done | Modify |
| `tests/contract/test_score_types.py` | ScoredItem/QuotaLine/ScoreResult schema | Create |
| `tests/contract/test_scoring_config.py` | `load_scoring_config` | Create |
| `tests/contract/test_score_unit.py` | Pure-function unit tests | Create |
| `tests/golden/test_score.py` | 6 golden cases (spec §9) | Create |
| `tests/golden/data/scoring_golden.yaml` | Frozen golden config | Create |
| `tests/contract/test_cli.py` | Append `run_dry_score` test | Modify |

---

### Task 1: Core types (ScoringConfig / ScoredItem / QuotaLine / ScoreResult)

**Files:**
- Modify: `src/core/types.py` (append at end, after `DedupResult`)
- Test: `tests/contract/test_score_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_score_types.py`:

```python
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (ScoredItem, QuotaLine, ScoreResult, ScoringConfig,
                            SourceType)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _scored(**over):
    base = dict(title_en="T", link="https://a/1", source="openai",
                source_type=SourceType.PAPER, published_at=NOW,
                cluster_id="evt-2026-05-30-001", score=88,
                score_breakdown={"时效": 10.0}, is_explore=False)
    base.update(over)
    return ScoredItem(**base)


def test_scored_item_extends_newsitem():
    si = _scored()
    assert si.score == 88
    assert si.cluster_id == "evt-2026-05-30-001"   # inherited from NewsItem
    assert si.title_en == "T"                       # inherited from RawItem
    assert si.is_explore is False


def test_scored_item_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        _scored(score=150)
    with pytest.raises(ValidationError):
        _scored(score=-1)


def test_quota_line_and_result_shapes():
    line = QuotaLine(source_type="paper", available=3, quota=2, selected=2)
    res = ScoreResult(selected_items=[_scored()], all_scored=[_scored()],
                      quota_report={"paper": line}, input_count=1,
                      selected_count=1, is_silent=False)
    assert res.quota_report["paper"].selected == 2
    assert res.selected_count == 1


def test_scoring_config_defaults():
    c = ScoringConfig()
    assert c.quota["paper"] == 2
    assert c.total_limit == 8
    assert c.fresh_bonus == 10
    assert c.dimension_scores["official"]["一手性"] == 20
    assert c.priority_bonus[1] == 6
    assert c.sources_registry_path == "config/sources.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_score_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScoredItem'`.

- [ ] **Step 3: Implement the types**

Append to `src/core/types.py` (after the `DedupResult` dataclass at the end). The file already has `from dataclasses import dataclass, field` and `from pydantic import BaseModel, Field, field_validator` imported:

```python
# --- score layer (Circle 3) ---
class ScoredItem(NewsItem):
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, float]
    is_explore: bool = False


@dataclass
class ScoringConfig:
    dimension_scores: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "official":  {"机构影响力": 18, "一手性": 20, "技术价值": 10, "产业影响": 12, "扩散潜力": 9},
        "paper":     {"机构影响力": 14, "一手性": 20, "技术价值": 16, "产业影响": 8,  "扩散潜力": 7},
        "model":     {"机构影响力": 15, "一手性": 18, "技术价值": 14, "产业影响": 10, "扩散潜力": 9},
        "tool":      {"机构影响力": 10, "一手性": 14, "技术价值": 12, "产业影响": 10, "扩散潜力": 10},
        "news":      {"机构影响力": 12, "一手性": 8,  "技术价值": 6,  "产业影响": 12, "扩散潜力": 11},
        "community": {"机构影响力": 6,  "一手性": 10, "技术价值": 8,  "产业影响": 6,  "扩散潜力": 12},
        "blog":      {"机构影响力": 6,  "一手性": 8,  "技术价值": 8,  "产业影响": 6,  "扩散潜力": 8},
    })
    priority_bonus: dict[int, int] = field(default_factory=lambda: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4})
    priority_bonus_default: int = 0
    fresh_hours: int = 24
    fresh_bonus: float = 10
    mid_hours: int = 48
    mid_bonus: float = 4
    stale_hours: int = 72
    stale_penalty: float = -10
    same_source_penalty: float = -5
    quota: dict[str, int] = field(default_factory=lambda: {
        "paper": 2, "model": 1, "tool": 2, "official": 1, "community": 1, "news": 1, "blog": 0})
    total_limit: int = 8
    sources_registry_path: str = "config/sources.yaml"


@dataclass
class QuotaLine:
    source_type: str
    available: int
    quota: int
    selected: int


@dataclass
class ScoreResult:
    selected_items: list[ScoredItem]
    all_scored: list[ScoredItem]
    quota_report: dict[str, QuotaLine]
    input_count: int
    selected_count: int
    is_silent: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_score_types.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_score_types.py
git commit -m "feat(score): core types (ScoringConfig/ScoredItem/QuotaLine/ScoreResult)"
```

---

### Task 2: Config loader + `config/scoring.yaml`

**Files:**
- Modify: `src/core/config.py` (append `load_scoring_config`)
- Create: `config/scoring.yaml`
- Test: `tests/contract/test_scoring_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_scoring_config.py`:

```python
from src.core.config import load_scoring_config
from src.core.types import ScoringConfig


def test_missing_file_returns_defaults():
    c = load_scoring_config("does/not/exist.yaml")
    assert isinstance(c, ScoringConfig)
    assert c.total_limit == 8
    assert c.quota["paper"] == 2


def test_loads_and_flattens_nested_recency_and_penalty(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "recency:\n"
        "  fresh_hours: 12\n"
        "  fresh_bonus: 20\n"
        "  mid_hours: 36\n"
        "  mid_bonus: 5\n"
        "  stale_hours: 60\n"
        "  stale_penalty: -15\n"
        "penalty:\n"
        "  same_source: -8\n"
        "quota: {paper: 1, model: 1}\n"
        "total_limit: 5\n",
        encoding="utf-8")
    c = load_scoring_config(str(p))
    assert c.fresh_hours == 12 and c.fresh_bonus == 20
    assert c.mid_hours == 36 and c.mid_bonus == 5
    assert c.stale_hours == 60 and c.stale_penalty == -15
    assert c.same_source_penalty == -8
    assert c.quota == {"paper": 1, "model": 1}
    assert c.total_limit == 5
    # untouched keys keep defaults
    assert c.dimension_scores["official"]["一手性"] == 20


def test_repo_default_config_is_consistent():
    c = load_scoring_config("config/scoring.yaml")
    # invariant: sum of quotas must not exceed the hard total limit (spec §5.4)
    assert sum(c.quota.values()) <= c.total_limit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_scoring_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_scoring_config'`.

- [ ] **Step 3a: Create `config/scoring.yaml`**

```yaml
# 打分排序层配置 (Circle 3). 全部维度分/权重/配额可调, 不写死在代码里.
# 维度基分: source_type → 各维度贡献 (PRD §5.5 维度键, 0-100 多维).
dimension_scores:
  official:  {机构影响力: 18, 一手性: 20, 技术价值: 10, 产业影响: 12, 扩散潜力: 9}
  paper:     {机构影响力: 14, 一手性: 20, 技术价值: 16, 产业影响: 8,  扩散潜力: 7}
  model:     {机构影响力: 15, 一手性: 18, 技术价值: 14, 产业影响: 10, 扩散潜力: 9}
  tool:      {机构影响力: 10, 一手性: 14, 技术价值: 12, 产业影响: 10, 扩散潜力: 10}
  news:      {机构影响力: 12, 一手性: 8,  技术价值: 6,  产业影响: 12, 扩散潜力: 11}
  community: {机构影响力: 6,  一手性: 10, 技术价值: 8,  产业影响: 6,  扩散潜力: 12}
  blog:      {机构影响力: 6,  一手性: 8,  技术价值: 8,  产业影响: 6,  扩散潜力: 8}

# registry 中 source.priority (1=最高) → 折进"机构影响力"维度
priority_bonus: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4}
priority_bonus_default: 0

recency:                          # 时效分 (spec §5.2)
  fresh_hours: 24
  fresh_bonus: 10
  mid_hours: 48
  mid_bonus: 4
  stale_hours: 72
  stale_penalty: -10

penalty:
  same_source: -5                 # 同源第2+条各扣 (spec §5.3)

# 类型配额 (PRD §4.2). 各 quota 之和(=8) <= total_limit.
quota: {paper: 2, model: 1, tool: 2, official: 1, community: 1, news: 1, blog: 0}
total_limit: 8

sources_registry_path: "config/sources.yaml"
```

- [ ] **Step 3b: Implement `load_scoring_config`**

Append to `src/core/config.py` (the file already has `import yaml`; update the `from src.core.types import ...` line to also import `ScoringConfig`):

```python
from src.core.types import ScoringConfig


def load_scoring_config(path: str) -> ScoringConfig:
    """Load scoring weights/quota from YAML; missing file -> dataclass defaults.
    Flattens nested `recency`/`penalty` blocks into flat dataclass fields."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return ScoringConfig()
    d = ScoringConfig()
    recency = data.get("recency", {})
    penalty = data.get("penalty", {})
    return ScoringConfig(
        dimension_scores=data.get("dimension_scores", d.dimension_scores),
        priority_bonus=data.get("priority_bonus", d.priority_bonus),
        priority_bonus_default=data.get("priority_bonus_default", d.priority_bonus_default),
        fresh_hours=recency.get("fresh_hours", d.fresh_hours),
        fresh_bonus=recency.get("fresh_bonus", d.fresh_bonus),
        mid_hours=recency.get("mid_hours", d.mid_hours),
        mid_bonus=recency.get("mid_bonus", d.mid_bonus),
        stale_hours=recency.get("stale_hours", d.stale_hours),
        stale_penalty=recency.get("stale_penalty", d.stale_penalty),
        same_source_penalty=penalty.get("same_source", d.same_source_penalty),
        quota=data.get("quota", d.quota),
        total_limit=data.get("total_limit", d.total_limit),
        sources_registry_path=data.get("sources_registry_path", d.sources_registry_path),
    )
```

> Note: the existing `from src.core.types import DedupConfig` line at the top stays; add a second import line for `ScoringConfig` (or merge into one). Both are valid.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_scoring_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/config.py config/scoring.yaml tests/contract/test_scoring_config.py
git commit -m "feat(score): scoring config loader + config/scoring.yaml"
```

---

### Task 3: Pure `recency_band` + `compute_scores`

**Files:**
- Create: `src/pipeline/score.py`
- Test: `tests/contract/test_score_unit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_score_unit.py`:

```python
import logging
from datetime import datetime, timezone, timedelta
from src.core.types import RawItem, NewsItem, SourceType, ScoringConfig, RunContext
from src.pipeline.score import recency_band, compute_scores

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="u", now=NOW, logger=logging.getLogger("unit-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(title_en=title, link=link, source=source, source_type=st,
                    published_at=published, cluster_id="evt-x")


def test_recency_band_four_zones():
    c = ScoringConfig()
    assert recency_band(NOW, NOW, c) == c.fresh_bonus                       # 0h
    assert recency_band(NOW - timedelta(hours=36), NOW, c) == c.mid_bonus   # 36h
    assert recency_band(NOW - timedelta(hours=60), NOW, c) == 0.0           # 60h
    assert recency_band(NOW - timedelta(hours=100), NOW, c) == c.stale_penalty


def test_compute_scores_breakdown_has_nine_keys_and_sums_to_score():
    items = [_ni("A", "https://a/1", "openai", SourceType.OFFICIAL)]
    scored = compute_scores(items, {"openai": 1}, ScoringConfig(), _ctx())
    bd = scored[0].score_breakdown
    assert set(bd) == {"机构影响力", "一手性", "技术价值", "产业影响", "扩散潜力",
                       "可见指标", "时效", "惩罚", "读者相关度"}
    assert bd["可见指标"] == 0.0 and bd["读者相关度"] == 0.0
    assert scored[0].is_explore is False
    assert scored[0].score == max(0, min(100, round(sum(bd.values()))))
    # official base 一手性=20, priority 1 bonus folded into 机构影响力
    assert bd["机构影响力"] == 18 + 6


def test_compute_scores_missing_priority_uses_default():
    items = [_ni("A", "https://a/1", "unknown-src", SourceType.PAPER)]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())   # source not in map
    # priority_bonus_default == 0 -> 机构影响力 == paper base 14 + 0
    assert scored[0].score_breakdown["机构影响力"] == 14


def test_compute_scores_same_source_penalty_by_published_order():
    t1 = NOW - timedelta(hours=3)
    t2 = NOW - timedelta(hours=2)
    t3 = NOW - timedelta(hours=1)
    items = [
        _ni("late", "https://s/3", "blog-x", SourceType.BLOG, t3),
        _ni("early", "https://s/1", "blog-x", SourceType.BLOG, t1),
        _ni("mid", "https://s/2", "blog-x", SourceType.BLOG, t2),
    ]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0.0                          # earliest: no penalty
    assert pen["https://s/2"] == ScoringConfig().same_source_penalty
    assert pen["https://s/3"] == ScoringConfig().same_source_penalty


def test_compute_scores_sorted_desc_by_score():
    items = [
        _ni("blog", "https://b/1", "b", SourceType.BLOG),       # low base
        _ni("official", "https://o/2", "o", SourceType.OFFICIAL),  # high base
    ]
    scored = compute_scores(items, {}, ScoringConfig(), _ctx())
    assert [s.source_type for s in scored][0] == SourceType.OFFICIAL
    assert scored[0].score >= scored[1].score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_score_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.score'`.

- [ ] **Step 3: Implement `recency_band` + `compute_scores`**

Create `src/pipeline/score.py`:

```python
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from src.core.types import (NewsItem, ScoredItem, ScoringConfig, RunContext)

# PRD §5.5 fixed breakdown dimension keys.
DIMENSION_KEYS = ["机构影响力", "一手性", "技术价值", "产业影响", "扩散潜力",
                  "可见指标", "时效", "惩罚", "读者相关度"]
# Dimensions sourced directly from the per-type matrix (spec §5.1).
_MATRIX_DIMS = ["一手性", "技术价值", "产业影响", "扩散潜力"]


def recency_band(published_at: datetime, now: datetime, config: ScoringConfig) -> float:
    """时效分: 4 档 (spec §5.2). Uses injected `now` for determinism."""
    age_h = (now - published_at).total_seconds() / 3600.0
    if age_h <= config.fresh_hours:
        return float(config.fresh_bonus)
    if age_h <= config.mid_hours:
        return float(config.mid_bonus)
    if age_h <= config.stale_hours:
        return 0.0
    return float(config.stale_penalty)


def _same_source_penalty(items: list[NewsItem], config: ScoringConfig) -> dict[str, float]:
    """link -> 同源惩罚. Earliest per source = 0, rest = same_source_penalty (spec §5.3).
    Ordered by (published_at, link) so it is independent of score (deterministic)."""
    by_source: dict[str, list[NewsItem]] = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)
    out: dict[str, float] = {}
    for grp in by_source.values():
        ordered = sorted(grp, key=lambda it: (it.published_at, it.link))
        for i, it in enumerate(ordered):
            out[it.link] = 0.0 if i == 0 else float(config.same_source_penalty)
    return out


def compute_scores(items: list[NewsItem], priority_of: dict[str, int],
                   config: ScoringConfig, ctx: RunContext) -> list[ScoredItem]:
    """Pure scoring (spec §5.1). Returns ScoredItems sorted by (score desc,
    published_at asc, link asc)."""
    penalty_of = _same_source_penalty(items, config)
    scored: list[ScoredItem] = []
    for it in items:
        dims = config.dimension_scores.get(it.source_type.value, {})
        prio = priority_of.get(it.source)
        prio_bonus = (config.priority_bonus.get(prio, config.priority_bonus_default)
                      if prio is not None else config.priority_bonus_default)
        breakdown = {
            "机构影响力": float(dims.get("机构影响力", 0)) + float(prio_bonus),
            "可见指标": 0.0,
            "时效": recency_band(it.published_at, ctx.now, config),
            "惩罚": penalty_of[it.link],
            "读者相关度": 0.0,
        }
        for k in _MATRIX_DIMS:
            breakdown[k] = float(dims.get(k, 0))
        # normalize key order to the fixed PRD set
        breakdown = {k: breakdown[k] for k in DIMENSION_KEYS}
        raw = round(sum(breakdown.values()))
        score = max(0, min(100, raw))
        scored.append(ScoredItem(**it.model_dump(), score=score,
                                 score_breakdown=breakdown, is_explore=False))
    scored.sort(key=lambda s: (-s.score, s.published_at, s.link))
    return scored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_score_unit.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/score.py tests/contract/test_score_unit.py
git commit -m "feat(score): pure recency_band + compute_scores (multi-dim breakdown)"
```

---

### Task 4: Pure `apply_quota`

**Files:**
- Modify: `src/pipeline/score.py` (append `apply_quota`)
- Test: `tests/contract/test_score_unit.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_score_unit.py`:

```python
from src.pipeline.score import apply_quota


def _scored_list(ctx, *specs):
    # specs: (title, link, source, source_type, published)
    items = [_ni(*s) for s in specs]
    return compute_scores(items, {}, ScoringConfig(), ctx)


def test_apply_quota_trims_to_quota_keeping_top_scored():
    ctx = _ctx()
    # 3 papers from distinct sources, varying recency -> distinct scores
    fresh = NOW
    mid = NOW - timedelta(hours=36)
    stale = NOW - timedelta(hours=100)
    scored = _scored_list(
        ctx,
        ("p-fresh", "https://p/1", "p1", SourceType.PAPER, fresh),
        ("p-mid", "https://p/2", "p2", SourceType.PAPER, mid),
        ("p-stale", "https://p/3", "p3", SourceType.PAPER, stale),
    )
    cfg = ScoringConfig()
    cfg.quota = {"paper": 2}
    selected, report = apply_quota(scored, cfg)
    assert report["paper"].available == 3
    assert report["paper"].quota == 2
    assert report["paper"].selected == 2
    links = {s.link for s in selected}
    assert links == {"https://p/1", "https://p/2"}      # stale dropped (lowest)


def test_apply_quota_keeps_all_when_under_quota():
    ctx = _ctx()
    scored = _scored_list(ctx, ("t", "https://t/1", "t1", SourceType.TOOL, NOW))
    cfg = ScoringConfig()
    cfg.quota = {"tool": 2}
    selected, report = apply_quota(scored, cfg)
    assert report["tool"].available == 1
    assert report["tool"].selected == 1                 # min(quota, available)
    assert len(selected) == 1


def test_apply_quota_zero_for_unlisted_type():
    ctx = _ctx()
    scored = _scored_list(ctx, ("n", "https://n/1", "n1", SourceType.NEWS, NOW))
    cfg = ScoringConfig()
    cfg.quota = {"paper": 2}                             # news not listed
    selected, report = apply_quota(scored, cfg)
    assert report["news"].quota == 0 and report["news"].selected == 0
    assert selected == []


def test_apply_quota_respects_total_limit():
    ctx = _ctx()
    scored = _scored_list(
        ctx,
        ("a", "https://a/1", "s1", SourceType.PAPER, NOW),
        ("b", "https://b/2", "s2", SourceType.MODEL, NOW),
        ("c", "https://c/3", "s3", SourceType.TOOL, NOW),
    )
    cfg = ScoringConfig()
    cfg.quota = {"paper": 1, "model": 1, "tool": 1}
    cfg.total_limit = 2
    selected, _ = apply_quota(scored, cfg)
    assert len(selected) == 2                            # trimmed to total_limit
    # kept the 2 highest-scored
    assert selected[0].score >= selected[1].score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_score_unit.py::test_apply_quota_trims_to_quota_keeping_top_scored -v`
Expected: FAIL with `ImportError: cannot import name 'apply_quota'`.

- [ ] **Step 3: Implement `apply_quota`**

Append to `src/pipeline/score.py`:

```python
from src.core.types import QuotaLine


def apply_quota(scored: list[ScoredItem], config: ScoringConfig
                ) -> tuple[list[ScoredItem], dict[str, QuotaLine]]:
    """Strict per-type quota selection (spec §5.4). No cross-type fill.
    `scored` is assumed sorted (compute_scores output) but we re-sort defensively."""
    by_type: dict[str, list[ScoredItem]] = defaultdict(list)
    for s in scored:
        by_type[s.source_type.value].append(s)

    selected: list[ScoredItem] = []
    report: dict[str, QuotaLine] = {}
    for stype, group in by_type.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = config.quota.get(stype, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[stype] = QuotaLine(source_type=stype, available=len(group),
                                  quota=q, selected=len(take))

    selected.sort(key=lambda s: (-s.score, s.published_at, s.link))
    if len(selected) > config.total_limit:
        selected = selected[:config.total_limit]
    return selected, report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_score_unit.py -v`
Expected: PASS (9 tests total in this file).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/score.py tests/contract/test_score_unit.py
git commit -m "feat(score): pure apply_quota (strict per-type, total_limit cap)"
```

---

### Task 5: `score()` orchestrator + golden cases

**Files:**
- Modify: `src/pipeline/score.py` (append `score`)
- Create: `tests/golden/data/scoring_golden.yaml`
- Create: `tests/golden/test_score.py`

- [ ] **Step 1a: Create the frozen golden config `tests/golden/data/scoring_golden.yaml`**

Round numbers make golden math easy to assert. Registry path is intentionally nonexistent so `priority_of == {}` and `priority_bonus_default` (0) applies — keeps scores independent of registry contents.

```yaml
dimension_scores:
  official:  {机构影响力: 20, 一手性: 20, 技术价值: 10, 产业影响: 10, 扩散潜力: 10}
  paper:     {机构影响力: 10, 一手性: 20, 技术价值: 20, 产业影响: 10, 扩散潜力: 10}
  model:     {机构影响力: 10, 一手性: 10, 技术价值: 10, 产业影响: 10, 扩散潜力: 10}
  tool:      {机构影响力: 10, 一手性: 10, 技术价值: 10, 产业影响: 10, 扩散潜力: 10}
  news:      {机构影响力: 10, 一手性: 5,  技术价值: 5,  产业影响: 10, 扩散潜力: 10}
  community: {机构影响力: 5,  一手性: 5,  技术价值: 5,  产业影响: 5,  扩散潜力: 10}
  blog:      {机构影响力: 5,  一手性: 5,  技术价值: 5,  产业影响: 5,  扩散潜力: 5}
priority_bonus: {1: 5, 2: 0, 3: 0}
priority_bonus_default: 0
recency:
  fresh_hours: 24
  fresh_bonus: 10
  mid_hours: 48
  mid_bonus: 4
  stale_hours: 72
  stale_penalty: -10
penalty:
  same_source: -5
quota: {paper: 2, model: 1, tool: 2, official: 1, community: 1, news: 1, blog: 0}
total_limit: 8
sources_registry_path: "does/not/exist.yaml"
```

- [ ] **Step 1b: Write the failing golden test**

Create `tests/golden/test_score.py`:

```python
import logging
from datetime import datetime, timezone, timedelta
from src.core.types import NewsItem, SourceType, RunContext, ScoringConfig
from src.core.config import load_scoring_config
from src.pipeline.score import score, compute_scores

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(title_en=title, link=link, source=source, source_type=st,
                    published_at=published, cluster_id="evt-x")


def _cfg():
    return load_scoring_config("tests/golden/data/scoring_golden.yaml")


# Case 1 (spec §9.1): over-quota type trimmed to top-scored
def test_golden_quota_trims_top_scored():
    items = [
        _ni("p-fresh", "https://p/1", "p1", SourceType.PAPER, NOW),
        _ni("p-mid", "https://p/2", "p2", SourceType.PAPER, NOW - timedelta(hours=36)),
        _ni("p-stale", "https://p/3", "p3", SourceType.PAPER, NOW - timedelta(hours=100)),
    ]
    res = score(items, _cfg(), _ctx())
    assert res.quota_report["paper"].selected == 2          # quota paper=2
    assert res.quota_report["paper"].available == 3
    kept = {s.link for s in res.selected_items}
    assert kept == {"https://p/1", "https://p/2"}           # stale dropped


# Case 2 (spec §9.2): under-quota type fully kept, no fabrication
def test_golden_under_quota_keeps_all():
    items = [_ni("t", "https://t/1", "t1", SourceType.TOOL, NOW)]   # quota tool=2
    res = score(items, _cfg(), _ctx())
    assert res.quota_report["tool"].available == 1
    assert res.quota_report["tool"].selected == 1
    assert res.selected_count == 1


# Case 3 (spec §9.3): recency bands
def test_golden_recency_bands():
    items = [
        _ni("fresh", "https://o/1", "s1", SourceType.OFFICIAL, NOW),
        _ni("mid", "https://o/2", "s2", SourceType.OFFICIAL, NOW - timedelta(hours=36)),
        _ni("zero", "https://o/3", "s3", SourceType.OFFICIAL, NOW - timedelta(hours=60)),
        _ni("stale", "https://o/4", "s4", SourceType.OFFICIAL, NOW - timedelta(hours=100)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    band = {s.link: s.score_breakdown["时效"] for s in scored}
    assert band["https://o/1"] == 10
    assert band["https://o/2"] == 4
    assert band["https://o/3"] == 0
    assert band["https://o/4"] == -10


# Case 4 (spec §9.4): same-source penalty by published order
def test_golden_same_source_penalty():
    items = [
        _ni("late", "https://s/3", "dup", SourceType.NEWS, NOW - timedelta(hours=1)),
        _ni("early", "https://s/1", "dup", SourceType.NEWS, NOW - timedelta(hours=3)),
        _ni("mid", "https://s/2", "dup", SourceType.NEWS, NOW - timedelta(hours=2)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0       # earliest
    assert pen["https://s/2"] == -5
    assert pen["https://s/3"] == -5


# Case 5 (spec §9.5): empty input -> silent
def test_golden_empty_input_is_silent():
    res = score([], _cfg(), _ctx())
    assert res.selected_items == [] and res.all_scored == []
    assert res.input_count == 0 and res.selected_count == 0
    assert res.is_silent is True


# Case 6 (spec §9.6): determinism + clamp + breakdown sums to score
def test_golden_clamp_and_breakdown_sum_and_determinism():
    items = [_ni("a", "https://a/1", "s1", SourceType.OFFICIAL, NOW)]
    # high config -> clamp to 100
    hi = ScoringConfig()
    hi.dimension_scores = {"official": {"机构影响力": 90, "一手性": 90,
                                        "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    s1 = compute_scores(items, {}, hi, _ctx())
    assert s1[0].score == 100
    assert s1[0].score == max(0, min(100, round(sum(s1[0].score_breakdown.values()))))
    # low/negative config -> clamp to 0
    lo = ScoringConfig()
    lo.dimension_scores = {"official": {"机构影响力": -50, "一手性": -50,
                                        "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    lo.fresh_bonus = 0
    s2 = compute_scores(items, {}, lo, _ctx())
    assert s2[0].score == 0
    # determinism: same input + same ctx -> identical scores
    again = compute_scores(items, {}, hi, _ctx())
    assert [x.score for x in s1] == [x.score for x in again]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_score.py -v`
Expected: FAIL with `ImportError: cannot import name 'score'`.

- [ ] **Step 3: Implement `score()` orchestrator**

Append to `src/pipeline/score.py` (add the imports for `load_source_priorities`, `emit`, and `ScoreResult` at the top of the file with the other imports):

At the top, extend the existing imports:

```python
from src.core.types import (NewsItem, ScoredItem, ScoringConfig, RunContext,
                            QuotaLine, ScoreResult)
from src.core.registry import load_source_priorities
from src.observability.events import emit
```

> (Merge with the import lines already added in Tasks 3-4 — `QuotaLine` was imported in Task 4; add `ScoreResult` here and the two new module imports.)

Append the orchestrator:

```python
def score(items: list[NewsItem], config: ScoringConfig, ctx: RunContext) -> ScoreResult:
    """Orchestrate scoring: load registry priority map, run pure compute_scores +
    apply_quota, emit runs events (spec §3, §11)."""
    emit(ctx.logger, "score_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "score_done", input_count=0, selected_count=0, silent=True)
        return ScoreResult(selected_items=[], all_scored=[], quota_report={},
                           input_count=0, selected_count=0, is_silent=True)

    priority_of = load_source_priorities(config.sources_registry_path)
    scored = compute_scores(items, priority_of, config, ctx)
    for s in scored:
        emit(ctx.logger, "item_scored", link=s.link,
             source_type=s.source_type.value, score=s.score)

    selected, report = apply_quota(scored, config)
    for stype, line in report.items():
        emit(ctx.logger, "quota_applied", source_type=stype,
             available=line.available, quota=line.quota, selected=line.selected)
    for s in selected:
        emit(ctx.logger, "item_selected", link=s.link,
             source_type=s.source_type.value, score=s.score)

    result = ScoreResult(selected_items=selected, all_scored=scored,
                         quota_report=report, input_count=len(items),
                         selected_count=len(selected), is_silent=False)
    emit(ctx.logger, "score_done", input_count=result.input_count,
         selected_count=result.selected_count, silent=False)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_score.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/score.py tests/golden/test_score.py tests/golden/data/scoring_golden.yaml
git commit -m "feat(score): score() orchestration + golden cases (spec §9)"
```

---

### Task 6: CLI `--score` chain

**Files:**
- Modify: `src/cli.py`
- Test: `tests/contract/test_cli.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_cli.py`:

```python
from src.cli import run_dry_score


def test_run_dry_score_returns_scoreresult_json():
    out = run_dry_score(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),
    )
    assert "selected_count" in out and "selected_items" in out
    assert "quota_report" in out
    assert out["input_count"] >= out["selected_count"]
    json.dumps(out)                                  # must be JSON-serializable
```

> `FakeEmbeddingProvider` and `json`/`datetime` are already imported at the top of this file from the earlier dedup test.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli.py::test_run_dry_score_returns_scoreresult_json -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_score'`.

- [ ] **Step 3: Implement `run_dry_score` + `--score` flag**

In `src/cli.py`, add imports near the top (after the existing dedup imports):

```python
from dataclasses import asdict
from src.core.config import load_scoring_config
from src.pipeline.score import score
```

> The existing `from src.core.config import load_dedup_config` line stays; add the `load_scoring_config` import (separate line is fine). `asdict` is new.

Add the function after `run_dry_dedup`:

```python
def run_dry_score(registry_path: str, now: datetime | None = None,
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
    dres = dedup(coll.items, dcfg, ctx,
                 embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": sres.input_count,
        "selected_count": sres.selected_count,
        "is_silent": sres.is_silent,
        "quota_report": {k: asdict(v) for k, v in sres.quota_report.items()},
        "selected_items": [si.model_dump(mode="json") for si in sres.selected_items],
    }
```

Add the `--score` flag in `main()` (after the `--dedup` add_argument):

```python
    p.add_argument("--score", action="store_true",
                   help="chain collect -> dedup -> score, print ScoreResult JSON")
```

Add the dispatch branch in `main()`, placed BEFORE the `if args.dry_run and args.dedup:` branch (so `--score` takes precedence; it implies the full chain):

```python
    if args.dry_run and args.score:
        out = run_dry_score(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli.py -v`
Expected: PASS (all 3 tests in file).

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/contract/test_cli.py
git commit -m "feat(cli): --score chain (collect -> dedup -> score) emitting ScoreResult JSON"
```

---

### Task 7: Full suite + ROADMAP update

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (60 from Circles 1-2 + new score tests).

- [ ] **Step 2: Update `docs/ROADMAP.md`**

Edit the §1 mermaid: change `C3` class from `todo` to `done`:

```
    C3["③ 打分配额<br/>score()"]:::done
```

Edit the §2 progress table row ③ from:

```
| ③ | 打分配额 score | — | — | — | — | ⬜ |
```
to:
```
| ③ | 打分配额 score | `specs/score.md` | ✅ `pipeline/score.py`（纯打分+配额） | ✅ golden | ✅ `--dry-run --score` 实跑 | **🟩 已合并 (master)** |
```

Update the "最后更新" date line near the top to:
```
> 每完成一个 Circle 更新此文。最后更新：2026-05-31（Circle 3 score 已合并）。
```

Update §5 "下一步" to point at Circle 4 (interpret) and move the score summary into a "已完成" note (mirror how Circle 2 was recorded).

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: ROADMAP — mark score layer (Circle 3) done"
```

---

## Self-Review

**1. Spec coverage:**
- §3 contract `score(items, config, ctx)` + pure `compute_scores`/`apply_quota` → Tasks 3,4,5. ✅
- §4 data types (ScoredItem/QuotaLine/ScoreResult) → Task 1. ✅
- §5.1 9-dim breakdown + priority folded into 机构影响力 → Task 3. ✅
- §5.2 recency bands → Task 3 (`recency_band`). ✅
- §5.3 same-source penalty by published order → Task 3 (`_same_source_penalty`). ✅
- §5.4 strict per-type quota + tie-break + total_limit → Task 4. ✅
- §6 config/scoring.yaml + loader (flatten recency/penalty) → Task 2. ✅
- §7 degrade (empty input silent, missing priority default) → Task 5 (empty), Task 3 (priority default). ✅
- §8 invariants 1-9 → asserted across Tasks 3-5 golden/unit tests. ✅
- §9 6 golden cases → Task 5. ✅
- §11 events (item_scored/quota_applied/item_selected/score_done) → Task 5. ✅
- §12 acceptance (#4 配额生效 + breakdown, #1 end-to-end via CLI, #8 silent) → Tasks 5,6. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". All steps carry full code. ✅

**3. Type consistency:** `ScoredItem`/`QuotaLine`/`ScoreResult`/`ScoringConfig` names identical across Tasks 1→3→4→5→6. Function names `recency_band`/`compute_scores`/`apply_quota`/`score` consistent. `score_breakdown` dict, `quota` dict[str,int], `priority_bonus` dict[int,int] consistent. `same_source_penalty` field name matches between config dataclass (Task 1), loader (Task 2), and usage (Task 3). ✅
