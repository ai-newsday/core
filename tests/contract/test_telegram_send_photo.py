from unittest.mock import AsyncMock, MagicMock

import pytest

from src.notifiers.telegram_polling import TelegramPollingNotifier


@pytest.fixture
def notifier():
    n = TelegramPollingNotifier.__new__(TelegramPollingNotifier)
    n._bot = MagicMock()
    n._bot.send_photo = AsyncMock()
    cfg = MagicMock()
    cfg.chat_id = "12345"
    n._cfg = cfg
    return n


async def test_send_photo_calls_bot_with_file_and_caption(notifier, tmp_path):
    p = tmp_path / "test.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")
    await notifier.send_photo(p, "hello 📊")
    notifier._bot.send_photo.assert_awaited_once()
    call = notifier._bot.send_photo.await_args
    assert call.kwargs["chat_id"] == "12345"
    assert call.kwargs["caption"] == "hello 📊"
    assert call.kwargs["parse_mode"] == "HTML"
    assert call.kwargs["photo"] is not None
