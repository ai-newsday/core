from datetime import datetime, timezone
from src.core.types import (SourceType, Evidence, InterpretedItem,
                            ReviewDecision, ReviewConfig)
from src.pipeline.review import apply_decision, order_reviewed

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _interp(link="https://a/1", status="ok", title="中文标题",
            summary="中文摘要。", takeaway="怎么用。", tags=None,
            evidence=None, related=None, score=80, eligible=True):
    return InterpretedItem(
        title_en="X released", link=link, source="src",
        source_type=SourceType.MODEL, published_at=NOW,
        raw_summary="A summary.", cluster_id="evt-1",
        related_links=related or [], score=score,
        score_breakdown={"机构影响力": float(score)}, is_explore=False,
        title=title, summary=summary, takeaway=takeaway, hot_take="锐评。",
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [
            Evidence(claim="事实", anchor=link)],
        interpretation_status=status, eligible_for_must_read=eligible)


CFG = ReviewConfig()


def test_apply_keep_passthrough():
    it = _interp()
    r = apply_decision(it, ReviewDecision(action="keep"), CFG)
    assert r is not None
    assert r.review_action == "keep" and r.was_edited is False
    assert r.edited_fields == [] and r.title == "中文标题"


def test_apply_drop_returns_none():
    it = _interp()
    assert apply_decision(it, ReviewDecision(action="drop"), CFG) is None


def test_apply_edit_overrides_and_records_fields():
    it = _interp()
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"title": "改后标题", "hot_take": "新锐评"}), CFG)
    assert r.review_action == "edit" and r.was_edited is True
    assert set(r.edited_fields) == {"title", "hot_take"}
    assert r.title == "改后标题" and r.hot_take == "新锐评"
    # 未改字段保留原值
    assert r.summary == "中文摘要。"


def test_apply_edit_reclamps_title_and_summary():
    it = _interp()
    long_title = "标" * 100
    long_summary = "要" * 200
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"title": long_title, "summary": long_summary}),
        CFG)
    assert len(r.title) == CFG.title_max_chars
    assert len(r.summary) == CFG.summary_max_chars


def test_apply_edit_provenance_readonly():
    it = _interp(link="https://a/1", score=80)
    r = apply_decision(it, ReviewDecision(
        action="edit", edits={"score": 5, "link": "https://evil/x",
                              "title": "改后"}), CFG)
    # 出处字段被忽略, 恒等上游
    assert r.score == 80 and r.link == "https://a/1"
    assert r.title == "改后"
    assert "score" not in r.edited_fields and "link" not in r.edited_fields


def test_apply_edit_drops_illegal_anchor():
    it = _interp(link="https://a/1", related=["https://r/1"])
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"evidence": [{"claim": "x", "anchor": "https://evil/x"}]}), CFG)
    assert r.evidence == []
    assert r.eligible_for_must_read is False


def test_apply_edit_recomputes_gate_true():
    it = _interp(eligible=False)
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"takeaway": "可操作",
               "evidence": [{"claim": "事实", "anchor": "https://a/1"}]}), CFG)
    assert r.eligible_for_must_read is True


def test_apply_edit_cannot_whitewash_fallback():
    it = _interp(status="extractive_fallback", takeaway="", evidence=[],
                 eligible=False)
    r = apply_decision(it, ReviewDecision(
        action="edit",
        edits={"takeaway": "硬补", "evidence": [
            {"claim": "事实", "anchor": "https://a/1"}]}), CFG)
    assert r.interpretation_status == "extractive_fallback"
    assert r.eligible_for_must_read is False


def test_apply_edit_empty_edits_not_edited():
    it = _interp()
    r = apply_decision(it, ReviewDecision(action="edit", edits={}), CFG)
    assert r.review_action == "edit" and r.was_edited is False
    assert r.edited_fields == []


def test_order_reviewed_respects_order_then_upstream():
    a = _interp(link="https://a/1")   # upstream index 0
    b = _interp(link="https://a/2")   # upstream index 1
    c = _interp(link="https://a/3")   # upstream index 2
    items = [a, b, c]
    decisions = {"https://a/1": ReviewDecision(order=1),
                 "https://a/2": ReviewDecision(order=0)}
    ordered = order_reviewed(items, decisions)
    # a2(order0), a1(order1), 然后无 order 的 a3 保持上游序
    assert [i.link for i in ordered] == ["https://a/2", "https://a/1",
                                         "https://a/3"]
