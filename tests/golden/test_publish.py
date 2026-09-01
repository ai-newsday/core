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
    """human-keep 条目质量底 = min_display_score(default 40): <40 砍, >=40 留。"""
    items = [
        _ri("https://a/1", score=80),
        _ri("https://a/2", score=39),  # below floor — should be dropped
        _ri("https://a/3", score=40),  # at floor — should be kept
    ]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 2
    all_links = [it.link for cat in rep.categories for it in cat.items]
    assert "https://a/1" in all_links
    assert "https://a/3" in all_links
    assert "https://a/2" not in all_links


def test_build_report_content_certainty_penalty_does_not_drop_kept_item_below_floor():
    """human-keep 的条目原始分(去掉'内容确定性'扣分)达标时, 即使扣分后 < min_display_score(40)
    也不该被地板过滤掉——扣分只该影响 apply_quota() 排序, 不该造成硬性剔除。"""
    penalized = _ri("https://a/1", score=50).model_copy(
        update={"score": 35, "score_breakdown": {"机构影响力": 50.0, "内容确定性": -15.0}}
    )
    items = [penalized]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 1
    all_links = [it.link for cat in rep.categories for it in cat.items]
    assert "https://a/1" in all_links


def test_build_report_ordinary_low_score_without_penalty_still_filtered():
    """没有'内容确定性'扣分、原本就低于 40 分的条目, 地板行为不受本次改动影响。"""
    items = [_ri("https://a/1", score=25)]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 0
    assert rep.categories == []


def test_build_report_penalty_does_not_exempt_item_below_floor_before_penalty():
    """扣分前分数本来就没达标(< 40)的条目仍被过滤——豁免只保护"扣分前本可通过地板"的条目。"""
    penalized = _ri("https://a/1", score=30).model_copy(
        update={"score": 15, "score_breakdown": {"机构影响力": 30.0, "内容确定性": -15.0}}
    )
    items = [penalized]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 0
    assert rep.categories == []


def test_build_report_all_items_below_floor_gives_empty_categories():
    items = [
        _ri("https://a/1", score=30),
        _ri("https://a/2", score=20),
    ]
    rep = build_report(_rr(items), "2026-05-30", CFG)
    assert rep.item_count == 0
    assert rep.categories == []


def test_build_report_applies_per_genre_quota():
    """kept 集合某 genre 超配额 → 只留该类 top-N(按 score)。"""
    cfg = PublishConfig()
    cfg.quota = {"paper": 1}
    cfg.total_limit = 99
    items = [
        _ri("https://a/1", score=80, genre=Genre.paper, title="高分论文"),
        _ri("https://a/2", score=70, genre=Genre.paper, title="低分论文"),
    ]
    rep = build_report(_rr(items), "2026-05-30", cfg)
    assert rep.item_count == 1
    titles = {it.title for c in rep.categories for it in c.items}
    assert titles == {"高分论文"}


def test_build_report_respects_total_limit():
    cfg = PublishConfig()
    cfg.quota = {"paper": 5, "model": 5}
    cfg.total_limit = 2
    items = [
        _ri("https://a/1", score=90, genre=Genre.paper),
        _ri("https://a/2", score=80, genre=Genre.model),
        _ri("https://a/3", score=70, genre=Genre.paper),
    ]
    rep = build_report(_rr(items), "2026-05-30", cfg)
    assert rep.item_count == 2


def test_build_report_adapter_quota_applies_before_genre_quota():
    """采集渠道封顶(spec §5) 在 genre 配额之前生效: github_releases 超额条目先被砍掉,
    腾出的 genre 名额优先给非 GitHub 条目, 而不是被同 adapter 的次优条目占用。"""
    cfg = PublishConfig(
        quota={"announcement": 2},
        total_limit=99,
        adapter_quota={"github_releases": 1},
    )
    items = [
        _ri(link="https://gh/1", genre=Genre.announcement, score=90).model_copy(
            update={"adapter": "github_releases"}
        ),
        _ri(link="https://gh/2", genre=Genre.announcement, score=85).model_copy(
            update={"adapter": "github_releases"}
        ),
        _ri(link="https://openai/1", genre=Genre.announcement, score=70).model_copy(
            update={"adapter": "rss"}
        ),
    ]
    report = build_report(_rr(items), "2026-07-14", cfg)
    links = {it.link for sec in report.categories for it in sec.items}
    # github_releases capped to 1 (highest-scored: gh/1) -> frees a genre slot for the rss item
    assert links == {"https://gh/1", "https://openai/1"}


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
    # 摘要自带 `今日亮点：` 标签, 渲染层不再叠加 `**今日看点**：`(#130)
    assert "> 看点。" in md
    assert "今日看点" not in md
    # 2026-08-05 结构: 条目顺读, 无 genre 大分类标题, 链接收到文末参考章节
    assert "### GLM-5 发布" in md
    assert "开源 MoE。" in md
    assert "## 模型" not in md  # 大分类标题已去掉
    assert "[1](https://a/1)" not in md  # 正文里不出现裸链接
    assert "## 参考链接" in md
    assert "1. [GLM-5 发布](https://a/1)" in md  # 参考表用条目标题, 不用源名
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
    """每条正文末行 = 参考编号 + 分数; 链接本身不出现在正文里。"""
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
    assert "\n[1]\n" in md
    assert "https://a/1" not in md.split("## 参考链接")[0]  # 正文段里没有裸 URL
    assert "1. [T](https://a/1)" in md


def test_render_markdown_evidence_anchors_equal_to_source_link_add_no_extra_ref():
    # 2026-07-25 的去重规则在编号制下延续: 锚点全等于来源链接时不再多编一个号。
    items = [
        _ri(
            "https://a/1",
            title="T",
            body="B。",
            evidence=[
                Evidence(claim="事实一", anchor="https://a/1"),
                Evidence(claim="事实二", anchor="https://a/1"),
            ],
            score=75,
        )
    ]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "\n[1]\n" in md
    assert "[2]" not in md  # 没有多余编号
    refs = md.split("## 参考链接")[1]
    assert refs.count("https://a/1") == 1  # 参考表里只列一次


def test_render_markdown_distinct_evidence_anchor_gets_its_own_ref():
    items = [
        _ri(
            "https://a/1",
            title="T",
            body="B。",
            related=["https://a/related"],
            evidence=[
                Evidence(claim="事实一", anchor="https://a/1"),
                Evidence(claim="事实二", anchor="https://a/related"),
            ],
            score=75,
        )
    ]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "\n[1][2]\n" in md
    refs = md.split("## 参考链接")[1]
    assert "1. [T](https://a/1)" in refs
    assert "2. [事实二](https://a/related)" in refs


def test_render_markdown_reference_numbering_is_sequential_across_items():
    items = [
        _ri("https://a/1", title="T1", body="B1。", genre=Genre.model, score=80),
        _ri("https://a/2", title="T2", body="B2。", genre=Genre.model, score=70),
    ]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "\n[1]\n" in md
    assert "\n[2]\n" in md
    refs = md.split("## 参考链接")[1]
    assert "1. [T1](https://a/1)" in refs
    assert "2. [T2](https://a/2)" in refs


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
        _ri("https://a/low", title="低分条目", score=30, genre=Genre.paper),
    ]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert "高分条目" in md
    assert "低分条目" not in md


def test_render_markdown_below_floor_item_absent_and_leaves_no_gap():
    """低分条目被地板砍掉后, 正文里不留任何痕迹(编号也不该跳号)。"""
    items = [
        _ri("https://a/1", title="论文A", score=30, genre=Genre.paper),
        _ri("https://a/2", title="模型B", score=80, genre=Genre.model),
    ]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert "论文A" not in md
    assert "### 模型B" in md
    assert "\n[1]\n" in md  # 编号从 1 起, 不因被砍条目跳号
    assert "1. [模型B](https://a/2)" in md.split("## 参考链接")[1]


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
            score=30,  # below floor (min_display_score=40) — should not appear
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
    assert "## 论文" not in res.markdown  # 2026-08-05: 去掉 genre 大分类标题
    assert "## 模型" not in res.markdown
    assert "### GLM-5 发布" in res.markdown
    assert "低分新闻" not in res.markdown  # below floor
    assert "## 参考链接" in res.markdown
    # 条目顺序仍按 genre_labels 键序(paper 在 model 前), 所以 88 分那条(model)拿 [2]
    assert "\n[2]\n" in res.markdown
    refs = res.markdown.split("## 参考链接")[1]
    assert "1. [新论文](https://a/2)" in refs  # paper 先出现
    assert "2. [GLM-5 发布](https://a/1)" in refs  # model 次之
    if not SNAPSHOT.exists():  # 首次运行固化快照
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(res.markdown, encoding="utf-8")
    assert res.markdown == SNAPSHOT.read_text(encoding="utf-8")


def test_generated_title_reaches_front_matter_and_h1():
    items = [_ri("https://a/1", score=80)]
    rr = _rr(items, daily_take="今日亮点：甲发布 X。详见正文，参考链接见文末。")
    rr.wechat_title = "甲发布X | 乙提出Y【AI日报】"
    res = publish(rr, "2026-05-30", CFG, _ctx())
    assert 'title: "甲发布X | 乙提出Y【AI日报】"' in res.markdown
    assert "# 甲发布X | 乙提出Y【AI日报】" in res.markdown
    assert "# AI Daily · 2026-05-30" not in res.markdown


def test_missing_generated_title_falls_back_to_the_dated_one():
    res = publish(
        _rr([_ri("https://a/1", score=80)], daily_take="看点。"), "2026-05-30", CFG, _ctx()
    )
    assert 'title: "AI Daily · 2026-05-30"' in res.markdown
    assert "# AI Daily · 2026-05-30" in res.markdown


def test_no_score_in_rendered_items():
    """分数对读者零区分度(实测一期九条全在 94-100), 且暴露内部打分。"""
    res = publish(
        _rr([_ri("https://a/1", score=80)], daily_take="看点。"), "2026-05-30", CFG, _ctx()
    )
    assert " 分" not in res.markdown
    assert "80" not in res.markdown.split("## 参考链接")[0]


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


def test_front_matter_caps_summary_length():
    long = "看" * 200
    rep = build_report(_rr([_ri("https://a/1", score=80)], daily_take=long), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert "看" * 141 not in fm


def test_front_matter_summary_cuts_at_a_sentence_not_mid_word():
    """回归: 硬切 140 曾把 "DeepSeek" 切成 "De" 露在站点 meta / 社交预览里
    (2026-09-01 线上实测)。超长时应截到句末, 不在词中间下刀。"""
    long = "今日亮点：甲方发布了模型。" * 20 + "尾句 DeepSeek 很长。"
    rep = build_report(_rr([_ri("https://a/1", score=80)], daily_take=long), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    summary = fm.split("summary: ")[1].split("\n")[0].strip('"')
    assert len(summary) <= 140
    assert summary.endswith("。")
    assert "De" not in summary or "DeepSeek" in summary


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


from src.pipeline.publish import merge_story_groups


def test_merge_story_groups_passthrough_when_no_story_id():
    items = [_ri("https://a/1"), _ri("https://a/2")]
    out = merge_story_groups(items, max_support=3)
    assert len(out) == 2
    assert {i.link for i in out} == {"https://a/1", "https://a/2"}


def test_merge_story_groups_merges_group_keeps_earliest_as_primary():
    from datetime import timedelta

    early = _ri("https://a/1", score=70)
    early = early.model_copy(update={"story_id": "story-1"})
    late = _ri("https://a/2", score=90)
    late = late.model_copy(update={"story_id": "story-1", "published_at": NOW + timedelta(hours=2)})
    out = merge_story_groups([early, late], max_support=3)
    assert len(out) == 1
    assert out[0].link == "https://a/1"  # earliest = primary, regardless of score


def test_merge_story_groups_appends_support_sentence_to_primary_body():
    primary = _ri("https://a/1", body="原始正文。").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2", score=90).model_copy(
        update={"story_id": "story-1", "link": "https://support.example.com/post"}
    )
    out = merge_story_groups([primary, support], max_support=3)
    assert "原始正文。" in out[0].body
    assert "跟进支持" in out[0].body
    assert "example.com" in out[0].body  # 域名末两段, 不是内部 source slug


def test_merge_story_groups_support_link_registered_as_evidence():
    primary = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2").model_copy(
        update={"story_id": "story-1", "link": "https://support.example.com/post"}
    )
    out = merge_story_groups([primary, support], max_support=3)
    anchors = [e.anchor for e in out[0].evidence]
    assert "https://support.example.com/post" in anchors


def test_merge_story_groups_never_uses_internal_source_as_display_name():
    primary = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2").model_copy(
        update={
            "story_id": "story-1",
            "link": "https://support.example.com/post",
            "source": "x-ai-company",
        }
    )
    out = merge_story_groups([primary, support], max_support=3)
    assert "x-ai-company" not in out[0].body


def test_merge_story_groups_caps_support_at_max_support_by_score():
    primary = _ri("https://a/1", score=100).model_copy(update={"story_id": "story-1"})
    supports = [
        _ri(f"https://s{i}/x", score=s).model_copy(
            update={"story_id": "story-1", "link": f"https://s{i}.example.com/post"}
        )
        for i, s in enumerate([50, 90, 70, 60], start=1)
    ]
    out = merge_story_groups([primary, *supports], max_support=2)
    assert len(out) == 1
    anchors = [e.anchor for e in out[0].evidence]
    # top 2 by score among supports: s2 (90), s3 (70)
    assert "https://s2.example.com/post" in anchors
    assert "https://s3.example.com/post" in anchors
    assert "https://s1.example.com/post" not in anchors
    assert "https://s4.example.com/post" not in anchors


def test_merge_story_groups_dropped_members_removed_from_result():
    primary = _ri("https://a/1", score=100).model_copy(update={"story_id": "story-1"})
    supports = [
        _ri(f"https://s{i}/x", score=s).model_copy(
            update={"story_id": "story-1", "link": f"https://s{i}.example.com/post"}
        )
        for i, s in enumerate([50, 90, 70, 60], start=1)
    ]
    out = merge_story_groups([primary, *supports], max_support=2)
    # 5 items in -> 1 out (primary absorbs top-2 support, drops the rest)
    assert len(out) == 1


def test_merge_story_groups_mixed_grouped_and_ungrouped():
    grouped_a = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    grouped_b = _ri("https://a/2").model_copy(update={"story_id": "story-1"})
    solo = _ri("https://a/3")
    out = merge_story_groups([grouped_a, grouped_b, solo], max_support=3)
    assert len(out) == 2
    assert {i.link for i in out} == {"https://a/1", "https://a/3"}


def test_merge_story_groups_tie_on_published_at_picks_stable_primary_regardless_of_input_order():
    # Same published_at on both members: sort must not depend on stability of input
    # order alone (upstream order is not guaranteed run to run) -- link is the
    # deterministic tiebreaker, so the same primary wins no matter the input order.
    a = _ri("https://a/1", score=50).model_copy(update={"story_id": "story-1"})
    b = _ri("https://a/2", score=90).model_copy(update={"story_id": "story-1"})

    out_forward = merge_story_groups([a, b], max_support=3)
    out_reversed = merge_story_groups([b, a], max_support=3)

    assert out_forward[0].link == "https://a/1"
    assert out_reversed[0].link == "https://a/1"


def test_support_display_name_reduces_to_second_level_domain():
    from src.pipeline.publish import _support_display_name

    assert _support_display_name("https://www.together.ai/post") == "together.ai"
    assert _support_display_name("https://ollama.com/post") == "ollama.com"


def test_support_display_name_never_full_multi_label_host():
    # 硬约束不能回归: 展示名不能是 item.source 内部 slug, 也不能是未收窄的完整多段 host
    from src.pipeline.publish import _support_display_name

    assert _support_display_name("https://www.together.ai/post") != "www.together.ai"


def test_merge_story_groups_uses_configured_support_template():
    primary = _ri("https://a/1").model_copy(update={"story_id": "story-1"})
    support = _ri("https://a/2").model_copy(
        update={"story_id": "story-1", "link": "https://ollama.com/post"}
    )
    out = merge_story_groups(
        [primary, support], max_support=3, support_template="\n\n[custom] {names} 已支持。"
    )
    assert "[custom] ollama.com 已支持。" in out[0].body


def test_build_report_story_group_costs_one_quota_slot():
    """merge_story_groups 必须在 apply_quota 之前跑, 否则故事组会按组内条目数占用
    多个配额位, 挤掉其它当天条目 —— 这条测试锁住这个顺序(spec 2026-08-28 review)。
    proof: 临时把 merge_story_groups 挪到 apply_quota 之后, 这条测试会失败。"""
    from src.core.types import PublishConfig

    cfg = PublishConfig(quota={"model": 3}, total_limit=12)
    group = [
        _ri(f"https://g/{i}", genre=Genre.model, score=100 - i).model_copy(
            update={"story_id": "story-1"}
        )
        for i in range(3)
    ]
    ungrouped = [
        _ri("https://u/1", genre=Genre.model, score=50),
        _ri("https://u/2", genre=Genre.model, score=49),
    ]
    rep = build_report(_rr([*group, *ungrouped]), "2026-08-28", cfg)
    links = {it.link for cat in rep.categories for it in cat.items}
    assert "https://u/1" in links
    assert "https://u/2" in links


def test_dollar_signs_in_body_are_escaped():
    """回归(#131): 正文里成对的 `$` 被 Markdown 当行内数学定界符, 2026-09-01 实测
    把 `最佳模型成本低于 $6.9K，性能逼近` 渲染成 `***，*** **性** **能** **逼** **近**`。
    prompt 层已要求写「美元」, 但硬约束不能只靠 prompt(长度那次写 ≤120 实测 145),
    渲染层再兜一道: CommonMark 的 `\\$` 转义渲染成字面 `$` 且不触发数学。"""
    body = "成本低于 $6.9K，约 $4.4K 即可达到同等水平。"
    res = publish(
        _rr([_ri("https://a/1", score=80, body=body)], daily_take="看点。"),
        "2026-05-30",
        CFG,
        _ctx(),
    )
    assert "\\$6.9K" in res.markdown
    assert "\\$4.4K" in res.markdown
    assert " $6.9K" not in res.markdown


def test_body_without_dollar_is_untouched():
    res = publish(
        _rr([_ri("https://a/1", score=80, body="成本约 6900 美元。")], daily_take="看点。"),
        "2026-05-30",
        CFG,
        _ctx(),
    )
    assert "成本约 6900 美元。" in res.markdown
    assert "\\" not in res.markdown.split("## 参考链接")[0].split("### ")[1]
