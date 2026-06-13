from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from src.core.config import load_publish_config, load_review_config
from src.core.types import InterpretedItem, ReviewDecision
from src.notifiers import Notifier
from src.observability.events import emit
from src.pipeline.publish import publish
from src.pipeline.review import review
from src.state.db import Database


def _item_id(item: InterpretedItem) -> str:
    """稳定唯一 ID: sha256(link) 前 16 字符。"""
    return hashlib.sha256(item.link.encode()).hexdigest()[:16]


def _source_type_label(source_type_value: str) -> str:
    labels = {
        "official": "官方",
        "paper": "论文",
        "model": "模型",
        "tool": "工具",
        "news": "新闻",
        "community": "社区",
        "blog": "博客",
    }
    return labels.get(source_type_value, source_type_value)


def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _source_type_label(item.source_type.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "summary_zh": item.summary,
        "takeaway": item.takeaway,
        "hot_take": item.hot_take,
    }


async def run_collect_tick(
    run_id: str,
    now: datetime,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
) -> None:
    """采集 tick: 把新候选写 DB + 推 Telegram 卡片，再 poll 决策写回 DB。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "collect")
    emit(logger, "tick_collect_start", run_id=run_id, date=date, item_count=len(interpreted_items))
    pushed = 0
    for item in interpreted_items:
        item_id = _item_id(item)
        await db.upsert_pending_review(
            item_id=item_id,
            run_id=run_id,
            link=item.link,
            source=item.source,
            title_en=item.title_en,
            title_zh=item.title,
            summary_zh=item.summary,
            takeaway=item.takeaway,
            hot_take=item.hot_take,
            score=item.score,
            signals=item.signals,
            date=date,
        )
        # 只推之前没发过卡片的条目（msg_id 仍为 NULL）
        rows = await db.get_pending_reviews_for_date(date)
        row = next((r for r in rows if r["item_id"] == item_id), None)
        if row and row["msg_id"] is None:
            card = _build_card(item)
            for notifier in notifiers:
                try:
                    msg_id = await notifier.send_review_card(item_id, card)
                    if msg_id is not None:
                        await db.update_msg_id(item_id, msg_id)
                except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
                    emit(logger, "notifier_send_error", item_id=item_id, error=str(e))
            pushed += 1
    # 收决策 — 支持循环轮询的 notifier 等待用户操作
    for notifier in notifiers:
        try:
            if pushed > 0 and hasattr(notifier, "poll_decisions_loop"):
                decisions = await notifier.poll_decisions_loop(expected=pushed, timeout_secs=120)
            else:
                decisions = await notifier.poll_decisions()
            for decision_item_id, action in decisions:
                await db.update_decision(decision_item_id, action)
        except Exception as e:  # noqa: BLE001 - notifier poll failure is non-fatal
            emit(logger, "notifier_poll_error", error=str(e))
    emit(logger, "tick_collect_done", run_id=run_id, pushed=pushed)


async def run_finalize_tick(
    run_id: str,
    now: datetime,
    date_label: str,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
) -> dict:
    """定稿 tick: 读决策 → review → publish → send_final_report。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "finalize")
    emit(logger, "tick_finalize_start", run_id=run_id, date=date)
    # 读累积决策（未审默认 keep，review 层自动处理无决策的条目）
    decisions_raw = await db.get_decisions_dict(date)
    decisions = {link: ReviewDecision(action=action) for link, action in decisions_raw.items()}
    from src.core.types import RunContext

    ctx = RunContext(run_id=run_id, now=now, logger=logger)
    rcfg = load_review_config("config/review.yaml")
    rres = review(interpreted_items, daily_take, decisions, rcfg, ctx)
    pcfg = load_publish_config("config/publish.yaml")
    pres = publish(rres, date_label, pcfg, ctx)
    summary = {
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "must_read_count": len(pres.report.must_read),
    }
    for notifier in notifiers:
        try:
            await notifier.send_final_report(pres.markdown, summary)
        except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
            emit(logger, "notifier_final_report_error", error=str(e))
    emit(
        logger,
        "tick_finalize_done",
        run_id=run_id,
        item_count=pres.report.item_count,
        must_read_count=len(pres.report.must_read),
    )
    return {
        "run_id": run_id,
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "must_read_count": len(pres.report.must_read),
        "is_pending": pres.is_pending,
    }
