from __future__ import annotations

import html as html_lib

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from src.core.types import TelegramConfig


def _fmt_signals(signals: dict) -> str:
    parts = []
    if signals.get("upvotes"):
        parts.append(f"👍 <b>{signals['upvotes']:,}</b>")
    if signals.get("likes"):
        parts.append(f"👍 <b>{signals['likes']:,}</b>")
    if signals.get("hn_points"):
        parts.append(f"🔥 HN <b>{signals['hn_points']}</b>")
    return "  ｜  ".join(parts) if parts else ""


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


def _make_final_message(summary: dict) -> str:
    """终稿推送 = 简报 + 链接(HTML)。不再 dump markdown, 规避 4096 截断。"""
    esc = html_lib.escape
    date_label = esc(str(summary.get("date_label", "")))
    item_count = summary.get("item_count", 0)
    url = str(summary.get("url", ""))
    lines = [f"<b>AI Daily · {date_label}</b>", f"共 {item_count} 条", ""]
    if url:
        lines.append(f'<a href="{esc(url)}">阅读全文 →</a>')
    return "\n".join(lines)


class TelegramPollingNotifier:
    def __init__(self, config: TelegramConfig):
        self._cfg = config
        self._bot = Bot(token=config.bot_token)

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

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        text = _make_final_message(summary)
        await self._bot.send_message(
            chat_id=self._cfg.chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
