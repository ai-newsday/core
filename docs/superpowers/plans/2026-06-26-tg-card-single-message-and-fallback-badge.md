# TG 卡片合一 + 回退徽章 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TG 评审卡片要么完整发出(含按钮)、要么不发(消除孤儿封面),并把 LLM 回退降级状态明示给用户。

**Architecture:** 改 [src/notifiers/telegram_polling.py](../../../src/notifiers/telegram_polling.py) 把卡片从"封面+正文"两条消息合为单条带按钮的消息;改 [src/pipeline/tick.py](../../../src/pipeline/tick.py) `_build_card` 透出 `interpretation_status`;renderer 检测 `extractive_fallback` 时 cover 加前缀 `⚠️ [未解读] `,空 body 填占位文。

**Tech Stack:** Python 3.12, python-telegram-bot, pytest(mock), ruff。

## Global Constraints

- 外科手术式: 只动 `src/notifiers/telegram_polling.py` + `src/pipeline/tick.py` `_build_card`;不改 interpret/webhook/publish/markdown 报告通路。
- 占位文固定字面值: `(未生成解读，请参见原文链接)`;徽章固定字面值: `⚠️ [未解读] `(带尾随空格,接在原 cover 第一行前)。
- 单消息长度上限 4096(Telegram 硬限);现实测算 cover ~80 + body ≤240 + tags ~30 ≈ 350,远未触及。
- TDD: 先写失败测试。`uv run python -m pytest`(本仓 deps 经 uv 装)。**commit 前本地 `uv run ruff check` + `ruff format --check`**(CI 跑,pytest 不含)。
- 设计文档: [docs/superpowers/specs/2026-06-26-tg-card-single-message-and-fallback-badge-design.md](../specs/2026-06-26-tg-card-single-message-and-fallback-badge-design.md)

---

### Task 1: 卡片合并为单消息 + 空 body 占位(治病 1: 孤儿/无按钮)

把 `_make_card_messages`(返回 cover/body tuple)改为 `_make_card_message`(返回单 str,含 cover + body + tags)。`send_review_card` 只调 `send_message` 一次,按钮挂这条消息。空 body 填占位文。

**Files:**
- Modify: `src/notifiers/telegram_polling.py`(`_make_card_messages` → `_make_card_message`;`send_review_card` 两次 send 合一)
- Modify: `tests/contract/test_telegram_notifier.py`(两个旧测试改用新签名 + 加 4 个新测试)

**Interfaces:**
- Produces: `_make_card_message(item_id: str, card: dict) -> str`(HTML 文本,含 cover/body/tags,可直接进 send_message)。`send_review_card` 内 `send_message` 调用次数 == 1。

- [ ] **Step 1: 改两个既有测试为新签名(失败)**

`tests/contract/test_telegram_notifier.py`,替换 `test_card_cover_escapes_link_url`:

```python
def test_card_cover_escapes_link_url():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "T",
        "title_en": "T",
        "source_label": "论文",
        "source": "s",
        "link": "https://x/search?a=1&b=2<script>",
        "score": 88,
        "signals": {},
        "body": "x",
        "tags": [],
    }
    msg = _make_card_message("id1", card)
    assert "&amp;" in msg  # & escaped
    assert "<script>" not in msg  # raw < not present
    assert 'href="https://x/search?a=1&b=2<script>"' not in msg
```

替换 `test_card_body_bounded_under_telegram_limit`:

```python
def test_card_body_bounded_under_telegram_limit():
    from src.notifiers.telegram_polling import _make_card_message

    big = "字" * 5000
    card = {
        "title_zh": "T",
        "title_en": "T",
        "source_label": "论文",
        "source": "s",
        "link": "https://x/1",
        "score": 88,
        "signals": {},
        "body": big,
        "tags": [],
    }
    msg = _make_card_message("id1", card)
    assert len(msg) < 4096
```

- [ ] **Step 2: 写新失败测试 — 空 body 占位 + 单消息含 cover 三部分**

`tests/contract/test_telegram_notifier.py` 末尾追加:

```python
def test_card_empty_body_uses_placeholder():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "标题",
        "title_en": "Title",
        "source_label": "模型",
        "source": "hf-models",
        "link": "https://x/1",
        "score": 80,
        "signals": {},
        "body": "",  # interpret 回退 + raw_summary 空
        "tags": [],
    }
    msg = _make_card_message("id1", card)
    assert "(未生成解读，请参见原文链接)" in msg
    assert msg.strip()  # 不空


def test_card_message_contains_cover_body_and_tags():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "中文标题",
        "title_en": "English title",
        "source_label": "论文",
        "source": "hf-papers",
        "link": "https://x/1",
        "score": 88,
        "signals": {"upvotes": 12},
        "body": "正文内容",
        "tags": ["#a", "#b"],
    }
    msg = _make_card_message("id1", card)
    # cover 三件套
    assert "[论文]" in msg and "中文标题" in msg
    assert "English title" in msg
    assert "88" in msg and "hf-papers" in msg
    # body + tags
    assert "正文内容" in msg
    assert "#a #b" in msg
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py -k "card_" -q`
Expected: FAIL — `cannot import name '_make_card_message'`(import 错误,新名字尚未存在)。

- [ ] **Step 4: 实现 `_make_card_message`(合并 cover/body 为单 str + 空 body 占位)**

`src/notifiers/telegram_polling.py`,替换 `_make_card_messages` 整个函数为:

```python
def _make_card_message(item_id: str, card: dict) -> str:
    """卡片合一: 返回单条 HTML 文本(含封面+正文+tags),按钮在 send_review_card 挂这条上。
    空 body 用占位文兜底(interpret 回退 + raw_summary 空时,防 Telegram 拒空 text 致孤儿)。"""
    esc = html_lib.escape

    def _clip(s: str, n: int = 1000) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    source_label = esc(card.get("source_label", ""))
    title_zh = esc(card.get("title_zh", ""))
    title_en = esc(card.get("title_en", ""))
    score = card.get("score", 0)
    source = esc(card.get("source", ""))
    link = card.get("link", "")
    sig_line = _fmt_signals(card.get("signals", {}))
    raw_body = card.get("body", "") or ""
    body = esc(_clip(raw_body)) if raw_body else "(未生成解读，请参见原文链接)"
    tags = " ".join(esc(str(t)) for t in card.get("tags", []))

    cover = (
        f"<b>[{source_label}]</b> {title_zh}\n"
        f"<i>{title_en}</i>\n\n"
        f"<b>{score}</b> 分"
        + (f" ｜ {sig_line}" if sig_line else "")
        + f'\n<a href="{esc(link)}">{source}</a>'
    )
    return cover + "\n\n" + body + (f"\n\n{tags}" if tags else "")
```

- [ ] **Step 5: 跑前 4 个测试确认通过**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py -k "card_" -q`
Expected: PASS(4)

- [ ] **Step 6: 写新失败测试 — send_review_card 只调 send_message 一次**

`tests/contract/test_telegram_notifier.py` 末尾追加:

```python
def test_send_review_card_calls_send_message_once(monkeypatch):
    """病 1 修复: 卡片合一 → send_message 调一次, 不可能出现孤儿封面。"""
    from unittest.mock import AsyncMock, MagicMock

    from src.core.types import TelegramConfig
    from src.notifiers.telegram_polling import TelegramPollingNotifier

    cfg = TelegramConfig(bot_token="t", chat_id=1, webhook_decisions_path="/tmp/d.json")
    notifier = TelegramPollingNotifier(cfg)
    sent_msg = MagicMock(message_id=999)
    notifier._bot.send_message = AsyncMock(return_value=sent_msg)

    card = {
        "title_zh": "T", "title_en": "T", "source_label": "论文",
        "source": "s", "link": "https://x/1", "score": 88,
        "signals": {}, "body": "b", "tags": [],
    }

    import asyncio
    msg_id = asyncio.run(notifier.send_review_card("id1", card))

    assert notifier._bot.send_message.await_count == 1
    assert msg_id == 999
    # reply_markup(按钮)必须挂在这条消息上
    _, kwargs = notifier._bot.send_message.await_args
    assert kwargs.get("reply_markup") is not None
```

确认 `TelegramConfig` 的字段名: 看文件顶部 import 与 fixture 用法,若 `webhook_decisions_path` 字段不存在则去掉该 kw。

- [ ] **Step 7: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py::test_send_review_card_calls_send_message_once -q`
Expected: FAIL — `assert 2 == 1`(当前实现仍调 2 次 send_message)。

- [ ] **Step 8: 实现 — `send_review_card` 合并为单次 send_message**

`src/notifiers/telegram_polling.py` `send_review_card` 整体替换为:

```python
    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        text = _make_card_message(item_id, card)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ 留", callback_data=f"{item_id}:keep"),
                    InlineKeyboardButton("❌ 删", callback_data=f"{item_id}:drop"),
                    InlineKeyboardButton("⏭ 跳", callback_data=f"{item_id}:skip"),
                ]
            ]
        )
        msg = await self._bot.send_message(
            chat_id=self._cfg.chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )
        return msg.message_id
```

- [ ] **Step 9: 跑全 telegram_notifier 套件 + ruff**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py -q`
Expected: PASS(全部)
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 通过(不过则 `uv run ruff format src tests`)

- [ ] **Step 10: Commit**

```bash
git add src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py
git commit -m "fix(tg-card): merge cover+body into one message, placeholder when body empty

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 回退徽章(治病 2: 不翻译 UX 透明)

`_build_card` 透出 `status` 字段(`interpretation_status`)。`_make_card_message` 检测 `status == "extractive_fallback"` → cover 前置 `⚠️ [未解读] ` 徽章。

**Files:**
- Modify: `src/pipeline/tick.py` `_build_card`(加 `status` 字段)
- Modify: `src/notifiers/telegram_polling.py` `_make_card_message`(读 status 加徽章)
- Modify: `tests/contract/test_telegram_notifier.py`(2 新测试)

**Interfaces:**
- Consumes: `_make_card_message(item_id, card)`(Task 1)。
- Produces: card dict 多一个 `status: str` 键(取自 `item.interpretation_status`);renderer status=="extractive_fallback" → 输出以 `⚠️ [未解读] ` 开头。

- [ ] **Step 1: 写失败测试 — fallback 徽章 / ok 不带**

`tests/contract/test_telegram_notifier.py` 末尾追加:

```python
def test_card_fallback_shows_badge():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "mauriceboe/TREK", "title_en": "mauriceboe/TREK",
        "source_label": "博客 / 工具", "source": "gh-trending-ai",
        "link": "https://x/1", "score": 95, "signals": {},
        "body": "A self-hosted planner.", "tags": [],
        "status": "extractive_fallback",
    }
    msg = _make_card_message("id1", card)
    assert msg.startswith("⚠️ [未解读] ")


def test_card_ok_has_no_badge():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "中文标题", "title_en": "Title",
        "source_label": "论文", "source": "hf-papers",
        "link": "https://x/1", "score": 88, "signals": {},
        "body": "正文", "tags": [],
        "status": "ok",
    }
    msg = _make_card_message("id1", card)
    assert "未解读" not in msg
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py -k "fallback_shows_badge or ok_has_no_badge" -q`
Expected: FAIL — fallback 用例不带前缀(当前实现不读 status)。

- [ ] **Step 3: 实现 — renderer 加徽章**

`src/notifiers/telegram_polling.py` `_make_card_message` 末尾 `return` 前(在拼接 cover 之后),加:

```python
    if card.get("status") == "extractive_fallback":
        cover = "⚠️ [未解读] " + cover
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py -k "fallback_shows_badge or ok_has_no_badge" -q`
Expected: PASS(2)

- [ ] **Step 5: 写失败测试 — `_build_card` 透出 status**

`tests/contract/test_telegram_notifier.py` 末尾追加(顶部已 import 区在文件最上;此处局部 import):

```python
def test_build_card_includes_interpretation_status():
    """_build_card 透出 status 字段, renderer 用它决定徽章。"""
    import hashlib
    from datetime import datetime, timezone

    from src.core.types import Evidence, Genre, InterpretedItem, Publisher
    from src.pipeline.tick import _build_card

    item = InterpretedItem(
        title_en="x", link="https://x/1", source="s",
        genre=Genre.writeup, publisher=Publisher.individual,
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        signals={},
        cluster_id=hashlib.sha256(b"x").hexdigest()[:16],
        related_links=[],
        score=80, score_breakdown={"技术价值": 80.0},
        title="x", body="b",
        tags=["#a"], evidence=[Evidence(claim="c", anchor="https://x/1")],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
    )
    card = _build_card(item)
    assert card["status"] == "extractive_fallback"
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py::test_build_card_includes_interpretation_status -q`
Expected: FAIL — `KeyError: 'status'`。

- [ ] **Step 7: 实现 — `_build_card` 加 status 字段**

`src/pipeline/tick.py` `_build_card` 返回的 dict,加一行 `"status": item.interpretation_status,`(放在末尾, "tags" 后):

```python
def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
        "status": item.interpretation_status,
    }
```

- [ ] **Step 8: 跑全 tick + telegram 套件 + ruff**

Run: `uv run python -m pytest tests/contract/test_telegram_notifier.py tests/contract/test_tick_decisions.py tests/contract/test_tick_cli.py tests/golden/test_tick.py -q`
Expected: PASS(全部)
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: 通过

- [ ] **Step 9: Commit**

```bash
git add src/pipeline/tick.py src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py
git commit -m "feat(tg-card): badge extractive_fallback cards so degraded LLM output is visible

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 目标1(不可能孤儿)→ Task 1 合一 + send_message 调用次数测试 ✓
- 目标2(空 body 不致命)→ Task 1 Step 4 占位文 + Step 2 测试 ✓
- 目标3(降级可见)→ Task 2 status 字段 + 徽章 + 2 个测试 ✓
- 目标4(外科手术)→ 只动 `telegram_polling.py` + `tick.py:_build_card`;interpret/webhook/publish 未碰 ✓

**Placeholder scan:** 无 TBD/TODO;每个 code step 含完整代码。Task 1 Step 6 提示验证 `TelegramConfig` 字段名 = 适配性指引,非 placeholder。

**Type consistency:** `_make_card_message(item_id: str, card: dict) -> str` 全程一致(Task 1 定义,Task 2 复用)。card dict `"status"` 键(Task 2 Step 7 加,Step 3 renderer 读)统一命名。`item.interpretation_status` 与既有 InterpretedItem 字段名一致(grep 已确认 5 处用例)。
