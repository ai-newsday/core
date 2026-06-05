from src.notifiers import Notifier, FakeNotifier
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
