"""公众号版渲染 (spec docs/superpowers/specs/2026-08-31-wechat-format-design.md §1/§3b/§5/§6)。

这份 md 至今是我每天从网站版手工转换的:加目录、清标签、改裸 URL、补分隔线。
2026-09-03 漏目录、更早漏标签清洗与摘要——**出错的每一次都在手工那一步,不在流水线**。
这个测试文件的存在就是为了把那些环节收进代码。
"""

from src.core.types import (
    CategorySection,
    DailyReport,
    Evidence,
    Genre,
    Overview,
    PublishConfig,
    Publisher,
    ReviewedItem,
)
from src.pipeline.publish import render_markdown, render_wechat

NOW_LABEL = "2026-09-04"


def _item(title, link, *, status="ok", body="正文内容。", tags=None, evidence=(), image=None):
    it = ReviewedItem(
        title_en="en title",
        link=link,
        source="src",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at="2026-09-04T00:00:00Z",
        cluster_id="c",
        related_links=[],
        score=80,
        score_breakdown={},
        title=title,
        body=body,
        tags=list(tags or []),
        evidence=[Evidence(claim=c, anchor=a) for c, a in evidence],
        interpretation_status=status,
        eligible_for_must_read=True,
        review_action="keep",
        was_edited=False,
    )
    it.image_url = image
    return it


def _report(items, *, title="标题一 | 标题二【AI日报】", digest="今日亮点：X。"):
    return DailyReport(
        date_label=NOW_LABEL,
        daily_take=digest,
        wechat_title=title,
        categories=[CategorySection(genre="model", label="模型", items=items)],
        overview=Overview(total=len(items), by_genre={}, by_source={}),
        is_pending=False,
        item_count=len(items),
        explore_count=0,
    )


def _cfg():
    return PublishConfig()


def test_no_front_matter_no_footer_no_h1():
    """spec §1: 三样粘进 doocs/md 会变成垃圾的东西都不能有。"""
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    assert not out.startswith("---")
    assert "draft:" not in out
    assert "RSS · 历史归档 · 主站" not in out
    assert "# AI Daily" not in out


def test_title_is_the_first_line_as_plain_text():
    """标题要能直接复制进公众号编辑器的标题栏,所以放第一行;
    但不是 H1(spec §1: 正文不该再重复一个 H1)。"""
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    first = out.split("\n", 1)[0]
    assert first == "标题一 | 标题二【AI日报】"
    assert not first.startswith("#")


def test_digest_is_plain_not_a_blockquote():
    """网站版把摘要渲染成引用块(`> `),公众号版不需要那层。"""
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    assert "今日亮点：X。" in out
    assert "> 今日亮点" not in out


def test_toc_lists_every_item_in_order():
    """spec §3b: 摘要下方、第一条正文之前,无锚点编号列表。"""
    items = [
        _item("条目一", "https://a"),
        _item("条目二", "https://b"),
        _item("条目三", "https://c"),
    ]
    out = render_wechat(_report(items), _cfg())
    toc = out.split("## 目录", 1)[1].split("---", 1)[0]
    assert "1. 条目一" in toc
    assert "2. 条目二" in toc
    assert "3. 条目三" in toc


def test_toc_sits_between_the_digest_and_the_first_item():
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    assert out.index("今日亮点") < out.index("## 目录") < out.index("## 条目一")


def test_toc_has_no_links():
    """公众号锚点跳不动,做成链接只会让读者点了没反应,比不做更差(spec §3b)。"""
    toc = render_wechat(_report([_item("条目一", "https://a")]), _cfg()).split("## 目录", 1)[1]
    toc = toc.split("## 条目一", 1)[0]
    assert "](" not in toc and "http" not in toc


def test_extractive_fallback_items_are_excluded():
    """spec §5: 回退条目按定义就是"没解读成功"——标题是英文原文、正文是原始摘要。"""
    items = [
        _item("正常条目", "https://a"),
        _item("Raw English Title", "https://b", status="extractive_fallback"),
    ]
    out = render_wechat(_report(items), _cfg())
    assert "正常条目" in out
    assert "Raw English Title" not in out


def test_numbering_stays_consistent_after_excluding_a_fallback():
    """回归:目录 / 正文角标 / 参考表三处编号必须一致。
    剔除回退条目后如果只在其中一处重排,读者看到的角标就会指错行。"""
    items = [
        _item("第一条", "https://a"),
        _item("Dropped", "https://b", status="extractive_fallback"),
        _item("第二条", "https://c"),
    ]
    out = render_wechat(_report(items), _cfg())
    toc = out.split("## 目录", 1)[1].split("## 第一条", 1)[0]
    assert "1. 第一条" in toc and "2. 第二条" in toc
    refs = out.split("## 参考链接", 1)[1]
    assert "1. 第一条 — https://a" in refs
    assert "2. 第二条 — https://c" in refs
    assert "https://b" not in out


def test_references_use_bare_urls_not_markdown_links():
    """spec §6: 公众号里 Markdown 链接渲染成不可点的彩色文字,反而把地址藏起来;
    裸 URL 至少能被复制。"""
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    refs = out.split("## 参考链接", 1)[1]
    assert "1. 条目一 — https://a" in refs
    assert "](https://a)" not in refs


def test_website_version_still_uses_markdown_links():
    """两版差异只在公众号那边,网站版不受影响。"""
    out = render_markdown(_report([_item("条目一", "https://a")]), _cfg())
    assert "1. [条目一](https://a)" in out


def test_items_are_separated_by_a_rule():
    """spec §4: 条目之间加 `---`。"""
    items = [_item("条目一", "https://a"), _item("条目二", "https://b")]
    out = render_wechat(_report(items), _cfg())
    between = out.split("## 条目一", 1)[1].split("## 条目二", 1)[0]
    assert "\n---\n" in between


def test_item_keeps_heading_image_body_tags_and_marks():
    it = _item(
        "条目一",
        "https://a",
        body="正文内容。",
        tags=["#标签一", "#标签二"],
        image="https://img/x.png",
    )
    out = render_wechat(_report([it]), _cfg())
    assert "## 条目一" in out
    assert "![](https://img/x.png)" in out
    assert "正文内容。" in out
    assert "#标签一 #标签二" in out
    assert "[1]" in out


def test_no_score_line():
    out = render_wechat(_report([_item("条目一", "https://a")]), _cfg())
    assert "分" not in out.split("## 条目一", 1)[1].split("\n")[2]


def test_all_items_fallback_yields_a_structurally_complete_but_short_document():
    """spec §8: 全部回退时不报错,可以很短但结构完整。"""
    items = [_item("A", "https://a", status="extractive_fallback")]
    out = render_wechat(_report(items), _cfg())
    assert out.split("\n", 1)[0] == "标题一 | 标题二【AI日报】"
    assert "今日亮点" in out
    assert "## 目录" not in out  # 没有条目就没有目录, 不留一个空壳章节


def test_missing_title_and_digest_do_not_crash():
    r = _report([_item("条目一", "https://a")], title=None, digest=None)
    out = render_wechat(r, _cfg())
    assert "## 条目一" in out


def test_evidence_anchor_gets_its_own_reference_number():
    it = _item("条目一", "https://a", evidence=[("依据", "https://evidence")])
    out = render_wechat(_report([it]), _cfg())
    refs = out.split("## 参考链接", 1)[1]
    assert "1. 条目一 — https://a" in refs
    assert "2. 依据 — https://evidence" in refs
    assert "[1][2]" in out
