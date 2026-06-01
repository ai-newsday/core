from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, ReviewedItem, PublishConfig,
                            DailyReport, ReviewResult)
from src.pipeline.publish import (select_must_read, group_by_category,
                                  build_overview, build_report, render_markdown)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
CFG = PublishConfig()


def _ri(link="https://a/1", source_type=SourceType.MODEL, score=80,
        title="中文标题", summary="中文摘要。", takeaway="怎么用。",
        hot_take="锐评。", tags=None, evidence=None, related=None,
        eligible=True, is_explore=False, status="ok"):
    return ReviewedItem(
        title_en="X released", link=link, source="src",
        source_type=source_type, published_at=NOW, raw_summary="A.",
        cluster_id="evt-1", related_links=related or [], score=score,
        score_breakdown={"机构影响力": float(score)}, is_explore=is_explore,
        title=title, summary=summary, takeaway=takeaway, hot_take=hot_take,
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [
            Evidence(claim="事实", anchor=link)],
        interpretation_status=status, eligible_for_must_read=eligible,
        review_action="keep", was_edited=False, edited_fields=[])


def test_select_must_read_only_eligible_top_n():
    items = [_ri("https://a/1", eligible=True),
             _ri("https://a/2", eligible=False),
             _ri("https://a/3", eligible=True),
             _ri("https://a/4", eligible=True),
             _ri("https://a/5", eligible=True)]
    mr = select_must_read(items, CFG)
    # 仅 eligible, 保上游序, 取前 3
    assert [i.link for i in mr] == ["https://a/1", "https://a/3", "https://a/4"]


def test_select_must_read_fewer_than_n():
    items = [_ri("https://a/1", eligible=True),
             _ri("https://a/2", eligible=False)]
    mr = select_must_read(items, CFG)
    assert [i.link for i in mr] == ["https://a/1"]


def test_group_by_category_order_and_grouping():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.PAPER),
             _ri("https://a/3", source_type=SourceType.MODEL)]
    cats = group_by_category(items, CFG)
    # type_labels 键序: official, paper, model... → paper 组在 model 组前
    assert [c.source_type for c in cats] == ["paper", "model"]
    assert cats[0].label == "论文" and cats[1].label == "模型"
    # 空类目不产 section
    assert all(len(c.items) > 0 for c in cats)
    # 组内保上游序
    assert [i.link for i in cats[1].items] == ["https://a/1", "https://a/3"]
    # 全量目录: 不漏
    assert sum(len(c.items) for c in cats) == 3


def test_group_by_category_unknown_type_last():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.BLOG)]
    # 构造一个不在表里的类型测兜底
    cfg = PublishConfig(type_labels={"model": "模型"})
    cats = group_by_category(items, cfg)
    # model 在表里排前, blog 不在表里排末尾且 label 回退英文
    assert [c.source_type for c in cats] == ["model", "blog"]
    assert cats[1].label == "blog"


def test_build_overview_distribution_and_keywords():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 tags=["#MoE", "#Agent"]),
             _ri("https://a/2", source_type=SourceType.MODEL,
                 tags=["#MoE", "#推理"]),
             _ri("https://a/3", source_type=SourceType.PAPER,
                 tags=["#MoE"])]
    ov = build_overview(items, CFG)
    assert ov.type_distribution == {"paper": 1, "model": 2}
    # MoE 频次最高在前; 去 # 前缀; 按频次降序、同频按首现序
    assert ov.keywords[0] == "MoE"
    assert set(ov.keywords) == {"MoE", "Agent", "推理"}


def test_build_overview_keywords_top_n_and_empty_tags():
    items = [_ri("https://a/1", tags=[]),
             _ri("https://a/2", tags=["#x", "#y", "#z", "#w", "#v"])]
    cfg = PublishConfig(top_keywords=2)
    ov = build_overview(items, cfg)
    assert len(ov.keywords) == 2


def _rr(items, daily_take="看点。", is_pending=False, is_silent=False):
    n = len(items)
    return ReviewResult(
        reviewed_items=items, daily_take=daily_take, input_count=n,
        kept_count=n, dropped_count=0, edited_count=0,
        is_reviewed=not is_pending, is_pending=is_pending, is_silent=is_silent)


def test_build_report_assembles_blocks():
    items = [_ri("https://a/1", source_type=SourceType.MODEL, eligible=True),
             _ri("https://a/2", source_type=SourceType.PAPER, eligible=False,
                 is_explore=True)]
    rep = build_report(_rr(items), "2026-05-30（周六）", CFG)
    assert rep.date_label == "2026-05-30（周六）"
    assert rep.item_count == 2 and rep.explore_count == 1
    assert [i.link for i in rep.must_read] == ["https://a/1"]
    assert [c.source_type for c in rep.categories] == ["paper", "model"]
    assert rep.is_pending is False
    # 必读子集: must_read 出现在其类型分组里
    model_cat = [c for c in rep.categories if c.source_type == "model"][0]
    assert "https://a/1" in [i.link for i in model_cat.items]
    # 全量目录守恒
    assert sum(len(c.items) for c in rep.categories) == rep.item_count


def test_render_markdown_full():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 title="GLM-5 发布", summary="开源 MoE。", tags=["#MoE"])]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    assert md.startswith("# AI Daily · 2026-05-30")
    assert "> **今日看点**：看点。" in md
    assert "## 🏆 今日必读" in md
    assert "### 1. [模型] GLM-5 发布（X released）" in md
    assert "**一句话**：开源 MoE。" in md
    assert "**对你**：怎么用。" in md
    assert "**锐评**：锐评。" in md
    assert "[src](https://a/1)" in md
    assert "## 📚 分类速览" in md
    assert "## 📊 数据概览" in md
    assert "MoE" in md


def test_render_markdown_pending_watermark():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, is_pending=True), "d", CFG), CFG)
    assert CFG.pending_watermark in md


def test_render_markdown_no_watermark_when_reviewed():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, is_pending=False), "d", CFG), CFG)
    assert CFG.pending_watermark not in md


def test_render_markdown_omits_empty_daily_take():
    items = [_ri("https://a/1")]
    md = render_markdown(build_report(_rr(items, daily_take=None), "d", CFG), CFG)
    assert "今日看点" not in md


def test_render_markdown_omits_must_read_when_none_eligible():
    items = [_ri("https://a/1", eligible=False)]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "今日必读" not in md
    assert "## 📚 分类速览" in md      # 速览仍在


def test_render_markdown_explore_marker():
    items = [_ri("https://a/1", is_explore=True, eligible=False)]
    md = render_markdown(build_report(_rr(items), "d", CFG), CFG)
    assert "🧭探索" in md
