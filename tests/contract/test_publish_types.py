from datetime import datetime, timezone

from src.core.types import (
    CategorySection,
    DailyReport,
    Evidence,
    Genre,
    Overview,
    PublishConfig,
    Publisher,
    PublishResult,
    ReviewedItem,
)

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ri(link="https://a/1", genre=Genre.model, score=80, eligible=True, is_explore=False):
    return ReviewedItem(
        title_en="X released",
        link=link,
        source="src",
        genre=genre,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary="A.",
        cluster_id="evt-1",
        related_links=[],
        score=score,
        score_breakdown={"机构影响力": float(score)},
        is_explore=is_explore,
        title="中文标题",
        summary="中文摘要。",
        takeaway="怎么用。",
        hot_take="锐评。",
        tags=["#a", "#b", "#c"],
        evidence=[Evidence(claim="事实", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=eligible,
        review_action="keep",
        was_edited=False,
        edited_fields=[],
    )


def test_overview_shape():
    o = Overview(genre_distribution={"model": 2}, keywords=["MoE", "Agent"])
    assert o.genre_distribution == {"model": 2}
    assert o.keywords == ["MoE", "Agent"]


def test_category_section_shape():
    c = CategorySection(genre="model", label="模型", items=[_ri()])
    assert c.genre == "model" and c.label == "模型"
    assert len(c.items) == 1 and c.items[0].score == 80


def test_daily_report_shape():
    rep = DailyReport(
        date_label="2026-05-30（周六）",
        daily_take="看点。",
        must_read=[_ri()],
        categories=[CategorySection(genre="model", label="模型", items=[_ri()])],
        overview=Overview(genre_distribution={"model": 1}, keywords=["a"]),
        is_pending=False,
        item_count=1,
        explore_count=0,
    )
    assert rep.date_label == "2026-05-30（周六）"
    assert rep.daily_take == "看点。" and rep.is_pending is False
    assert rep.item_count == 1 and rep.explore_count == 0
    assert rep.must_read[0].title == "中文标题"


def test_daily_report_daily_take_optional():
    rep = DailyReport(
        date_label="d",
        daily_take=None,
        must_read=[],
        categories=[],
        overview=Overview(genre_distribution={}, keywords=[]),
        is_pending=True,
        item_count=0,
        explore_count=0,
    )
    assert rep.daily_take is None and rep.is_pending is True


def test_publish_config_defaults():
    c = PublishConfig()
    assert c.must_read_count == 3 and c.top_keywords == 4
    assert "未审" in c.pending_watermark
    assert c.genre_labels["model"] == "模型"
    # genre_labels 键顺序即组间顺序
    assert list(c.genre_labels)[0] == "paper"


def test_publish_result_shape():
    res = PublishResult(
        report=DailyReport(
            date_label="d",
            daily_take=None,
            must_read=[],
            categories=[],
            overview=Overview(genre_distribution={}, keywords=[]),
            is_pending=True,
            item_count=0,
            explore_count=0,
        ),
        markdown="",
        is_pending=True,
        is_silent=True,
    )
    assert res.markdown == "" and res.is_silent is True
    assert res.is_pending is True
