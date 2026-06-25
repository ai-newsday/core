from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from src.adapters.decisions.worker import DecisionStore
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


def select_report_items(
    items: list[InterpretedItem], decisions: dict[str, ReviewDecision]
) -> list[InterpretedItem]:
    """确认门: 报告只收显式 keep/edit 的条目, 未决策 + drop 都排除。

    实现 spec(review.md §3.4 / publish.md)一直推迟给"发布层/CLI"的"未审自动发拦截":
    review 层仍默认 keep + 标 is_pending, 真正的"未确认不发"在 finalize 这层落地。
    决策仍按 link(由 item_id 解耦匹配而来)查, 不引入日期耦合(保留 #33)。
    """
    return [
        it
        for it in items
        if (dec := decisions.get(it.link)) is not None and dec.action in ("keep", "edit")
    ]


def _genre_label(genre_value: str) -> str:
    labels = {
        "paper": "论文",
        "model": "模型",
        "announcement": "官方",
        "writeup": "博客 / 工具",
        "news": "新闻",
    }
    return labels.get(genre_value, genre_value)


def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
    }


async def run_collect_tick(
    run_id: str,
    now: datetime,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
) -> None:
    """采集 tick: 把新候选写 DB + 推 Telegram 卡片。决策由 webhook 异步收集, finalize 时拉取。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "collect")
    emit(logger, "tick_collect_start", run_id=run_id, date=date, item_count=len(interpreted_items))
    pushed = 0
    for item in interpreted_items:
        if not item.relevant:
            continue
        item_id = _item_id(item)
        await db.upsert_pending_review(
            item_id=item_id,
            run_id=run_id,
            link=item.link,
            source=item.source,
            title_en=item.title_en,
            title_zh=item.title,
            summary_zh=item.body,
            takeaway="",
            hot_take="",
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
    emit(logger, "tick_collect_done", run_id=run_id, pushed=pushed)


async def run_finalize_tick(
    run_id: str,
    now: datetime,
    date_label: str,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
    decision_store: DecisionStore | None = None,
    site_base_url: str = "",
) -> dict:
    """定稿 tick: 读决策 → review → publish → send_final_report。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "finalize")
    emit(logger, "tick_finalize_start", run_id=run_id, date=date)
    # webhook 决策按 item_id 直接匹配本报条目(与采集日解耦); 失败降级=未审默认 keep
    decisions_raw: dict[str, str] = {}
    if decision_store is not None:
        try:
            remote = await decision_store.fetch()  # {item_id: action}
            id_to_link = {_item_id(it): it.link for it in interpreted_items}
            for item_id, action in remote.items():
                link = id_to_link.get(item_id)
                if link is not None and action in ("keep", "drop"):
                    decisions_raw[link] = action
                    await db.update_decision(item_id, action)  # 记录用, 无行则 no-op
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命
            emit(logger, "decisions_fetch_error", run_id=run_id, error=str(e))
    decisions = {link: ReviewDecision(action=action) for link, action in decisions_raw.items()}
    from src.core.types import RunContext

    ctx = RunContext(run_id=run_id, now=now, logger=logger)
    rcfg = load_review_config("config/review.yaml")
    # 条目选择: 有决策走确认门(只发 keep/edit); 零决策(没碰 TG / 拉取失败)兜底自动发,
    # 由 publish 的 relevant+地板(+配额)截 top-N。is_pending 仍 True → 草稿水印 + draft:true。
    # feedback 仍吃全量(下方)。
    if decisions:
        report_items = select_report_items(interpreted_items, decisions)
    else:
        report_items = list(interpreted_items)
    # 已发布去重: 排除已在别的 date_label 报告里发过的条目(72h 窗口内同条目跨天复发 → 去重)。
    already = await db.already_published_elsewhere(
        [_item_id(it) for it in report_items], date_label
    )
    report_items = [it for it in report_items if _item_id(it) not in already]
    rres = review(report_items, daily_take, decisions, rcfg, ctx)
    pcfg = load_publish_config("config/publish.yaml")
    pres = publish(rres, date_label, pcfg, ctx)
    # 记录本报已发布条目(按 date_label), 供后续 tick 跨天去重。首发 label 固定。
    await db.mark_published([_item_id(it) for it in report_items], date_label)
    summary = {
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "url": (site_base_url.rstrip("/") + "/posts/" + date_label + "/") if site_base_url else "",
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
    )
    # 反馈闭环 (PRD §4.5): 派生 → 幂等入账 → 增量重算权重 → 写回。非致命。
    if not await db.has_feedback_for_run(run_id):
        from src.core.config import load_feedback_config
        from src.pipeline.feedback import derive_events, feedback

        try:
            fcfg = load_feedback_config("config/feedback.yaml")
            run_events = derive_events(interpreted_items, decisions, run_id=run_id, now=now)
            await db.append_feedback_events(run_events)
            prior = await db.get_quality_weights()
            fres = feedback(run_events, prior, fcfg, ctx)
            if not fres.is_silent:
                await db.upsert_quality_weights(fres.quality_weights)
        except Exception as e:  # noqa: BLE001 - feedback persistence is non-fatal
            emit(logger, "feedback_persist_error", run_id=run_id, error=str(e))
    return {
        "run_id": run_id,
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "is_pending": pres.is_pending,
    }
