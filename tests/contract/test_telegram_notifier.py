import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.notifiers.telegram_polling import TelegramPollingNotifier
from src.core.types import TelegramConfig


def _cfg():
    return TelegramConfig(bot_token="fake_token", chat_id="12345", mode="polling")


def test_send_review_card_sends_two_messages():
    """发一张卡片 = 两条消息: 封面 + 正文+按钮。"""
    async def go():
        with patch("src.notifiers.telegram_polling.Bot") as MockBot:
            mock_bot = AsyncMock()
            MockBot.return_value = mock_bot
            mock_bot.send_message.return_value = MagicMock(message_id=42)
            notifier = TelegramPollingNotifier(_cfg())
            card = {
                "title_zh": "DeepSeek-V4-Pro 发布",
                "title_en": "deepseek-ai/DeepSeek-V4-Pro",
                "source_label": "模型",
                "source": "hf-models",
                "link": "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro",
                "score": 92,
                "signals": {"likes": 4622, "hn_points": 12},
                "summary_zh": "DeepSeek 旗舰模型。",
                "takeaway": "可替换 API。",
                "hot_take": "护城河变薄。",
            }
            msg_id = await notifier.send_review_card("item_1", card)
            assert mock_bot.send_message.call_count == 2
            assert msg_id == 42
    asyncio.run(go())


def test_poll_decisions_returns_queued():
    async def go():
        with patch("src.notifiers.telegram_polling.Bot"):
            notifier = TelegramPollingNotifier(_cfg())
            notifier._decision_queue.put_nowait(("item_1", "keep"))
            notifier._decision_queue.put_nowait(("item_2", "drop"))
            decisions = await notifier.poll_decisions()
            assert set(decisions) == {("item_1", "keep"), ("item_2", "drop")}
            assert await notifier.poll_decisions() == []
    asyncio.run(go())


def test_send_final_report_sends_message():
    async def go():
        with patch("src.notifiers.telegram_polling.Bot") as MockBot:
            mock_bot = AsyncMock()
            MockBot.return_value = mock_bot
            notifier = TelegramPollingNotifier(_cfg())
            await notifier.send_final_report(
                "# AI Daily · 2026-06-05\n内容",
                {"date_label": "2026-06-05", "must_read_count": 3, "item_count": 8})
            mock_bot.send_message.assert_called_once()
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == "12345"
            assert "2026-06-05" in call_kwargs["text"]
    asyncio.run(go())
