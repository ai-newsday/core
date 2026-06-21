from __future__ import annotations

from src.core.types import (
    Evidence,
    InterpretedItem,
    ReviewConfig,
    ReviewDecision,
    ReviewedItem,
    ReviewResult,
    RunContext,
)
from src.observability.events import emit

# edit 只允许覆盖这些内容字段; 其余(出处)字段只读
EDITABLE_FIELDS = ("title", "body", "tags", "evidence")


def _filter_evidence(raw_evidence, item: InterpretedItem) -> list[Evidence]:
    """保留 anchor ∈ link ∪ related_links 的证据; 非法锚点丢弃(不编造)。"""
    allowed = {item.link, *item.related_links}
    out: list[Evidence] = []
    for e in raw_evidence or []:
        if isinstance(e, Evidence):
            claim, anchor = e.claim, e.anchor
        elif isinstance(e, dict):
            claim = str(e.get("claim", "")).strip()
            anchor = str(e.get("anchor", "")).strip()
        else:
            continue
        if claim and anchor in allowed:
            out.append(Evidence(claim=claim, anchor=anchor))
    return out


def _gate(status: str, evidence: list[Evidence], body: str, config: ReviewConfig) -> bool:
    """必读门(spec §5.8); 与解读层 §5.4 同式。status 只读, 回退条目洗不白。"""
    return status == "ok" and len(evidence) >= config.min_evidence and body != ""


def apply_decision(
    item: InterpretedItem, decision: ReviewDecision, config: ReviewConfig
) -> ReviewedItem | None:
    """单条决策应用(spec §5.2–§5.4, §5.8)。drop -> None; keep/edit -> ReviewedItem。"""
    if decision.action == "drop":
        return None
    base = item.model_dump()
    if decision.action != "edit":
        return ReviewedItem(**base, review_action="keep", was_edited=False, edited_fields=[])
    # edit: 只覆盖可改字段, 记录实际改动
    edited_fields: list[str] = []
    for key in EDITABLE_FIELDS:
        if key in decision.edits:
            base[key] = decision.edits[key]
            edited_fields.append(key)
    # 改后重新校验
    base["title"] = str(base["title"])[: config.title_max_chars]
    base["body"] = str(base["body"])[: config.body_max_chars]
    base["evidence"] = _filter_evidence(base.get("evidence"), item)
    base["eligible_for_must_read"] = _gate(
        base["interpretation_status"], base["evidence"], base["body"], config
    )
    return ReviewedItem(
        **base, review_action="edit", was_edited=bool(edited_fields), edited_fields=edited_fields
    )


def order_reviewed(
    items: list[ReviewedItem], decisions: dict[str, ReviewDecision]
) -> list[ReviewedItem]:
    """排序(spec §5.5): 有 order 的按 order 升序在前, 无 order 的保持上游序。
    稳定排序键 (无order=1/有order=0, order值, 上游下标)。"""
    indexed = list(enumerate(items))

    def key(pair):
        idx, it = pair
        dec = decisions.get(it.link)
        if dec is not None and dec.order is not None:
            return (0, dec.order, idx)
        return (1, 0, idx)

    return [it for _, it in sorted(indexed, key=key)]


DAILY_TAKE_KEY = "__daily_take__"


def review(
    items: list[InterpretedItem],
    daily_take: str | None,
    decisions: dict[str, ReviewDecision],
    config: ReviewConfig,
    ctx: RunContext,
) -> ReviewResult:
    """审阅 orchestrator(spec §3, §5)。纯函数: 无 LLM / 网络副作用。"""
    emit(ctx.logger, "review_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(
            ctx.logger,
            "review_done",
            input_count=0,
            kept_count=0,
            dropped_count=0,
            edited_count=0,
            is_pending=True,
            silent=True,
        )
        return ReviewResult(
            reviewed_items=[],
            daily_take=daily_take,
            input_count=0,
            kept_count=0,
            dropped_count=0,
            edited_count=0,
            is_reviewed=False,
            is_pending=True,
            is_silent=True,
        )

    kept: list[ReviewedItem] = []
    kept_count = dropped_count = edited_count = 0
    for it in items:
        decision = decisions.get(it.link, ReviewDecision())
        result = apply_decision(it, decision, config)
        if result is None:
            dropped_count += 1
            emit(ctx.logger, "item_dropped", link=it.link)
            continue
        if result.review_action == "edit":
            edited_count += 1
            emit(ctx.logger, "item_edited", link=result.link, edited_fields=result.edited_fields)
        else:
            kept_count += 1
        emit(ctx.logger, "item_kept", link=result.link, edited=result.was_edited)
        kept.append(result)

    ordered = order_reviewed(kept, decisions)

    # 今日看点覆盖(§5.6)
    daily_dec = decisions.get(DAILY_TAKE_KEY)
    daily_overridden = (
        daily_dec is not None and daily_dec.action == "edit" and "daily_take" in daily_dec.edits
    )
    out_daily = daily_dec.edits["daily_take"] if daily_overridden else daily_take

    # 已审/待审(§5.7): 命中任一 item 决策, 或 daily_take 覆盖
    item_links = {it.link for it in items}
    is_reviewed = daily_overridden or any(k in item_links for k in decisions)
    is_pending = not is_reviewed

    emit(
        ctx.logger,
        "review_done",
        input_count=len(items),
        kept_count=kept_count,
        dropped_count=dropped_count,
        edited_count=edited_count,
        is_pending=is_pending,
        silent=False,
    )
    return ReviewResult(
        reviewed_items=ordered,
        daily_take=out_daily,
        input_count=len(items),
        kept_count=kept_count,
        dropped_count=dropped_count,
        edited_count=edited_count,
        is_reviewed=is_reviewed,
        is_pending=is_pending,
        is_silent=False,
    )
