import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.types import TelegramConfig
from src.notifiers.telegram_polling import TelegramPollingNotifier


def _cfg():
    return TelegramConfig(bot_token="fake_token", chat_id="12345", mode="polling")


def test_send_review_card_sends_one_message_with_keyboard():
    """病1 修复: 卡片合一 → send_message 调一次, 按钮挂这条, 消除孤儿封面。"""

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
                "body": "DeepSeek 旗舰模型，可替换 API，护城河变薄。",
                "tags": ["#DeepSeek", "#模型", "#API"],
            }
            msg_id = await notifier.send_review_card("item_1", card)
            assert mock_bot.send_message.call_count == 1
            assert msg_id == 42
            # 按钮(reply_markup)必须挂在这唯一消息上
            kwargs = mock_bot.send_message.call_args.kwargs
            assert kwargs.get("reply_markup") is not None

    asyncio.run(go())


def test_send_final_report_sends_message():
    async def go():
        with patch("src.notifiers.telegram_polling.Bot") as MockBot:
            mock_bot = AsyncMock()
            MockBot.return_value = mock_bot
            notifier = TelegramPollingNotifier(_cfg())
            await notifier.send_final_report(
                "# AI Daily · 2026-06-05\n内容",
                {"date_label": "2026-06-05", "item_count": 8},
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
            "url": "https://ai-newsday.github.io/core/posts/2026-06-19/",
        }
    )
    assert "2026-06-19" in msg
    assert "共 7 条" in msg
    assert "https://ai-newsday.github.io/core/posts/2026-06-19/" in msg
    assert "<pre>" not in msg
    assert len(msg) < 4096
    # no must-read count or titles
    assert "必读" not in msg


def test_make_final_message_no_must_read_fields():
    from src.notifiers.telegram_polling import _make_final_message

    msg = _make_final_message(
        {
            "date_label": "2026-06-20",
            "item_count": 5,
        }
    )
    assert "共 5 条" in msg
    assert "必读" not in msg
    assert "2026-06-20" in msg


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
    assert msg.strip()


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
    assert "[论文]" in msg and "中文标题" in msg
    assert "English title" in msg
    assert "88" in msg and "hf-papers" in msg
    assert "正文内容" in msg
    assert "#a #b" in msg
