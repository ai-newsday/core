# 审阅层 (Review) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现七层流水线第 5 层"审阅层"的核心：把解读层产出的 `InterpretedItem` 列表，按人工"留/删/改/排序"决策转成可发布的 `ReviewedItem`，并把审阅动作回收成反馈信号。

**Architecture:** 纯函数 + 注入数据。`review(items, daily_take, decisions, config, ctx) -> ReviewResult` 不调 LLM、不打网络；唯一 IO 是启动时读决策 JSON（`load_review_decisions`）。决策应用（`apply_decision`）、排序（`order_reviewed`）、必读门重算全是纯函数，注入冻结 fixtures + 内存 decisions 即可离线确定性测。本圈只做核心逻辑，Web 审阅页延后。

**Tech Stack:** Python 3.12（uv 管理）、pydantic v2、PyYAML、pytest。**运行测试一律 `uv run pytest`**（裸 `pytest` 会选到错误的 miniconda 3.13 解释器导致 import 失败）。基线分支 master，本圈在 feature 分支 `circle5-review` 上做。

**Spec:** `docs/specs/review.md`（12 节，golden 用例 §9、不变量 §8）。

---

## 前置：开 feature 分支

CLAUDE.md「不在 master 上直接实现」。执行第一个任务前先：

```bash
git checkout -b circle5-review
git rev-parse --abbrev-ref HEAD   # 期望输出: circle5-review
```

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/core/types.py` | 追加 `ReviewDecision` / `ReviewedItem` / `ReviewConfig` / `ReviewResult` | Modify（在文件末尾 interpret 段之后追加 `# --- review layer (Circle 5) ---`） |
| `src/core/config.py` | 追加 `load_review_config` + `load_review_decisions` | Modify |
| `config/review.yaml` | 审阅层字段上限 / 决策路径 | Create |
| `src/pipeline/review.py` | 纯函数 `apply_decision` / `order_reviewed` + orchestrator `review()` | Create |
| `src/cli.py` | 追加 `run_dry_review` + `--review` 分支 | Modify |
| `tests/contract/test_review_types.py` | 类型 schema 契约测 | Create |
| `tests/contract/test_review_config.py` | 配置 + 决策加载契约测 | Create |
| `tests/golden/test_review.py` | §9 的 9 个 golden 用例 | Create |

依赖顺序：Task 1（类型）→ Task 2（配置/加载）→ Task 3（纯函数 apply/order）→ Task 4（orchestrator + golden）→ Task 5（CLI）→ Task 6（全套件 + ROADMAP）。

---

## Task 1: 核心类型（ReviewDecision / ReviewedItem / ReviewConfig / ReviewResult）

**Files:**
- Modify: `src/core/types.py`（末尾追加，现有最后一行是 `InterpretResult` 的 `is_silent: bool`，第 205 行附近）
- Test: `tests/contract/test_review_types.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/contract/test_review_types.py`：

```python
import logging
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, ReviewedItem, ReviewConfig,
                            ReviewResult)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _interp(**over):
    base = dict(title_en="GLM-5 released", link="https://hf.co/glm5",
                source="Hugging Face", source_type=SourceType.MODEL,
                published_at=NOW, raw_summary="MoE open weights model.",
                cluster_id="evt-1", related_links=["https://blog/glm5"],
                score=88, score_breakdown={"机构影响力": 88.0}, is_explore=False,
                title="智谱发布 GLM-5", summary="开源 MoE 模型。",
                takeaway="可自建推理。", hot_take="护城河又薄了。",
                tags=["#开源", "#MoE", "#GLM"],
                evidence=[Evidence(claim="MoE", anchor="https://hf.co/glm5")],
                interpretation_status="ok", eligible_for_must_read=True)
    base.update(over)
    return InterpretedItem(**base)


def test_review_config_defaults():
    c = ReviewConfig()
    assert c.decisions_path == "data/review_decisions.json"
    assert c.title_max_chars == 64 and c.summary_max_chars == 120
    assert c.tags_count == 3 and c.min_evidence == 1


def test_review_decision_defaults_and_enum():
    d = ReviewDecision()
    assert d.action == "keep" and d.order is None and d.edits == {}
    e = ReviewDecision(action="edit", order=2, edits={"title": "新标题"})
    assert e.action == "edit" and e.order == 2 and e.edits["title"] == "新标题"


def test_review_decision_rejects_unknown_action():
    with pytest.raises(ValidationError):
        ReviewDecision(action="frobnicate")


def test_reviewed_item_extends_interpreted_item():
    it = _interp()
    r = ReviewedItem(**it.model_dump(), review_action="keep",
                     was_edited=False, edited_fields=[])
    # 继承上游不变量
    assert r.score == 88 and r.cluster_id == "evt-1"
    assert r.interpretation_status == "ok"
    assert r.review_action == "keep" and r.was_edited is False
    assert r.edited_fields == []


def test_reviewed_item_edited_fields_recorded():
    it = _interp()
    r = ReviewedItem(**it.model_dump(), review_action="edit",
                     was_edited=True, edited_fields=["title", "summary"])
    assert r.review_action == "edit" and r.was_edited is True
    assert r.edited_fields == ["title", "summary"]


def test_review_result_shape():
    res = ReviewResult(reviewed_items=[], daily_take=None, input_count=0,
                       kept_count=0, dropped_count=0, edited_count=0,
                       is_reviewed=False, is_pending=True, is_silent=True)
    assert res.is_silent is True and res.is_pending is True
    assert res.is_reviewed is False and res.daily_take is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_review_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReviewDecision'`

- [ ] **Step 3: 追加类型实现**

在 `src/core/types.py` 末尾（`InterpretResult` 之后）追加：

```python
# --- review layer (Circle 5) ---
class ReviewDecision(BaseModel):
    action: Literal["keep", "drop", "edit"] = "keep"
    order: int | None = None                 # 重排序号(升序); None=不指定
    edits: dict = Field(default_factory=dict)  # action==edit 时覆盖的字段


class ReviewedItem(InterpretedItem):         # InterpretedItem 的下游演进
    review_action: Literal["keep", "edit"]   # drop 的条目不进结果
    was_edited: bool
    edited_fields: list[str] = Field(default_factory=list)


@dataclass
class ReviewConfig:
    decisions_path: str = "data/review_decisions.json"
    title_max_chars: int = 64
    summary_max_chars: int = 120
    tags_count: int = 3
    min_evidence: int = 1


@dataclass
class ReviewResult:
    reviewed_items: list[ReviewedItem]
    daily_take: str | None
    input_count: int
    kept_count: int
    dropped_count: int
    edited_count: int
    is_reviewed: bool
    is_pending: bool
    is_silent: bool
```

> 注：`Literal` 和 `dataclass`、`Field` 已在文件顶部 import（第 2、5、7 行），无需新增 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_review_types.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/core/types.py tests/contract/test_review_types.py
git commit -m "feat(review): core types — ReviewDecision/ReviewedItem/ReviewConfig/ReviewResult"
```

---

## Task 2: 配置加载（load_review_config + load_review_decisions + config/review.yaml）

**Files:**
- Modify: `src/core/config.py`（顶部 import 行 + 末尾追加两个函数）
- Create: `config/review.yaml`
- Test: `tests/contract/test_review_config.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/contract/test_review_config.py`：

```python
import json
import pytest
from pydantic import ValidationError
from src.core.config import load_review_config, load_review_decisions
from src.core.types import ReviewConfig, ReviewDecision


def test_load_review_config_missing_returns_defaults(tmp_path):
    cfg = load_review_config(str(tmp_path / "nope.yaml"))
    assert cfg == ReviewConfig()


def test_load_review_config_overrides_fields(tmp_path):
    p = tmp_path / "review.yaml"
    p.write_text("title_max_chars: 40\nmin_evidence: 2\n"
                 'decisions_path: "x/y.json"\n', encoding="utf-8")
    cfg = load_review_config(str(p))
    assert cfg.title_max_chars == 40 and cfg.min_evidence == 2
    assert cfg.decisions_path == "x/y.json"
    # 未覆盖字段保持默认
    assert cfg.summary_max_chars == 120 and cfg.tags_count == 3


def test_load_review_decisions_missing_returns_empty(tmp_path):
    assert load_review_decisions(str(tmp_path / "nope.json")) == {}


def test_load_review_decisions_parses_keyed_by_link(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "https://a/1": {"action": "drop"},
        "https://a/2": {"action": "edit", "order": 0,
                        "edits": {"title": "新标题"}},
        "__daily_take__": {"action": "edit",
                           "edits": {"daily_take": "人工看点"}},
    }), encoding="utf-8")
    out = load_review_decisions(str(p))
    assert set(out) == {"https://a/1", "https://a/2", "__daily_take__"}
    assert isinstance(out["https://a/1"], ReviewDecision)
    assert out["https://a/1"].action == "drop"
    assert out["https://a/2"].order == 0
    assert out["https://a/2"].edits["title"] == "新标题"


def test_load_review_decisions_rejects_unknown_action(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"https://a/1": {"action": "zap"}}),
                 encoding="utf-8")
    with pytest.raises(ValidationError):
        load_review_decisions(str(p))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_review_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_review_config'`

- [ ] **Step 3: 实现加载器**

`src/core/config.py` 顶部 import（第 3 行）改为追加新类型：

```python
from src.core.types import (DedupConfig, ScoringConfig, InterpretConfig,
                            ReviewConfig, ReviewDecision)
```

文件末尾追加：

```python
def load_review_config(path: str) -> ReviewConfig:
    """Load review field limits / decisions path from YAML; missing -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return ReviewConfig()
    d = ReviewConfig()
    return ReviewConfig(
        decisions_path=data.get("decisions_path", d.decisions_path),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
    )


def load_review_decisions(path: str) -> dict[str, ReviewDecision]:
    """Read审阅决策 JSON(按 link 索引); 缺文件 -> {}(全 keep/待审).
    每个 value 过 ReviewDecision 校验(非法 action 即抛 ValidationError)。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}
    except FileNotFoundError:
        return {}
    return {k: ReviewDecision(**v) for k, v in raw.items()}
```

- [ ] **Step 4: 创建 config/review.yaml**

创建 `config/review.yaml`：

```yaml
decisions_path: "data/review_decisions.json"  # 决策表; 缺则全 keep/待审
title_max_chars: 64        # 与解读层一致
summary_max_chars: 120     # 与解读层一致
tags_count: 3              # 一致性参考(本层不因 tags 数强制回退)
min_evidence: 1            # 必读门: 至少 1 条证据
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_review_config.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: 提交**

```bash
git add src/core/config.py config/review.yaml tests/contract/test_review_config.py
git commit -m "feat(review): config + decisions loaders, config/review.yaml"
```

---

## Task 3: 纯函数 apply_decision + order_reviewed

**Files:**
- Create: `src/pipeline/review.py`
- Test: `tests/golden/test_review.py`（本任务先建文件 + 写纯函数级用例；orchestrator 用例在 Task 4 补）

实现 spec §5.2–§5.5、§5.8：单条决策应用（keep/drop/edit）、edit 后重夹长度 + 过滤证据 + 重算必读门、排序。

- [ ] **Step 1: 写失败测试**

创建 `tests/golden/test_review.py`：

```python
from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, ReviewConfig)
from src.pipeline.review import apply_decision, order_reviewed

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _interp(link="https://a/1", status="ok", title="中文标题",
            summary="中文摘要。", takeaway="怎么用。", tags=None,
            evidence=None, related=None, score=80, eligible=True):
    return InterpretedItem(
        title_en="X released", link=link, source="src",
        source_type=SourceType.MODEL, published_at=NOW,
        raw_summary="A summary.", cluster_id="evt-1",
        related_links=related or [], score=score,
        score_breakdown={"机构影响力": float(score)}, is_explore=False,
        title=title, summary=summary, takeaway=takeaway, hot_take="锐评。",
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [
            Evidence(claim="事实", anchor=link)],
        interpretation_status=status, eligible_for_must_read=eligible)


CFG = ReviewConfig()


def test_apply_keep_passthrough():
    it = _interp()
    r = apply_decision(it, ReviewDecision(action="keep"), CFG)
    assert r is not None
    assert r.review_action == "keep" and r.was_edited is False
    assert r.edited_fields == [] and r.title == "中文标题"


def test_apply_drop_returns_none():
    it = _interp()
    assert apply_decision(it, ReviewDecision(action="drop"), CFG) is None


def test_apply_edit_overrides_and_records_fields():
    it = _interp()
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"title": "改后标题", "hot_take": "新锐评"}), CFG)
    assert r.review_action == "edit" and r.was_edited is True
    assert set(r.edited_fields) == {"title", "hot_take"}
    assert r.title == "改后标题" and r.hot_take == "新锐评"
    # 未改字段保留原值
    assert r.summary == "中文摘要。"


def test_apply_edit_reclamps_title_and_summary():
    it = _interp()
    long_title = "标" * 100
    long_summary = "要" * 200
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"title": long_title, "summary": long_summary}),
        CFG)
    assert len(r.title) == CFG.title_max_chars
    assert len(r.summary) == CFG.summary_max_chars


def test_apply_edit_provenance_readonly():
    it = _interp(link="https://a/1", score=80)
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"score": 5, "link": "https://evil/x",
                              "title": "改后"}), CFG)
    # 出处字段被忽略, 恒等上游
    assert r.score == 80 and r.link == "https://a/1"
    assert r.title == "改后"
    assert "score" not in r.edited_fields and "link" not in r.edited_fields


def test_apply_edit_drops_illegal_anchor():
    it = _interp(link="https://a/1", related=["https://r/1"])
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"evidence": [{"claim": "x", "anchor": "https://evil/x"}]}), CFG)
    assert r.evidence == []
    assert r.eligible_for_must_read is False


def test_apply_edit_recomputes_gate_true():
    it = _interp(eligible=False)
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"takeaway": "可操作",
               "evidence": [{"claim": "事实", "anchor": "https://a/1"}]}), CFG)
    assert r.eligible_for_must_read is True


def test_apply_edit_cannot_whitewash_fallback():
    it = _interp(status="extractive_fallback", takeaway="", evidence=[],
                 eligible=False)
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"takeaway": "硬补", "evidence": [
            {"claim": "事实", "anchor": "https://a/1"}]}), CFG)
    assert r.interpretation_status == "extractive_fallback"
    assert r.eligible_for_must_read is False


def test_apply_edit_empty_edits_not_edited():
    it = _interp()
    r = apply_decision(it, ReviewDecision(action="edit", edits={}), CFG)
    assert r.review_action == "edit" and r.was_edited is False
    assert r.edited_fields == []


def test_order_reviewed_respects_order_then_upstream():
    a = _interp(link="https://a/1")   # upstream index 0
    b = _interp(link="https://a/2")   # upstream index 1
    c = _interp(link="https://a/3")   # upstream index 2
    items = [a, b, c]
    decisions = {"https://a/1": ReviewDecision(order=1),
                 "https://a/2": ReviewDecision(order=0)}
    ordered = order_reviewed(items, decisions)
    # a2(order0), a1(order1), 然后无 order 的 a3 保持上游序
    assert [i.link for i in ordered] == ["https://a/2", "https://a/1",
                                         "https://a/3"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/golden/test_review.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_decision'`

- [ ] **Step 3: 实现纯函数**

创建 `src/pipeline/review.py`：

```python
from __future__ import annotations
from src.core.types import (InterpretedItem, ReviewedItem, ReviewDecision,
                            ReviewConfig, Evidence, RunContext, ReviewResult)
from src.observability.events import emit

# edit 只允许覆盖这些内容字段; 其余(出处)字段只读
EDITABLE_FIELDS = ("title", "summary", "takeaway", "hot_take", "tags", "evidence")


def _filter_evidence(raw_evidence, item: InterpretedItem) -> list[Evidence]:
    """保留 anchor ∈ link ∪ related_links 的证据; 非法锚点丢弃(不编造)。"""
    allowed = {item.link, *item.related_links}
    out: list[Evidence] = []
    for e in raw_evidence or []:
        if isinstance(e, Evidence):
            claim, anchor = e.claim, e.anchor
        elif isinstance(e, dict):
            claim = str(e.get("claim", "")).strip()
            anchor = str(e.get("anchor", "")).strip()
        else:
            continue
        if claim and anchor in allowed:
            out.append(Evidence(claim=claim, anchor=anchor))
    return out


def _gate(status: str, evidence: list[Evidence], takeaway: str,
          config: ReviewConfig) -> bool:
    """必读门(spec §5.8); 与解读层 §5.4 同式。status 只读, 回退条目洗不白。"""
    return (status == "ok"
            and len(evidence) >= config.min_evidence
            and takeaway != "")


def apply_decision(item: InterpretedItem, decision: ReviewDecision,
                   config: ReviewConfig) -> ReviewedItem | None:
    """单条决策应用(spec §5.2–§5.4, §5.8)。drop -> None; keep/edit -> ReviewedItem。"""
    if decision.action == "drop":
        return None
    base = item.model_dump()
    if decision.action != "edit":
        return ReviewedItem(**base, review_action="keep", was_edited=False,
                            edited_fields=[])
    # edit: 只覆盖可改字段, 记录实际改动
    edited_fields: list[str] = []
    for key in EDITABLE_FIELDS:
        if key in decision.edits:
            base[key] = decision.edits[key]
            edited_fields.append(key)
    # 改后重新校验
    base["title"] = str(base["title"])[:config.title_max_chars]
    base["summary"] = str(base["summary"])[:config.summary_max_chars]
    base["evidence"] = _filter_evidence(base.get("evidence"), item)
    base["eligible_for_must_read"] = _gate(
        base["interpretation_status"], base["evidence"], base["takeaway"], config)
    return ReviewedItem(**base, review_action="edit",
                        was_edited=bool(edited_fields),
                        edited_fields=edited_fields)


def order_reviewed(items: list[ReviewedItem],
                   decisions: dict[str, ReviewDecision]) -> list[ReviewedItem]:
    """排序(spec §5.5): 有 order 的按 order 升序在前, 无 order 的保持上游序。
    稳定排序键 (无order=1/有order=0, order值, 上游下标)。"""
    indexed = list(enumerate(items))

    def key(pair):
        idx, it = pair
        dec = decisions.get(it.link)
        if dec is not None and dec.order is not None:
            return (0, dec.order, idx)
        return (1, 0, idx)

    return [it for _, it in sorted(indexed, key=key)]
```

> 注：`ReviewResult` / `RunContext` / `emit` 在 Task 4 的 `review()` 用到，本步先 import 备用（import 多余不报错；若 lint 严格可在 Task 4 再加）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/golden/test_review.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/review.py tests/golden/test_review.py
git commit -m "feat(review): pure apply_decision + order_reviewed (keep/drop/edit, re-gate, sort)"
```

---

## Task 4: review() orchestrator + golden 用例（spec §9）

**Files:**
- Modify: `src/pipeline/review.py`（追加 `review()` orchestrator）
- Modify: `tests/golden/test_review.py`（追加 §9 的 orchestrator 级用例）

实现 spec §5.1（空输入短路）、§5.6（daily_take 覆盖）、§5.7（已审/待审）、§3 计数、§11 事件。

- [ ] **Step 1: 写失败测试**

在 `tests/golden/test_review.py` 末尾追加（顶部 import 增补 `review`, `ReviewResult`, `RunContext`, `logging`）：

```python
import logging
from src.core.types import ReviewResult, RunContext
from src.pipeline.review import review


def _ctx():
    return RunContext(run_id="g", now=NOW,
                      logger=logging.getLogger("golden-review"))


# Case 1 (§9.1): 全透传待审
def test_golden_passthrough_pending():
    items = [_interp("https://a/1"), _interp("https://a/2")]
    res = review(items, "看点。", {}, CFG, _ctx())
    assert res.is_reviewed is False and res.is_pending is True
    assert res.is_silent is False
    assert all(r.review_action == "keep" for r in res.reviewed_items)
    assert [r.link for r in res.reviewed_items] == ["https://a/1", "https://a/2"]
    assert res.kept_count == 2 and res.dropped_count == 0
    assert res.daily_take == "看点。"


# Case 2 (§9.2): 删除生效 + 账目守恒
def test_golden_drop_removes_and_counts():
    items = [_interp("https://a/1"), _interp("https://a/2")]
    decisions = {"https://a/1": ReviewDecision(action="drop")}
    res = review(items, None, decisions, CFG, _ctx())
    assert [r.link for r in res.reviewed_items] == ["https://a/2"]
    assert res.dropped_count == 1 and res.kept_count == 1
    assert (res.kept_count + res.edited_count + res.dropped_count
            == res.input_count == 2)
    assert res.is_reviewed is True and res.is_pending is False


# Case 3 (§9.3): 改写 + 重夹 + 重算门
def test_golden_edit_reclamp_and_gate():
    items = [_interp("https://a/1", eligible=False, takeaway="")]
    decisions = {"https://a/1": ReviewDecision(
        action="edit", edits={"title": "标" * 100, "takeaway": "可操作",
                              "evidence": [{"claim": "事实",
                                            "anchor": "https://a/1"}]})}
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert len(one.title) == CFG.title_max_chars
    assert one.eligible_for_must_read is True
    assert one.review_action == "edit" and res.edited_count == 1


# Case 4 (§9.4): 改写不能洗白回退
def test_golden_edit_cannot_whitewash_fallback():
    items = [_interp("https://a/1", status="extractive_fallback",
                     takeaway="", evidence=[], eligible=False)]
    decisions = {"https://a/1": ReviewDecision(
        action="edit", edits={"takeaway": "硬补", "evidence": [
            {"claim": "事实", "anchor": "https://a/1"}]})}
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert one.interpretation_status == "extractive_fallback"
    assert one.eligible_for_must_read is False


# Case 5 (§9.5): edit 非法锚点丢弃
def test_golden_edit_illegal_anchor_dropped():
    items = [_interp("https://a/1", related=["https://r/1"])]
    decisions = {"https://a/1": ReviewDecision(
        action="edit", edits={"evidence": [
            {"claim": "x", "anchor": "https://evil/x"}]})}
    res = review(items, None, decisions, CFG, _ctx())
    assert res.reviewed_items[0].evidence == []
    assert res.reviewed_items[0].eligible_for_must_read is False


# Case 6 (§9.6): 重排序 + 确定性
def test_golden_reorder_and_deterministic():
    items = [_interp("https://a/1"), _interp("https://a/2"),
             _interp("https://a/3")]
    decisions = {"https://a/1": ReviewDecision(order=1),
                 "https://a/2": ReviewDecision(order=0)}
    res1 = review(items, None, decisions, CFG, _ctx())
    res2 = review(items, None, decisions, CFG, _ctx())
    order1 = [r.link for r in res1.reviewed_items]
    assert order1 == ["https://a/2", "https://a/1", "https://a/3"]
    assert order1 == [r.link for r in res2.reviewed_items]


# Case 7 (§9.7): 空输入 silent
def test_golden_empty_input_silent():
    res = review([], None, {}, CFG, _ctx())
    assert res.is_silent is True and res.reviewed_items == []
    assert res.is_pending is True and res.input_count == 0
    assert res.daily_take is None


# Case 8 (§9.8): 今日看点覆盖
def test_golden_daily_take_override():
    items = [_interp("https://a/1")]
    decisions = {"__daily_take__": ReviewDecision(
        action="edit", edits={"daily_take": "人工改写的看点"})}
    res = review(items, "原看点", decisions, CFG, _ctx())
    assert res.daily_take == "人工改写的看点"
    assert res.is_reviewed is True
    # __daily_take__ 不进 reviewed_items / 不计数
    assert [r.link for r in res.reviewed_items] == ["https://a/1"]
    assert res.kept_count == 1 and res.edited_count == 0


# Case 9 (§9.9): 出处只读
def test_golden_provenance_readonly():
    items = [_interp("https://a/1", score=80)]
    decisions = {"https://a/1": ReviewDecision(
        action="edit", edits={"score": 5, "link": "https://evil/x",
                              "title": "改后"})}
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert one.score == 80 and one.link == "https://a/1"
    assert one.title == "改后"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/golden/test_review.py -v`
Expected: FAIL with `ImportError: cannot import name 'review'`

- [ ] **Step 3: 实现 orchestrator**

在 `src/pipeline/review.py` 末尾追加：

```python
DAILY_TAKE_KEY = "__daily_take__"


def review(items: list[InterpretedItem], daily_take: str | None,
           decisions: dict[str, ReviewDecision], config: ReviewConfig,
           ctx: RunContext) -> ReviewResult:
    """审阅 orchestrator(spec §3, §5)。纯函数: 无 LLM / 网络副作用。"""
    emit(ctx.logger, "review_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "review_done", input_count=0, kept_count=0,
             dropped_count=0, edited_count=0, is_pending=True, silent=True)
        return ReviewResult(reviewed_items=[], daily_take=daily_take,
                            input_count=0, kept_count=0, dropped_count=0,
                            edited_count=0, is_reviewed=False, is_pending=True,
                            is_silent=True)

    kept: list[ReviewedItem] = []
    kept_count = dropped_count = edited_count = 0
    for it in items:
        decision = decisions.get(it.link, ReviewDecision())
        result = apply_decision(it, decision, config)
        if result is None:
            dropped_count += 1
            emit(ctx.logger, "item_dropped", link=it.link)
            continue
        if result.review_action == "edit":
            edited_count += 1
            emit(ctx.logger, "item_edited", link=result.link,
                 edited_fields=result.edited_fields)
        else:
            kept_count += 1
        emit(ctx.logger, "item_kept", link=result.link,
             edited=result.was_edited)
        kept.append(result)

    ordered = order_reviewed(kept, decisions)

    # 今日看点覆盖(§5.6)
    daily_dec = decisions.get(DAILY_TAKE_KEY)
    daily_overridden = (daily_dec is not None and daily_dec.action == "edit"
                        and "daily_take" in daily_dec.edits)
    out_daily = daily_dec.edits["daily_take"] if daily_overridden else daily_take

    # 已审/待审(§5.7): 命中任一 item 决策, 或 daily_take 覆盖
    item_links = {it.link for it in items}
    is_reviewed = daily_overridden or any(
        k in item_links for k in decisions)
    is_pending = not is_reviewed

    emit(ctx.logger, "review_done", input_count=len(items),
         kept_count=kept_count, dropped_count=dropped_count,
         edited_count=edited_count, is_pending=is_pending, silent=False)
    return ReviewResult(reviewed_items=ordered, daily_take=out_daily,
                        input_count=len(items), kept_count=kept_count,
                        dropped_count=dropped_count, edited_count=edited_count,
                        is_reviewed=is_reviewed, is_pending=is_pending,
                        is_silent=False)
```

> `item_kept` 事件对 keep 和 edit 条目都 emit（spec §11：每条保留都记 `{link, edited}`）；edit 条目额外 emit `item_edited`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/golden/test_review.py -v`
Expected: PASS（10 + 9 = 19 passed）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/review.py tests/golden/test_review.py
git commit -m "feat(review): review() orchestrator + golden cases (spec §9)"
```

---

## Task 5: CLI --review 链路

**Files:**
- Modify: `src/cli.py`（import 增补 + 追加 `run_dry_review` + `--review` 分支）

链路 `collect→dedup→score→interpret→review`，产 `ReviewResult` JSON（spec §7 `--dry-run`、§12 #1）。决策从 `config/review.yaml` 的 `decisions_path` 加载（缺则全 keep/待审）。

- [ ] **Step 1: 写失败测试**

创建 `tests/contract/test_cli_review.py`：

```python
from datetime import datetime, timezone
from src.cli import run_dry_review
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider
import json

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_review_returns_shape(tmp_path):
    # 决策文件缺失 -> 全 keep/待审; 用空 registry 触发 silent 链路
    reg = tmp_path / "sources.yaml"
    reg.write_text("sources: []\n", encoding="utf-8")
    out = run_dry_review(
        registry_path=str(reg), now=NOW,
        embedder=FakeEmbeddingProvider({}),
        llm=FakeLLMProvider({}, default=json.dumps({"highlights": "h"})),
        decisions_path=str(tmp_path / "nope.json"))
    assert "run_id" in out and out["now"] == NOW.isoformat()
    assert "kept_count" in out and "dropped_count" in out
    assert "edited_count" in out and "is_pending" in out
    assert "reviewed_items" in out and "daily_take" in out
    assert out["is_silent"] is True
```

> 空 registry → collect 静默返回空 → dedup/score/interpret 链路均空，embedder/llm 实际不会被调用；这里注入 fake 只为避免触达真实网络 provider。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_cli_review.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_dry_review'`

- [ ] **Step 3: 实现 CLI 链路**

`src/cli.py` import 段（第 13–15 行附近）追加：

```python
from src.core.config import load_review_config, load_review_decisions
from src.pipeline.review import review
```

在 `run_dry_interpret` 之后追加 `run_dry_review`：

```python
def run_dry_review(registry_path: str, now: datetime | None = None,
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
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "input_count": rres.input_count,
        "kept_count": rres.kept_count,
        "dropped_count": rres.dropped_count,
        "edited_count": rres.edited_count,
        "is_reviewed": rres.is_reviewed,
        "is_pending": rres.is_pending,
        "is_silent": rres.is_silent,
        "daily_take": rres.daily_take,
        "reviewed_items": [it.model_dump(mode="json")
                           for it in rres.reviewed_items],
    }
```

在 `main()` 的 argparse 段追加 flag（`--interpret` 之后）：

```python
    p.add_argument("--review", action="store_true",
                   help="chain collect -> ... -> review, print ReviewResult JSON")
```

在 `main()` 的分支段，`--interpret` 分支**之前**插入（保持"更下游优先匹配"的既有顺序）：

```python
    if args.dry_run and args.review:
        out = run_dry_review(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_cli_review.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli.py tests/contract/test_cli_review.py
git commit -m "feat(review): CLI --review chain (collect->...->review)"
```

---

## Task 6: 全套件绿 + 更新 ROADMAP

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: 跑全套件**

Run: `uv run pytest -q`
Expected: 全绿（既有 128 + 本圈新增 contract/golden ≈ 6 + 5 + 19 + 1 = 31，约 159 passed）。若有红，定位修复后再继续。

- [ ] **Step 2: 更新 ROADMAP**

打开 `docs/ROADMAP.md`，把第 5 层 ⑤ 行状态从未完成改为 `🟩 已合并`，mermaid 节点 `C5:::done`，填日期 `2026-06-02`；在文档地图加 `S5: docs/specs/review.md` / `P5: docs/superpowers/plans/2026-06-02-review-layer.md`；§"下一步"指向 Circle 6（publish）。

> 具体行文对齐 Circle 4 在 ROADMAP 里的既有格式（读现有文件后照搬结构，只换层号/日期/路径）。

- [ ] **Step 3: 提交**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): mark Circle 5 (review) merged"
```

- [ ] **Step 4: 收尾**

REQUIRED SUB-SKILL: 用 `superpowers:finishing-a-development-branch` 验证测试、选择合并方式（预期：合并回 master 本地、删 `circle5-review` 分支）。

---

## 验收对照（spec §12）

| 验收点 | 由哪个 Task 覆盖 |
|---|---|
| #6 审阅可操作（留/删/改/排序核心） | Task 3（apply/order）+ Task 4（orchestrator） |
| #5 解读零幻觉延续（重算门、不编锚点、不洗白回退） | Task 3 测试 + Task 4 golden case 4/5 |
| #1 端到端（`collect→...→review` dry-run） | Task 5 |
| #8 静默正确 | Task 4 golden case 7 |
| 待审正确（§3.4） | Task 4 golden case 1 |
| #9 可观察（review_done 等事件） | Task 4 orchestrator emit |
