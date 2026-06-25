# 零决策兜底自动出报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** finalize 时若整个 run 零决策(用户没碰 TG / 决策拉取失败),自动发"打分自动选的当日 top-N"草稿,而非空报。

**Architecture:** 单点改动 [src/pipeline/tick.py](../../../src/pipeline/tick.py) `run_finalize_tick` 的条目选择分支:`decisions` 非空走确认门 `select_report_items`(不变),`decisions` 为空则把全部 interpreted items 交给下游,由既有 publish 的 relevant+地板(+配额,若已合 #49)截 top-N。`is_pending` 逻辑不动——零决策时本就 True,渲染层自动加水印 + `draft:true`。

**Tech Stack:** Python 3.12, pytest (contract tests), ruff。

## Global Constraints

- 外科手术式: 只动 `run_finalize_tick` 一处 if/else; 不改 `select_report_items`、`review`、`publish`、webhook/DecisionStore 协议。
- TDD: 先写失败测试。CI 跑 `ruff check` + `ruff format --check`(pytest 不含),**commit 前本地 `uv run ruff` 一遍**。
- 测试用 `uv run python -m pytest`(本仓库依赖经 uv 装; 裸 python 缺 feedparser)。
- 设计文档: [docs/superpowers/specs/2026-06-25-finalize-fallback-when-unreviewed-design.md](../specs/2026-06-25-finalize-fallback-when-unreviewed-design.md)
- 本分支基于 master(不含 #49); master 的 interpreted_items ≈ score quota→top-11, 故"全部 interpreted"已自然有界, publish 地板(60)再截。

---

### Task 1: 零决策兜底自动出报

`run_finalize_tick` 的 `report_items` 选择: `decisions` 为空 → 全部 interpreted(兜底); 非空 → 确认门(不变)。同步反转两个旧测试(它们断言旧"零决策=空"行为)。

**Files:**
- Modify: `src/pipeline/tick.py`(`run_finalize_tick` 选择点,约 145-146 行)
- Test: `tests/contract/test_tick_decisions.py`

**Interfaces:**
- Consumes: `select_report_items(items, decisions)`(现有,确认门,不变); `run_finalize_tick(...) -> dict`(返回含 `item_count`、`is_pending`)。
- Produces: `run_finalize_tick` 在 `decisions == {}` 时 `report_items = list(interpreted_items)`(兜底); 否则确认门。

- [ ] **Step 1: 反转零决策测试(失败)**

`tests/contract/test_tick_decisions.py` 的 `test_finalize_zero_confirmations_empty_report` 整体替换为:

```python
def test_finalize_zero_decisions_falls_back_to_auto_publish(tmp_path):
    """零决策(没碰 TG) → 兜底自动发 top-N 草稿, 而非空报; 标 is_pending。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A"), _item("https://x/2", "B")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=FakeDecisionStore({}),
            site_base_url="https://s/",
        )
        assert out["item_count"] == 2  # 两条都过 publish 地板(score=80>60), relevant
        assert out["is_pending"] is True  # 未审 → 草稿水印 + draft:true

    asyncio.run(go())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_tick_decisions.py::test_finalize_zero_decisions_falls_back_to_auto_publish -q`
Expected: FAIL — `assert out["item_count"] == 2` 实得 0(当前零决策走确认门返回空)。

- [ ] **Step 3: 实现兜底分支**

`src/pipeline/tick.py` `run_finalize_tick`,把:

```python
    # 确认门: 只把显式 keep/edit 的条目送进报告(未确认/drop 不发)。feedback 仍吃全量(下方)。
    report_items = select_report_items(interpreted_items, decisions)
```

替换为:

```python
    # 条目选择: 有决策走确认门(只发 keep/edit); 零决策(没碰 TG / 拉取失败)兜底自动发,
    # 由 publish 的 relevant+地板(+配额)截 top-N。is_pending 仍 True → 草稿水印 + draft:true。
    if decisions:
        report_items = select_report_items(interpreted_items, decisions)
    else:
        report_items = list(interpreted_items)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/contract/test_tick_decisions.py::test_finalize_zero_decisions_falls_back_to_auto_publish -q`
Expected: PASS

- [ ] **Step 5: 更新拉取失败测试(失败 → 通过)**

`test_finalize_decision_fetch_failure_is_non_fatal` 现断言失败 → 空报。新语义: 拉取失败 = 零决策 = 兜底。整体替换为:

```python
def test_finalize_decision_fetch_failure_is_non_fatal(tmp_path):
    """拉取失败非致命: finalize 不崩。失败=零决策 → 兜底自动发(不空报)。"""

    class BoomStore:
        async def fetch(self):
            raise RuntimeError("worker down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=BoomStore(),
            site_base_url="https://s/",
        )
        # 非致命: 跑完不抛; 拉取失败 → 零决策 → 兜底发
        assert out["item_count"] == 1
        assert out["is_pending"] is True

    asyncio.run(go())
```

- [ ] **Step 6: 跑两个相关测试确认通过**

Run: `uv run python -m pytest tests/contract/test_tick_decisions.py -q`
Expected: PASS(全部)。重点确认:
- `test_finalize_merges_remote_decision`(drop 决策 → 非空 decisions → 确认门 → item_count 0,`<=1` 仍过)
- `test_finalize_only_ships_confirmed_items`(有 keep → 确认门 → 1)
两者因 `decisions` 非空,**不触发兜底**,行为不变。

- [ ] **Step 7: 跑 tick + publish 套件 + ruff**

Run: `uv run python -m pytest tests/contract/test_tick_decisions.py tests/contract/test_tick_cli.py tests/golden/test_tick.py tests/golden/test_publish.py -q`
Expected: PASS
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 通过(不过则 `uv run ruff format src tests`)

- [ ] **Step 8: Commit**

```bash
git add src/pipeline/tick.py tests/contract/test_tick_decisions.py
git commit -m "feat(finalize): auto-publish top-N draft when zero decisions (no TG review)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 目标1(零决策→兜底 top-N)→ Step 3 if/else + Step 1 测试 ✓
- 目标2(有参与→确认门不变)→ Step 6 验证 merges/only_ships 不变 ✓
- 目标3(草稿+水印)→ `is_pending` 不动, Step 1/5 断言 `is_pending is True` ✓
- 目标4(外科手术,不改协议)→ 仅改一处 if/else ✓
- 设计"拉取失败也兜底"→ Step 5 ✓

**Placeholder scan:** 无 TBD/TODO; 每个 code step 含完整代码。

**Type consistency:** `report_items`、`select_report_items(interpreted_items, decisions)`、`out["item_count"]`/`out["is_pending"]` 与现有 `run_finalize_tick` 返回 dict 一致。
