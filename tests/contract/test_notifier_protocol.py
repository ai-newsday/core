from src.notifiers import Notifier, FakeNotifier
from src.notifiers.website import WebsiteNotifier
from src.core.types import WebsiteConfig
import asyncio


def test_fake_notifier_is_notifier():
    fn = FakeNotifier()
    assert isinstance(fn, Notifier)


def test_fake_notifier_captures_cards():
    async def go():
        fn = FakeNotifier()
        await fn.send_review_card("id1", {"title_zh": "测试", "score": 80})
        await fn.send_review_card("id2", {"title_zh": "测试2", "score": 70})
        assert len(fn.sent_cards) == 2
        assert fn.sent_cards[0] == ("id1", {"title_zh": "测试", "score": 80})
    asyncio.run(go())


def test_fake_notifier_poll_decisions():
    async def go():
        fn = FakeNotifier()
        fn.queue_decision("id1", "keep")
        fn.queue_decision("id2", "drop")
        decisions = await fn.poll_decisions()
        assert ("id1", "keep") in decisions
        assert ("id2", "drop") in decisions
        assert await fn.poll_decisions() == []
    asyncio.run(go())


def test_fake_notifier_captures_final_report():
    async def go():
        fn = FakeNotifier()
        await fn.send_final_report("# Daily", {"date_label": "2026-06-05"})
        assert fn.final_report == "# Daily"
    asyncio.run(go())


def test_website_notifier_writes_file(tmp_path):
    async def go():
        cfg = WebsiteConfig(enabled=True, output_dir=str(tmp_path), git_push=False)
        notifier = WebsiteNotifier(cfg)
        await notifier.send_final_report(
            "# AI Daily · 2026-06-05\n\n内容。",
            {"date_label": "2026-06-05", "item_count": 5, "must_read_count": 3})
        out = tmp_path / "2026-06-05.md"
        assert out.exists()
        assert "# AI Daily · 2026-06-05" in out.read_text(encoding="utf-8")
    asyncio.run(go())


def test_website_notifier_disabled_does_nothing(tmp_path):
    async def go():
        cfg = WebsiteConfig(enabled=False, output_dir=str(tmp_path), git_push=False)
        notifier = WebsiteNotifier(cfg)
        await notifier.send_final_report("# Daily", {"date_label": "2026-06-05"})
        assert not any(tmp_path.iterdir())
    asyncio.run(go())


def test_website_notifier_send_review_card_is_noop(tmp_path):
    async def go():
        cfg = WebsiteConfig(enabled=True, output_dir=str(tmp_path), git_push=False)
        notifier = WebsiteNotifier(cfg)
        result = await notifier.send_review_card("id1", {"title_zh": "X"})
        assert result is None
    asyncio.run(go())
