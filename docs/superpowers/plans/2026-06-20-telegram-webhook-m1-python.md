# M1 Plan 1 — Python 流水线（finalize 拉决策 + collect 去轮询 + 发送 bug 修复）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 finalize 通过可注入的 `DecisionStore` 拉回 webhook 决策并入库、collect 不再同步轮询、Telegram 终稿/卡片发送不再截断报错——全部用 Fake 注入完整 TDD，不依赖 CF Worker 先存在。

**Architecture:** 新增 `src/adapters/decisions/` 决策读取适配器（Protocol + Fake + Worker HTTP 实现）；`run_finalize_tick` 拉取远端决策幂等并入 `state.db`（失败降级不致命）；`run_collect_tick` 删掉 120s 轮询块；`TelegramPollingNotifier` 终稿改"简报+链接"、卡片字段限长——发送相关逻辑抽成纯函数直测。

**Tech Stack:** Python 3.12, pytest, httpx, python-telegram-bot, aiosqlite。

**设计依据:** `docs/superpowers/specs/2026-06-20-telegram-webhook-feedback-loop-design.md` §4.2–4.5。

---

## 文件结构

- Create: `src/adapters/decisions/__init__.py` — 决策适配器导出
- Create: `src/adapters/decisions/worker.py` — `DecisionStore` 协议 + `FakeDecisionStore` + `WorkerDecisionStore`
- Create: `tests/contract/test_decision_store.py` — 适配器测试
- Modify: `src/core/types.py` — 新增 `DecisionsApiConfig`；`WebsiteConfig` 加 `site_base_url`；`DeliveryConfig` 加 `decisions_api`
- Modify: `src/core/config.py` — `load_delivery_config` 读 decisions_api + site_base_url
- Modify: `src/pipeline/tick.py` — `run_finalize_tick` 拉取并入 + 富 summary；`run_collect_tick` 删轮询
- Modify: `src/notifiers/telegram_polling.py` — `_make_final_message` 纯函数 + `send_final_report` 改用之；`_make_card_messages` 字段限长
- Modify: `src/cli.py` — `run_tick` 构造 `WorkerDecisionStore` 传入 finalize
- Modify: `config/delivery.yaml` — 加 `decisions_api` + `website.site_base_url`
- Modify: `tests/contract/test_tick_cli.py` — collect 去轮询 + finalize 拉取的契约

---

## Task 1: 决策读取适配器（Protocol + Fake + Worker）

**Files:**
- Create: `src/adapters/decisions/__init__.py`
- Create: `src/adapters/decisions/worker.py`
- Test: `tests/contract/test_decision_store.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/contract/test_decision_store.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.decisions.worker import FakeDecisionStore, WorkerDecisionStore


def test_fake_decision_store_returns_dict_and_counts():
    store = FakeDecisionStore({"abc": "keep", "def": "drop"})
    out = asyncio.run(store.fetch())
    assert out == {"abc": "keep", "def": "drop"}
    assert store.fetch_count == 1


def test_worker_decision_store_parses_json_with_auth():
    async def go():
        with patch("src.adapters.decisions.worker.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            MockClient.return_value.__aenter__.return_value = client
            resp = MagicMock()
            resp.json.return_value = {"abc": "keep"}
            resp.raise_for_status = MagicMock()
            client.get.return_value = resp
            store = WorkerDecisionStore("https://w.example.com/", "sek", timeout_s=5)
            out = await store.fetch()
            assert out == {"abc": "keep"}
            args, kwargs = client.get.call_args
            assert args[0] == "https://w.example.com/decisions"
            assert kwargs["headers"]["Authorization"] == "Bearer sek"

    asyncio.run(go())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_decision_store.py -v`
Expected: FAIL — `ModuleNotFoundError: src.adapters.decisions.worker`

- [ ] **Step 3: 写最小实现**

```python
# src/adapters/decisions/worker.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class DecisionStore(Protocol):
    async def fetch(self) -> dict[str, str]:
        """返回 {item_id: action}，近 7 天内全部决策。"""
        ...


class FakeDecisionStore:
    """测试用，记录 fetch 次数。"""

    def __init__(self, decisions: dict[str, str] | None = None):
        self._decisions = dict(decisions or {})
        self.fetch_count = 0

    async def fetch(self) -> dict[str, str]:
        self.fetch_count += 1
        return dict(self._decisions)


class WorkerDecisionStore:
    """从 Cloudflare Worker 的 GET /decisions 拉决策。"""

    def __init__(self, url: str, secret: str, timeout_s: float = 10.0):
        self._url = url.rstrip("/") + "/decisions"
        self._secret = secret
        self._timeout = timeout_s

    async def fetch(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._secret}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return {str(k): str(v) for k, v in data.items()}
```

```python
# src/adapters/decisions/__init__.py
from src.adapters.decisions.worker import (
    DecisionStore,
    FakeDecisionStore,
    WorkerDecisionStore,
)

__all__ = ["DecisionStore", "FakeDecisionStore", "WorkerDecisionStore"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_decision_store.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/adapters/decisions/ tests/contract/test_decision_store.py
git commit -m "feat(decisions): DecisionStore adapter (Fake + Worker HTTP)"
```

---

## Task 2: 配置（DecisionsApiConfig + site_base_url）

**Files:**
- Modify: `src/core/types.py`（`TelegramConfig` 段附近，约 410-430 行）
- Modify: `src/core/config.py`（`load_delivery_config`）
- Modify: `config/delivery.yaml`
- Test: `tests/contract/test_delivery_config.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/contract/test_delivery_config.py
def test_decisions_api_and_site_base_url(tmp_path, monkeypatch):
    from src.core.config import load_delivery_config

    p = tmp_path / "delivery.yaml"
    p.write_text(
        "telegram:\n  mode: webhook\n"
        "website:\n  site_base_url: https://example.com/site/\n"
        "decisions_api:\n  url: https://w.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISIONS_API_SECRET", "sek")
    cfg = load_delivery_config(str(p))
    assert cfg.decisions_api.url == "https://w.example.com"
    assert cfg.decisions_api.secret == "sek"
    assert cfg.website.site_base_url == "https://example.com/site/"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_delivery_config.py::test_decisions_api_and_site_base_url -v`
Expected: FAIL — `AttributeError: 'DeliveryConfig' object has no attribute 'decisions_api'`

- [ ] **Step 3: 改 types.py**

在 `src/core/types.py` 的 `WebsiteConfig` 加字段、新增 `DecisionsApiConfig`、`DeliveryConfig` 加字段（用 `field` 默认）：

```python
@dataclass
class WebsiteConfig:
    enabled: bool = True
    output_dir: str = "content/posts"
    git_push: bool = False
    site_base_url: str = "https://ai-newsday.github.io/core/"


@dataclass
class DecisionsApiConfig:
    url: str = ""
    secret: str = ""  # 优先从 DECISIONS_API_SECRET 环境变量读


@dataclass
class DeliveryConfig:
    telegram: TelegramConfig
    website: WebsiteConfig
    decisions_api: DecisionsApiConfig = field(default_factory=DecisionsApiConfig)
```

确认文件顶部已 `from dataclasses import dataclass, field`；若只 import 了 `dataclass`，补上 `field`。

- [ ] **Step 4: 改 config.py**

`load_delivery_config` 里 `web = WebsiteConfig(...)` 加 `site_base_url`，并构造 `decisions_api`：

```python
    web = WebsiteConfig(
        enabled=web_data.get("enabled", True),
        output_dir=web_data.get("output_dir", "content/posts"),
        git_push=web_data.get("git_push", False),
        site_base_url=web_data.get("site_base_url", "https://ai-newsday.github.io/core/"),
    )
    da_data = data.get("decisions_api", {})
    decisions_api = DecisionsApiConfig(
        url=da_data.get("url", ""),
        secret=os.environ.get("DECISIONS_API_SECRET", da_data.get("secret", "")),
    )
    return DeliveryConfig(telegram=tg, website=web, decisions_api=decisions_api)
```

确认 `config.py` 顶部 import 含 `DecisionsApiConfig`（与 `WebsiteConfig` 同处导入）。

- [ ] **Step 5: 改 config/delivery.yaml**

追加：

```yaml
  site_base_url: "https://ai-newsday.github.io/core/"   # 放在 website: 段下

decisions_api:
  url: ""               # CF Worker 基址, 例 https://xxx.workers.dev
  # secret 通过 DECISIONS_API_SECRET 环境变量注入, 不要写死
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_delivery_config.py -v`
Expected: PASS（含新用例）

- [ ] **Step 7: 提交**

```bash
git add src/core/types.py src/core/config.py config/delivery.yaml tests/contract/test_delivery_config.py
git commit -m "feat(config): decisions_api + website.site_base_url"
```

---

## Task 3: finalize tick 拉取并入决策 + 富 summary

**Files:**
- Modify: `src/pipeline/tick.py`（`run_finalize_tick`）
- Test: `tests/contract/test_tick_decisions.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/contract/test_tick_decisions.py
import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import Evidence, Genre, InterpretedItem
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database
from src.notifiers import FakeNotifier

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


def _item(link: str, title: str) -> InterpretedItem:
    return InterpretedItem(
        link=link, title=title, title_en=title, source="hf-papers",
        genre=Genre.PAPER, score=88, signals={}, summary="s", takeaway="t",
        hot_take="h", tags=["#a", "#b", "#c"],
        evidence=[Evidence(claim="c", anchor=link)],
        eligible_for_must_read=True, interpretation_status="ok",
    )


def _iid(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def test_finalize_merges_remote_decision(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        link = "https://x/1"
        items = [_item(link, "Keep me"), _item("https://x/2", "Drop me")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        # 远端把第二条标 drop
        store = FakeDecisionStore({_iid("https://x/2"): "drop"})
        out = await run_finalize_tick(
            "r2", NOW, "2026-06-19", items, "take", db,
            [FakeNotifier()], decision_store=store, site_base_url="https://s/",
        )
        assert store.fetch_count == 1
        # drop 生效: 终稿条目数应 < 2
        assert out["item_count"] <= 1

    asyncio.run(go())


def test_finalize_decision_fetch_failure_is_non_fatal(tmp_path):
    class BoomStore:
        async def fetch(self):
            raise RuntimeError("worker down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2", NOW, "2026-06-19", items, "take", db,
            [FakeNotifier()], decision_store=BoomStore(), site_base_url="https://s/",
        )
        assert out["item_count"] >= 1  # 未崩, 降级用 DB(默认 keep)

    asyncio.run(go())
```

> 注:`InterpretedItem` 字段以 `src/core/types.py` 当前定义为准;若签名不同,按实际必填字段补齐 `_item`（保持 `eligible_for_must_read=True`、3 个 tags、合法 evidence anchor=link）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_tick_decisions.py -v`
Expected: FAIL — `run_finalize_tick() got an unexpected keyword argument 'decision_store'`

- [ ] **Step 3: 改 run_finalize_tick**

签名加两个可选参数，并在读决策前拉取并入；summary 补 `url` + `must_read_titles`：

```python
async def run_finalize_tick(
    run_id: str,
    now: datetime,
    date_label: str,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
    decision_store=None,
    site_base_url: str = "",
) -> dict:
```

在 `decisions_raw = await db.get_decisions_dict(date)` **之前** 插入：

```python
    # 先把 webhook 远端决策幂等并入 DB(失败降级, 非致命)
    if decision_store is not None:
        try:
            remote = await decision_store.fetch()
            pending = await db.get_pending_reviews_for_date(date)
            today_ids = {r["item_id"] for r in pending}
            for item_id, action in remote.items():
                if item_id in today_ids:
                    await db.update_decision(item_id, action)
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命
            emit(logger, "decisions_fetch_error", run_id=run_id, error=str(e))
```

把 `summary = {...}` 改为：

```python
    summary = {
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "must_read_count": len(pres.report.must_read),
        "url": (site_base_url.rstrip("/") + "/posts/" + date + "/") if site_base_url else "",
        "must_read_titles": [it.title for it in pres.report.must_read],
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_tick_decisions.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/tick.py tests/contract/test_tick_decisions.py
git commit -m "feat(tick): finalize pulls webhook decisions via DecisionStore (non-fatal)"
```

---

## Task 4: collect tick 删除 120s 轮询块

**Files:**
- Modify: `src/pipeline/tick.py`（`run_collect_tick`）
- Test: `tests/contract/test_tick_decisions.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_collect_no_longer_polls_decisions(tmp_path):
    """collect 只发卡片, 不再消费 FakeNotifier 里排队的决策。"""
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        notifier = FakeNotifier()
        notifier.queue_decision(_iid("https://x/1"), "drop")  # 排一个决策
        await run_collect_tick("r1", NOW, items, "take", db, [notifier])
        # collect 不轮询 -> 该 drop 不应落库
        decided = await db.get_decisions_dict("2026-06-19")
        assert decided == {}

    asyncio.run(go())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_tick_decisions.py::test_collect_no_longer_polls_decisions -v`
Expected: FAIL — 决策被旧轮询逻辑消费并落库，`decided` 非空

- [ ] **Step 3: 删轮询块**

在 `run_collect_tick` 中删除整段「收决策」逻辑（从 `# 收决策 — 支持循环轮询的 notifier 等待用户操作` 注释起，到该 `for notifier in notifiers:` poll 循环结束），保留其后的 `emit(logger, "tick_collect_done", ...)`。collect 只保留发卡片 + 写 pending。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_tick_decisions.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/tick.py tests/contract/test_tick_decisions.py
git commit -m "refactor(tick): collect no longer blocks polling for decisions"
```

---

## Task 5: 终稿改"简报+链接"（纯函数 + send_final_report）

**Files:**
- Modify: `src/notifiers/telegram_polling.py`
- Test: `tests/contract/test_telegram_notifier.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_make_final_message_is_summary_with_link():
    from src.notifiers.telegram_polling import _make_final_message

    msg = _make_final_message({
        "date_label": "2026-06-19",
        "item_count": 7,
        "must_read_count": 2,
        "must_read_titles": ["Moebius 反超 FLUX", "RATs 玩出技能"],
        "url": "https://ai-newsday.github.io/core/posts/2026-06-19/",
    })
    assert "2026-06-19" in msg
    assert "Moebius 反超 FLUX" in msg
    assert "https://ai-newsday.github.io/core/posts/2026-06-19/" in msg
    assert "<pre>" not in msg          # 不再 dump markdown
    assert len(msg) < 4096             # 不会触发 Telegram 长度上限
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_telegram_notifier.py::test_make_final_message_is_summary_with_link -v`
Expected: FAIL — `cannot import name '_make_final_message'`

- [ ] **Step 3: 加纯函数 + 改 send_final_report**

在 `src/notifiers/telegram_polling.py` 顶部辅助区加：

```python
def _make_final_message(summary: dict) -> str:
    """终稿推送 = 简报 + 链接(HTML)。不再 dump markdown, 规避 4096 截断。"""
    esc = html_lib.escape
    date_label = esc(str(summary.get("date_label", "")))
    item_count = summary.get("item_count", 0)
    must_read = summary.get("must_read_count", 0)
    titles = summary.get("must_read_titles", []) or []
    url = str(summary.get("url", ""))
    lines = [f"<b>AI Daily · {date_label}</b>", f"共 {item_count} 条，必读 {must_read} 篇", ""]
    for i, t in enumerate(titles, 1):
        lines.append(f"{i}. {esc(str(t))}")
    if url:
        lines.append("")
        lines.append(f'<a href="{esc(url)}">阅读全文 →</a>')
    return "\n".join(lines)
```

把 `send_final_report` 主体替换为：

```python
    async def send_final_report(self, markdown: str, summary: dict) -> None:
        text = _make_final_message(summary)
        await self._bot.send_message(
            chat_id=self._cfg.chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
```

（保留 `markdown` 形参以符合 `Notifier` 协议；本通道不再使用它，落站由 `WebsiteNotifier` 负责。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_telegram_notifier.py -v`
Expected: PASS（含旧 `test_send_final_report_sends_message`——它只断言 date 在 text 中 + 一次 send，仍成立）

- [ ] **Step 5: 提交**

```bash
git add src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py
git commit -m "fix(telegram): final report = summary + link (no markdown dump / no 3800 cut)"
```

---

## Task 6: 卡片字段限长（防 4096 报错）

**Files:**
- Modify: `src/notifiers/telegram_polling.py`（`_make_card_messages`）
- Test: `tests/contract/test_telegram_notifier.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_card_body_bounded_under_telegram_limit():
    from src.notifiers.telegram_polling import _make_card_messages

    big = "字" * 5000
    card = {
        "title_zh": "T", "title_en": "T", "source_label": "论文", "source": "s",
        "link": "https://x/1", "score": 88, "signals": {},
        "summary_zh": big, "takeaway": big, "hot_take": big,
    }
    cover, body = _make_card_messages("id1", card)
    assert len(cover) < 4096
    assert len(body) < 4096
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_telegram_notifier.py::test_card_body_bounded_under_telegram_limit -v`
Expected: FAIL — body 远超 4096

- [ ] **Step 3: 字段转义前限长**

在 `_make_card_messages` 里，对 `summary_zh / takeaway / hot_take` **转义前**各截到 1000 字（够展示、三段合计稳在 4096 内）。在函数内加局部 helper 并改取值：

```python
    def _clip(s: str, n: int = 1000) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    summary_zh = esc(_clip(card.get("summary_zh", "")))
    takeaway = esc(_clip(card.get("takeaway", "")))
    hot_take = esc(_clip(card.get("hot_take", "")))
```

（其余字段如 title 通常短，保持不变。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_telegram_notifier.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py
git commit -m "fix(telegram): clip card fields under 4096 to avoid send errors"
```

---

## Task 7: cli 接线 + 全量回归

**Files:**
- Modify: `src/cli.py`（`run_tick`）
- Test: `tests/contract/test_tick_cli.py`（已存在用例回归）

- [ ] **Step 1: 改 run_tick 构造 decision_store 并传入 finalize**

`src/cli.py` 顶部加导入：

```python
from src.adapters.decisions.worker import WorkerDecisionStore
```

在 `run_tick` 里 `dcfg = load_delivery_config(...)` 之后构造：

```python
    decision_store = None
    if dcfg.decisions_api.url and dcfg.decisions_api.secret:
        decision_store = WorkerDecisionStore(
            dcfg.decisions_api.url, dcfg.decisions_api.secret
        )
```

把 finalize 分支的调用改为传参：

```python
        result = asyncio.run(
            run_finalize_tick(
                run_id=ctx.run_id,
                now=now,
                date_label=date_label,
                interpreted_items=ires.interpreted_items,
                daily_take=ires.daily_take,
                db=db,
                notifiers=notifiers,
                decision_store=decision_store,
                site_base_url=dcfg.website.site_base_url,
            )
        )
```

- [ ] **Step 2: 跑相关回归**

Run: `uv run pytest tests/contract/test_tick_cli.py -v`
Expected: PASS（collect/finalize shape 用例不受影响；无 `decisions_api.url` 时 `decision_store=None`，finalize 退化为读 DB）

- [ ] **Step 3: 全量测试**

Run: `uv run pytest -q`
Expected: 全绿（含原有 287+ 用例 + 本计划新增）

- [ ] **Step 4: lint**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 无报错（如 format 有差异，先 `uv run ruff format src tests` 再提交）

- [ ] **Step 5: 提交**

```bash
git add src/cli.py
git commit -m "feat(cli): wire WorkerDecisionStore + site_base_url into finalize tick"
```

---

## Self-review 结果（写计划时自查）

- **Spec 覆盖**:§4.2 collect 去轮询=Task4;§4.3 finalize 拉取并入=Task3;§4.4 决策适配器=Task1;§4.5 发送 bug(终稿+卡片)=Task5/6;§5 配置=Task2;cli 接线=Task7。**§4.1 Worker / §4.6 PAT workflow 不在本 plan**（Plan 2 / Plan 3）。
- **类型一致**:`DecisionStore.fetch()`、`FakeDecisionStore.fetch_count`、`WorkerDecisionStore(url, secret, timeout_s)`、`run_finalize_tick(..., decision_store=None, site_base_url="")`、`_make_final_message(summary)`、`_make_card_messages` 全程一致。
- **占位**:无 TBD/TODO；每步含完整代码或确切命令。
- **风险点**:`InterpretedItem` 构造字段需对照 `src/core/types.py` 实际定义补齐（Task3 已注明）；`post URL permalink`(`/posts/<date>/`) 需在 Plan 3 实测时确认 PaperMod 真实 permalink，必要时改 `site_base_url` 拼接规则。
