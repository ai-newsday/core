from __future__ import annotations

import html as html_lib
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.types import TelegramConfig
from src.state.db import Database


def _fmt_signals(signals: dict) -> str:
    parts = []
    if signals.get("upvotes"):
        parts.append(f"👍 <b>{signals['upvotes']:,}</b>")
    if signals.get("likes"):
        parts.append(f"👍 <b>{signals['likes']:,}</b>")
    if signals.get("hn_points"):
        parts.append(f"🔥 HN <b>{signals['hn_points']}</b>")
    return "  ｜  ".join(parts) if parts else ""


def _make_card_messages(item_id: str, card: dict) -> tuple[str, str]:
    """返回 (封面文字, 内容文字)，均为 HTML 格式。"""
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
    summary_zh = esc(_clip(card.get("summary_zh", "")))
    takeaway = esc(_clip(card.get("takeaway", "")))
    hot_take = esc(_clip(card.get("hot_take", "")))

    cover = (
        f"<b>[{source_label}]</b>  {title_zh}\n"
        f"<i>{title_en}</i>\n\n"
        f"📊 <b>{score}</b> 分"
        + (f"  ｜  {sig_line}" if sig_line else "")
        + f'\n🔗 <a href="{link}">{source}</a>'
    )
    body = (
        f"💬 <b>一句话</b>\n{summary_zh}\n\n🛠 <b>对你</b>\n{takeaway}\n\n⚡️ <b>锐评</b>\n{hot_take}"
    )
    return cover, body


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


class TelegramPollingNotifier:
    def __init__(self, config: TelegramConfig, db: Database | None = None):
        self._cfg = config
        self._bot = Bot(token=config.bot_token)
        self._db = db

    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        cover, body = _make_card_messages(item_id, card)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ 留", callback_data=f"{item_id}:keep"),
                    InlineKeyboardButton("❌ 删", callback_data=f"{item_id}:drop"),
                    InlineKeyboardButton("⏭ 跳", callback_data=f"{item_id}:skip"),
                ]
            ]
        )
        # 消息 1: 封面
        await self._bot.send_message(
            chat_id=self._cfg.chat_id, text=cover, parse_mode="HTML", disable_web_page_preview=True
        )
        # 消息 2: 正文 + 按钮
        msg = await self._bot.send_message(
            chat_id=self._cfg.chat_id, text=body, parse_mode="HTML", reply_markup=keyboard
        )
        return msg.message_id

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        text = _make_final_message(summary)
        await self._bot.send_message(
            chat_id=self._cfg.chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _fetch_once(self) -> list[tuple[str, str]]:
        """单次 getUpdates，处理 callback_query 并持久化 offset。"""
        logger = logging.getLogger("ai-newsday")
        if self._db is None:
            return []
        offset_str = await self._db.get_kv("telegram_offset")
        offset = int(offset_str) if offset_str else None
        try:
            updates = await self._bot.get_updates(
                offset=offset, timeout=10, allowed_updates=["callback_query"]
            )
        except Exception as e:
            logger.warning("getUpdates failed: %s", e)
            return []
        out: list[tuple[str, str]] = []
        action_labels = {"keep": "✅ 已保留", "drop": "❌ 已删除", "skip": "⏭ 已跳过"}
        for update in updates:
            query = update.callback_query
            if query and query.data:
                parts = query.data.split(":", 1)
                if len(parts) == 2:
                    item_id, action = parts
                    out.append((item_id, action))
                    label = action_labels.get(action, action)
                    try:
                        await query.answer(text=label)
                    except Exception:
                        pass
                    try:
                        if query.message:
                            old_text = query.message.text or ""
                            await self._bot.edit_message_text(
                                chat_id=query.message.chat_id,
                                message_id=query.message.message_id,
                                text=f"{old_text}\n\n{label}",
                                parse_mode="HTML",
                            )
                    except Exception:
                        pass
            await self._db.set_kv("telegram_offset", str(update.update_id + 1))
        return out

    async def poll_decisions(self) -> list[tuple[str, str]]:
        """单次拉取已有 callback_query（向后兼容）。"""
        return await self._fetch_once()

    async def poll_decisions_loop(
        self, expected: int, timeout_secs: int = 120
    ) -> list[tuple[str, str]]:
        """持续轮询直到收齐 expected 条决策或超时。

        Telegram long-poll timeout=10s，所以每轮 ~10s 循环一次。
        用户点击后秒级响应，answer() 弹 toast + 编辑消息。
        """
        logger = logging.getLogger("ai-newsday")
        all_decisions: list[tuple[str, str]] = []
        seen_items: set[str] = set()
        elapsed = 0
        while len(seen_items) < expected and elapsed < timeout_secs:
            batch = await self._fetch_once()
            for item_id, action in batch:
                if item_id not in seen_items:
                    seen_items.add(item_id)
                    all_decisions.append((item_id, action))
            if len(seen_items) < expected and elapsed < timeout_secs:
                elapsed += 10  # long-poll 本身就等了 ~10s
                logger.info(
                    "poll_decisions_loop: %d/%d decided, %.0fs elapsed",
                    len(seen_items),
                    expected,
                    elapsed,
                )
        return all_decisions
