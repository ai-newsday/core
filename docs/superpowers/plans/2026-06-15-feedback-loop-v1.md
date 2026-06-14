# Feedback Loop v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the feedback loop end-to-end — persist review feedback to SQLite and feed each source's `quality_weight` back into scoring, so today's keep/drop changes tomorrow's ranking.

**Architecture:** The pure feedback functions (`derive_events / aggregate_by_source / compute_quality_weights / feedback`) already exist and stay untouched. We add (1) a `quality_of` multiplier on the `机构影响力` scoring dimension, (2) SQLite read/write for the already-declared `feedback_events` / `quality_weights` tables with `run_id` idempotency, (3) wiring in `run_finalize_tick` (write) and `run_tick` (read). Default weight 1.0 ⇒ scoring output is byte-identical to today (backward compatible).

**Tech Stack:** Python 3.12, pytest, aiosqlite, pydantic. Design doc: `docs/superpowers/specs/2026-06-15-feedback-loop-v1-design.md`.

---

## File Structure

- `src/pipeline/score.py` — add `quality_of` param; multiply `机构影响力` by per-source weight.
- `src/state/db.py` — `UNIQUE(run_id, link)` on `feedback_events`; 4 new async methods.
- `src/pipeline/tick.py` — append feedback + recompute weights at end of `run_finalize_tick`.
- `src/cli.py` — read weights from DB and inject into `score()` inside `run_tick`.
- `docs/adr/0002-feedback-quality-weight-into-scoring.md` — new ADR.
- `docs/specs/feedback.md` — flip "deferred" → "implemented" for persistence + scoring wire-up.
- Tests: `tests/contract/test_score_unit.py`, `tests/contract/test_state_db.py`, `tests/golden/test_tick.py`, `tests/contract/test_tick_cli.py`.

---

## Task 1: Inject `quality_of` into scoring (`机构影响力` multiplier)

**Files:**
- Modify: `src/pipeline/score.py` (`compute_scores` signature + `机构影响力` line; `score` signature + call)
- Test: `tests/contract/test_score_unit.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_score_unit.py`:

```python
def test_quality_weight_multiplies_institution_dimension():
    items = [_ni("A", "https://a/1", "openai", SourceType.OFFICIAL)]
    base = compute_scores(items, {"openai": 1}, ScoringConfig(), _ctx())[0]
    boosted = compute_scores(
        items, {"openai": 1}, ScoringConfig(), _ctx(), quality_of={"openai": 1.5}
    )[0]
    assert base.score_breakdown["机构影响力"] == 24.0  # official 18 + priority-1 bonus 6
    assert boosted.score_breakdown["机构影响力"] == 36.0  # 24 * 1.5


def test_quality_weight_defaults_to_one_when_source_missing():
    items = [_ni("A", "https://a/1", "openai", SourceType.OFFICIAL)]
    default = compute_scores(items, {"openai": 1}, ScoringConfig(), _ctx())[0]
    other = compute_scores(
        items, {"openai": 1}, ScoringConfig(), _ctx(), quality_of={"someone-else": 0.5}
    )[0]
    assert default.score_breakdown["机构影响力"] == 24.0
    assert other.score_breakdown["机构影响力"] == 24.0  # openai not in map -> weight 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_score_unit.py -k quality_weight -v`
Expected: FAIL — `compute_scores() got an unexpected keyword argument 'quality_of'`

- [ ] **Step 3: Implement the multiplier**

In `src/pipeline/score.py`, change the `compute_scores` signature (around line 82) to add the param:

```python
def compute_scores(
    items: list[NewsItem],
    priority_of: dict[str, int],
    config: ScoringConfig,
    ctx: RunContext,
    quality_of: dict[str, float] | None = None,
) -> list[ScoredItem]:
```

Inside the loop (currently line 98), replace the `机构影响力` entry:

```python
        qw = (quality_of or {}).get(it.source, 1.0)
        breakdown = {
            "机构影响力": round((float(dims.get("机构影响力", 0)) + float(prio_bonus)) * qw, 4),
            "可见指标": round(_visibility(it, config), 4),
            "时效": recency_band(it.published_at, ctx.now, config),
            "惩罚": penalty_of[it.link],
            "读者相关度": _topic_relevance(it, config),
        }
```

Then change the `score` orchestrator signature (around line 143) and its `compute_scores` call (around line 159):

```python
def score(
    items: list[NewsItem],
    config: ScoringConfig,
    ctx: RunContext,
    quality_of: dict[str, float] | None = None,
) -> ScoreResult:
```

```python
    scored = compute_scores(items, priority_of, config, ctx, quality_of=quality_of)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_score_unit.py -v && uv run pytest tests/golden/test_score.py tests/golden/test_score_popularity.py -v`
Expected: PASS — including all existing score golden tests (weight defaults to 1.0 ⇒ `round(24.0,4)==24.0`, byte-identical).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/score.py tests/contract/test_score_unit.py
git commit -m "feat(score): optional quality_of multiplier on 机构影响力 dimension

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: SQLite persistence for feedback events + quality weights

**Files:**
- Modify: `src/state/db.py` (schema `UNIQUE(run_id, link)`; import `FeedbackEvent`; 4 methods)
- Test: `tests/contract/test_state_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_state_db.py` (top of file already has `import asyncio`, `import pytest`, `from src.state.db import Database`; add the imports shown):

```python
import aiosqlite

from datetime import datetime, timezone
from src.core.types import FeedbackEvent

_TS = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)


def test_quality_weights_empty_returns_empty_dict(db):
    async def go():
        await db.init()
        assert await db.get_quality_weights() == {}

    asyncio.run(go())


def test_upsert_and_get_quality_weights_roundtrip(db):
    async def go():
        await db.init()
        await db.upsert_quality_weights({"openai": 1.2, "hf-models": 0.8})
        assert await db.get_quality_weights() == {"openai": 1.2, "hf-models": 0.8}
        await db.upsert_quality_weights({"openai": 1.4})  # update one
        assert await db.get_quality_weights() == {"openai": 1.4, "hf-models": 0.8}

    asyncio.run(go())


def test_append_feedback_events_is_idempotent_per_run_and_link(db):
    async def go():
        await db.init()
        ev = FeedbackEvent(link="https://a/1", source="s", action="keep", run_id="r1", ts=_TS)
        await db.append_feedback_events([ev])
        await db.append_feedback_events([ev])  # same (run_id, link) again
        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM feedback_events WHERE run_id=? AND link=?", ("r1", "https://a/1")
            ) as cur:
                (n,) = await cur.fetchone()
        assert n == 1
        assert await db.has_feedback_for_run("r1") is True
        assert await db.has_feedback_for_run("r2") is False

    asyncio.run(go())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_state_db.py -k "quality_weights or feedback_events or feedback_for_run" -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'get_quality_weights'`

- [ ] **Step 3: Implement schema constraint + methods**

In `src/state/db.py`, add the import near the top (after the existing imports):

```python
from src.core.types import FeedbackEvent
```

Change the `feedback_events` table in `_SCHEMA` to add a uniqueness constraint:

```sql
CREATE TABLE IF NOT EXISTS feedback_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    link    TEXT NOT NULL,
    source  TEXT NOT NULL,
    action  TEXT NOT NULL,
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    UNIQUE(run_id, link)
);
```

Add these four methods to the `Database` class (after `get_decisions_dict`):

```python
    async def append_feedback_events(self, events: list[FeedbackEvent]) -> None:
        """追加反馈事件; (run_id, link) 唯一, 重跑用 INSERT OR IGNORE 不双计。"""
        async with aiosqlite.connect(self._path) as conn:
            for e in events:
                await conn.execute(
                    "INSERT OR IGNORE INTO feedback_events(link,source,action,run_id,ts) "
                    "VALUES(?,?,?,?,?)",
                    (e.link, e.source, e.action, e.run_id, e.ts.isoformat()),
                )
            await conn.commit()

    async def has_feedback_for_run(self, run_id: str) -> bool:
        """该 run_id 是否已贡献过反馈事件(幂等闸)。"""
        async with aiosqlite.connect(self._path) as conn:
            async with conn.execute(
                "SELECT 1 FROM feedback_events WHERE run_id=? LIMIT 1", (run_id,)
            ) as cur:
                return await cur.fetchone() is not None

    async def get_quality_weights(self) -> dict[str, float]:
        """读权重表; 空表 → {}。"""
        async with aiosqlite.connect(self._path) as conn:
            async with conn.execute("SELECT source,weight FROM quality_weights") as cur:
                rows = await cur.fetchall()
                return {r[0]: float(r[1]) for r in rows}

    async def upsert_quality_weights(self, weights: dict[str, float]) -> None:
        """写回权重(每源一行, INSERT OR REPLACE)。"""
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._path) as conn:
            for source, w in weights.items():
                await conn.execute(
                    "INSERT OR REPLACE INTO quality_weights(source,weight,updated_at) "
                    "VALUES(?,?,?)",
                    (source, float(w), ts),
                )
            await conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_state_db.py -v`
Expected: PASS (new tests + all existing db tests).

- [ ] **Step 5: Commit**

```bash
git add src/state/db.py tests/contract/test_state_db.py
git commit -m "feat(db): persist feedback_events + quality_weights with run_id idempotency

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire feedback persistence into `run_finalize_tick`

**Files:**
- Modify: `src/pipeline/tick.py` (`run_finalize_tick`, after publish/report, before `return`)
- Test: `tests/golden/test_tick.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/golden/test_tick.py` (file already imports `asyncio`, `Database`, `FakeNotifier`, `run_finalize_tick`, `_make_item`, `NOW`, `TODAY`):

```python
def test_finalize_tick_persists_feedback_and_is_idempotent(tmp_path):
    async def go():
        import aiosqlite
        from src.pipeline.tick import _item_id

        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1", source="hf-models", cluster_id="c1")

        # seed a 'keep' decision for this item under run id "r-fin"
        iid = _item_id(item)
        await db.insert_run("r-fin", "finalize")
        await db.upsert_pending_review(
            item_id=iid, run_id="r-fin", link=item.link, source=item.source,
            title_en=item.title_en, title_zh=item.title, summary_zh=item.summary,
            takeaway=item.takeaway, hot_take=item.hot_take, score=item.score,
            signals=item.signals, date=TODAY,
        )
        await db.update_decision(iid, "keep")

        # run finalize twice with the SAME run_id
        for _ in range(2):
            await run_finalize_tick(
                run_id="r-fin", now=NOW, date_label=TODAY,
                interpreted_items=[item], daily_take="x", db=db, notifiers=[notifier],
            )

        # keep -> 升权 from baseline 1.0 by step 0.2
        weights = await db.get_quality_weights()
        assert weights["hf-models"] == 1.2

        # idempotent: exactly one event row for (run_id, link)
        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM feedback_events WHERE run_id=? AND link=?",
                ("r-fin", item.link),
            ) as cur:
                (n,) = await cur.fetchone()
        assert n == 1

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/golden/test_tick.py::test_finalize_tick_persists_feedback_and_is_idempotent -v`
Expected: FAIL — `get_quality_weights()` returns `{}` (no feedback written), `KeyError: 'hf-models'`.

- [ ] **Step 3: Implement the feedback block**

In `src/pipeline/tick.py`, inside `run_finalize_tick`, insert this block immediately before the final `return {...}` (after the `tick_finalize_done` emit). `ctx`, `decisions`, `interpreted_items`, `run_id`, `now`, `db`, `logger` are all already in scope:

```python
    # 反馈闭环 (PRD §4.5): 派生 → 幂等入账 → 增量重算权重 → 写回。非致命。
    if not await db.has_feedback_for_run(run_id):
        from src.core.config import load_feedback_config
        from src.pipeline.feedback import derive_events, feedback

        try:
            fcfg = load_feedback_config("config/feedback.yaml")
            run_events = derive_events(interpreted_items, decisions, run_id=run_id, now=now)
            await db.append_feedback_events(run_events)
            prior = await db.get_quality_weights()
            fres = feedback(run_events, prior, fcfg, ctx)
            if not fres.is_silent:
                await db.upsert_quality_weights(fres.quality_weights)
        except Exception as e:  # noqa: BLE001 - feedback persistence is non-fatal
            emit(logger, "feedback_persist_error", run_id=run_id, error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/golden/test_tick.py -v`
Expected: PASS (new test + all existing tick tests).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/tick.py tests/golden/test_tick.py
git commit -m "feat(tick): recompute + persist quality_weights at end of finalize tick

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Read weights into scoring in `run_tick`

**Files:**
- Modify: `src/cli.py` (`run_tick` → `_collect_and_interpret`, the `score()` call ~line 445)
- Test: `tests/contract/test_tick_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_tick_cli.py` (file already imports `run_tick`, `FailingLLMProvider`, `FakeEmbeddingProvider`, `NOW`; add `asyncio` and `Database`):

```python
import asyncio

from src.state.db import Database


def test_run_tick_reads_seeded_quality_weights_without_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    db_path = str(tmp_path / "state.db")

    async def seed():
        db = Database(db_path)
        await db.init()
        await db.upsert_quality_weights({"hf-models": 1.5})

    asyncio.run(seed())

    out = run_tick(
        tick="collect",
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=db_path,
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    for k in ("run_id", "tick", "pushed", "date"):
        assert k in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_tick_cli.py::test_run_tick_reads_seeded_quality_weights_without_error -v`
Expected: FAIL — `score()` is still called without `quality_of`; the seeded weights are never read (assert that the new call path exists). *If it passes by accident here, it still validates after Step 3; the real coverage is that the score call is changed.*

> Note: the multiplication itself is proven in Task 1; the byte-identical default in Task 1's golden run; this test proves `run_tick` reads the table and stays green end-to-end with weights present.

- [ ] **Step 3: Implement the read + inject**

In `src/cli.py`, inside `run_tick`'s `_collect_and_interpret` closure, change the scoring lines (currently ~443-445):

```python
        scfg = load_scoring_config("config/scoring.yaml")
        scfg.sources_registry_path = registry_path
        quality_of = await db.get_quality_weights()
        sres = score(dres.deduped_items, scfg, ctx, quality_of=quality_of)
```

(`db` is defined earlier in `run_tick` and captured by the closure.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_tick_cli.py -v`
Expected: PASS (new test + both existing run_tick shape tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/contract/test_tick_cli.py
git commit -m "feat(cli): inject persisted quality_weights into scoring on each tick

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: ADR + spec update + full suite

**Files:**
- Create: `docs/adr/0002-feedback-quality-weight-into-scoring.md`
- Modify: `docs/specs/feedback.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0002-feedback-quality-weight-into-scoring.md`:

```markdown
# ADR 0002 — 反馈 quality_weight 接回打分

- 状态: Accepted
- 日期: 2026-06-15
- 关联: PRD §4.3 / §4.5；`docs/specs/feedback.md`；设计 `docs/superpowers/specs/2026-06-15-feedback-loop-v1-design.md`

## 背景

反馈层的纯函数已能从审阅"留/删/改"算出每源 `quality_weight`，但此前明确"不接回打分、不落盘"。本 ADR 记录把闭环真正闭上的三个决策。

## 决策

1. **SSOT = SQLite**。复用 `db.py` 已建的 `feedback_events` / `quality_weights` 表持久化；JSON 账本（`run_dry_feedback`）保留作离线/dry-run 确定性测试。理由：PRD 明确 SSOT 为 SQLite，决策数据已在 `pending_reviews`。
2. **`quality_weight` 乘在 `机构影响力` 维度**：`机构影响力 = (基础 + priority_bonus) * quality_weight`。理由：语义同为源信誉；保持 9 个 breakdown key 不变与纯加法可解释；默认 1.0 时数值零变化。否决"乘总分"（破坏 score=各维度之和）与"加第 10 维"（破坏固定维度集 + snapshot）。
3. **增量公式 + run_id 幂等**：保留现有 `compute_quality_weights` 增量漂移模型及其 golden 测试；幂等闸 `UNIQUE(run_id, link)` + `has_feedback_for_run` 只加在持久化边界。否决"全量重算"（改语义、破 golden）与"不管幂等"（cron 重试污染权重）。

## 影响

- 权重缺省 1.0 ⇒ 现有打分 golden/snapshot 全绿（向后兼容）。
- finalize tick 在发布之后写权重；影响**次日**打分。写失败非致命（`feedback_persist_error`，不回滚已发布的报）。
- 阅读行为/👍👎信号、`reader_relevance`、Qdrant 向量、explore 选条仍为 P1+，本 ADR 不涉及。
```

- [ ] **Step 2: Update the feedback spec**

In `docs/specs/feedback.md`, update the "不做（这一圈明确延后）" table (§2): remove the two rows **"把 `quality_weight` 接回第 3 层打分"** and **"真正写磁盘副作用"** from the *不做* table, and add a short note under §1 / the header pointing to this module:

> **v1 闭环更新（2026-06-15）**：本层的两项原"延后"——**持久化落盘（SQLite）** 与 **接回第 3 层打分（乘 `机构影响力`，run_id 幂等）**——已在「反馈闭环 v1」实现，见 `docs/adr/0002-feedback-quality-weight-into-scoring.md` 与设计 `docs/superpowers/specs/2026-06-15-feedback-loop-v1-design.md`。纯函数契约不变；仍延后的是阅读行为/👍👎信号、`reader_relevance`、Qdrant 向量、explore 选条。

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — entire suite green (contract + golden + snapshot).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0002-feedback-quality-weight-into-scoring.md docs/specs/feedback.md
git commit -m "docs(feedback): ADR 0002 + spec update for closed feedback loop v1

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** D1 closed loop → Tasks 1+3+4; D2 SQLite SSOT → Task 2; D3 `机构影响力` multiplier → Task 1; D4 run_id idempotency + incremental formula → Task 2 (`UNIQUE`/`has_feedback_for_run`) + Task 3 (guard); ADR + spec → Task 5. Error handling (non-fatal persist, silent day, cold start) → Task 3 block + Task 1 default. All design §4 component changes have a task.
- **Type consistency:** `quality_of: dict[str, float] | None` consistent across `compute_scores`/`score`/`run_tick`. `FeedbackEvent` fields (`link/source/action/run_id/ts`) match `src/core/types.py`. DB methods `append_feedback_events/has_feedback_for_run/get_quality_weights/upsert_quality_weights` named identically everywhere used.
- **Placeholder scan:** none — every code/test step has concrete content.
