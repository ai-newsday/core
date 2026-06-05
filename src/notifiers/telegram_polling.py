from __future__ import annotations
import asyncio
import html as html_lib
import queue
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler
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


def _make_card_messages(item_id: str, card: dict) -> tuple[str, str]:
    """返回 (封面文字, 内容文字)，均为 HTML 格式。"""
    esc = html_lib.escape
    source_label = esc(card.get("source_label", ""))
    title_zh = esc(card.get("title_zh", ""))
    title_en = esc(card.get("title_en", ""))
    score = card.get("score", 0)
    source = esc(card.get("source", ""))
    link = card.get("link", "")
    sig_line = _fmt_signals(card.get("signals", {}))
    summary_zh = esc(card.get("summary_zh", ""))
    takeaway = esc(card.get("takeaway", ""))
    hot_take = esc(card.get("hot_take", ""))

    cover = (
        f"<b>[{source_label}]</b>  {title_zh}\n"
        f"<i>{title_en}</i>\n\n"
        f"📊 <b>{score}</b> 分"
        + (f"  ｜  {sig_line}" if sig_line else "") +
        f"\n🔗 <a href=\"{link}\">{source}</a>"
    )
    body = (
        f"💬 <b>一句话</b>\n{summary_zh}\n\n"
        f"🛠 <b>对你</b>\n{takeaway}\n\n"
        f"⚡️ <b>锐评</b>\n{hot_take}"
    )
    return cover, body


class TelegramPollingNotifier:
    def __init__(self, config: TelegramConfig):
        self._cfg = config
        self._bot = Bot(token=config.bot_token)
        self._decision_queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._app: Application | None = None

    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        cover, body = _make_card_messages(item_id, card)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 留", callback_data=f"{item_id}:keep"),
            InlineKeyboardButton("❌ 删", callback_data=f"{item_id}:drop"),
            InlineKeyboardButton("⏭ 跳", callback_data=f"{item_id}:skip"),
        ]])
        # 消息 1: 封面
        await self._bot.send_message(
            chat_id=self._cfg.chat_id, text=cover,
            parse_mode="HTML", disable_web_page_preview=True)
        # 消息 2: 正文 + 按钮
        msg = await self._bot.send_message(
            chat_id=self._cfg.chat_id, text=body,
            parse_mode="HTML", reply_markup=keyboard)
        return msg.message_id

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        date_label = summary.get("date_label", "")
        must_read = summary.get("must_read_count", 0)
        item_count = summary.get("item_count", 0)
        header = (f"📰 <b>AI Daily · {html_lib.escape(date_label)}</b>\n"
                  f"共 {item_count} 条  |  必读 {must_read} 篇\n\n")
        body = markdown[:3800]
        await self._bot.send_message(
            chat_id=self._cfg.chat_id,
            text=header + f"<pre>{html_lib.escape(body)}</pre>",
            parse_mode="HTML")

    async def poll_decisions(self) -> list[tuple[str, str]]:
        out = []
        try:
            while True:
                out.append(self._decision_queue.get_nowait())
        except queue.Empty:
            pass
        return out

    def start_polling(self) -> None:
        """启动后台 polling 线程（长期运行时调用一次）。"""
        import threading

        self._app = (Application.builder()
                     .token(self._cfg.bot_token)
                     .build())

        decision_queue = self._decision_queue  # capture for closure

        async def _callback_handler(update: Update, context) -> None:
            query = update.callback_query
            if query and query.data:
                parts = query.data.split(":", 1)
                if len(parts) == 2:
                    item_id, action = parts
                    decision_queue.put((item_id, action))
            if query:
                try:
                    await query.answer()
                except Exception:
                    pass

        self._app.add_handler(CallbackQueryHandler(_callback_handler))

        def _run():
            self._app.run_polling(stop_signals=None)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def stop_polling(self) -> None:
        if self._app:
            self._app.stop()
