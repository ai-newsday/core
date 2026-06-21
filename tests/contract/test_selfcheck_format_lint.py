from datetime import datetime, timezone

from src.core.types import Evidence, Genre, InterpretedItem, Publisher, SelfCheckConfig
from src.pipeline.selfcheck import format_lint

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _item(**over):
    base = dict(
        title_en="X",
        link="https://a/1",
        source="s",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary="r",
        cluster_id="c",
        related_links=["https://a/2"],
        score=80.0,
        score_breakdown={"机构影响力": 80.0},
        is_explore=False,
        title="标题",
        body="正文内容",
        tags=["#a", "#b", "#c"],
        evidence=[Evidence(claim="f", anchor="https://a/1")],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )
    base.update(over)
    return InterpretedItem(**base)


def test_compliant_item_has_no_flags():
    assert format_lint(_item(), SelfCheckConfig()) == []


def test_wrong_tag_count_flagged():
    flags = format_lint(_item(tags=["#a", "#b"]), SelfCheckConfig())
    assert [f.code for f in flags] == ["format_lock"]
    assert flags[0].field == "tags"


def test_illegal_anchor_flagged():
    item = _item(evidence=[Evidence(claim="f", anchor="https://evil/x")])
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "evidence" for f in flags)


def test_eligible_but_no_evidence_flagged():
    item = _item(evidence=[], eligible_for_must_read=True)
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "evidence" for f in flags)


def test_oversize_body_flagged():
    item = _item(body="超" * 300)
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "body" for f in flags)


def test_missing_body_on_eligible_flagged():
    item = _item(body="", eligible_for_must_read=True)
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "body" for f in flags)
