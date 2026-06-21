from datetime import datetime, timezone

from src.core.types import Evidence, Genre, InterpretedItem, Publisher, ReviewConfig, ReviewDecision
from src.pipeline.review import apply_decision, order_reviewed

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _interp(
    link="https://a/1",
    status="ok",
    title="中文标题",
    body="中文正文。怎么用。锐评。",
    tags=None,
    evidence=None,
    related=None,
    score=80,
    eligible=True,
):
    return InterpretedItem(
        title_en="X released",
        link=link,
        source="src",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary="A summary.",
        cluster_id="evt-1",
        related_links=related or [],
        score=score,
        score_breakdown={"机构影响力": float(score)},
        is_explore=False,
        title=title,
        body=body,
        tags=tags if tags is not None else ["#a", "#b", "#c"],
        evidence=evidence if evidence is not None else [Evidence(claim="事实", anchor=link)],
        interpretation_status=status,
        eligible_for_must_read=eligible,
    )


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
    r = apply_decision(
        it, ReviewDecision(action="edit", edits={"title": "改后标题", "body": "新正文。"}), CFG
    )
    assert r.review_action == "edit" and r.was_edited is True
    assert set(r.edited_fields) == {"title", "body"}
    assert r.title == "改后标题" and r.body == "新正文。"
    # 未改字段保留原值
    assert r.tags == ["#a", "#b", "#c"]


def test_apply_edit_reclamps_title_and_body():
    it = _interp()
    long_title = "标" * 100
    long_body = "正" * 300
    r = apply_decision(
        it, ReviewDecision(action="edit", edits={"title": long_title, "body": long_body}), CFG
    )
    assert len(r.title) == CFG.title_max_chars
    assert len(r.body) == CFG.body_max_chars


def test_apply_edit_provenance_readonly():
    it = _interp(link="https://a/1", score=80)
    r = apply_decision(
        it,
        ReviewDecision(
            action="edit", edits={"score": 5, "link": "https://evil/x", "title": "改后"}
        ),
        CFG,
    )
    # 出处字段被忽略, 恒等上游
    assert r.score == 80 and r.link == "https://a/1"
    assert r.title == "改后"
    assert "score" not in r.edited_fields and "link" not in r.edited_fields


def test_apply_edit_drops_illegal_anchor():
    it = _interp(link="https://a/1", related=["https://r/1"])
    r = apply_decision(
        it,
        ReviewDecision(
            action="edit", edits={"evidence": [{"claim": "x", "anchor": "https://evil/x"}]}
        ),
        CFG,
    )
    assert r.evidence == []
    assert r.eligible_for_must_read is False


def test_apply_edit_recomputes_gate_true():
    it = _interp(eligible=False, body="")
    r = apply_decision(
        it,
        ReviewDecision(
            action="edit",
            edits={"body": "可操作", "evidence": [{"claim": "事实", "anchor": "https://a/1"}]},
        ),
        CFG,
    )
    assert r.eligible_for_must_read is True


def test_apply_edit_cannot_whitewash_fallback():
    it = _interp(status="extractive_fallback", body="", evidence=[], eligible=False)
    r = apply_decision(
        it,
        ReviewDecision(
            action="edit",
            edits={"body": "硬补", "evidence": [{"claim": "事实", "anchor": "https://a/1"}]},
        ),
        CFG,
    )
    assert r.interpretation_status == "extractive_fallback"
    assert r.eligible_for_must_read is False


def test_apply_edit_empty_edits_not_edited():
    it = _interp()
    r = apply_decision(it, ReviewDecision(action="edit", edits={}), CFG)
    assert r.review_action == "edit" and r.was_edited is False
    assert r.edited_fields == []


def test_order_reviewed_respects_order_then_upstream():
    a = _interp(link="https://a/1")  # upstream index 0
    b = _interp(link="https://a/2")  # upstream index 1
    c = _interp(link="https://a/3")  # upstream index 2
    items = [a, b, c]
    decisions = {"https://a/1": ReviewDecision(order=1), "https://a/2": ReviewDecision(order=0)}
    ordered = order_reviewed(items, decisions)
    # a2(order0), a1(order1), 然后无 order 的 a3 保持上游序
    assert [i.link for i in ordered] == ["https://a/2", "https://a/1", "https://a/3"]


# --- orchestrator: review() golden cases (spec §9) ---
import logging

from src.core.types import RunContext
from src.pipeline.review import review


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-review"))


# Case 1 (§9.1): 全透传待审
def test_golden_passthrough_pending():
    items = [_interp("https://a/1"), _interp("https://a/2")]
    res = review(items, "看点。", {}, CFG, _ctx())
    assert res.is_reviewed is False and res.is_pending is True
    assert res.is_silent is False
    assert all(r.review_action == "keep" for r in res.reviewed_items)
    assert [r.link for r in res.reviewed_items] == ["https://a/1", "https://a/2"]
    assert res.kept_count == 2 and res.dropped_count == 0
    assert res.daily_take == "看点。"


# Case 2 (§9.2): 删除生效 + 账目守恒
def test_golden_drop_removes_and_counts():
    items = [_interp("https://a/1"), _interp("https://a/2")]
    decisions = {"https://a/1": ReviewDecision(action="drop")}
    res = review(items, None, decisions, CFG, _ctx())
    assert [r.link for r in res.reviewed_items] == ["https://a/2"]
    assert res.dropped_count == 1 and res.kept_count == 1
    assert res.kept_count + res.edited_count + res.dropped_count == res.input_count == 2
    assert res.is_reviewed is True and res.is_pending is False


# Case 3 (§9.3): 改写 + 重夹 + 重算门
def test_golden_edit_reclamp_and_gate():
    items = [_interp("https://a/1", eligible=False, body="")]
    decisions = {
        "https://a/1": ReviewDecision(
            action="edit",
            edits={
                "title": "标" * 100,
                "body": "可操作",
                "evidence": [{"claim": "事实", "anchor": "https://a/1"}],
            },
        )
    }
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert len(one.title) == CFG.title_max_chars
    assert one.eligible_for_must_read is True
    assert one.review_action == "edit" and res.edited_count == 1


# Case 4 (§9.4): 改写不能洗白回退
def test_golden_edit_cannot_whitewash_fallback():
    items = [
        _interp("https://a/1", status="extractive_fallback", body="", evidence=[], eligible=False)
    ]
    decisions = {
        "https://a/1": ReviewDecision(
            action="edit",
            edits={"body": "硬补", "evidence": [{"claim": "事实", "anchor": "https://a/1"}]},
        )
    }
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert one.interpretation_status == "extractive_fallback"
    assert one.eligible_for_must_read is False


# Case 5 (§9.5): edit 非法锚点丢弃
def test_golden_edit_illegal_anchor_dropped():
    items = [_interp("https://a/1", related=["https://r/1"])]
    decisions = {
        "https://a/1": ReviewDecision(
            action="edit", edits={"evidence": [{"claim": "x", "anchor": "https://evil/x"}]}
        )
    }
    res = review(items, None, decisions, CFG, _ctx())
    assert res.reviewed_items[0].evidence == []
    assert res.reviewed_items[0].eligible_for_must_read is False


# Case 6 (§9.6): 重排序 + 确定性
def test_golden_reorder_and_deterministic():
    items = [_interp("https://a/1"), _interp("https://a/2"), _interp("https://a/3")]
    decisions = {"https://a/1": ReviewDecision(order=1), "https://a/2": ReviewDecision(order=0)}
    res1 = review(items, None, decisions, CFG, _ctx())
    res2 = review(items, None, decisions, CFG, _ctx())
    order1 = [r.link for r in res1.reviewed_items]
    assert order1 == ["https://a/2", "https://a/1", "https://a/3"]
    assert order1 == [r.link for r in res2.reviewed_items]


# Case 7 (§9.7): 空输入 silent
def test_golden_empty_input_silent():
    res = review([], None, {}, CFG, _ctx())
    assert res.is_silent is True and res.reviewed_items == []
    assert res.is_pending is True and res.input_count == 0
    assert res.daily_take is None


# Case 8 (§9.8): 今日看点覆盖
def test_golden_daily_take_override():
    items = [_interp("https://a/1")]
    decisions = {
        "__daily_take__": ReviewDecision(action="edit", edits={"daily_take": "人工改写的看点"})
    }
    res = review(items, "原看点", decisions, CFG, _ctx())
    assert res.daily_take == "人工改写的看点"
    assert res.is_reviewed is True
    # __daily_take__ 不进 reviewed_items / 不计数
    assert [r.link for r in res.reviewed_items] == ["https://a/1"]
    assert res.kept_count == 1 and res.edited_count == 0


# Case 9 (§9.9): 出处只读
def test_golden_provenance_readonly():
    items = [_interp("https://a/1", score=80)]
    decisions = {
        "https://a/1": ReviewDecision(
            action="edit", edits={"score": 5, "link": "https://evil/x", "title": "改后"}
        )
    }
    res = review(items, None, decisions, CFG, _ctx())
    one = res.reviewed_items[0]
    assert one.score == 80 and one.link == "https://a/1"
    assert one.title == "改后"
