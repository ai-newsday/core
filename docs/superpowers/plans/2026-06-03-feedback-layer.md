# Feedback Layer (Circle 7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 7th pipeline layer — recycle the human review action (keep/drop/edit) into per-source reputation stats and incrementally update each source's `quality_weight`, persisted as a JSON ledger, with no wiring back into scoring.

**Architecture:** Pure-core functions (`derive_events` → `aggregate_by_source` → `compute_quality_weights`) plus a `feedback()` orchestrator, mirroring the publish layer (`src/pipeline/publish.py`). Config + dataclass results in `src/core/types.py`, loaders in `src/core/config.py`, a `config/feedback.yaml`, and a `--feedback` CLI chain in `src/cli.py`. JSON event ledger stands in for SQLite/Qdrant; `--dry-run` only prints, never writes disk.

**Tech Stack:** Python 3.12 (run tests with `uv run pytest` — bare `pytest` picks the wrong interpreter), pydantic v2, PyYAML, pytest. Spec: `docs/specs/feedback.md`.

---

## Background for the implementer (read this first)

You are adding one layer to an existing 7-layer news pipeline. The five upstream layers already exist and are green. You only touch the files listed below. **Do not** modify scoring, review, or any earlier layer. **Do not** add LLM/network/disk side effects to the layer functions — they are pure.

Key upstream types you will consume (already defined in `src/core/types.py`, do not redefine):

- `InterpretedItem` — a pydantic model with (among others) `link: str`, `source: str`, `source_type: SourceType`. These are the items that go *into* the review layer (pre-review, so dropped items are still present here).
- `ReviewDecision` — pydantic model: `action: Literal["keep","drop","edit"] = "keep"`, `order: int | None`, `edits: dict`. Loaded by `load_review_decisions(path) -> dict[str, ReviewDecision]` (keyed by link).
- `SourceType` — str enum (`paper`/`model`/`tool`/`community`/`official`/`news`/`blog`).
- `RunContext` — dataclass: `run_id: str`, `now: datetime`, `logger`.
- `Evidence` — pydantic model: `claim`, `anchor` (both min_length 1).

The events helper: `from src.observability.events import emit` — call `emit(logger, "event_name", key=value, ...)`; it writes one JSON log line.

`src/core/types.py` already imports at the top: `from dataclasses import dataclass, field`, `from datetime import datetime`, `from typing import Literal`, `from pydantic import BaseModel, Field, field_validator`. No new imports needed there.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/core/types.py` | `FeedbackEvent`, `SourceFeedbackStats` (pydantic); `FeedbackConfig`, `FeedbackResult` (dataclass) | Modify (append) |
| `src/core/config.py` | `load_feedback_config`, `load_feedback_events`, `load_quality_weights` | Modify (append) |
| `config/feedback.yaml` | Formula coefficients + ledger paths | Create |
| `src/pipeline/feedback.py` | `derive_events`, `aggregate_by_source`, `compute_quality_weights`, `feedback` (all pure) | Create |
| `src/cli.py` | `run_dry_feedback` + `--feedback` flag/dispatch | Modify |
| `tests/contract/test_feedback_types.py` | type/schema shape | Create |
| `tests/contract/test_feedback_config.py` | config + ledger loaders | Create |
| `tests/contract/test_cli_feedback.py` | `--feedback` output shape, JSON-serializable | Create |
| `tests/golden/test_feedback.py` | spec §8 invariants + §9 cases | Create |

---

## Task 1: Core types (FeedbackEvent / SourceFeedbackStats / FeedbackConfig / FeedbackResult)

**Files:**
- Modify: `src/core/types.py` (append at end, after `PublishResult`)
- Test: `tests/contract/test_feedback_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_feedback_types.py`:

```python
from datetime import datetime, timezone
from src.core.types import (FeedbackEvent, SourceFeedbackStats,
                            FeedbackConfig, FeedbackResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_feedback_event_shape():
    e = FeedbackEvent(link="https://a/1", source="src", action="drop",
                      run_id="r1", ts=NOW)
    assert e.action == "drop" and e.source == "src"
    assert e.link == "https://a/1" and e.run_id == "r1"
    assert e.ts == NOW


def test_source_feedback_stats_shape():
    s = SourceFeedbackStats(source="src", keep=2, edit=1, drop=1, total=4)
    assert s.total == 4 and s.keep == 2 and s.edit == 1 and s.drop == 1


def test_feedback_config_defaults():
    c = FeedbackConfig()
    assert c.events_path == "data/feedback_events.json"
    assert c.weights_path == "data/quality_weights.json"
    assert c.baseline_weight == 1.0
    assert c.min_weight == 0.5 and c.max_weight == 1.5
    assert c.step == 0.2 and c.edit_factor == 0.5
    assert c.min_events == 1


def test_feedback_result_shape():
    res = FeedbackResult(
        source_stats=[SourceFeedbackStats(source="src", keep=1, edit=0,
                                          drop=0, total=1)],
        quality_weights={"src": 1.2}, weight_diff={"src": (1.0, 1.2)},
        event_count=1, source_count=1, is_silent=False)
    assert res.quality_weights == {"src": 1.2}
    assert res.weight_diff["src"] == (1.0, 1.2)
    assert res.event_count == 1 and res.source_count == 1
    assert res.is_silent is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_feedback_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'FeedbackEvent'`

- [ ] **Step 3: Append the types to `src/core/types.py`**

Add at the very end of the file (after the `PublishResult` dataclass):

```python
# --- feedback layer (Circle 7) ---
class FeedbackEvent(BaseModel):
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    action: Literal["keep", "drop", "edit"]
    run_id: str = Field(min_length=1)
    ts: datetime                              # injected; layer never calls now()


class SourceFeedbackStats(BaseModel):
    source: str
    keep: int = 0
    edit: int = 0
    drop: int = 0
    total: int = 0


@dataclass
class FeedbackConfig:
    events_path: str = "data/feedback_events.json"
    weights_path: str = "data/quality_weights.json"
    baseline_weight: float = 1.0
    min_weight: float = 0.5
    max_weight: float = 1.5
    step: float = 0.2
    edit_factor: float = 0.5
    min_events: int = 1


@dataclass
class FeedbackResult:
    source_stats: list[SourceFeedbackStats]
    quality_weights: dict[str, float]
    weight_diff: dict[str, tuple[float, float]]
    event_count: int
    source_count: int
    is_silent: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_feedback_types.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/core/types.py tests/contract/test_feedback_types.py
git commit -m "feat(feedback): core types FeedbackEvent/Stats/Config/Result"
```

---

## Task 2: Config + ledger loaders (load_feedback_config / load_feedback_events / load_quality_weights) + config/feedback.yaml

**Files:**
- Modify: `src/core/config.py` (append at end; extend the existing `from src.core.types import (...)` line)
- Create: `config/feedback.yaml`
- Test: `tests/contract/test_feedback_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_feedback_config.py`:

```python
import json
import pytest
from pydantic import ValidationError
from src.core.config import (load_feedback_config, load_feedback_events,
                             load_quality_weights)
from src.core.types import FeedbackConfig


def test_load_feedback_config_missing_returns_defaults(tmp_path):
    cfg = load_feedback_config(str(tmp_path / "nope.yaml"))
    assert cfg == FeedbackConfig()


def test_load_feedback_config_overrides_fields(tmp_path):
    p = tmp_path / "feedback.yaml"
    p.write_text("step: 0.1\nmin_events: 3\nedit_factor: 0.25\n"
                 'events_path: "x/ev.json"\n', encoding="utf-8")
    cfg = load_feedback_config(str(p))
    assert cfg.step == 0.1 and cfg.min_events == 3
    assert cfg.edit_factor == 0.25 and cfg.events_path == "x/ev.json"
    # uncovered fields keep defaults
    assert cfg.baseline_weight == 1.0 and cfg.max_weight == 1.5


def test_load_feedback_events_missing_returns_empty(tmp_path):
    assert load_feedback_events(str(tmp_path / "none.json")) == []


def test_load_feedback_events_parses_and_validates(tmp_path):
    p = tmp_path / "ev.json"
    p.write_text(json.dumps([
        {"link": "https://a/1", "source": "s", "action": "keep",
         "run_id": "r1", "ts": "2026-05-30T12:00:00+00:00"}]),
        encoding="utf-8")
    evs = load_feedback_events(str(p))
    assert len(evs) == 1 and evs[0].action == "keep" and evs[0].source == "s"


def test_load_feedback_events_rejects_bad_action(tmp_path):
    p = tmp_path / "ev.json"
    p.write_text(json.dumps([
        {"link": "https://a/1", "source": "s", "action": "nope",
         "run_id": "r1", "ts": "2026-05-30T12:00:00+00:00"}]),
        encoding="utf-8")
    with pytest.raises(ValidationError):
        load_feedback_events(str(p))


def test_load_quality_weights_missing_returns_empty(tmp_path):
    assert load_quality_weights(str(tmp_path / "none.json")) == {}


def test_load_quality_weights_parses(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"src": 1.2, "other": 0.8}), encoding="utf-8")
    w = load_quality_weights(str(p))
    assert w == {"src": 1.2, "other": 0.8}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_feedback_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_feedback_config'`

- [ ] **Step 3: Create `config/feedback.yaml`**

```yaml
events_path: "data/feedback_events.json"   # JSON 事件账本(追加式)
weights_path: "data/quality_weights.json"  # 权重落账
baseline_weight: 1.0                        # 冷启动/未知源基准
min_weight: 0.5                             # 权重下夹界
max_weight: 1.5                             # 权重上夹界
step: 0.2                                    # 每轮步长
edit_factor: 0.5                            # edit 记作半个正向
min_events: 1                               # 样本下限, 不足不动权重
```

- [ ] **Step 4: Extend the import line in `src/core/config.py`**

The current first import is:

```python
from src.core.types import (DedupConfig, ScoringConfig, InterpretConfig,
                            ReviewConfig, ReviewDecision, PublishConfig)
```

Change it to add the feedback types:

```python
from src.core.types import (DedupConfig, ScoringConfig, InterpretConfig,
                            ReviewConfig, ReviewDecision, PublishConfig,
                            FeedbackConfig, FeedbackEvent)
```

- [ ] **Step 5: Append the loaders to `src/core/config.py`**

Add at the end of the file (after `load_publish_config`):

```python
def load_feedback_config(path: str) -> FeedbackConfig:
    """Load feedback formula coefficients / ledger paths from YAML;
    missing/empty file -> dataclass defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return FeedbackConfig()
    d = FeedbackConfig()
    return FeedbackConfig(
        events_path=data.get("events_path", d.events_path),
        weights_path=data.get("weights_path", d.weights_path),
        baseline_weight=data.get("baseline_weight", d.baseline_weight),
        min_weight=data.get("min_weight", d.min_weight),
        max_weight=data.get("max_weight", d.max_weight),
        step=data.get("step", d.step),
        edit_factor=data.get("edit_factor", d.edit_factor),
        min_events=data.get("min_events", d.min_events),
    )


def load_feedback_events(path: str) -> list[FeedbackEvent]:
    """读 JSON 事件账本(数组); 缺文件 -> []。
    每个元素过 FeedbackEvent 校验(非法 action 即抛 ValidationError)。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or []
    except FileNotFoundError:
        return []
    return [FeedbackEvent(**v) for v in raw]


def load_quality_weights(path: str) -> dict[str, float]:
    """读权重账本 JSON 对象 {source: float}; 缺文件 -> {}。只读不写。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_feedback_config.py -v`
Expected: PASS (7 passed)

- [ ] **Step 7: Commit**

```bash
git add src/core/config.py config/feedback.yaml tests/contract/test_feedback_config.py
git commit -m "feat(feedback): config + JSON ledger loaders"
```

---

## Task 3: Pure derive_events + aggregate_by_source

**Files:**
- Create: `src/pipeline/feedback.py`
- Test: `tests/golden/test_feedback.py` (created here; extended in Tasks 4–5)

- [ ] **Step 1: Write the failing test**

Create `tests/golden/test_feedback.py`:

```python
from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, FeedbackConfig)
from src.pipeline.feedback import derive_events, aggregate_by_source

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
CFG = FeedbackConfig()


def _ii(link="https://a/1", source="src", source_type=SourceType.MODEL):
    return InterpretedItem(
        title_en="X", link=link, source=source, source_type=source_type,
        published_at=NOW, raw_summary="A.", cluster_id="evt-1",
        related_links=[], score=80, score_breakdown={"机构影响力": 80.0},
        is_explore=False, title="标题", summary="摘要。", takeaway="用法。",
        hot_take="锐评。", tags=["#a"],
        evidence=[Evidence(claim="事实", anchor=link)],
        interpretation_status="ok", eligible_for_must_read=True)


def test_derive_events_includes_drop_and_default_keep():
    items = [_ii("https://a/1", source="s1"),
             _ii("https://a/2", source="s2"),
             _ii("https://a/3", source="s3")]
    decisions = {"https://a/1": ReviewDecision(action="drop"),
                 "https://a/2": ReviewDecision(action="edit")}
    evs = derive_events(items, decisions, run_id="r1", now=NOW)
    # 每条进审阅前条目恰产一个事件(被删的也在)
    assert len(evs) == 3
    by_link = {e.link: e for e in evs}
    assert by_link["https://a/1"].action == "drop"   # 删也回收
    assert by_link["https://a/2"].action == "edit"
    assert by_link["https://a/3"].action == "keep"   # 无决策默认 keep
    assert by_link["https://a/1"].source == "s1"
    assert all(e.run_id == "r1" and e.ts == NOW for e in evs)


def test_aggregate_by_source_counts_and_alpha_order():
    evs = derive_events(
        [_ii("https://a/1", source="b"), _ii("https://a/2", source="b"),
         _ii("https://a/3", source="a")],
        {"https://a/1": ReviewDecision(action="drop")},
        run_id="r1", now=NOW)
    stats = aggregate_by_source(evs)
    # source 字母序: a 在 b 前(与输入序无关)
    assert [s.source for s in stats] == ["a", "b"]
    a = [s for s in stats if s.source == "a"][0]
    b = [s for s in stats if s.source == "b"][0]
    assert a.keep == 1 and a.total == 1
    assert b.keep == 1 and b.drop == 1 and b.total == 2
    # 聚合不漏: 总 total == 事件数
    assert sum(s.total for s in stats) == len(evs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.feedback'`

- [ ] **Step 3: Create `src/pipeline/feedback.py` with the two pure functions**

```python
from __future__ import annotations
from datetime import datetime
from src.core.types import (InterpretedItem, ReviewDecision, FeedbackEvent,
                            SourceFeedbackStats, FeedbackConfig,
                            FeedbackResult, RunContext)
from src.observability.events import emit


def derive_events(items: list[InterpretedItem],
                  decisions: dict[str, ReviewDecision],
                  run_id: str, now: datetime) -> list[FeedbackEvent]:
    """从进审阅前全量条目派生事件; 无决策默认 keep; 带 source; ts 注入。
    遍历审阅前条目(非保留结果)→ 被删条目也产 drop 事件。"""
    out: list[FeedbackEvent] = []
    for it in items:
        dec = decisions.get(it.link)
        action = dec.action if dec is not None else "keep"
        out.append(FeedbackEvent(link=it.link, source=it.source,
                                 action=action, run_id=run_id, ts=now))
    return out


def aggregate_by_source(events: list[FeedbackEvent]
                        ) -> list[SourceFeedbackStats]:
    """按 source 聚合 keep/edit/drop/total; 输出按 source 字母序(确定性)。"""
    buckets: dict[str, dict[str, int]] = {}
    for e in events:
        b = buckets.setdefault(e.source, {"keep": 0, "edit": 0, "drop": 0})
        b[e.action] += 1
    out: list[SourceFeedbackStats] = []
    for source in sorted(buckets):
        b = buckets[source]
        out.append(SourceFeedbackStats(
            source=source, keep=b["keep"], edit=b["edit"], drop=b["drop"],
            total=b["keep"] + b["edit"] + b["drop"]))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/feedback.py tests/golden/test_feedback.py
git commit -m "feat(feedback): pure derive_events + aggregate_by_source"
```

---

## Task 4: Pure compute_quality_weights

**Files:**
- Modify: `src/pipeline/feedback.py` (add `compute_quality_weights` + a `_clamp` helper)
- Test: `tests/golden/test_feedback.py` (append cases)

- [ ] **Step 1: Append the failing tests**

Add to `tests/golden/test_feedback.py` (the imports `aggregate_by_source` is already imported; add `compute_quality_weights` to the existing import from `src.pipeline.feedback`):

Change the import line to:

```python
from src.pipeline.feedback import (derive_events, aggregate_by_source,
                                   compute_quality_weights)
```

Then append:

```python
def _stats(source, keep=0, edit=0, drop=0):
    return SourceFeedbackStats(source=source, keep=keep, edit=edit, drop=drop,
                               total=keep + edit + drop)


def test_compute_all_keep_raises_weight():
    stats = [_stats("a", keep=3)]
    w, diff = compute_quality_weights(stats, {}, CFG)
    # 冷启动 baseline 1.0; 全 keep → 升; 夹界内
    assert w["a"] == 1.2                       # 1.0 + 0.2*(1) = 1.2
    assert diff["a"] == (1.0, 1.2)
    assert CFG.min_weight <= w["a"] <= CFG.max_weight


def test_compute_all_drop_lowers_weight():
    stats = [_stats("b", drop=3)]
    w, diff = compute_quality_weights(stats, {}, CFG)
    assert w["b"] == 0.8                        # 1.0 + 0.2*(-1) = 0.8
    assert diff["b"] == (1.0, 0.8)


def test_compute_edit_is_half_positive():
    stats = [_stats("e", edit=4)]
    w, _ = compute_quality_weights(stats, {}, CFG)
    assert w["e"] == 1.1                        # 1.0 + 0.2*(0.5) = 1.1
    # edit 升幅 < 全 keep 升幅
    assert w["e"] < 1.2


def test_compute_clamp_upper_bound():
    stats = [_stats("c", keep=5)]
    w, _ = compute_quality_weights(stats, {"c": 1.45}, CFG)
    # 1.45 + 0.2 = 1.65 → 夹到 1.5
    assert w["c"] == 1.5


def test_compute_clamp_lower_bound():
    stats = [_stats("d", drop=5)]
    w, _ = compute_quality_weights(stats, {"d": 0.55}, CFG)
    # 0.55 - 0.2 = 0.35 → 夹到 0.5
    assert w["d"] == 0.5


def test_compute_insufficient_sample_unchanged():
    cfg = FeedbackConfig(min_events=2)
    stats = [_stats("f", keep=1)]               # total=1 < 2
    w, diff = compute_quality_weights(stats, {"f": 1.3}, cfg)
    assert w["f"] == 1.3                         # 不动
    assert diff["f"] == (1.3, 1.3)


def test_compute_preserves_unseen_prior_sources():
    stats = [_stats("a", keep=2)]
    w, diff = compute_quality_weights(stats, {"a": 1.0, "g": 0.9}, CFG)
    # 本轮没出现的 g 原样保留, 但不进 diff
    assert w["g"] == 0.9
    assert "g" not in diff
```

Also ensure `SourceFeedbackStats` is imported at the top of the test file. Update the type import line to:

```python
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, FeedbackConfig, SourceFeedbackStats)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_quality_weights'`

- [ ] **Step 3: Add the function to `src/pipeline/feedback.py`**

Insert after `aggregate_by_source` (before any orchestrator):

```python
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_quality_weights(
        stats: list[SourceFeedbackStats],
        prior_weights: dict[str, float],
        config: FeedbackConfig
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """增量更新每源权重: 留升/删降/改半正; 样本不足不动; 夹界 [min,max]。
    本轮未出现的 prior 源原样保留进结果但不进 diff。"""
    weights: dict[str, float] = dict(prior_weights)   # 历史不丢
    diff: dict[str, tuple[float, float]] = {}
    for s in stats:
        old = prior_weights.get(s.source, config.baseline_weight)
        if s.total < config.min_events:
            new = old
        else:
            kr = s.keep / s.total
            er = s.edit / s.total
            dr = s.drop / s.total
            raw = old + config.step * (kr + config.edit_factor * er - dr)
            new = _clamp(raw, config.min_weight, config.max_weight)
        new = round(new, 10)                          # 抹去浮点尾噪, 确定性
        weights[s.source] = new
        diff[s.source] = (old, new)
    return weights, diff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: PASS (9 passed). If a float equality assertion is off by floating dust, the `round(new, 10)` already handles the documented cases (1.2/0.8/1.1/1.5/0.5).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/feedback.py tests/golden/test_feedback.py
git commit -m "feat(feedback): pure compute_quality_weights (incremental, clamped)"
```

---

## Task 5: feedback() orchestration + golden invariants

**Files:**
- Modify: `src/pipeline/feedback.py` (add `feedback`)
- Test: `tests/golden/test_feedback.py` (append orchestration + determinism + silent cases)

- [ ] **Step 1: Append the failing tests**

Add to the top of `tests/golden/test_feedback.py` the orchestrator + context imports:

```python
import logging
from src.core.types import RunContext, FeedbackResult
from src.pipeline.feedback import feedback
```

(Place these with the other imports.) Then append:

```python
def _ctx():
    return RunContext(run_id="g", now=NOW,
                      logger=logging.getLogger("golden-feedback"))


def _events(*specs):
    # specs: (source, action) tuples
    return [FeedbackEvent(link=f"https://a/{i}", source=src, action=act,
                          run_id="r1", ts=NOW)
            for i, (src, act) in enumerate(specs)]


def test_feedback_empty_input_silent():
    res = feedback([], {"x": 1.2}, CFG, _ctx())
    assert res.is_silent is True
    assert res.quality_weights == {"x": 1.2}     # 原样透传
    assert res.weight_diff == {} and res.event_count == 0
    assert res.source_count == 0 and res.source_stats == []


def test_feedback_assembles_result():
    evs = _events(("a", "keep"), ("a", "keep"), ("b", "drop"))
    res = feedback(evs, {}, CFG, _ctx())
    assert res.event_count == 3 and res.source_count == 2
    assert res.is_silent is False
    # 计数自洽 + 聚合守恒
    assert sum(s.total for s in res.source_stats) == res.event_count
    assert res.quality_weights["a"] > 1.0        # 全 keep 升
    assert res.quality_weights["b"] < 1.0        # 全 drop 降
    # 夹界
    for v in res.quality_weights.values():
        assert CFG.min_weight <= v <= CFG.max_weight


def test_feedback_deterministic_order_independent():
    e1 = _events(("a", "keep"), ("b", "drop"), ("a", "edit"))
    e2 = _events(("a", "edit"), ("a", "keep"), ("b", "drop"))  # 打乱
    r1 = feedback(e1, {}, CFG, _ctx())
    r2 = feedback(e2, {}, CFG, _ctx())
    assert r1.quality_weights == r2.quality_weights
    assert r1.weight_diff == r2.weight_diff
    assert [s.model_dump() for s in r1.source_stats] == \
           [s.model_dump() for s in r2.source_stats]
```

Also import `FeedbackEvent` in the test type-import line:

```python
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, FeedbackConfig, SourceFeedbackStats,
                            FeedbackEvent, RunContext, FeedbackResult)
```

(Remove the now-duplicate `from src.core.types import RunContext, FeedbackResult` line you added above — keep a single consolidated import.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: FAIL with `ImportError: cannot import name 'feedback'`

- [ ] **Step 3: Add the orchestrator to `src/pipeline/feedback.py`**

Append at the end of the file:

```python
def feedback(events: list[FeedbackEvent], prior_weights: dict[str, float],
             config: FeedbackConfig, ctx: RunContext) -> FeedbackResult:
    """编排: 聚合 → 增量算权重 → 组装结果。空事件→静默, 权重原样透传。
    无网络/LLM/落盘副作用。"""
    emit(ctx.logger, "feedback_start", run_id=ctx.run_id,
         event_count=len(events))
    if not events:
        emit(ctx.logger, "feedback_done", event_count=0, source_count=0,
             silent=True)
        return FeedbackResult(
            source_stats=[], quality_weights=dict(prior_weights),
            weight_diff={}, event_count=0, source_count=0, is_silent=True)
    stats = aggregate_by_source(events)
    weights, diff = compute_quality_weights(stats, prior_weights, config)
    changed = sum(1 for old, new in diff.values() if old != new)
    emit(ctx.logger, "weights_computed", source_count=len(stats),
         changed_count=changed)
    emit(ctx.logger, "feedback_done", event_count=len(events),
         source_count=len(stats), silent=False)
    return FeedbackResult(
        source_stats=stats, quality_weights=weights, weight_diff=diff,
        event_count=len(events), source_count=len(stats), is_silent=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_feedback.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/feedback.py tests/golden/test_feedback.py
git commit -m "feat(feedback): feedback() orchestration + golden invariants"
```

---

## Task 6: CLI --feedback chain

**Files:**
- Modify: `src/cli.py` (add imports, `run_dry_feedback`, `--feedback` flag + dispatch)
- Test: `tests/contract/test_cli_feedback.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_cli_feedback.py`:

```python
import json
from datetime import datetime, timezone
from src.cli import run_dry_feedback
from tests.fakes import FakeEmbeddingProvider, FailingLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_feedback_shape():
    out = run_dry_feedback(
        registry_path="tests/golden/data/registry_min.yaml", now=NOW,
        embedder=FakeEmbeddingProvider({}), llm=FailingLLMProvider(),
        decisions_path="tests/golden/data/__no_such_decisions__.json")
    for k in ("run_id", "now", "event_count", "source_count",
              "is_silent", "quality_weights", "weight_diff"):
        assert k in out
    assert isinstance(out["quality_weights"], dict)
    assert isinstance(out["is_silent"], bool)
    # JSON 可序列化(weight_diff 的 tuple 会序列化成数组)
    json.dumps(out, ensure_ascii=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_cli_feedback.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_feedback'`

- [ ] **Step 3: Add imports to `src/cli.py`**

After the existing line `from src.pipeline.publish import publish` (line 19), add:

```python
from src.core.config import (load_feedback_config, load_feedback_events,
                             load_quality_weights)
from src.pipeline.feedback import derive_events, feedback
```

- [ ] **Step 4: Add `run_dry_feedback` to `src/cli.py`**

Insert after `run_dry_publish` (after its `return {...}` block, before `def main`):

```python
def run_dry_feedback(registry_path: str, now: datetime | None = None,
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

    fcfg = load_feedback_config("config/feedback.yaml")
    # 本轮事件从"进审阅前"全量条目派生(被删条目也回收), 并入历史账本
    run_events = derive_events(ires.interpreted_items, decisions,
                               run_id=ctx.run_id, now=now)
    history = load_feedback_events(fcfg.events_path)
    prior = load_quality_weights(fcfg.weights_path)
    fres = feedback(history + run_events, prior, fcfg, ctx)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "event_count": fres.event_count,
        "source_count": fres.source_count,
        "is_silent": fres.is_silent,
        "quality_weights": fres.quality_weights,
        "weight_diff": {k: list(v) for k, v in fres.weight_diff.items()},
    }
```

- [ ] **Step 5: Add the `--feedback` flag in `main`**

After the `--publish` argument (line 247-248), add:

```python
    p.add_argument("--feedback", action="store_true",
                   help="chain collect -> ... -> review -> feedback, print quality_weights JSON")
```

- [ ] **Step 6: Add the dispatch branch in `main`**

Insert immediately after `if not args.dry_run:` block and BEFORE the `--publish` branch (so it is the first checked, matching the "latest layer first" ordering already used):

```python
    if args.dry_run and args.feedback:
        out = run_dry_feedback(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_cli_feedback.py -v`
Expected: PASS (1 passed)

- [ ] **Step 8: Commit**

```bash
git add src/cli.py tests/contract/test_cli_feedback.py
git commit -m "feat(feedback): --feedback CLI chain (dry-run prints weights + diff)"
```

---

## Task 7: Full suite green + ROADMAP update

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — all prior tests (186) plus the new feedback tests (4 + 7 + 12 + 1 = 24) green. Total ≈ 210 passed. If anything fails, fix before continuing — do not edit the ROADMAP on red.

- [ ] **Step 2: Verify the CLI runs end-to-end (no network needed with min registry)**

Run: `uv run python -m src.cli --dry-run --feedback --registry tests/golden/data/registry_min.yaml`
Expected: prints a JSON object with `quality_weights` and `weight_diff` keys, exit 0. (With the failing/empty upstream it may be `is_silent: true` and empty weights — that is correct, not an error.)

- [ ] **Step 3: Update `docs/ROADMAP.md`**

Open `docs/ROADMAP.md` and make these edits (match the exact style used for Circle 6):

1. Update the header date to `2026-06-03` and note Circle 7 feedback 已合并.
2. In the mermaid diagram, mark the feedback node done: change its class to `:::done` (e.g. `C7:::done`).
3. In the layer status table, change row ⑦ (feedback) status to `🟩 已合并 (master)` and reference `docs/specs/feedback.md` + `src/pipeline/feedback.py` + `--dry-run --feedback`.
4. In the doc map, mark `S7: feedback.md ✅` and `P7: 2026-06-03-feedback-layer.md ✅`.
5. In §5 下一步, note the 7-layer MVP loop is complete; next is P1 items (multi-channel publish, feedback→scoring wiring via ADR, SQLite/Qdrant persistence).
6. Add a "已完成（Circle 7 · feedback）" note mirroring the Circle 6 entry.

(The exact current wording lives in the file; read it first, then apply the equivalent edits. Do not invent table columns that don't exist — match the existing schema.)

- [ ] **Step 4: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: ROADMAP — Circle 7 feedback merged; 7-layer MVP loop complete"
```

---

## Self-Review (completed by plan author)

**1. Spec coverage** (against `docs/specs/feedback.md`):
- §3 interface `feedback()` + 3 helpers → Tasks 3–5. ✅
- §4 data contract (FeedbackEvent/SourceFeedbackStats/FeedbackConfig/FeedbackResult) → Task 1. ✅
- §5.1 empty→silent → Task 5 `test_feedback_empty_input_silent`. ✅
- §5.2 derive (pre-review, default keep, drop visible) → Task 3. ✅
- §5.3 aggregate (alpha order, counts) → Task 3. ✅
- §5.4 weight formula (keep up / drop down / edit half / clamp / insufficient) → Task 4 (6 tests). ✅
- §5.5 orchestration → Task 5. ✅
- §6.1 config/feedback.yaml + FeedbackConfig defaults → Tasks 1, 2. ✅
- §6.2 ledger loaders (missing→empty, bad action raises) → Task 2. ✅
- §7 fallbacks (no decision→keep, prior missing→baseline, clamp) → Tasks 3–4 tests. ✅
- §8 invariants 1–12 → covered across Tasks 3–5 golden tests. ✅
- §10 contract + golden test split → Tasks 1,2,6 (contract) + 3,4,5 (golden). ✅
- §11 events (feedback_start/weights_computed/feedback_done) → Task 5 orchestrator. ✅
- §12 acceptance: --dry-run --feedback end-to-end → Task 6 + Task 7 step 2. ✅

**2. Placeholder scan:** No TBD/TODO/"similar to"/"add validation" — every code step has complete code. ✅

**3. Type consistency:** `FeedbackEvent(link, source, action, run_id, ts)`, `SourceFeedbackStats(source, keep, edit, drop, total)`, `FeedbackConfig(events_path, weights_path, baseline_weight, min_weight, max_weight, step, edit_factor, min_events)`, `FeedbackResult(source_stats, quality_weights, weight_diff, event_count, source_count, is_silent)` — used identically in types, loaders, functions, CLI, and tests. Function names `derive_events`/`aggregate_by_source`/`compute_quality_weights`/`feedback` consistent throughout. ✅
