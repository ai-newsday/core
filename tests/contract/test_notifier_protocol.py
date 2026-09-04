import asyncio

from src.core.types import WebsiteConfig
from src.notifiers import FakeNotifier, Notifier
from src.notifiers.website import WebsiteNotifier


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
            {"date_label": "2026-06-05", "item_count": 5, "must_read_count": 3},
        )
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


def test_website_notifier_writes_the_wechat_file_alongside_the_post(tmp_path):
    """公众号版落盘 (spec 2026-08-31 §1: 两份文件, 不是一份)。

    这份 md 至今每天由人手工从网站版转换, 漏目录/漏标签清洗/漏摘要都出在那一步。
    """

    async def go():
        cfg = WebsiteConfig(
            enabled=True,
            output_dir=str(tmp_path / "posts"),
            wechat_output_dir=str(tmp_path / "wechat"),
            git_push=False,
        )
        await WebsiteNotifier(cfg).send_final_report(
            "---\ntitle: x\n---\n# AI Daily · 2026-09-04",
            {"date_label": "2026-09-04"},
            wechat_markdown="标题【AI日报】\n\n今日亮点：X。\n",
        )
        post = tmp_path / "posts" / "2026-09-04.md"
        wechat = tmp_path / "wechat" / "2026-09-04.md"
        assert post.exists() and wechat.exists()
        assert "# AI Daily" in post.read_text(encoding="utf-8")
        w = wechat.read_text(encoding="utf-8")
        assert w.startswith("标题【AI日报】")
        assert "front matter" not in w and "title: x" not in w

    asyncio.run(go())


def test_website_notifier_without_wechat_markdown_writes_only_the_post(tmp_path):
    """向后兼容: 不传公众号内容时不产出空文件。"""

    async def go():
        cfg = WebsiteConfig(
            enabled=True,
            output_dir=str(tmp_path / "posts"),
            wechat_output_dir=str(tmp_path / "wechat"),
            git_push=False,
        )
        await WebsiteNotifier(cfg).send_final_report("# Daily", {"date_label": "2026-09-04"})
        assert (tmp_path / "posts" / "2026-09-04.md").exists()
        assert not (tmp_path / "wechat").exists()

    asyncio.run(go())
