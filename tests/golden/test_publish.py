import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.types import (
    Evidence,
    Genre,
    PublishConfig,
    Publisher,
    ReviewedItem,
    ReviewResult,
    RunContext,
)
from src.pipeline.publish import (
    build_overview,
    build_report,
    flip_draft,
    group_by_category,
    publish,
    render_front_matter,
    render_markdown,
    select_must_read,
)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
CFG = PublishConfig()


def _ri(
    link="https://a/1",
    genre=Genre.model,
    publisher=Publisher.company,
    score=80,
    title="中文标题",
    body="正文一段。",
    tags=None,
    evidence=None,
    related=None,
    eligible=True,
    is_explore=False,
    status="ok",
):
    return ReviewedItem(
        title_en="X released",
        link=link,
        source="src",
        genre=genre,
        publisher=publisher,
        published_at=NOW,
        raw_summary="A.",
        cluster_id="evt-1",
        related_links=related or [],
        score=score,
        score_breakdown={"机构影响力": float(score)},
        is_explore=is_explore,
        title=title,
        body=body,
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [Evidence(claim="事实", anchor=link)],
        interpretation_status=status,
        eligible_for_must_read=eligible,
        review_action="keep",
        was_edited=False,
        edited_fields=[],
    )


def test_select_must_read_only_eligible_top_n():
    items = [
        _ri("https://a/1", eligible=True),
        _ri("https://a/2", eligible=False),
        _ri("https://a/3", eligible=True),
        _ri("https://a/4", eligible=True),
        _ri("https://a/5", eligible=True),
    ]
    mr = select_must_read(items, CFG)
    # 仅 eligible, 保上游序, 取前 3
    assert [i.link for i in mr] == ["https://a/1", "https://a/3", "https://a/4"]


def test_select_must_read_fewer_than_n():
    items = [_ri("https://a/1", eligible=True), _ri("https://a/2", eligible=False)]
    mr = select_must_read(items, CFG)
    assert [i.link for i in mr] == ["https://a/1"]


def test_group_by_category_order_and_grouping():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company),
        _ri("https://a/2", genre=Genre.paper, publisher=Publisher.company),
        _ri("https://a/3", genre=Genre.model, publisher=Publisher.company),
    ]
    cats = group_by_category(items, CFG)
    # genre_labels 键序: paper 在 model 前
    assert [c.genre for c in cats] == ["paper", "model"]
    assert cats[0].label == "论文" and cats[1].label == "模型"
    # 空类目不产 section
    assert all(len(c.items) > 0 for c in cats)
    # 组内保上游序
    assert [i.link for i in cats[1].items] == ["https://a/1", "https://a/3"]
    # 全量目录: 不漏
    assert sum(len(c.items) for c in cats) == 3


def test_group_by_category_unknown_type_last():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company),
        _ri("https://a/2", genre=Genre.writeup, publisher=Publisher.individual),
    ]
    # 构造一个不在表里的 genre 测兜底
    cfg = PublishConfig(genre_labels={"model": "模型"})
    cats = group_by_category(items, cfg)
    # model 在表里排前, writeup 不在表里排末尾且 label 回退英文
    assert [c.genre for c in cats] == ["model", "writeup"]
    assert cats[1].label == "writeup"


def test_build_overview_distribution_and_keywords():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company, tags=["#MoE", "#Agent"]),
        _ri("https://a/2", genre=Genre.model, publisher=Publisher.company, tags=["#MoE", "#推理"]),
        _ri("https://a/3", genre=Genre.paper, publisher=Publisher.company, tags=["#MoE"]),
    ]
    ov = build_overview(items, CFG)
    assert ov.genre_distribution == {"paper": 1, "model": 2}
    # MoE 频次最高在前; 去 # 前缀; 按频次降序、同频按首现序
    assert ov.keywords[0] == "MoE"
    assert set(ov.keywords) == {"MoE", "Agent", "推理"}


def test_build_overview_keywords_top_n_and_empty_tags():
    items = [_ri("https://a/1", tags=[]), _ri("https://a/2", tags=["#x", "#y", "#z", "#w", "#v"])]
    cfg = PublishConfig(top_keywords=2)
    ov = build_overview(items, cfg)
    assert len(ov.keywords) == 2


def _rr(items, daily_take="看点。", is_pending=False, is_silent=False):
    n = len(items)
    return ReviewResult(
        reviewed_items=items,
        daily_take=daily_take,
        input_count=n,
        kept_count=n,
        dropped_count=0,
        edited_count=0,
        is_reviewed=not is_pending,
        is_pending=is_pending,
        is_silent=is_silent,
    )


def test_build_report_assembles_blocks():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company, eligible=True, score=80),
        _ri(
            "https://a/2",
            genre=Genre.paper,
            publisher=Publisher.company,
            eligible=False,
            is_explore=True,
            score=70,
        ),
    ]
    rep = build_report(_rr(items), "2026-05-30（周六）", CFG)
    assert rep.date_label == "2026-05-30（周六）"
    assert rep.item_count == 2 and rep.explore_count == 1
    # must_read is always [] now
    assert rep.must_read == []
    assert [c.genre for c in rep.categories] == ["paper", "model"]
    assert rep.is_pending is False
    # 全量目录守恒
    assert sum(len(c.items) for c in rep.categories) == rep.item_count


def test_build_report_score_floor_filters_weak_items():
    """items below min_display_score (default 60) are dropped."""
    items = [
        _ri("https://a/1", score=80),
        _ri("https://a/2", score=59),  # below floor — should be dropped
        _ri("https://a/3", score=60),  # at floor — should be kept
    ]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 2
    all_links = [it.link for cat in rep.categories for it in cat.items]
    assert "https://a/1" in all_links
    assert "https://a/3" in all_links
    assert "https://a/2" not in all_links


def test_build_report_all_items_below_floor_gives_empty_categories():
    items = [
        _ri("https://a/1", score=50),
        _ri("https://a/2", score=30),
    ]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 0
    assert rep.categories == []


def test_render_markdown_full():
    items = [
        _ri(
            "https://a/1",
            genre=Genre.model,
            publisher=Publisher.company,
            title="GLM-5 发布",
            body="开源 MoE。",
            tags=["#MoE"],
            score=80,
        )
    ]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert md.startswith("# AI Daily · 2026-05-30")
    assert "> **今日看点**：看点。" in md
    # new structure: ## {label}, ### {title}, no emoji, no numbering
    assert "## 模型" in md
    assert "### GLM-5 发布" in md
    assert "开源 MoE。" in md
    assert "[src](https://a/1)" in md
    # no old structure
    assert "今日必读" not in md
    assert "分类速览" not in md
    assert "数据概览" not in md
    assert "#MoE" in md  # tags rendered as a line, keeping the # prefix
    # footer
    assert "RSS · 历史归档 · 主站 ｜ AI News Daily" in md


def test_render_markdown_no_emoji():
    items = [_ri("https://a/1", score=80)]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    # no emoji in structural lines
    assert "🏆" not in md
    assert "📚" not in md
    assert "📊" not in md
    assert "📬" not in md


def test_render_markdown_source_line_last():
    """Each item's last non-blank line must be 来源 [{source}]({link}) · {score} 分"""
    items = [
        _ri(
            "https://a/1",
            genre=Genre.model,
            title="T",
            body="B。",
            tags=["#x"],
            score=75,
        )
    ]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "来源 [src](https://a/1) · 75 分" in md


def test_render_markdown_pending_watermark():
    items = [_ri("https://a/1", score=80)]
    cfg = PublishConfig()
    md = render_markdown(build_report(_rr(items, is_pending=True), "d", cfg), cfg)
    assert cfg.pending_watermark in md
    assert "草稿待定稿" in md


def test_render_markdown_no_watermark_when_reviewed():
    items = [_ri("https://a/1", score=80)]
    cfg = PublishConfig()
    md = render_markdown(build_report(_rr(items, is_pending=False), "d", cfg), cfg)
    assert cfg.pending_watermark not in md


def test_render_markdown_omits_empty_daily_take():
    items = [_ri("https://a/1", score=80)]
    md = render_markdown(build_report(_rr(items, daily_take=None), "d", CFG), CFG)
    assert "今日看点" not in md


def test_render_markdown_score_floor_items_absent():
    """Items below score floor must not appear in rendered markdown."""
    items = [
        _ri("https://a/high", title="高分条目", score=80, genre=Genre.model),
        _ri("https://a/low", title="低分条目", score=40, genre=Genre.paper),
    ]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert "高分条目" in md
    assert "低分条目" not in md


def test_render_markdown_category_with_all_below_floor_not_rendered():
    """A genre with all items below floor produces no ## {label} section."""
    items = [
        _ri("https://a/1", title="论文A", score=40, genre=Genre.paper),
        _ri("https://a/2", title="模型B", score=80, genre=Genre.model),
    ]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert "## 论文" not in md
    assert "## 模型" in md


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-publish"))


def test_publish_empty_input_silent():
    res = publish(_rr([], daily_take=None, is_silent=True), "d", CFG, _ctx())
    assert res.is_silent is True and res.markdown == ""
    assert res.report.item_count == 0


def test_publish_pending_propagates():
    items = [_ri("https://a/1", score=80)]
    res = publish(_rr(items, is_pending=True), "d", CFG, _ctx())
    assert res.is_pending is True
    assert CFG.pending_watermark in res.markdown


def test_publish_deterministic():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company, score=80),
        _ri("https://a/2", genre=Genre.paper, publisher=Publisher.company, score=75),
    ]
    r1 = publish(_rr(items), "2026-05-30", CFG, _ctx())
    r2 = publish(_rr(items), "2026-05-30", CFG, _ctx())
    assert r1.markdown == r2.markdown
    assert r1.report.model_dump() == r2.report.model_dump()


SNAPSHOT = Path(__file__).parent / "data" / "publish_report.md"


def _snapshot_items():
    return [
        _ri(
            "https://a/1",
            genre=Genre.model,
            publisher=Publisher.company,
            title="GLM-5 发布",
            body="开源 MoE 旗舰，推理性能大幅超越上代。",
            score=88,
            tags=["#MoE", "#开源"],
            eligible=True,
        ),
        _ri(
            "https://a/2",
            genre=Genre.paper,
            publisher=Publisher.company,
            title="新论文",
            body="一句话摘要，提出新方法。",
            score=82,
            tags=["#MoE", "#推理"],
            eligible=True,
        ),
        _ri(
            "https://a/3",
            genre=Genre.writeup,
            publisher=Publisher.individual,
            title="社区热帖",
            body="探索选题，社区讨论激烈。",
            score=71,
            tags=["#Agent"],
            eligible=False,
            is_explore=True,
        ),
        _ri(
            "https://a/4",
            genre=Genre.news,
            publisher=Publisher.media,
            title="低分新闻",
            body="此条目分数不够。",
            score=45,  # below floor — should not appear
            tags=["#news"],
            eligible=False,
        ),
    ]


def test_publish_markdown_snapshot():
    res = publish(
        _rr(_snapshot_items(), daily_take="看点一句话。"), "2026-05-30（周六）", CFG, _ctx()
    )
    # publish 产物 = front matter(draft:true) + body
    assert res.markdown.startswith("---\n")
    assert "draft: true" in res.markdown.split("---", 2)[1]
    assert "# AI Daily · 2026-05-30（周六）" in res.markdown
    # new structure assertions
    assert "今日必读" not in res.markdown
    assert "分类速览" not in res.markdown
    assert "数据概览" not in res.markdown
    assert "## 论文" in res.markdown
    assert "## 模型" in res.markdown
    assert "### GLM-5 发布" in res.markdown
    assert "低分新闻" not in res.markdown  # below floor
    assert "来源 [src](https://a/1) · 88 分" in res.markdown
    if not SNAPSHOT.exists():  # 首次运行固化快照
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(res.markdown, encoding="utf-8")
    assert res.markdown == SNAPSHOT.read_text(encoding="utf-8")


def test_front_matter_draft_true():
    items = [
        _ri("https://a/1", genre=Genre.model, publisher=Publisher.company, score=80),
        _ri("https://a/2", genre=Genre.paper, publisher=Publisher.company, score=75),
    ]
    rep = build_report(_rr(items, daily_take="今天有两条。"), "2026-05-30（周六）", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert fm.startswith("---\n") and fm.rstrip().endswith("---")
    assert 'title: "AI Daily · 2026-05-30（周六）"' in fm
    assert "date: 2026-05-30T08:00:00+08:00" in fm
    assert "draft: true" in fm
    # tags = categories 的 label, genre_labels 序: paper 在 model 前
    assert 'tags: ["论文", "模型"]' in fm
    assert 'summary: "今天有两条。"' in fm


def test_front_matter_draft_false():
    rep = build_report(_rr([_ri("https://a/1", score=80)]), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=False)
    assert "draft: false" in fm
    assert "date: 2026-05-30T08:00:00+08:00" in fm


def test_front_matter_empty_daily_take():
    rep = build_report(_rr([_ri("https://a/1", score=80)], daily_take=None), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert 'summary: ""' in fm


def test_front_matter_truncates_summary_to_140():
    long = "看" * 200
    rep = build_report(_rr([_ri("https://a/1", score=80)], daily_take=long), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert "看" * 140 in fm
    assert "看" * 141 not in fm


def test_front_matter_escapes_double_quotes():
    rep = build_report(
        _rr([_ri("https://a/1", score=80)], daily_take='含"引号"的看点'), "2026-05-30", CFG
    )
    fm = render_front_matter(rep, CFG, draft=True)
    assert 'summary: "含\\"引号\\"的看点"' in fm


def test_flip_draft_true_to_false():
    text = '---\ntitle: "x"\ndraft: true\ntags: []\n---\n# body\n'
    out = flip_draft(text)
    assert "draft: false" in out
    assert "draft: true" not in out
    assert "# body" in out  # 正文不动


def test_flip_draft_idempotent_when_already_false():
    text = "---\ndraft: false\n---\nbody"
    assert flip_draft(text) == text


def test_flip_draft_no_front_matter_unchanged():
    text = "# just a body, no front matter\n"
    assert flip_draft(text) == text


def test_flip_draft_only_touches_front_matter_not_body():
    # 正文里出现 `draft: true`(如代码示例) 不应被改; 只改 front matter 那一处
    text = "---\ndraft: true\n---\n# body\n\n```yaml\ndraft: true\n```\n"
    out = flip_draft(text)
    assert out.count("draft: false") == 1
    assert out.count("draft: true") == 1  # 正文那一处保留


def test_build_report_filters_non_relevant():
    from src.core.types import PublishConfig
    from src.pipeline.publish import build_report

    ok = _ri(link="https://x/ok", title="AI 条目", score=80)
    junk = _ri(link="https://x/junk", title="非 AI", score=80).model_copy(
        update={"relevant": False}
    )
    rep = build_report(
        _rr([ok, junk], daily_take="t", is_pending=False), "2026-06-21", PublishConfig()
    )
    titles = [it.title for cat in rep.categories for it in cat.items]
    assert "AI 条目" in titles
    assert "非 AI" not in titles


def test_front_matter_escapes_newline_in_summary():
    # daily_take 含换行(LLM 输出常见): 必须转义成 \n, 不能撑断单行标量
    rep = build_report(
        _rr([_ri("https://a/1", score=80)], daily_take="第一行\n第二行"), "2026-05-30", CFG
    )
    fm = render_front_matter(rep, CFG, draft=True)
    assert "summary: " in fm
    assert "\\n" in fm  # 字面 \n 转义
    # front matter 仍是 7 行(未被裸换行撑断)
    assert fm.count("\n") == 6
