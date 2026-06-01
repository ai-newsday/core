from __future__ import annotations
from src.core.types import (InterpretedItem, ReviewedItem, ReviewDecision,
                            ReviewConfig, Evidence, RunContext, ReviewResult)
from src.observability.events import emit

# edit 只允许覆盖这些内容字段; 其余(出处)字段只读
EDITABLE_FIELDS = ("title", "summary", "takeaway", "hot_take", "tags", "evidence")


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


def _gate(status: str, evidence: list[Evidence], takeaway: str,
          config: ReviewConfig) -> bool:
    """必读门(spec §5.8); 与解读层 §5.4 同式。status 只读, 回退条目洗不白。"""
    return (status == "ok"
            and len(evidence) >= config.min_evidence
            and takeaway != "")


def apply_decision(item: InterpretedItem, decision: ReviewDecision,
                   config: ReviewConfig) -> ReviewedItem | None:
    """单条决策应用(spec §5.2–§5.4, §5.8)。drop -> None; keep/edit -> ReviewedItem。"""
    if decision.action == "drop":
        return None
    base = item.model_dump()
    if decision.action != "edit":
        return ReviewedItem(**base, review_action="keep", was_edited=False,
                            edited_fields=[])
    # edit: 只覆盖可改字段, 记录实际改动
    edited_fields: list[str] = []
    for key in EDITABLE_FIELDS:
        if key in decision.edits:
            base[key] = decision.edits[key]
            edited_fields.append(key)
    # 改后重新校验
    base["title"] = str(base["title"])[:config.title_max_chars]
    base["summary"] = str(base["summary"])[:config.summary_max_chars]
    base["evidence"] = _filter_evidence(base.get("evidence"), item)
    base["eligible_for_must_read"] = _gate(
        base["interpretation_status"], base["evidence"], base["takeaway"], config)
    return ReviewedItem(**base, review_action="edit",
                        was_edited=bool(edited_fields),
                        edited_fields=edited_fields)


def order_reviewed(items: list[ReviewedItem],
                   decisions: dict[str, ReviewDecision]) -> list[ReviewedItem]:
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
