# A1 定稿只处理保留条目 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** finalize 不再重跑采集,而是按 keep 决策从白天存的快照取条目、绕过缓存重新解读,每个没发出的 keep 条目都有带原因的日志。

**Architecture:** collect tick 推卡时把解读前的 `ScoredItem` 存进 state.db 新表 `review_snapshots`;`run_finalize_tick` 新增可注入的 `reinterpret` 回调,拿到决策后从快照取 keep 条目交给它重新解读;`src/cli.py::run_tick` 的 finalize 分支不再调用 `_collect_and_interpret`,只构造 `reinterpret`(`interpret(..., cache=None)`)。筛选规则(配额/一期一模型/地板)不变,只补跳过日志。

**Tech Stack:** Python 3.12, pydantic v2, aiosqlite, pytest, uv, ruff。

**Spec:** `docs/superpowers/specs/2026-09-28-finalize-from-snapshots-design.md`

## Global Constraints

- 确认门不变:未 keep 的条目不进报告(`select_report_items`)。
- keep 超过 `total_limit: 12`:按现有规则砍,**不顺延**;只加日志,不改筛选行为。
- 跳过原因枚举(精确字符串):`no_snapshot | interpret_failed | irrelevant | already_published | model_mismatch | rule_cut`。
- finalize 重解读 **`cache=None`**(绕过 36h 解读缓存)。
- drop 决策的负反馈不能丢:有快照的 drop 条目要进 `derive_events`。
- 写快照 / 读快照失败都**非致命**。
- 不在本计划:E3 记账集合、E2 并发、A2 合并、A1b TG 通知、删除 `storylink.py`。
- 提交前本地跑 `uv run ruff check . && uv run ruff format --check .`(CI 会跑,pytest 不跑)。
- 分支:从最新 `origin/master` 起 `refactor/finalize-from-snapshots`;开 issue 后 PR 描述引用 issue(issue-per-PR)。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/state/db.py` | 新表 `review_snapshots` + `upsert_snapshot` / `get_snapshots` | Modify |
| `src/pipeline/tick.py` | collect 写快照;finalize 从快照取条目、跳过日志、summary | Modify |
| `src/cli.py` | `run_tick` finalize 分支改接线,删夜间重采集 | Modify |
| `.github/workflows/finalize.yml` | 删 x-signals clone 与 `X_LIST_DATA_DIR` | Modify |
| `tests/contract/test_review_snapshots.py` | DB 方法契约 | Create |
| `tests/contract/test_tick_decisions.py` | collect 写快照 + finalize 从快照 | Modify |
| `tests/contract/test_tick_cli.py` | 真实入口接线 | Modify |

---

### Task 1: `review_snapshots` 表与读写方法

**Files:**
- Modify: `src/state/db.py`(`_SCHEMA` 末尾加表;`mark_published` 之前加两个方法)
- Test: `tests/contract/test_review_snapshots.py`

**Interfaces:**
- Produces:
  - `Database.upsert_snapshot(item_id: str, date: str, snapshot_json: str) -> None`(同 id 覆盖)
  - `Database.get_snapshots(item_ids: list[str]) -> dict[str, str]`(只返回存在的 id;空列表 → `{}`)

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_review_snapshots.py
import asyncio

from src.state.db import Database


def test_snapshot_upsert_overwrites_and_get_returns_only_known(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await db.upsert_snapshot("a", "2026-09-28", '{"v": 1}')
        await db.upsert_snapshot("a", "2026-09-29", '{"v": 2}')
        assert await db.get_snapshots(["a", "missing"]) == {"a": '{"v": 2}'}
        assert await db.get_snapshots([]) == {}

    asyncio.run(go())


def test_init_adds_snapshot_table_to_existing_db(tmp_path):
    """老库(缓存里恢复的 state.db)没有这张表; init() 幂等建表后必须能直接用。"""

    async def go():
        path = str(tmp_path / "s.db")
        await Database(path).init()
        db = Database(path)
        await db.init()
        await db.upsert_snapshot("a", "2026-09-28", "{}")
        assert await db.get_snapshots(["a"]) == {"a": "{}"}

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_review_snapshots.py -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'upsert_snapshot'`

- [ ] **Step 3: Write minimal implementation**

`_SCHEMA` 末尾(`published_items` 表之后、结束 `"""` 之前)加:

```sql

CREATE TABLE IF NOT EXISTS review_snapshots (
    item_id       TEXT PRIMARY KEY,
    date          TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
```

`Database` 类里 `mark_published` 之前加:

```python
    async def upsert_snapshot(self, item_id: str, date: str, snapshot_json: str) -> None:
        """A1: 推卡时存解读前的 ScoredItem, finalize 按它重新解读。同条重复采集覆盖为最新。"""
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "INSERT INTO review_snapshots(item_id,date,snapshot_json,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET date=excluded.date, "
                "snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at",
                (item_id, date, snapshot_json, ts),
            )
            await conn.commit()

    async def get_snapshots(self, item_ids: list[str]) -> dict[str, str]:
        """{item_id: snapshot_json}, 只含存在的 id。"""
        if not item_ids:
            return {}
        ph = ",".join("?" * len(item_ids))
        async with aiosqlite.connect(self._path) as conn:
            async with conn.execute(
                f"SELECT item_id, snapshot_json FROM review_snapshots WHERE item_id IN ({ph})",
                tuple(item_ids),
            ) as cur:
                return {r[0]: r[1] for r in await cur.fetchall()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_review_snapshots.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/state/db.py tests/contract/test_review_snapshots.py
git commit -m "feat(state): review_snapshots table for finalize-from-snapshots (A1)"
```

---

### Task 2: collect tick 推卡时写快照

**Files:**
- Modify: `src/pipeline/tick.py`(imports 加 `ScoredItem`;`_item_id` 之后加 `_snapshot_json`;`run_collect_tick` 的 `upsert_pending_review` 之后写快照)
- Test: `tests/contract/test_tick_decisions.py`(文件末尾追加)

**Interfaces:**
- Consumes: `Database.upsert_snapshot`(Task 1)
- Produces: `_snapshot_json(item: InterpretedItem) -> str` —— `ScoredItem` 的 JSON(不含解读字段)

- [ ] **Step 1: Write the failing tests**

```python
# 追加到 tests/contract/test_tick_decisions.py
def test_collect_tick_snapshots_scored_item_for_each_relevant_card(tmp_path):
    from src.core.types import ScoredItem

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        ok = _item("https://x/1", "A")
        junk = _item("https://x/junk", "Not AI").model_copy(update={"relevant": False})
        await run_collect_tick("r1", NOW, [ok, junk], "take", db, [FakeNotifier()])
        snaps = await db.get_snapshots([_iid("https://x/1"), _iid("https://x/junk")])
        assert set(snaps) == {_iid("https://x/1")}
        restored = ScoredItem.model_validate_json(snaps[_iid("https://x/1")])
        assert restored.link == "https://x/1"
        assert restored.score == 80
        assert "body" not in restored.model_dump()  # 存的是解读前的条目

    asyncio.run(go())


def test_collect_tick_snapshot_failure_does_not_block_card(tmp_path):
    class _BrokenSnapshots(Database):
        async def upsert_snapshot(self, *a, **k):
            raise RuntimeError("disk full")

    async def go():
        db = _BrokenSnapshots(str(tmp_path / "s.db"))
        await db.init()
        notifier = FakeNotifier()
        await run_collect_tick("r1", NOW, [_item("https://x/1", "A")], "take", db, [notifier])
        assert len(notifier.sent_cards) == 1

    asyncio.run(go())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_tick_decisions.py -k snapshot -v`
Expected: 第一条 FAIL(`set() != {...}`,快照没写);第二条 PASS(还没调用 `upsert_snapshot`)——它是 Step 3 之后的护栏。

- [ ] **Step 3: Write minimal implementation**

`src/pipeline/tick.py` 的 `from src.core.types import (...)` 里加 `ScoredItem`。`_item_id` 之后加:

```python
def _snapshot_json(item: InterpretedItem) -> str:
    """A1: 存解读前的 ScoredItem(interpret() 的输入), finalize 按它重新解读。"""
    fields = set(ScoredItem.model_fields)
    return ScoredItem.model_validate(item.model_dump(include=fields)).model_dump_json()
```

`run_collect_tick` 里 `await db.upsert_pending_review(...)` 调用之后、`# 只推之前没发过卡片的条目` 之前加:

```python
        try:
            await db.upsert_snapshot(item_id, date, _snapshot_json(item))
        except Exception as e:  # noqa: BLE001 - 快照失败不影响推卡
            emit(logger, "snapshot_write_error", item_id=item_id, error=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_tick_decisions.py tests/golden/test_tick.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/tick.py tests/contract/test_tick_decisions.py
git commit -m "feat(tick): snapshot pre-interpret item on each review card (A1)"
```

---

### Task 3: finalize 从快照取 keep 条目 + 跳过日志

**Files:**
- Modify: `src/pipeline/tick.py`(imports;新 helper `_load_kept_from_snapshots`;`run_finalize_tick` 签名、决策块、组稿后跳过统计、反馈用 `feedback_items`、返回值)
- Test: `tests/contract/test_tick_decisions.py`(追加)

**Interfaces:**
- Consumes: `Database.get_snapshots`(Task 1)、快照格式(Task 2)
- Produces:
  - `run_finalize_tick(..., reinterpret: Callable[[list[ScoredItem]], list[InterpretedItem]] | None = None)` —— `None` 时行为与现在完全一样(现有测试直接喂 `interpreted_items`)
  - 返回 dict 新增键 `"skipped_by_reason": dict[str, int]`
  - 日志事件 `finalize_item_skipped{run_id, item_id, reason, error}`、`finalize_summary{run_id, kept, published, skipped_by_reason}`

- [ ] **Step 1: Write the failing tests**

```python
# 追加到 tests/contract/test_tick_decisions.py
PREF = "agnes:agnes-2.5-flash"  # config/publish.yaml preferred_issue_model


def _reinterpreter(calls, fail=(), irrelevant=(), model_of=None):
    """假 reinterpret: 记录收到的条目; 按 link 制造失败/不相关/指定模型。"""
    model_of = model_of or {}

    def f(items):
        calls.append([it.link for it in items])
        out = []
        for it in items:
            upd = {"model": model_of.get(it.link, PREF)}
            if it.link in fail:
                upd.update(interpretation_status="extractive_fallback", fallback_reason="RateLimit")
            if it.link in irrelevant:
                upd["relevant"] = False
            out.append(_item(it.link, it.title_en).model_copy(update=upd))
        return out

    return f


async def _seed(db, links):
    await run_collect_tick("r1", NOW, [_item(u, u) for u in links], "take", db, [FakeNotifier()])


def _finalize(db, decisions, reinterpret):
    return run_finalize_tick(
        "r2",
        NOW,
        "2026-06-19",
        [],
        None,
        db,
        [FakeNotifier()],
        decision_store=FakeDecisionStore(decisions),
        reinterpret=reinterpret,
    )


def test_finalize_reinterprets_only_kept_snapshots(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1", "https://x/2", "https://x/3"])
        calls = []
        out = await _finalize(
            db,
            {_iid("https://x/1"): "keep", _iid("https://x/2"): "drop"},
            _reinterpreter(calls),
        )
        assert calls == [["https://x/1"]]  # 只解读 keep, 不碰 drop / 未决策
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {}

    asyncio.run(go())


def test_finalize_skips_with_reasons(tmp_path, caplog):
    import json as _json
    import logging

    caplog.set_level(logging.INFO, logger="ai-newsday")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/ok", "https://x/fail", "https://x/junk", "https://x/other"])
        decisions = {
            _iid(u): "keep"
            for u in ["https://x/ok", "https://x/fail", "https://x/junk", "https://x/other"]
        }
        decisions[_iid("https://x/nosnap")] = "keep"
        out = await _finalize(
            db,
            decisions,
            _reinterpreter(
                [],
                fail={"https://x/fail"},
                irrelevant={"https://x/junk"},
                model_of={"https://x/other": "modelscope:other"},
            ),
        )
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {
            "no_snapshot": 1,
            "interpret_failed": 1,
            "irrelevant": 1,
            "model_mismatch": 1,
        }

    asyncio.run(go())
    events = [_json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    skipped = [e for e in events if e["event"] == "finalize_item_skipped"]
    assert {e["reason"] for e in skipped} == {
        "no_snapshot",
        "interpret_failed",
        "irrelevant",
        "model_mismatch",
    }
    fail = next(e for e in skipped if e["reason"] == "interpret_failed")
    assert fail["error"] == "RateLimit"
    assert any(e["event"] == "finalize_summary" and e["kept"] == 5 for e in events)


def test_finalize_logs_rule_cut_when_keeps_exceed_total_limit(tmp_path):
    """keep 13 条 news(配额 news:1, total_limit 12)→ 只发 1 条, 12 条 rule_cut, 不顺延。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        links = [f"https://x/n{i}" for i in range(13)]
        await _seed(db, links)
        out = await _finalize(db, {_iid(u): "keep" for u in links}, _reinterpreter([]))
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {"rule_cut": 12}

    asyncio.run(go())


def test_finalize_keeps_drop_feedback_from_snapshots(tmp_path):
    """drop 条目不解读, 但负反馈必须照样入账(否则权重只升不降)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1", "https://x/2"])
        await _finalize(
            db,
            {_iid("https://x/1"): "keep", _iid("https://x/2"): "drop"},
            _reinterpreter([]),
        )
        import aiosqlite

        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute("SELECT link, action FROM feedback_events") as cur:
                rows = set(await cur.fetchall())
        assert rows == {("https://x/1", "keep"), ("https://x/2", "drop")}

    asyncio.run(go())


def test_finalize_snapshot_read_error_skips_all_without_crash(tmp_path):
    class _BrokenRead(Database):
        async def get_snapshots(self, item_ids):
            raise RuntimeError("no such table")

    async def go():
        db = _BrokenRead(str(tmp_path / "s.db"))
        await db.init()
        out = await _finalize(db, {_iid("https://x/1"): "keep"}, _reinterpreter([]))
        assert out["item_count"] == 0
        assert out["skipped_by_reason"] == {"no_snapshot": 1}

    asyncio.run(go())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_tick_decisions.py -k "finalize_reinterprets or skips_with or rule_cut or drop_feedback or read_error" -v`
Expected: FAIL with `TypeError: run_finalize_tick() got an unexpected keyword argument 'reinterpret'`

- [ ] **Step 3: Implement — imports 与 helper**

`src/pipeline/tick.py` 顶部:

```python
from collections import Counter
from collections.abc import Callable
```

`_snapshot_json` 之后加:

```python
async def _load_kept_from_snapshots(
    remote: dict[str, str],
    db: Database,
    reinterpret: Callable[[list[ScoredItem]], list[InterpretedItem]],
    logger: logging.Logger,
) -> tuple[list[InterpretedItem], list[ScoredItem], list[tuple[str, str, str | None]]]:
    """A1: 决策 id → 快照 → 只重新解读 keep。
    返回 (报告候选, 有快照的全部已决策条目, 跳过列表[(item_id, reason, error)])。
    drop 条目不解读, 但放进第二项: id→link 映射与反馈闭环要用(负反馈不能丢)。"""
    decided = [iid for iid, a in remote.items() if a in ("keep", "drop")]
    try:
        snaps = await db.get_snapshots(decided)
    except Exception as e:  # noqa: BLE001 - 读不出快照 = 全部 no_snapshot, 不崩
        emit(logger, "snapshot_read_error", error_type=type(e).__name__, error=str(e))
        snaps = {}
    pool = {iid: ScoredItem.model_validate_json(s) for iid, s in snaps.items()}
    keep_ids = [iid for iid in decided if remote[iid] == "keep"]
    skipped: list[tuple[str, str, str | None]] = [
        (iid, "no_snapshot", None) for iid in keep_ids if iid not in pool
    ]
    to_interpret = [pool[iid] for iid in keep_ids if iid in pool]
    kept: list[InterpretedItem] = []
    for it in reinterpret(to_interpret) if to_interpret else []:
        if it.interpretation_status != "ok":
            skipped.append((_item_id(it), "interpret_failed", it.fallback_reason))
        elif not it.relevant:
            skipped.append((_item_id(it), "irrelevant", None))
        else:
            kept.append(it)
    return kept, list(pool.values()), skipped
```

- [ ] **Step 4: Implement — `run_finalize_tick` 签名与决策块**

签名末尾(`item_image_config` 之后)加参数:

```python
    reinterpret: Callable[[list[ScoredItem]], list[InterpretedItem]] | None = None,
```

把从 `# webhook 决策按 item_id 直接匹配本报条目...` 注释开始、到 `if remote_raw: await db.record_decisions(...)` 为止的整块替换为:

```python
    # 决策按 item_id 匹配(与采集日解耦)。拉取失败 = 零决策 = 空稿(2026-08-06 起不再兜底自动发)。
    decisions_raw: dict[str, str] = {}
    remote_raw: dict[str, str] = {}
    if decision_store is not None:
        try:
            remote_raw = dict(await decision_store.fetch())  # {item_id: action}
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命
            emit(
                logger,
                "decisions_fetch_error",
                run_id=run_id,
                error_type=type(e).__name__,
                error=str(e),
            )
    feedback_items: list = list(interpreted_items)
    skipped: list[tuple[str, str, str | None]] = []
    if reinterpret is not None:
        interpreted_items, feedback_items, skipped = await _load_kept_from_snapshots(
            remote_raw, db, reinterpret, logger
        )
    id_to_link = {_item_id(it): it.link for it in [*interpreted_items, *feedback_items]}
    for item_id, action in remote_raw.items():
        link = id_to_link.get(item_id)
        if link is not None and action in ("keep", "drop"):
            decisions_raw[link] = action
            await db.update_decision(item_id, action)  # 记录用, 无行则 no-op
    # KV 只留 7 天; 留一份到库里, 才能按来源看长期保留率(2026-09-17)
    if remote_raw:
        await db.record_decisions(remote_raw, ts=now.isoformat())
```

- [ ] **Step 5: Implement — 跨期去重与组稿后的跳过统计**

把

```python
    report_items = [it for it in report_items if _item_id(it) not in already]
```

改为

```python
    skipped += [(_item_id(it), "already_published", None) for it in report_items if _item_id(it) in already]
    report_items = [it for it in report_items if _item_id(it) not in already]
```

在 `if not rres.reviewed_items:` 之前加 `final: list = []`;在 `else:` 分支里 `report = build_report(rres, date_label, pcfg)` 之后紧跟:

```python
        final = [it for cat in report.categories for it in cat.items]
```

在 `await db.mark_published(...)` 之前加:

```python
    # 没进成品的 keep 条目逐条记原因(只记日志, 不改筛选行为)。
    final_ids = {_item_id(it) for it in final}
    final_models = {it.model for it in final}
    for it in report_items:
        if _item_id(it) not in final_ids:
            reason = "model_mismatch" if final and it.model not in final_models else "rule_cut"
            skipped.append((_item_id(it), reason, None))
    for iid, reason, err in skipped:
        emit(logger, "finalize_item_skipped", run_id=run_id, item_id=iid, reason=reason, error=err)
    skipped_by_reason = dict(Counter(reason for _, reason, _ in skipped))
    emit(
        logger,
        "finalize_summary",
        run_id=run_id,
        kept=sum(1 for a in remote_raw.values() if a == "keep"),
        published=pres.report.item_count,
        skipped_by_reason=skipped_by_reason,
    )
```

- [ ] **Step 6: Implement — 反馈与返回值**

反馈块里 `derive_events(interpreted_items, decisions, ...)` 改为 `derive_events(feedback_items, decisions, ...)`。返回 dict 加一行:

```python
        "skipped_by_reason": skipped_by_reason,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_tick_decisions.py tests/golden/test_tick.py tests/contract/test_reminder_tick.py -v`
Expected: 全部 PASS(`reinterpret=None` 的旧测试行为不变)

- [ ] **Step 8: Commit**

```bash
git add src/pipeline/tick.py tests/contract/test_tick_decisions.py
git commit -m "feat(tick): finalize re-interprets kept snapshots, logs every skipped keep (A1)"
```

---

### Task 4: `run_tick` 接线 —— finalize 不再重采集

**Files:**
- Modify: `src/cli.py`(`run_tick`)
- Modify: `.github/workflows/finalize.yml`
- Test: `tests/contract/test_tick_cli.py`(用新测试**替换** `test_finalize_reuses_interpretations_written_by_collect`——它锁的"finalize 复用白天缓存"正是本次按用户决定取消的行为)

**Interfaces:**
- Consumes: `run_finalize_tick(..., reinterpret=...)`(Task 3)、`run_collect_tick` 写快照(Task 2)
- Produces: `run_tick(tick="finalize", ...)` 不调用 `collect`,返回 dict 含 `skipped_by_reason`

- [ ] **Step 1: Write the failing test(替换旧测试)**

删除 `test_finalize_reuses_interpretations_written_by_collect` 整个函数,在同位置写:

```python
def test_finalize_reinterprets_kept_snapshots_without_collecting(tmp_path, monkeypatch):
    """A1 接线: 走 run_tick 真实入口。collect 写快照 → finalize 不调 collect、
    只对 keep 条目绕过缓存(cache=None)重新解读, keep 那条进报告。"""
    from src.adapters.decisions.worker import FakeDecisionStore
    from src.core.types import CollectionResult, Genre, Publisher, RawItem
    from src.notifiers import FakeNotifier
    from src.pipeline.tick import _item_id
    from tests.fakes import FakeLLMProvider

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("DECISIONS_API_SECRET", "x")
    ok = json.dumps(
        {
            "title": "中文标题",
            "body": "正文。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "c", "anchor": "https://openai.com/news/0"}],
            "relevant": True,
        }
    )
    kw = dict(
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
    )
    raw = [
        RawItem(
            title_en=f"OpenAI ships thing {i}",
            link=f"https://openai.com/news/{i}",
            source="openai",
            genre=Genre.announcement,
            publisher=Publisher.lab,
            published_at=NOW,
            raw_summary="A summary.",
            adapter="rss",
        )
        for i in range(3)
    ]

    async def _fixed_collect(cfg, ctx):
        return CollectionResult(items=list(raw), source_reports=[], is_silent=False)

    monkeypatch.setattr(cli_module, "collect", _fixed_collect)
    run_tick(tick="collect", llm=FakeLLMProvider({}, default=ok), **kw)

    def _boom(*a, **k):
        raise AssertionError("finalize must not collect")

    class _NoImages:
        async def fetch_html(self, url):
            return None

        async def check_image(self, url):
            return False

    keep_id = _item_id(raw[0])
    monkeypatch.setattr(cli_module, "collect", _boom)
    monkeypatch.setattr(
        cli_module, "WorkerDecisionStore", lambda *a: FakeDecisionStore({keep_id: "keep"})
    )
    monkeypatch.setattr(cli_module, "WebsiteNotifier", lambda cfg: FakeNotifier())
    monkeypatch.setattr(cli_module, "ItemImageClient", lambda **k: _NoImages())
    seen = []
    real = cli_module.interpret

    def _spy(items, *a, **k):
        seen.append((len(items), k.get("cache")))
        return real(items, *a, **k)

    monkeypatch.setattr(cli_module, "interpret", _spy)
    out = run_tick(tick="finalize", llm=FakeLLMProvider({}, default=ok), **kw)
    assert seen == [(1, None)]  # 只解读 1 条 keep, 且绕过缓存
    assert out["item_count"] == 1
    assert out["skipped_by_reason"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_tick_cli.py -k reinterprets_kept -v`
Expected: FAIL with `AssertionError: finalize must not collect`

- [ ] **Step 3: Implement — `run_tick` finalize 分支前置**

在 `run_tick` 里 `head_llm_holder: dict = {}` 之前插入:

```python
    if tick == "finalize":
        # A1(2026-09-28): 定稿不再重采集。只对 keep 条目按白天快照重新解读,
        # 且绕过解读缓存(用户定: 终版前真正刷新)。
        icfg = load_interpret_config("config/interpret.yaml")
        fin_llm = llm or _make_llm(icfg)
        scfg = load_scoring_config("config/scoring.yaml")

        def _reinterpret(items):
            return interpret(
                items,
                icfg,
                ctx,
                fin_llm,
                uncertain_content_penalty=scfg.uncertain_content_penalty,
                generate_head=False,
                cache=None,
            ).interpreted_items

        ecfg = load_enrich_config("config/enrich.yaml")
        # 结算跑在当天 23:00 本地, 但报告标的是**发布日**(用户在午夜之后发, 对应
        # 北京早上八点), 所以晚上这次标第二天。详见 _report_date。
        result = asyncio.run(
            run_finalize_tick(
                run_id=ctx.run_id,
                now=now,
                date_label=_report_date(now=now),
                interpreted_items=[],
                daily_take=None,
                db=db,
                notifiers=notifiers,
                decision_store=decision_store,
                site_base_url=dcfg.website.site_base_url,
                llm=fin_llm,
                interpret_config=icfg,
                image_client=ItemImageClient(
                    timeout_s=ecfg.item_image.timeout_s, max_bytes=ecfg.item_image.max_bytes
                ),
                item_image_config=ecfg.item_image,
                reinterpret=_reinterpret,
            )
        )
        result["tick"] = "finalize"
        return result
    if tick != "collect":
        raise ValueError(f"Unknown tick: {tick!r}. Use 'collect' or 'finalize'.")
```

- [ ] **Step 4: Implement — 收掉 collect 闭包里的 finalize 残留**

`_collect_and_interpret` 闭包里把

```python
        if tick == "finalize":
            slcfg = load_storylink_config("config/storylink.yaml")
            sl_llm = _make_storylink_llm(slcfg)
            linked_items = link_stories(sres.selected_items, sl_llm, slcfg, ctx)
        else:
            # collect tick 从不发布(...)
            linked_items = sres.selected_items
```

整段替换为 `linked_items = sres.selected_items`(闭包现在只服务 collect;`link_stories` 仍被 dry-run 路径使用,import 保留)。

删除 `# #139: 标题/摘要必须用...` 注释里已不成立的 finalize 说明,改为一行:`# collect tick 用: 把 source_reports 带出闭包给零产出告警。`

把闭包之后 `if tick == "collect": ... return {...}` 的 `if` 去掉(上面已保证只剩 collect),并删除原 `elif tick == "finalize": ...` 与 `else: raise ValueError(...)` 两个分支。

- [ ] **Step 5: Run the CLI tests**

Run: `uv run pytest tests/contract/test_tick_cli.py -v`
Expected: 全部 PASS(包括 `test_run_tick_finalize_shape`、`test_run_tick_collect_does_not_call_storylink_llm`)

- [ ] **Step 6: Workflow —— 删夜间 x-signals clone**

`.github/workflows/finalize.yml`:删除 `env` 里 `# x-extension (utils/sync.ts)...` 注释与 `X_LIST_DATA_DIR: .cache/x-signals/data/x` 两行;删除 `# finalize's --tick finalize re-runs...` 四行注释与 `- name: Fetch x-signals repo` 整个 step。

- [ ] **Step 7: Full suite + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 全绿,ruff 无输出。有 format 差异则 `uv run ruff format .` 后重跑。

- [ ] **Step 8: Commit**

```bash
git add src/cli.py tests/contract/test_tick_cli.py .github/workflows/finalize.yml
git commit -m "refactor(cli): finalize reads kept snapshots instead of re-collecting (A1, E1)"
```

---

### Task 5: 真实验证 + 文档收尾

**Files:**
- Create(不提交): `<scratchpad>/a1_real_check.py`
- Modify: `docs/KANBAN.md`(A1 行改 ☑ + PR 号;§0 重构线表 A1 状态)
- Modify: `src/pipeline/tick.py` 不动(过时注释已在 Task 3 Step 4 替换)

**Interfaces:**
- Consumes: 全部前序任务;环境变量 `MODELSCOPE_API_KEY` / `AGNES_API_KEY`(`~/.zshrc`)

- [ ] **Step 1: 写验证脚本(真采集 + 真 LLM,不写仓库不发消息)**

```python
# <scratchpad>/a1_real_check.py  —— 在仓库根目录用 `uv run python <path>` 跑
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import src.cli as cli  # noqa: E402
from src.adapters.decisions.worker import FakeDecisionStore  # noqa: E402
from src.notifiers import FakeNotifier  # noqa: E402
from src.state.db import Database  # noqa: E402

os.environ.pop("TELEGRAM_BOT_TOKEN", None)  # → FakeNotifier, 不发 TG
cli.WebsiteNotifier = lambda cfg: FakeNotifier()  # 不写 content/posts
db_path = os.path.join(tempfile.mkdtemp(), "state.db")
REG = "config/sources.yaml"

out = cli.run_tick(tick="collect", registry_path=REG, db_path=db_path)
print("collect:", out)


async def pick():
    db = Database(db_path)
    rows = await db.get_all_pending_reviews()
    return [r["item_id"] for r in rows[:2]]


keep = asyncio.run(pick())
os.environ["DECISIONS_API_SECRET"] = "x"
cli.WorkerDecisionStore = lambda *a: FakeDecisionStore({i: "keep" for i in keep})
out = cli.run_tick(tick="finalize", registry_path=REG, db_path=db_path)
print("finalize:", json.dumps(out, ensure_ascii=False))
```

- [ ] **Step 2: 跑并核对 LLM 真的成功**

Run: `source ~/.zshrc && uv run python <scratchpad>/a1_real_check.py 2>&1 | tee <scratchpad>/a1_real_check.log`

核对(逐条,任何一条不满足就停下报告,不要写"验证通过"):
- finalize 段 `interpret_done` 的 `input_count == 2` 且 `interpreted_count == 2`(不是全回退;若有 429 → 如实记录为 `interpret_failed` 跳过,符合设计,但要注明这次没证明成功路径)
- finalize 段**没有** `collect_start` / `dedup_*` / `score_*` 事件
- `finalize:` 行 `item_count + sum(skipped_by_reason.values()) == 2`

- [ ] **Step 3: KANBAN 同 PR 更新**

`docs/KANBAN.md` §3 A1 行首 `☐` → `☑`,优先级列改 `~~P0~~ **已修 #<PR>**`;§0 重构线表 A1 状态改 `✅ 已合并 #<PR>(生产效果待恢复审阅后观察)`。

- [ ] **Step 4: Commit + PR**

```bash
git add docs/KANBAN.md docs/superpowers/specs/2026-09-28-finalize-from-snapshots-design.md docs/superpowers/plans/2026-09-28-finalize-from-snapshots.md
git commit -m "docs: A1 spec/plan + KANBAN"
```

PR 描述写明:实现 spec 路径;新增测试清单(Task 1–4);真实验证日志摘要(Step 2 三项核对结果原文);已知风险(快照在 Actions cache,缓存丢失 = 当晚全 `no_snapshot`,由 B 解决);近期用户不审 → 生产上暂时看不到效果。
