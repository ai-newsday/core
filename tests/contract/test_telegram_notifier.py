import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.types import TelegramConfig
from src.notifiers.telegram_polling import TelegramPollingNotifier


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



def test_send_final_report_sends_message():
    async def go():
        with patch("src.notifiers.telegram_polling.Bot") as MockBot:
            mock_bot = AsyncMock()
            MockBot.return_value = mock_bot
            notifier = TelegramPollingNotifier(_cfg())
            await notifier.send_final_report(
                "# AI Daily · 2026-06-05\n内容",
                {"date_label": "2026-06-05", "must_read_count": 3, "item_count": 8},
            )
            mock_bot.send_message.assert_called_once()
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == "12345"
            assert "2026-06-05" in call_kwargs["text"]

    asyncio.run(go())


def test_make_final_message_is_summary_with_link():
    from src.notifiers.telegram_polling import _make_final_message

    msg = _make_final_message(
        {
            "date_label": "2026-06-19",
            "item_count": 7,
            "must_read_count": 2,
            "must_read_titles": ["Moebius 反超 FLUX", "RATs 玩出技能"],
            "url": "https://ai-newsday.github.io/core/posts/2026-06-19/",
        }
    )
    assert "2026-06-19" in msg
    assert "Moebius 反超 FLUX" in msg
    assert "https://ai-newsday.github.io/core/posts/2026-06-19/" in msg
    assert "<pre>" not in msg
    assert len(msg) < 4096


def test_card_cover_escapes_link_url():
    from src.notifiers.telegram_polling import _make_card_messages

    card = {
        "title_zh": "T", "title_en": "T", "source_label": "论文", "source": "s",
        "link": "https://x/search?a=1&b=2<script>", "score": 88, "signals": {},
        "summary_zh": "x", "takeaway": "y", "hot_take": "z",
    }
    cover, _ = _make_card_messages("id1", card)
    assert "&amp;" in cover          # & escaped
    assert "<script>" not in cover   # raw < not present
    assert 'href="https://x/search?a=1&b=2<script>"' not in cover  # raw URL not present


def test_card_body_bounded_under_telegram_limit():
    from src.notifiers.telegram_polling import _make_card_messages

    big = "字" * 5000
    card = {
        "title_zh": "T",
        "title_en": "T",
        "source_label": "论文",
        "source": "s",
        "link": "https://x/1",
        "score": 88,
        "signals": {},
        "summary_zh": big,
        "takeaway": big,
        "hot_take": big,
    }
    cover, body = _make_card_messages("id1", card)
    assert len(cover) < 4096
    assert len(body) < 4096
