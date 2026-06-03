from __future__ import annotations
from datetime import datetime
from src.core.types import (InterpretedItem, ReviewDecision, FeedbackEvent,
                            SourceFeedbackStats, FeedbackConfig,
                            FeedbackResult, RunContext)
from src.observability.events import emit


def derive_events(items: list[InterpretedItem],
                  decisions: dict[str, ReviewDecision],
                  run_id: str, now: datetime) -> list[FeedbackEvent]:
    """从进审阅前全量条目派生事件; 无决策默认 keep; 带 source; ts 注入。
    遍历审阅前条目(非保留结果)→ 被删条目也产 drop 事件。"""
    out: list[FeedbackEvent] = []
    for it in items:
        dec = decisions.get(it.link)
        action = dec.action if dec is not None else "keep"
        out.append(FeedbackEvent(link=it.link, source=it.source,
                                 action=action, run_id=run_id, ts=now))
    return out


def aggregate_by_source(events: list[FeedbackEvent]
                        ) -> list[SourceFeedbackStats]:
    """按 source 聚合 keep/edit/drop/total; 输出按 source 字母序(确定性)。"""
    buckets: dict[str, dict[str, int]] = {}
    for e in events:
        b = buckets.setdefault(e.source, {"keep": 0, "edit": 0, "drop": 0})
        b[e.action] += 1
    out: list[SourceFeedbackStats] = []
    for source in sorted(buckets):
        b = buckets[source]
        out.append(SourceFeedbackStats(
            source=source, keep=b["keep"], edit=b["edit"], drop=b["drop"],
            total=b["keep"] + b["edit"] + b["drop"]))
    return out
