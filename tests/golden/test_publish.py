from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, ReviewedItem, PublishConfig)
from src.pipeline.publish import (select_must_read, group_by_category,
                                  build_overview)

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
