from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import datetime

from src.adapters.decisions.worker import DecisionStore
from src.core.config import load_publish_config, load_review_config
from src.core.prompts import load_prompt
from src.core.types import (
    InterpretConfig,
    InterpretedItem,
    PublishConfig,
    ReviewDecision,
    ReviewResult,
)
from src.notifiers import Notifier
from src.observability.events import emit
from src.pipeline.interpret import generate_daily_head
from src.pipeline.publish import build_report, publish
from src.pipeline.review import review
from src.state.db import Database


def _item_id(item: InterpretedItem) -> str:
    """稳定唯一 ID: sha256(link) 前 16 字符。"""
    return hashlib.sha256(item.link.encode()).hexdigest()[:16]


def regenerate_wechat_head(
    rres: ReviewResult,
    date_label: str,
    publish_config: PublishConfig,
    interpret_config: InterpretConfig,
    llm,
    ctx,
) -> ReviewResult:
    """标题/摘要必须基于最终发布条目生成, 不能用配额筛选前的全量解读池
    (#139: 2026-09-02 实测标题引用了 ChatGPT Ads/Qwen3.8-Flash-Next, 但这两条
    从未出现在最终发布的六条正文里——旧实现在 interpret() 阶段就生成了标题,
    早于 build_report() 的地板/adapter 配额/故事线合并/genre 配额过滤)。

    先跑一次 build_report() 拿到过滤后的最终列表(纯函数, 重复调用零副作用,
    publish() 内部本来就会再跑一次), 用这份列表重新生成标题/摘要, 再把新值
    写回 ReviewResult 让 publish() 正常渲染。全部条目都被过滤掉时原样返回,
    不浪费一次 LLM 调用。"""
    report = build_report(rres, date_label, publish_config)
    if not report.item_count:
        return rres
    final_items = [it for cat in report.categories for it in cat.items]
    daily_tpl = load_prompt(interpret_config.daily_prompt_path)
    title, digest = generate_daily_head(
        final_items, daily_tpl, interpret_config, llm, date_label, logger=ctx.logger
    )
    emit(
        ctx.logger,
        "wechat_head_regenerated",
        final_item_count=len(final_items),
        ok=digest is not None,
    )
    return replace(
        rres, wechat_title=title, daily_take=digest if digest is not None else rres.daily_take
    )


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
        "status": item.interpretation_status,
        "entity_uncertain": any(f.code == "entity_uncertain" for f in item.quality_flags),
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
    wechat_title: str | None = None,
    llm=None,
    interpret_config: InterpretConfig | None = None,
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
            emit(
                logger,
                "decisions_fetch_error",
                run_id=run_id,
                error_type=type(e).__name__,
                error=str(e),
            )
    decisions = {link: ReviewDecision(action=action) for link, action in decisions_raw.items()}
    from src.core.types import RunContext

    ctx = RunContext(run_id=run_id, now=now, logger=logger)
    rcfg = load_review_config("config/review.yaml")
    # 确认门: 只有显式 keep/edit 的条目进报告(2026-08-06: 去掉零决策兜底自动发——
    # 用户没审的内容不结算; decisions={} 时 select_report_items 天然返回空列表)。
    # feedback 仍吃全量(下方)。
    report_items = select_report_items(interpreted_items, decisions)
    # 已发布去重: 排除已在别的 date_label 报告里发过的条目(72h 窗口内同条目跨天复发 → 去重)。
    already = await db.already_published_elsewhere(
        [_item_id(it) for it in report_items], date_label
    )
    report_items = [it for it in report_items if _item_id(it) not in already]
    rres = review(report_items, daily_take, decisions, rcfg, ctx, wechat_title=wechat_title)
    pcfg = load_publish_config("config/publish.yaml")
    if llm is not None and interpret_config is not None:
        rres = regenerate_wechat_head(rres, date_label, pcfg, interpret_config, llm, ctx)
    pres = publish(rres, date_label, pcfg, ctx)
    # 记录本报已发布条目(按 date_label), 供后续 tick 跨天去重。首发 label 固定。
    await db.mark_published([_item_id(it) for it in report_items], date_label)
    summary = {
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "url": (site_base_url.rstrip("/") + "/posts/" + date_label + "/") if site_base_url else "",
    }
    # 空报(零决策/全砍)不通知: WebsiteNotifier 会无条件写文件, 空 markdown 写出来
    # 就是一篇没有 front matter 的空文件; Telegram 也不该发"今天 0 条"的噪音消息。
    if not pres.is_silent:
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


def count_undecided(rows: list[dict], decisions_raw: dict[str, str]) -> int:
    """纯函数: 今天推过的卡片(rows, 每条含 item_id/link)里, 有多少条还没有远端
    keep/drop 决策。按 item_id 匹配(webhook 决策以 item_id 为键), 非 keep/drop
    的值(协议外/防御性)一律不算已决策。"""
    decided_ids = {iid for iid, action in decisions_raw.items() if action in ("keep", "drop")}
    return sum(1 for r in rows if r["item_id"] not in decided_ids)


async def run_reminder_tick(
    *,
    now: datetime,
    db: Database,
    decision_store: DecisionStore | None,
    notifiers: list[Notifier],
) -> dict:
    """22:00 提醒 tick: 数今天推过的卡片里还有多少条没审, 非零才发一条提醒消息。
    只报个数, 不列标题(简单够用); 决策拉取失败保守地把当天全部条目算作待审,
    不假装 0 条从而漏发提醒——错报"还有几条"好过错过一次真实提醒。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    rows = await db.get_pending_reviews_for_date(date)
    decisions_raw: dict[str, str] = {}
    if decision_store is not None:
        try:
            decisions_raw = await decision_store.fetch()
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命, 保守当全部待审
            emit(
                logger,
                "reminder_decisions_fetch_error",
                error_type=type(e).__name__,
                error=str(e),
            )
    undecided_count = count_undecided(rows, decisions_raw)
    if undecided_count > 0:
        for notifier in notifiers:
            try:
                await notifier.send_reminder(undecided_count)
            except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
                emit(logger, "notifier_reminder_error", error=str(e))
    emit(logger, "tick_reminder_done", date=date, undecided_count=undecided_count)
    return {"date": date, "undecided_count": undecided_count}
